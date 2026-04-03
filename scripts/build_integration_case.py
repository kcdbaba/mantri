#!/usr/bin/env python3
"""
build_integration_case.py

One-command pipeline to create a complete integration test case from raw
WhatsApp chat directories and a time window.

Generates (in order):
  1. tests/evals/<case_name>/metadata.json
  2. tests/integration_tests/<case_name>/seed_tasks.json
  3. tests/integration_tests/<case_name>/replay_trace.json
  4. tests/integration_tests/<case_name>/expected_routing.json

Usage:
    PYTHONPATH=. python scripts/build_integration_case.py \
        --id R3-C-L3-02-INT \
        --name est_div_concurrent_client \
        --start "3/2/26, 09:00" --end "3/20/26, 23:59" \
        --chats data/raw_chats/full_chat_logs/est_div_jobs "Est Div Client Group" \
                data/raw_chats/full_chat_logs/Voltas_supplier "Voltas Supplier Group (shared)"

    # Shorter form — labels auto-derived from directory names:
    PYTHONPATH=. python scripts/build_integration_case.py \
        --id R1-D-L3-01 \
        --name sata_multi_item_multi_supplier \
        --start "3/1/26, 13:35" --end "3/24/26, 18:54" \
        --chats data/raw_chats/full_chat_logs/sata_jobs \
                data/raw_chats/full_chat_logs/Voltas_supplier \
                data/raw_chats/full_chat_logs/LG_supplier \
                data/raw_chats/full_chat_logs/Tasks
"""

import json
import argparse
import re
from pathlib import Path


def _parse_chats_arg(raw: list[str]) -> list[dict]:
    """Parse --chats arguments: alternating path [label] pairs.
    If a value looks like a path (contains / or exists as dir), it starts a new entry.
    Otherwise it's the label for the previous path."""
    chats = []
    i = 0
    while i < len(raw):
        path = raw[i]
        # Check if next arg is a label (not a path)
        if i + 1 < len(raw) and not Path(raw[i + 1]).is_dir() and '/' not in raw[i + 1]:
            label = raw[i + 1]
            i += 2
        else:
            # Auto-derive label from directory name
            label = Path(path).name.replace('_', ' ').title()
            i += 1
        chats.append({"path": path, "label": label})
    return chats


def _guess_data_source(chats: list[dict]) -> str:
    """Guess data_source from chat paths."""
    paths = " ".join(c["path"] for c in chats)
    if "incomplete" in paths:
        return "real-incomplete"
    if "raw_chats" in paths:
        return "real"
    return "unknown"


def _guess_framework(case_id: str) -> str:
    m = re.match(r'(R\d[A-Za-z]?(?:-[A-Z])?)', case_id)
    return m.group(1) if m else ""


def _guess_level(case_id: str) -> str:
    m = re.search(r'L(\d)', case_id)
    return f"L{m.group(1)}" if m else ""


def main():
    parser = argparse.ArgumentParser(
        description="Build a complete integration test case from raw chats",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--id", required=True, help="Case ID (e.g. R1-D-L3-01)")
    parser.add_argument("--name", required=True, help="Case name (e.g. sata_multi_item_multi_supplier)")
    parser.add_argument("--start", required=True, help="Start datetime (e.g. '3/1/26, 13:35')")
    parser.add_argument("--end", required=True, help="End datetime (e.g. '3/24/26, 18:54')")
    parser.add_argument("--chats", nargs="+", required=True,
                        help="Chat paths and optional labels: path1 [label1] path2 [label2] ...")
    parser.add_argument("--description", default="", help="Case description")
    args = parser.parse_args()

    case_id = args.id
    case_name = args.name
    dir_name = f"{case_id}_{case_name}"

    eval_dir = Path("tests/evals") / dir_name
    int_dir = Path("tests/integration_tests") / dir_name

    chats = _parse_chats_arg(args.chats)

    # Validate chat paths
    for c in chats:
        p = Path(c["path"])
        if not p.is_dir():
            print(f"Warning: {c['path']} is not a directory")

    # ── Step 1: metadata.json ────────────────────────────────────────────
    print(f"=== Step 1: Generate metadata.json ===")
    eval_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "id": case_id,
        "name": case_name,
        "framework": _guess_framework(case_id),
        "level": _guess_level(case_id),
        "sprint": "S3",
        "data_source": _guess_data_source(chats),
        "description": args.description,
        "chat_inputs": {
            "start": args.start,
            "end": args.end,
            "chats": chats,
        },
        "completeness": {
            "complete": False,
            "missing": [],
        },
        "expected_output": "",
        "pass_criteria": "",
        "notes": "",
    }

    meta_path = eval_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: {meta_path}")

    # ── Step 2: seed_tasks.json ──────────────────────────────────────────
    print(f"\n=== Step 2: Generate seed_tasks.json (scaffold) ===")
    int_dir.mkdir(parents=True, exist_ok=True)

    from scripts.build_seed_tasks import build_seed
    seed = build_seed(meta_path)

    seed_path = int_dir / "seed_tasks.json"
    seed_path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: {seed_path}")

    # ── Step 3: replay_trace.json ────────────────────────────────────────
    print(f"\n=== Step 3: Generate replay_trace.json ===")

    from scripts.build_replay_trace import build_trace
    trace = build_trace(meta_path)

    trace_data = {
        "_meta": {"case_id": case_id, "description": case_name.replace("_", " ").title()},
        "messages": trace,
    }
    trace_path = int_dir / "replay_trace.json"
    trace_path.write_text(json.dumps(trace_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: {trace_path}")

    # ── Step 4: expected_routing.json ────────────────────────────────────
    print(f"\n=== Step 4: Generate expected_routing.json ===")

    from scripts.build_expected_routing import build_expected_routing
    routing = build_expected_routing(trace_path, seed_path)

    routing_path = int_dir / "expected_routing.json"
    routing_path.write_text(json.dumps(routing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written: {routing_path}")

    # ── Summary ──────────────────────────────────────────────────────────
    routed = sum(1 for r in routing if r["routed"])
    print(f"\n{'='*60}")
    print(f"Case: {case_id} — {case_name}")
    print(f"Eval dir:        {eval_dir}/")
    print(f"Integration dir: {int_dir}/")
    print(f"Messages: {len(trace)} total, {routed} routed")
    print(f"Tasks: {len(seed['tasks'])}, Entities: {len(seed['entities'])}")
    print(f"Groups: {len(seed['monitored_groups'])}")
    print(f"{'='*60}")
    print(f"\nReview seed_tasks.json — check aliases, client_id linkages, group mappings.")
    print(f"If you make changes, regenerate routing:")
    print(f"  PYTHONPATH=. python scripts/build_expected_routing.py \\")
    print(f"      --trace {trace_path} \\")
    print(f"      --seed {seed_path}")
    print(f"\nRun dry replay:")
    print(f"  PYTHONPATH=. pytest tests/integration_tests/test_dry_replay.py -v")


if __name__ == "__main__":
    main()
