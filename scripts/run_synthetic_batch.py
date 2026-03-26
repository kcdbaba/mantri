#!/usr/bin/env python3
"""
run_synthetic_batch.py

Reads evaluations_data.csv, creates a case directory for each row,
runs the testing prompt, and evaluates results. All outputs land in
data/cases/ alongside real cases.

Usage:
    python scripts/run_synthetic_batch.py
    python scripts/run_synthetic_batch.py --ids R4-A-L1-01 R4-B-L1-01   # subset
    python scripts/run_synthetic_batch.py --skip-existing                 # skip already-run cases
"""

import os
import re
import csv
import json
import argparse
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import anthropic
from run_test import run_test, evaluate, DEFAULT_MODEL

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(s: str, max_len: int = 50) -> str:
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:max_len].rstrip('_')


def framework_from_id(case_id: str) -> str:
    """R4-A-L1-01 → R4-A"""
    parts = case_id.split('-')
    return '-'.join(parts[:2]) if len(parts) >= 2 else case_id


def level_from_id(case_id: str) -> str:
    """R4-A-L1-01 → L1"""
    parts = case_id.split('-')
    return parts[2] if len(parts) >= 3 else ""


def build_metadata(row: dict) -> dict:
    return {
        "id":          row["id"],
        "name":        slugify(row["scenario"]),
        "framework":   framework_from_id(row["id"]),
        "level":       level_from_id(row["id"]),
        "sprint":      row["sprint"],
        "data_source": row["data_source"],
        "description": row["scenario"],
        "chat_inputs": None,
        "completeness": {"complete": True, "missing": []},
        "expected_output": row["expected_output"],
        "pass_criteria":   row["pass_criteria"],
        "challenge":       row["challenge"],
        "notes":           row["notes"],
    }


def case_dir_name(row: dict) -> str:
    return f"{row['id']}_{slugify(row['scenario'])}"


def setup_case(row: dict, cases_root: Path) -> Path:
    """Create case directory, write metadata.json and threads.txt. Returns case_dir."""
    case_dir = cases_root / case_dir_name(row)
    case_dir.mkdir(parents=True, exist_ok=True)

    meta_path = case_dir / "metadata.json"
    meta_path.write_text(
        json.dumps(build_metadata(row), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    threads_path = case_dir / "threads.txt"
    threads_path.write_text(row["input_threads"], encoding="utf-8")

    return case_dir


# ── Main ───────────────────────────────────────────────────────────────────────

def run_batch(ids_filter: list[str] | None, skip_existing: bool,
              model: str, cases_root: Path) -> None:
    csv_path = Path("data/evaluations_data.csv")
    if not csv_path.exists():
        raise FileNotFoundError(f"Not found: {csv_path}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY not set. Add it to .env")
    client = anthropic.Anthropic(api_key=api_key)

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Filter to requested IDs if specified
    if ids_filter:
        rows = [r for r in rows if r["id"] in ids_filter]
        if not rows:
            print(f"No rows matched IDs: {ids_filter}")
            return

    print(f"Running {len(rows)} synthetic case(s) with model {model}\n")

    results = []
    for i, row in enumerate(rows, 1):
        case_id  = row["id"]
        scenario = row["scenario"]
        print(f"[{i}/{len(rows)}] {case_id} — {scenario}")

        case_dir = cases_root / case_dir_name(row)

        if skip_existing and (case_dir / "score.json").exists():
            print("  Skipped (score.json exists)\n")
            score = json.loads((case_dir / "score.json").read_text())
            results.append(score)
            continue

        # Set up case directory
        setup_case(row, cases_root)

        # Run test
        try:
            run_test(case_dir, model, client)
        except Exception as e:
            print(f"  ✗ Test failed: {e}\n")
            results.append({"case_id": case_id, "verdict": "ERROR", "overall_score": 0,
                             "error": str(e)})
            continue

        # Evaluate
        try:
            score = evaluate(case_dir, model, client)
            results.append(score)
        except Exception as e:
            print(f"  ✗ Evaluation failed: {e}\n")
            results.append({"case_id": case_id, "verdict": "ERROR", "overall_score": 0,
                             "error": str(e)})
            continue

        print()

    # ── Summary report ─────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("BATCH SUMMARY")
    print("═" * 60)

    passed  = [r for r in results if r.get("verdict") == "PASS"]
    partial = [r for r in results if r.get("verdict") == "PARTIAL"]
    failed  = [r for r in results if r.get("verdict") == "FAIL"]
    errored = [r for r in results if r.get("verdict") == "ERROR"]

    scores = [r["overall_score"] for r in results if isinstance(r.get("overall_score"), (int, float))]
    avg    = sum(scores) / len(scores) if scores else 0

    print(f"  Total   : {len(results)}")
    print(f"  PASS    : {len(passed)}")
    print(f"  PARTIAL : {len(partial)}")
    print(f"  FAIL    : {len(failed)}")
    print(f"  ERROR   : {len(errored)}")
    print(f"  Avg score: {avg:.1f}/100")
    print()

    # Per-framework breakdown
    by_framework: dict[str, list] = {}
    for r in results:
        fw = r.get("framework", "?")
        by_framework.setdefault(fw, []).append(r)

    print("  By framework:")
    for fw, fw_results in sorted(by_framework.items()):
        fw_scores = [r["overall_score"] for r in fw_results
                     if isinstance(r.get("overall_score"), (int, float))]
        fw_avg = sum(fw_scores) / len(fw_scores) if fw_scores else 0
        verdicts = [r.get("verdict", "?")[0] for r in fw_results]  # P/F/E initials
        print(f"    {fw:<8} avg={fw_avg:>5.1f}  [{' '.join(verdicts)}]")

    # Save summary
    summary = {
        "run_at":   datetime.now().isoformat(timespec="seconds"),
        "model":    model,
        "total":    len(results),
        "passed":   len(passed),
        "partial":  len(partial),
        "failed":   len(failed),
        "errored":  len(errored),
        "avg_score": round(avg, 1),
        "results":  results,
    }
    summary_path = cases_root / "synthetic_batch_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Full summary: {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids",           nargs="+", help="Run only these case IDs")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip cases that already have a score.json")
    parser.add_argument("--model",         default=DEFAULT_MODEL,
                        help=f"Model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    run_batch(
        ids_filter=args.ids,
        skip_existing=args.skip_existing,
        model=args.model,
        cases_root=Path("data/cases"),
    )
