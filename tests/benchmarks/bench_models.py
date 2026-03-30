"""
LLM model benchmark for update_agent.

Runs a sample of messages from the integration replay trace through the
update_agent prompt with different model backends. Compares:
  - Parse success rate (valid JSON + Pydantic validation)
  - Output similarity to Sonnet baseline (node_updates, items, ambiguity)
  - Latency (seconds per call)
  - Cost (estimated per call)

Usage:
    PYTHONPATH=. python tests/benchmarks/bench_models.py [--models gemini,haiku,qwen,gemma] [--n 20]
"""

import argparse
import json
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from src.agent.prompt import build_system_prompt, build_user_section
from src.agent.update_agent import AgentOutput, _parse_raw
from src.agent.templates import get_template
from src.store.db import SCHEMA

log = logging.getLogger(__name__)

CASE_DIR = Path("tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier")
RESULTS_DIR = Path("tests/benchmarks/results")

# Cost per 1M tokens (input/output)
COST_TABLE = {
    "claude-sonnet-4-6":      {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "gemini-2.5-flash":       {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro":         {"input": 1.25, "output": 10.00},
    "sarvam-30b":             {"input": 0.00, "output": 0.00},  # currently free
    "sarvam-105b":            {"input": 0.00, "output": 0.00},  # currently free
    "deepseek-chat":          {"input": 0.27, "output": 1.10},
    "qwen2.5:7b":             {"input": 0.00, "output": 0.00},  # local
    "gemma3:12b":             {"input": 0.00, "output": 0.00},  # local
}


def _seed_temp_db(seed: dict) -> str:
    """Create a temp DB with seeded tasks for prompt building."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript(SCHEMA)
    now = int(time.time())
    for task in seed["tasks"]:
        conn.execute(
            """INSERT INTO task_instances
               (id, order_type, client_id, supplier_ids, created_at, last_updated, stage, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (task["id"], task["order_type"], task["client_id"],
             json.dumps(task.get("supplier_ids", [])),
             now, now, task.get("stage", "active"), "benchmark_seed"),
        )
        template = get_template(task["order_type"])
        for node in template["nodes"]:
            default_status = "skipped" if node.get("optional") else "pending"
            conn.execute(
                """INSERT OR IGNORE INTO task_nodes
                   (id, task_id, node_type, name, status, updated_at, updated_by,
                    optional, requires_all, warns_if_incomplete)
                   VALUES (?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?)""",
                (f"{task['id']}_{node['id']}", task["id"], node["type"], node["name"],
                 default_status, now,
                 1 if node.get("optional") else 0,
                 json.dumps(node.get("requires_all", [])),
                 json.dumps(node.get("warns_if_incomplete", [])),),
            )
    conn.commit()
    conn.close()
    return tmp.name


def _get_node_states(db_path: str, task_id: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM task_nodes WHERE task_id=? ORDER BY id", (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _select_sample_messages(trace: list[dict], n: int) -> list[dict]:
    """Select a diverse sample of messages for benchmarking."""
    # Filter to messages with body text (skip empty/media-only)
    with_body = [m for m in trace if (m.get("body") or "").strip()]

    # Stratify: short (<20 chars), medium (20-80), complex (80+)
    short = [m for m in with_body if len(m["body"]) < 20]
    medium = [m for m in with_body if 20 <= len(m["body"]) < 80]
    long = [m for m in with_body if len(m["body"]) >= 80]

    # Take proportional samples
    import random
    random.seed(42)
    total = len(short) + len(medium) + len(long)
    n_short = max(1, round(n * len(short) / total))
    n_medium = max(1, round(n * len(medium) / total))
    n_long = max(1, n - n_short - n_medium)

    sample = (
        random.sample(short, min(n_short, len(short))) +
        random.sample(medium, min(n_medium, len(medium))) +
        random.sample(long, min(n_long, len(long)))
    )
    return sample[:n]


# ---------------------------------------------------------------------------
# Model backends
# ---------------------------------------------------------------------------

def call_anthropic(system: str, user: str, model: str) -> dict:
    """Call Anthropic API, return {raw, tokens_in, tokens_out, duration_s}."""
    import anthropic
    client = anthropic.Anthropic()
    t0 = time.time()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        duration = time.time() - t0
        return {
            "raw": response.content[0].text,
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
            "duration_s": duration,
            "error": None,
        }
    except Exception as e:
        return {"raw": "", "tokens_in": 0, "tokens_out": 0,
                "duration_s": time.time() - t0, "error": str(e)}


def call_gemini(system: str, user: str, model: str = "gemini-2.5-flash") -> dict:
    """Call Gemini API via google-genai SDK."""
    from google import genai
    from google.genai import types
    import os
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    t0 = time.time()
    try:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=4096,
            temperature=0.0,
            response_mime_type="application/json",
        )
        # Flash: disable thinking to avoid token budget consumption
        # Pro: requires thinking, set minimal budget
        if "flash" in model:
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif "pro" in model:
            config.thinking_config = types.ThinkingConfig(thinking_budget=1024)
        response = client.models.generate_content(
            model=model,
            contents=user,
            config=config,
        )
        duration = time.time() - t0
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
        return {
            "raw": response.text or "",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration,
            "error": None,
        }
    except Exception as e:
        return {"raw": "", "tokens_in": 0, "tokens_out": 0,
                "duration_s": time.time() - t0, "error": str(e)}


def call_ollama(system: str, user: str, model: str) -> dict:
    """Call local Ollama model via OpenAI-compatible API."""
    from openai import OpenAI
    client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    t0 = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1024,
            temperature=0.0,
        )
        duration = time.time() - t0
        raw = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        return {
            "raw": raw,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration,
            "error": None,
        }
    except Exception as e:
        return {"raw": "", "tokens_in": 0, "tokens_out": 0,
                "duration_s": time.time() - t0, "error": str(e)}


def call_sarvam(system: str, user: str, model: str = "sarvam-30b") -> dict:
    """Call Sarvam AI API."""
    from sarvamai import SarvamAI
    client = SarvamAI()
    t0 = time.time()
    try:
        response = client.chat.completions(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=4096,
            temperature=0.0,
            reasoning_effort="low",
        )
        duration = time.time() - t0
        msg = response.choices[0].message
        raw = msg.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        return {
            "raw": raw,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration,
            "error": None,
        }
    except Exception as e:
        return {"raw": "", "tokens_in": 0, "tokens_out": 0,
                "duration_s": time.time() - t0, "error": str(e)}


def call_deepseek(system: str, user: str, model: str = "deepseek-chat") -> dict:
    """Call DeepSeek API via OpenAI-compatible endpoint."""
    from openai import OpenAI
    client = OpenAI(
        base_url="https://api.deepseek.com",
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )
    t0 = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=2048,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        duration = time.time() - t0
        raw = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0
        return {
            "raw": raw,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration,
            "error": None,
        }
    except Exception as e:
        return {"raw": "", "tokens_in": 0, "tokens_out": 0,
                "duration_s": time.time() - t0, "error": str(e)}


MODEL_BACKENDS = {
    "sonnet":      lambda s, u: call_anthropic(s, u, "claude-sonnet-4-6"),
    "haiku":       lambda s, u: call_anthropic(s, u, "claude-haiku-4-5-20251001"),
    "gemini":      lambda s, u: call_gemini(s, u, "gemini-2.5-flash"),
    "gemini-pro":  lambda s, u: call_gemini(s, u, "gemini-2.5-pro"),
    "sarvam-30b":  lambda s, u: call_sarvam(s, u, "sarvam-30b"),
    "sarvam-105b": lambda s, u: call_sarvam(s, u, "sarvam-105b"),
    "deepseek":    lambda s, u: call_deepseek(s, u, "deepseek-chat"),
    "qwen":        lambda s, u: call_ollama(s, u, "qwen2.5:7b"),
    "gemma":       lambda s, u: call_ollama(s, u, "gemma3:12b"),
}

MODEL_IDS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "gemini": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
    "sarvam-30b": "sarvam-30b",
    "sarvam-105b": "sarvam-105b",
    "deepseek": "deepseek-chat",
    "qwen": "qwen2.5:7b",
    "gemma": "gemma3:12b",
}


