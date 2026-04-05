"""
Dry replay integration test.

Feeds replay traces through the deterministic layers (router only — no LLM)
and asserts routing decisions match expected_routing.json.

Parametrized across all case directories that have the required files.

Run:
    PYTHONPATH=. pytest tests/integration_tests/test_dry_replay.py -v
"""

import json
import time
import sqlite3
import tempfile
from pathlib import Path
from collections import Counter
from unittest.mock import patch

import pytest

from tests.integration_tests.conftest import save_run_record

CASES_DIR = Path(__file__).parent


def _discover_cases() -> list[Path]:
    """Find all case directories with required files."""
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        if not d.is_dir():
            continue
        if (d / "replay_trace.json").exists() and \
           (d / "expected_routing.json").exists() and \
           (d / "seed_tasks.json").exists():
            cases.append(d)
    return cases


def _load_json(case_dir: Path, filename: str):
    path = case_dir / filename
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


def _setup_case(case_dir: Path):
    """Set up temp DB and return (route_fn, trace, expected, seed, case_id)."""
    seed = _load_json(case_dir, "seed_tasks.json")
    trace_data = _load_json(case_dir, "replay_trace.json")
    trace = trace_data["messages"]
    expected = {r["message_id"]: r for r in _load_json(case_dir, "expected_routing.json")}

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _seed_db(tmp.name, seed)

    return tmp.name, seed, trace, expected


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------

CASES = _discover_cases()
CASE_IDS = [d.name for d in CASES]


@pytest.mark.parametrize("case_dir", CASES, ids=CASE_IDS)
class TestDryReplay:

    def test_all_messages_route_as_expected(self, case_dir):
        db_path, seed, trace, expected = _setup_case(case_dir)
        monitored = seed.get("monitored_groups", {})

        from src.router.alias_dict import invalidate_alias_cache
        with patch("src.store.db.DB_PATH", db_path), \
             patch("src.config.DB_PATH", db_path), \
             patch("src.router.router.MONITORED_GROUPS", monitored):
            invalidate_alias_cache()
            from src.router.router import route

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

        Path(db_path).unlink(missing_ok=True)

        if mismatches:
            mismatch_path = case_dir / "routing_mismatches.json"
            mismatch_path.write_text(
                json.dumps(mismatches, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            pytest.fail(
                f"{len(mismatches)} routing mismatches (see {mismatch_path}). "
                f"First: {mismatches[0]['message_id']}"
            )

    def test_dedicated_groups_all_routed(self, case_dir):
        """Non-empty messages from dedicated (non-null) groups must route."""
        db_path, seed, trace, expected = _setup_case(case_dir)
        monitored = seed.get("monitored_groups", {})
        dedicated = {g for g, tid in monitored.items() if tid is not None}

        if not dedicated:
            pytest.skip("No dedicated groups in this case")

        from src.router.alias_dict import invalidate_alias_cache
        with patch("src.store.db.DB_PATH", db_path), \
             patch("src.config.DB_PATH", db_path), \
             patch("src.router.router.MONITORED_GROUPS", monitored):
            invalidate_alias_cache()  # ensure DB aliases read from test DB
            from src.router.router import route
            unrouted = []
            for msg in trace:
                if msg["group_id"] not in dedicated:
                    continue
                if not (msg.get("body") or "").strip() and not msg.get("image_path"):
                    continue  # empty messages are legitimately dropped
                routes = route(msg)
                if not routes:
                    unrouted.append(msg["message_id"])

        Path(db_path).unlink(missing_ok=True)
        assert not unrouted, f"{len(unrouted)} dedicated-group messages unrouted: {unrouted[:5]}"

    def test_routing_summary_stats(self, case_dir):
        """Verify routing rate is reasonable and save run record."""
        db_path, seed, trace, expected = _setup_case(case_dir)
        monitored = seed.get("monitored_groups", {})
        case_id = case_dir.name.split("_")[0]  # e.g. "R1-D-L3-01"

        from src.router.alias_dict import invalidate_alias_cache
        with patch("src.store.db.DB_PATH", db_path), \
             patch("src.config.DB_PATH", db_path), \
             patch("src.router.router.MONITORED_GROUPS", monitored):
            invalidate_alias_cache()
            from src.router.router import route

            group_routed = Counter()
            group_unrouted = Counter()
            noise_count = 0
            for msg in trace:
                body = (msg.get("body") or "").strip()
                has_image = bool(msg.get("image_path") or msg.get("image_bytes"))
                if not body and not has_image:
                    noise_count += 1
                    continue  # noise — don't count in routing rate
                routes = route(msg)
                if routes:
                    group_routed[msg["group_id"]] += 1
                else:
                    group_unrouted[msg["group_id"]] += 1

        Path(db_path).unlink(missing_ok=True)

        total = len(trace)
        routable = total - noise_count
        routed_count = sum(group_routed.values())
        rate = routed_count / routable if routable else 0

        save_run_record("dry", case_id, {
            "total": total,
            "noise": noise_count,
            "routable": routable,
            "routed": routed_count,
            "unrouted": routable - routed_count,
            "routing_rate": round(rate, 4),
            "per_group": {
                g: {"routed": group_routed.get(g, 0), "unrouted": group_unrouted.get(g, 0)}
                for g in sorted(set(list(group_routed.keys()) + list(group_unrouted.keys())))
            },
        })

        # Routing rate sanity — at least some messages should route
        assert routed_count > 0, "No messages routed"
