#!/usr/bin/env python3
"""
run_test.py

Runs the testing prompt against a case's threads.txt, saves agent_output.txt.
Optionally runs LLM-as-judge evaluation and saves score.json.

Usage:
    # Run test only
    python scripts/run_test.py --case data/cases/R1-D-L3-01_sata_multi_item_multi_supplier

    # Run test + evaluate
    python scripts/run_test.py --case data/cases/R1-D-L3-01_sata_multi_item_multi_supplier --evaluate

    # Evaluate only (agent_output.txt already exists)
    python scripts/run_test.py --case data/cases/... --evaluate --skip-run

    # Override model (default: claude-sonnet-4-6)
    python scripts/run_test.py --case data/cases/... --model claude-opus-4-6
"""

import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import os
import signal
import subprocess
import time
import anthropic
from phoenix.otel import register as phoenix_register
from openinference.instrumentation.anthropic import AnthropicInstrumentor


def _ensure_phoenix():
    """Start Phoenix in the background if it isn't already running."""
    pid_file = os.path.expanduser("~/.phoenix.pid")
    running = False
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)  # signal 0 = existence check only
            running = True
        except (ProcessLookupError, ValueError):
            os.remove(pid_file)

    if not running:
        print("Phoenix not running — starting in background...")
        script = os.path.join(os.path.dirname(__file__), "phoenix_bg.sh")
        subprocess.run(["bash", script], check=True)
        time.sleep(2)  # give the server a moment to bind


_ensure_phoenix()
phoenix_register(project_name="mantri")
AnthropicInstrumentor().instrument()

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL    = "claude-sonnet-4-6"
EVAL_MODEL       = "claude-sonnet-4-6"   # evaluator always runs on Sonnet for consistency
TESTING_PROMPT   = Path("prompts/testing_prompt.txt")


def _model_slug(model: str) -> str:
    """Convert model name to a safe filename suffix. e.g. gemini-2.0-flash → gemini-2.0-flash"""
    return re.sub(r"[^a-z0-9\-.]", "-", model.lower())


def _output_paths(case_dir: Path, model: str) -> tuple[Path, Path]:
    """Return (agent_output_path, score_path) namespaced by model if non-default."""
    if model == DEFAULT_MODEL:
        return case_dir / "agent_output.txt", case_dir / "score.json"
    slug = _model_slug(model)
    return case_dir / f"agent_output_{slug}.txt", case_dir / f"score_{slug}.json"


def _call_gemini(model: str, system_prompt: str, user_content: str, max_tokens: int) -> str:
    """Call a Gemini model via google-genai SDK. Returns response text."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EnvironmentError("GOOGLE_API_KEY not set. Add it to .env")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_content,
        config=gtypes.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text

# Which quality dimensions apply to each framework prefix
# Dimensions not listed for a framework are scored as null
FRAMEWORK_DIMENSIONS = {
    "R4-A": ["entity_accuracy", "cross_thread_correlation", "ambiguity_flagging"],
    "R4-B": ["entity_accuracy", "cross_thread_correlation", "ambiguity_flagging"],
    "R3-C": ["task_recall", "entity_accuracy", "cross_thread_correlation", "ambiguity_flagging"],
    "R1-D": ["task_recall", "implicit_task_detection", "next_step_quality",
              "cross_thread_correlation", "ambiguity_flagging"],
    "R5":   ["next_step_quality", "ambiguity_flagging"],
    "R6":   ["task_recall", "implicit_task_detection", "ambiguity_flagging"],
    "R2":   ["task_recall", "implicit_task_detection", "next_step_quality"],
}

ALL_DIMENSIONS = [
    "task_recall",
    "entity_accuracy",
    "cross_thread_correlation",
    "next_step_quality",
    "implicit_task_detection",
    "ambiguity_flagging",
]

DIMENSION_DESCRIPTIONS = {
    "task_recall":              "Are all tasks — including implicit ones — identified? Missing a task is the most costly failure.",
    "entity_accuracy":          "Are the right customers, suppliers, items, and orders linked to each task?",
    "cross_thread_correlation": "Are messages about the same order correctly unified across multiple threads?",
    "next_step_quality":        "Are suggested next steps correct and actionable?",
    "implicit_task_detection":  "Does the agent recognise situations that imply a required action even when not explicitly stated?",
    "ambiguity_flagging":       "Does the agent flag uncertainty for human review rather than silently guessing wrong?",
}

# ── Judge prompt ───────────────────────────────────────────────────────────────

JUDGE_SYSTEM = """You are an expert evaluator for an AI operations monitoring agent built for an Army supply business in Guwahati, India.