def _parse_raw(raw: str) -> AgentOutput | None:
    """Try to parse raw model output into AgentOutput."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(cleaned)
        # Some models wrap output in an array
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        return AgentOutput.model_validate(data)
    except Exception:
        return None


def _compare_outputs(baseline: AgentOutput | None, candidate: AgentOutput | None) -> dict:
    """Compare candidate output to Sonnet baseline."""
    if baseline is None or candidate is None:
        return {"match": False, "node_match": False, "item_match": False, "ambiguity_match": False}

    # Node updates: compare set of (node_id, status) pairs
    base_nodes = {(u.node_id, u.new_status) for u in baseline.node_updates}
    cand_nodes = {(u.node_id, u.new_status) for u in candidate.node_updates}
    node_match = base_nodes == cand_nodes

    # Item extractions: compare set of (operation, description) pairs
    base_items = {(i.operation, i.description.lower()) for i in baseline.item_extractions}
    cand_items = {(i.operation, i.description.lower()) for i in candidate.item_extractions}
    item_match = base_items == cand_items

    # Ambiguity: compare count and severity distribution
    base_sev = sorted([f.severity for f in baseline.ambiguity_flags])
    cand_sev = sorted([f.severity for f in candidate.ambiguity_flags])
    ambiguity_match = base_sev == cand_sev

    return {
        "match": node_match and item_match and ambiguity_match,
        "node_match": node_match,
        "item_match": item_match,
        "ambiguity_match": ambiguity_match,
        "base_nodes": len(base_nodes),
        "cand_nodes": len(cand_nodes),
        "node_overlap": len(base_nodes & cand_nodes),
        "base_items": len(base_items),
        "cand_items": len(cand_items),
        "base_ambiguity": len(baseline.ambiguity_flags),
        "cand_ambiguity": len(candidate.ambiguity_flags),
    }


def _estimate_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    model_id = MODEL_IDS.get(model_key, model_key)
    costs = COST_TABLE.get(model_id, {"input": 0, "output": 0})
    return (tokens_in * costs["input"] + tokens_out * costs["output"]) / 1_000_000


def run_benchmark(models: list[str], n_samples: int):
    """Run the full benchmark."""
    trace = json.loads((CASE_DIR / "replay_trace.json").read_text())
    seed = json.loads((CASE_DIR / "seed_tasks.json").read_text())

    db_path = _seed_temp_db(seed)
    task_id = seed["tasks"][0]["id"]  # primary task
    task = seed["tasks"][0]

    from unittest.mock import patch
    with patch("src.store.db.DB_PATH", db_path), \
         patch("src.config.DB_PATH", db_path):

        node_states = _get_node_states(db_path, task_id)
        system_prompt = build_system_prompt(task_id, task=task)
        sample = _select_sample_messages(trace, n_samples)

        print(f"\nBenchmark: {len(sample)} messages × {len(models)} models")
        print(f"System prompt: ~{len(system_prompt)} chars")
        print(f"Models: {', '.join(models)}")
        print(f"{'='*70}\n")

        results = {m: [] for m in models}

        for i, msg in enumerate(sample):
            body = (msg.get("body") or "")[:50]
            print(f"[{i+1}/{len(sample)}] \"{body}\"")

            user_section = build_user_section(
                node_states, [], [msg], [], routing_confidence=0.9
            )

            for model_key in models:
                backend = MODEL_BACKENDS[model_key]
                result = backend(system_prompt, user_section)

                parsed = None
                if not result["error"]:
                    parsed = _parse_raw(result["raw"])

                cost = _estimate_cost(model_key, result["tokens_in"], result["tokens_out"])

                entry = {
                    "message_id": msg.get("message_id", f"msg_{i}"),
                    "body": (msg.get("body") or "")[:80],
                    "parsed_ok": parsed is not None,
                    "tokens_in": result["tokens_in"],
                    "tokens_out": result["tokens_out"],
                    "duration_s": round(result["duration_s"], 2),
                    "cost_usd": round(cost, 6),
                    "error": result["error"],
                    "output": parsed,
                }
                results[model_key].append(entry)

                status = "OK" if parsed else ("ERR" if result["error"] else "PARSE_FAIL")
                print(f"  {model_key:8s}: {status:10s} {result['duration_s']:.1f}s "
                      f"${cost:.4f} "
                      f"({result['tokens_in']}→{result['tokens_out']} tok)")

            print()

    # Compare all models against Sonnet baseline
    baseline_key = "sonnet" if "sonnet" in models else models[0]

    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY (baseline: {baseline_key})")
    print(f"{'='*70}\n")

    summary = {}
    for model_key in models:
        entries = results[model_key]
        n_ok = sum(1 for e in entries if e["parsed_ok"])
        total_cost = sum(e["cost_usd"] for e in entries)
        avg_latency = sum(e["duration_s"] for e in entries) / max(len(entries), 1)
        avg_tokens_in = sum(e["tokens_in"] for e in entries) / max(len(entries), 1)
        avg_tokens_out = sum(e["tokens_out"] for e in entries) / max(len(entries), 1)

        # Compare against baseline
        comparisons = []
        if model_key != baseline_key:
            for j, entry in enumerate(entries):
                base_entry = results[baseline_key][j]
                comp = _compare_outputs(base_entry["output"], entry["output"])
                comparisons.append(comp)

        node_match_rate = 0
        item_match_rate = 0
        full_match_rate = 0
        if comparisons:
            node_match_rate = sum(1 for c in comparisons if c["node_match"]) / len(comparisons)
            item_match_rate = sum(1 for c in comparisons if c["item_match"]) / len(comparisons)
            full_match_rate = sum(1 for c in comparisons if c["match"]) / len(comparisons)

        summary[model_key] = {
            "parse_rate": f"{n_ok}/{len(entries)}",
            "parse_pct": round(100 * n_ok / max(len(entries), 1), 1),
            "avg_latency_s": round(avg_latency, 2),
            "avg_tokens_in": round(avg_tokens_in),
            "avg_tokens_out": round(avg_tokens_out),
            "total_cost": round(total_cost, 4),
            "cost_per_call": round(total_cost / max(len(entries), 1), 5),
            "node_match_rate": round(100 * node_match_rate, 1) if comparisons else "baseline",
            "item_match_rate": round(100 * item_match_rate, 1) if comparisons else "baseline",
            "full_match_rate": round(100 * full_match_rate, 1) if comparisons else "baseline",
        }

        print(f"  {model_key:8s}: parse={summary[model_key]['parse_rate']} "
              f"({summary[model_key]['parse_pct']}%)  "
              f"latency={summary[model_key]['avg_latency_s']}s  "
              f"cost=${summary[model_key]['cost_per_call']}/call  "
              f"node_match={summary[model_key]['node_match_rate']}%  "
              f"full_match={summary[model_key]['full_match_rate']}%")

    # Estimate daily cost at Ashish's volume
    print(f"\n{'='*70}")
    print("ESTIMATED DAILY COST (150 update_agent calls/day)")
    print(f"{'='*70}\n")
    for model_key in models:
        daily = summary[model_key]["cost_per_call"] * 150
        print(f"  {model_key:8s}: ${daily:.2f}/day  (${daily*30:.0f}/month)")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    out_path = RESULTS_DIR / f"bench_{ts}.json"
    out_path.write_text(json.dumps({
        "timestamp": ts,
        "n_samples": n_samples,
        "models": models,
        "summary": summary,
        "details": {k: [{**e, "output": None} for e in v] for k, v in results.items()},
    }, indent=2, default=str))
    print(f"\nResults saved to: {out_path}")

    # Cleanup
    Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="sonnet,haiku,gemini",
                        help="Comma-separated model keys: sonnet,haiku,gemini,qwen,gemma")
    parser.add_argument("--n", type=int, default=10, help="Number of sample messages")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    models = [m.strip() for m in args.models.split(",")]
    run_benchmark(models, args.n)
