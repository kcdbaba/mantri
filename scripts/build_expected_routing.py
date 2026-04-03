#!/usr/bin/env python3
"""
build_expected_routing.py

Seeds a temporary SQLite DB with task/entity data from seed_tasks.json, then
runs route() on each message in a replay trace to generate expected_routing.json.

The output MUST be manually reviewed and curated before use as ground truth —
the router's current behaviour is the starting point, not necessarily correct.

Usage:

    python scripts/build_expected_routing.py \
        --trace tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/replay_trace.json \
        --seed tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/seed_tasks.json

Output: expected_routing.json in the same directory as the trace file.
"""

import json
import time
import uuid
import sqlite3
import argparse
import tempfile
from pathlib import Path
from unittest.mock import patch


def _seed_db(db_path: str, seed: dict):
    """Create schema and insert seed tasks + entities into a temp DB."""
    from src.store.db import SCHEMA

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    now = int(time.time())
    for task in seed["tasks"]:
        conn.execute(
            """INSERT INTO task_instances
               (id, order_type, client_id, supplier_ids, created_at, last_updated, stage, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task["id"], task["order_type"], task["client_id"],
                json.dumps(task.get("supplier_ids", [])),
                now, now, task.get("stage", "active"), "integration_test_seed",
            ),
        )

    for entity in seed.get("entities", []):
        for alias in entity.get("aliases", []):
            conn.execute(
                """INSERT OR IGNORE INTO entity_aliases
                   (alias, entity_id, entity_type, confidence, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (alias.lower(), entity["id"], "client", 1.0, "integration_test_seed"),
            )

    conn.commit()
    conn.close()


def build_expected_routing(trace_path: Path, seed_path: Path) -> list[dict]:
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    trace = trace_data["messages"]
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    # Create temp DB and seed it
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _seed_db(tmp.name, seed)

    # Patch config and DB to use temp DB + seed group mapping
    monitored = seed.get("monitored_groups", {})

    config_overrides = seed.get("config_overrides", {})
    enable_conv = config_overrides.get("ENABLE_CONVERSATION_ROUTING", False)

    with patch("src.store.db.DB_PATH", tmp.name), \
         patch("src.config.DB_PATH", tmp.name), \
         patch("src.router.router.MONITORED_GROUPS", monitored), \
         patch("src.config.ENABLE_CONVERSATION_ROUTING", enable_conv), \
         patch("src.router.router.ENABLE_CONVERSATION_ROUTING", enable_conv):

        from src.router.router import route

        results = []
        routed_count = 0
        unrouted_count = 0

        for msg in trace:
            routes = route(msg)

            result = {
                "message_id": msg["message_id"],
                "timestamp_raw": msg["timestamp_raw"],
                "group_id": msg["group_id"],
                "sender_jid": msg["sender_jid"],
                "body_preview": (msg["body"] or "")[:80],
                "routes": [
                    {"task_id": task_id, "confidence": round(conf, 2)}
                    for task_id, conf in routes
                ],
                "routed": len(routes) > 0,
            }
            results.append(result)

            if routes:
                routed_count += 1
            else:
                unrouted_count += 1

    # Cleanup
    Path(tmp.name).unlink(missing_ok=True)

    print(f"  {len(results)} messages processed")
    print(f"  {routed_count} routed, {unrouted_count} unrouted")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate expected routing from replay trace + seed data"
    )
    parser.add_argument("--trace", required=True, help="Path to replay_trace.json")
    parser.add_argument("--seed", required=True, help="Path to seed_tasks.json")
    parser.add_argument("--output", help="Output path (default: same dir as trace)")
    args = parser.parse_args()

    trace_path = Path(args.trace)
    seed_path = Path(args.seed)
    output_path = Path(args.output) if args.output else trace_path.parent / "expected_routing.json"

    print(f"Trace: {trace_path}")
    print(f"Seed:  {seed_path}")

    results = build_expected_routing(trace_path, seed_path)

    output_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  Written to: {output_path}")

    # Summary by group
    from collections import Counter
    group_routes = Counter()
    group_unrouted = Counter()
    for r in results:
        if r["routed"]:
            group_routes[r["group_id"]] += 1
        else:
            group_unrouted[r["group_id"]] += 1

    print("\n  Per-group routing summary:")
    all_groups = sorted(set(list(group_routes.keys()) + list(group_unrouted.keys())))
    for g in all_groups:
        routed = group_routes.get(g, 0)
        unrouted = group_unrouted.get(g, 0)
        total = routed + unrouted
        print(f"    {g}: {routed}/{total} routed ({unrouted} unrouted)")


if __name__ == "__main__":
    main()