The agent's job is to read multiple WhatsApp threads, correlate orders across threads, and produce a unified hierarchical task list.

You will be given:
1. The case metadata (what was being tested, expected output, pass criteria)
2. The agent's actual output
3. The quality dimensions to score for this specific case

Your job is to evaluate the agent's output and return a structured JSON score.

Scoring guidance:
- 90-100: Excellent. Fully meets criteria with no meaningful gaps.
- 70-89:  Good. Meets most criteria, minor gaps that wouldn't cause operational failures.
- 50-69:  Partial. Meets some criteria, meaningful gaps that could cause issues.
- 20-49:  Poor. Major failures against core criteria.
- 0-19:   Fail. Does not meet the criteria at all.

For dimensions not listed in active_dimensions, return null.

Return only valid JSON matching this exact schema — no markdown fences:
{
  "verdict": "PASS" | "PARTIAL" | "FAIL",
  "overall_score": <0-100>,
  "dimensions": {
    "task_recall":              {"score": <0-100 or null>, "notes": "<specific observations>"},
    "entity_accuracy":          {"score": <0-100 or null>, "notes": "<specific observations>"},
    "cross_thread_correlation": {"score": <0-100 or null>, "notes": "<specific observations>"},
    "next_step_quality":        {"score": <0-100 or null>, "notes": "<specific observations>"},
    "implicit_task_detection":  {"score": <0-100 or null>, "notes": "<specific observations>"},
    "ambiguity_flagging":       {"score": <0-100 or null>, "notes": "<specific observations>"}
  },
  "pass_criteria_met": <true | false>,
  "passes": ["<specific things the agent got right>"],
  "failures": ["<specific things the agent got wrong or missed>"],
  "notes": "<overall assessment and key observations>"
}"""

# ── Run test ───────────────────────────────────────────────────────────────────

def run_test(case_dir: Path, model: str, client: anthropic.Anthropic) -> str:
    threads_path = case_dir / "threads.txt"
    if not threads_path.exists():
        raise FileNotFoundError(
            f"threads.txt not found in {case_dir}. Run case_extractor.py first."
        )

    system_prompt = TESTING_PROMPT.read_text(encoding="utf-8")
    threads       = threads_path.read_text(encoding="utf-8")
    out_path, _   = _output_paths(case_dir, model)

    print(f"  Model  : {model}")
    print(f"  Threads: {threads_path} ({len(threads):,} chars)")

    if model.startswith("gemini"):
        output = _call_gemini(model, system_prompt, threads, max_tokens=16000)
    else:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system_prompt,
            messages=[{"role": "user", "content": threads}],
        )
        output = response.content[0].text

    out_path.write_text(output, encoding="utf-8")
    print(f"  Output : {out_path} ({len(output):,} chars)")
    return output


# ── Evaluate ───────────────────────────────────────────────────────────────────

def _get_active_dimensions(framework: str) -> list[str]:
    """Return relevant dimensions for a framework prefix (e.g. 'R1-D' or 'R3-C')."""
    for prefix, dims in FRAMEWORK_DIMENSIONS.items():
        if framework.startswith(prefix):
            return dims
    return ALL_DIMENSIONS  # fallback: score everything


def evaluate(case_dir: Path, model: str, client: anthropic.Anthropic) -> dict:
    meta_path   = case_dir / "metadata.json"
    agent_out_path, score_path = _output_paths(case_dir, model)

    if not meta_path.exists():
        raise FileNotFoundError(f"metadata.json not found in {case_dir}")
    if not agent_out_path.exists():
        raise FileNotFoundError(f"{agent_out_path.name} not found. Run test first.")

    metadata     = json.loads(meta_path.read_text(encoding="utf-8"))
    agent_output = agent_out_path.read_text(encoding="utf-8")
    framework    = metadata.get("framework", "")
    active_dims  = _get_active_dimensions(framework)

    dim_descriptions = {k: v for k, v in DIMENSION_DESCRIPTIONS.items() if k in active_dims}

    judge_input = f"""## Case metadata

