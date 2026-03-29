#!/usr/bin/env python3
"""
log_eval_scores.py

Pushes pre-computed eval scores (score.json files) to Phoenix as named experiments.
Run after /eval-real or /eval-synth to make results visible in the Phoenix UI.

Usage:
    # Single case (after /eval-real --case ...)
    python scripts/log_eval_scores.py --case data/cases/R4-A-L1-01_... --suite eval-real

    # All cases in a directory
    python scripts/log_eval_scores.py --dir data/cases --suite eval-real

    # All cases from a batch summary (after /eval-synth)
    python scripts/log_eval_scores.py --batch data/cases/synthetic_batch_summary.json --suite eval-synth
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


# ── Phoenix auto-start ─────────────────────────────────────────────────────────

def _ensure_phoenix():
    pid_file = os.path.expanduser("~/.phoenix.pid")
    running = False
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)
            running = True
        except (ProcessLookupError, ValueError):
            os.remove(pid_file)
    if not running:
        script = Path(__file__).parent / "phoenix_bg.sh"
        subprocess.run(["bash", str(script)], check=True)
        time.sleep(2)


# ── Score loading ──────────────────────────────────────────────────────────────

def _load_score(case_dir: Path) -> dict | None:
    score_path = case_dir / "score.json"
    if not score_path.exists():
        return None
    score = json.loads(score_path.read_text(encoding="utf-8"))

    threads_path = case_dir / "threads.txt"
    if threads_path.exists():
        score["_threads_preview"] = threads_path.read_text(encoding="utf-8")[:2000]

    meta_path = case_dir / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        score["_expected_output"] = meta.get("expected_output", "")
        score["_pass_criteria"] = meta.get("pass_criteria", "")
        score["_description"] = meta.get("description", "")

    score["_case_dir"] = str(case_dir)
    return score


# ── Phoenix upload ─────────────────────────────────────────────────────────────

ALL_DIMENSIONS = [
    "task_recall", "entity_accuracy", "cross_thread_correlation",
    "next_step_quality", "implicit_task_detection", "ambiguity_flagging",
]


def push_to_phoenix(scores: list[dict], suite_name: str) -> None:
    import phoenix as px
    from phoenix.experiments import run_experiment

    _ensure_phoenix()
    client = px.Client()

    dataset = client.upload_dataset(
        dataset_name=f"mantri-{suite_name}",
        inputs=[
            {
                "case_id":          s.get("case_id", "?"),
                "framework":        s.get("framework", ""),
                "description":      s.get("_description", ""),
                "threads_preview":  s.get("_threads_preview", ""),
            }
            for s in scores
        ],
        outputs=[
            {
                "expected_output": s.get("_expected_output", ""),
                "pass_criteria":   s.get("_pass_criteria", ""),
            }
            for s in scores
        ],
        metadata=[{"case_id": s.get("case_id")} for s in scores],
    )

    scores_by_id = {s.get("case_id"): s for s in scores}

    # Passthrough task — returns the pre-computed score dict
    def passthrough(input):  # noqa: A002
        return scores_by_id.get(input["case_id"], {"verdict": "ERROR", "overall_score": 0})

    # Evaluators
    def overall_score(output) -> float:
        return (output.get("overall_score") or 0) / 100.0

    def verdict_label(output) -> str:
        return output.get("verdict", "ERROR")

    def _make_dim_eval(dim: str):
        def evaluator(output) -> float | None:
            d = (output.get("dimensions") or {}).get(dim, {})
            score = d.get("score") if d else None
            return score / 100.0 if score is not None else None
        evaluator.__name__ = dim
        return evaluator

    evaluators: dict = {"overall_score": overall_score, "verdict": verdict_label}
    for dim in ALL_DIMENSIONS:
        evaluators[dim] = _make_dim_eval(dim)

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    exp_name = f"{suite_name}-{ts}"
    run_experiment(
        dataset,
        task=passthrough,
        evaluators=evaluators,
        experiment_name=exp_name,
        print_summary=False,
    )
    print(f"  Phoenix: http://localhost:6006  (dataset: mantri-{suite_name}, experiment: {exp_name})")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case",  metavar="DIR",  help="Single case directory")
    group.add_argument("--dir",   metavar="DIR",  help="Scan directory for score.json files")
    group.add_argument("--batch", metavar="FILE",
                       help="Batch summary JSON (data/cases/synthetic_batch_summary.json)")
    parser.add_argument("--suite", default="eval-real",
                        choices=["eval-real", "eval-synth"],
                        help="Suite name (default: eval-real)")
    args = parser.parse_args()

    scores: list[dict] = []

    if args.case:
        s = _load_score(Path(args.case))
        if s:
            scores.append(s)
        else:
            print(f"No score.json found in {args.case}")
            return

    elif args.dir:
        for case_dir in sorted(Path(args.dir).iterdir()):
            if not case_dir.is_dir():
                continue
            s = _load_score(case_dir)
            if s:
                scores.append(s)
        print(f"Found {len(scores)} scored cases in {args.dir}")

    elif args.batch:
        summary = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        for r in summary.get("results", []):
            if r.get("verdict") not in ("ERROR", None):
                scores.append(r)
        print(f"Loaded {len(scores)} results from {args.batch}")

    if not scores:
        print("No scores to push.")
        return

    print(f"Pushing {len(scores)} scores to Phoenix (suite: {args.suite})...")
    push_to_phoenix(scores, args.suite)


if __name__ == "__main__":
    main()
