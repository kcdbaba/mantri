"""
Dry replay integration test.

Feeds a replay trace through the deterministic layers (router only — no LLM)
and asserts routing decisions match expected_routing.json.

Run:
    PYTHONPATH=. pytest tests/integration_tests/test_dry_replay.py -v
"""

import json
import time
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.integration_tests.conftest import save_run_record

CASE_DIR = Path(__file__).parent / "R1-D-L3-01_sata_multi_item_multi_supplier"
CASE_ID = "R1-D-L3-01"


def _load_json(filename: str) -> list | dict:
    path = CASE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not found — run the builder script first")
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_db(db_path: str, seed: dict):
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


@pytest.fixture(scope="module")
def replay_env():
    """Set up temp DB with seed data and patch config for entire module."""
    seed = _load_json("seed_tasks.json")
    monitored = seed.get("monitored_groups", {})

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _seed_db(tmp.name, seed)

    with patch("src.store.db.DB_PATH", tmp.name), \
         patch("src.config.DB_PATH", tmp.name), \
         patch("src.router.router.MONITORED_GROUPS", monitored):
        from src.router.router import route
        yield route

    Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture(scope="module")
def trace():
    return _load_json("replay_trace.json")


@pytest.fixture(scope="module")
def expected():
    return {r["message_id"]: r for r in _load_json("expected_routing.json")}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDryReplay:
    """Route every trace message and assert against expected_routing.json."""

    def test_all_messages_route_as_expected(self, replay_env, trace, expected):
        route = replay_env
        mismatches = []

        for msg in trace:
            mid = msg["message_id"]
            exp = expected.get(mid)
            if exp is None:
                continue

            actual_routes = route(msg)
            actual = sorted(
                [{"task_id": tid, "confidence": round(c, 2)} for tid, c in actual_routes],
                key=lambda x: x["task_id"],
            )
            exp_routes = sorted(exp["routes"], key=lambda x: x["task_id"])

            if actual != exp_routes:
                mismatches.append({
                    "message_id": mid,
                    "body_preview": (msg["body"] or "")[:60],
                    "expected": exp_routes,
                    "actual": actual,
                })

        if mismatches:
            # Write mismatches to file for review
            mismatch_path = CASE_DIR / "routing_mismatches.json"
            mismatch_path.write_text(
                json.dumps(mismatches, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            pytest.fail(
                f"{len(mismatches)} routing mismatches (see {mismatch_path}). "
                f"First: {mismatches[0]['message_id']}"
            )

    def test_dedicated_groups_all_routed(self, replay_env, trace, expected):
        """Messages from dedicated groups (sata_jobs, Voltas, LG) must always route."""
        route = replay_env
        dedicated = {"sata_jobs", "Voltas_supplier", "LG_supplier"}
        unrouted = []

        for msg in trace:
            if msg["group_id"] not in dedicated:
                continue
            routes = route(msg)
            if not routes:
                unrouted.append(msg["message_id"])

        assert not unrouted, f"{len(unrouted)} dedicated-group messages unrouted: {unrouted[:5]}"

    def test_dedicated_groups_route_to_correct_task(self, replay_env, trace, expected):
        """Each dedicated group should map to its expected task_id."""
        route = replay_env
        group_to_task = {
            "sata_jobs": "task_sata_client",
            "Voltas_supplier": "task_voltas_supplier",
            "LG_supplier": "task_lg_supplier",
        }
        wrong_routes = []

        for msg in trace:
            if msg["group_id"] not in group_to_task:
                continue
            expected_task = group_to_task[msg["group_id"]]
            routes = route(msg)
            task_ids = [tid for tid, _ in routes]
            if expected_task not in task_ids:
                wrong_routes.append((msg["message_id"], task_ids))

        assert not wrong_routes, f"{len(wrong_routes)} wrong routes: {wrong_routes[:5]}"

    def test_routing_summary_stats(self, replay_env, trace, expected):
        """Sanity check: verify overall routing rate is reasonable."""
        route = replay_env
        routed_count = 0
        unrouted_count = 0
        from collections import Counter
        group_routed = Counter()
        group_unrouted = Counter()

        for msg in trace:
            routes = route(msg)
            if routes:
                routed_count += 1
                group_routed[msg["group_id"]] += 1
            else:
                unrouted_count += 1
                group_unrouted[msg["group_id"]] += 1

        total = len(trace)
        rate = routed_count / total

        # Save run record for publishing
        save_run_record("dry", CASE_ID, {
            "total": total,
            "routed": routed_count,
            "unrouted": unrouted_count,
            "routing_rate": round(rate, 4),
            "per_group": {
                g: {"routed": group_routed.get(g, 0), "unrouted": group_unrouted.get(g, 0)}
                for g in sorted(set(list(group_routed.keys()) + list(group_unrouted.keys())))
            },
        })

        # Dedicated groups (sata_jobs + Voltas + LG) = 463 messages, should all route
        # Tasks group = 441, mostly unrouted. Overall rate ~50-55%.
        assert rate > 0.40, f"Routing rate {rate:.2%} too low"
        assert rate < 0.80, f"Routing rate {rate:.2%} suspiciously high"