Case ID       : {metadata.get('id', '')}
Framework     : {framework}
Level         : {metadata.get('level', '')}
Description   : {metadata.get('description', '')}
Expected output:
{metadata.get('expected_output', '(not specified)')}

Pass criteria :
{metadata.get('pass_criteria', '(not specified)')}

Completeness note: {json.dumps(metadata.get('completeness', {}), indent=2)}

Additional notes: {metadata.get('notes', '')}

## Active quality dimensions to score

{json.dumps(dim_descriptions, indent=2)}

(All other dimensions should be scored as null.)

## Agent output

{agent_output}"""

    print(f"  Evaluating with {EVAL_MODEL}...")
    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=4096,
        system=JUDGE_SYSTEM,
        messages=[{"role": "user", "content": judge_input}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw.strip())

    score = json.loads(raw)

    # Stamp metadata
    score["case_id"]    = metadata.get("id", case_dir.name)
    score["case_name"]  = metadata.get("name", "")
    score["framework"]  = framework
    score["level"]      = metadata.get("level", "")
    score["evaluated_at"] = datetime.now().isoformat(timespec="seconds")
    score["model_used"]   = model
    score["eval_model"]   = EVAL_MODEL

    score_path.write_text(json.dumps(score, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Score  : {score_path}")
    _print_score_summary(score)
    return score


def _print_score_summary(score: dict) -> None:
    verdict = score.get("verdict", "?")
    overall = score.get("overall_score", "?")
    print(f"\n  ┌─ Verdict: {verdict}  Overall: {overall}/100")
    for dim, result in score.get("dimensions", {}).items():
        if result and result.get("score") is not None:
            s = result["score"]
            bar = "█" * (s // 10) + "░" * (10 - s // 10)
            print(f"  │  {dim:<30} {bar} {s:>3}")
    print(f"  └─ Pass criteria met: {score.get('pass_criteria_met', '?')}")
    if score.get("failures"):
        print(f"\n  Failures:")
        for f in score["failures"]:
            print(f"    ✗ {f}")
    if score.get("passes"):
        print(f"\n  Passes:")
        for p in score["passes"]:
            print(f"    ✓ {p}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case",      required=True, help="Case directory path")
    parser.add_argument("--evaluate",  action="store_true",
                        help="Run LLM-as-judge evaluation after the test")
    parser.add_argument("--skip-run",  action="store_true",
                        help="Skip the test run (use existing agent_output.txt)")
    parser.add_argument("--model",     default=DEFAULT_MODEL,
                        help=f"Model to use (default: {DEFAULT_MODEL})")
    parser.add_argument("--eval-model", default=EVAL_MODEL,
                        help=f"Model for LLM-as-judge (default: {EVAL_MODEL}, always Sonnet for consistency)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set. Add it to .env")
    client   = anthropic.Anthropic(api_key=api_key)
    case_dir = Path(args.case)

    if not args.skip_run:
        print(f"\nRunning test: {case_dir.name}")
        run_test(case_dir, args.model, client)

    if args.evaluate:
        print(f"\nEvaluating: {case_dir.name}")
        evaluate(case_dir, args.model, client)  # model sets file paths; eval always uses EVAL_MODEL
