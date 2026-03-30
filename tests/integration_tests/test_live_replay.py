"""
Live replay integration test.

Feeds a replay trace through the full production pipeline (router → update_agent
→ linkage_agent) using real LLM calls against a temp DB, then snapshots final
state for comparison against expected output.

This test is non-deterministic and costs API calls — run deliberately, not in CI.

Run:
    PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live

Skip linkage (update_agent only):
    PYTHONPATH=. pytest tests/integration_tests/test_live_replay.py -v -s --run-live --skip-linkage
"""

import json
import time
import sqlite3
import tempfile
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock
from collections import defaultdict

import pytest

from src.agent.templates import get_template
from tests.integration_tests.conftest import save_run_record

CASE_DIR = Path(__file__).parent / "R1-D-L3-01_sata_multi_item_multi_supplier"
CASE_ID = "R1-D-L3-01"

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mock Redis that captures stream events
# ---------------------------------------------------------------------------

class StreamCapture:
    """Minimal Redis mock that captures xadd calls and no-ops everything else."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []  # (stream_key, fields)
        self._seq = 0

    def xadd(self, stream_key: str, fields: dict, **kwargs):
        self._seq += 1
        event_id = f"replay-{self._seq}"
        self.events.append((stream_key, fields))
        return event_id

    def xack(self, *args, **kwargs):
        pass

    def drain_events(self) -> list[tuple[str, dict]]:
        """Return and clear captured events."""
        events = list(self.events)
        self.events.clear()
        return events


# ---------------------------------------------------------------------------
# DB seeding
# ---------------------------------------------------------------------------

def _load_json(filename: str):
    path = CASE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_db(db_path: str, seed: dict):
    """Create schema and seed tasks with full node trees from templates."""
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
                now, now, task.get("stage", "active"), "live_replay_seed",
            ),
        )

        # Seed nodes from template
        template = get_template(task["order_type"])
        for node in template["nodes"]:
            default_status = "skipped" if node.get("optional") else "pending"
            conn.execute(
                """INSERT OR IGNORE INTO task_nodes
                   (id, task_id, node_type, name, status, updated_at, updated_by,
                    optional, requires_all, warns_if_incomplete)
                   VALUES (?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?)""",
                (
                    f"{task['id']}_{node['id']}", task["id"], node["type"], node["name"],
                    default_status, now,
                    1 if node.get("optional") else 0,
                    json.dumps(node.get("requires_all", [])),
                    json.dumps(node.get("warns_if_incomplete", [])),
                ),
            )

    for entity in seed.get("entities", []):
        for alias in entity.get("aliases", []):
            conn.execute(
                """INSERT OR IGNORE INTO entity_aliases
                   (alias, entity_id, entity_type, confidence, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (alias.lower(), entity["id"], "client", 1.0, "live_replay_seed"),
            )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------

def _snapshot_state(db_path: str) -> dict:
    """Read final state from the DB after replay."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Node states per task
    nodes = conn.execute(
        "SELECT task_id, id, name, status, confidence, updated_by FROM task_nodes ORDER BY task_id, id"
    ).fetchall()
    node_states = defaultdict(list)
    for n in nodes:
        node_states[n["task_id"]].append({
            "node_id": n["id"],
            "name": n["name"],
            "status": n["status"],
            "confidence": n["confidence"],
            "updated_by": n["updated_by"],
        })

    # Order items per task (split across client and supplier tables)
    items = defaultdict(list)
    for table in ("client_order_items", "supplier_order_items"):
        try:
            rows = conn.execute(
                f"SELECT task_id, description, unit, quantity, specs FROM {table} ORDER BY task_id"
            ).fetchall()
            for row in rows:
                items[row["task_id"]].append({
                    "description": row["description"],
                    "unit": row["unit"],
                    "quantity": row["quantity"],
                    "specs": row["specs"],
                    "source_table": table,
                })
        except sqlite3.OperationalError:
            pass

    # Fulfillment links
    links = conn.execute(
        """SELECT client_order_id, supplier_order_id, client_item_description,
                  supplier_item_description, quantity_allocated, match_confidence, status
           FROM fulfillment_links ORDER BY client_order_id, supplier_order_id"""
    ).fetchall()
    fulfillment = [dict(row) for row in links]

    # Ambiguity queue
    ambiguity = conn.execute(
        "SELECT task_id, node_id, severity, category, description, status FROM ambiguity_queue ORDER BY created_at"
    ).fetchall()
    ambiguity_flags = [dict(row) for row in ambiguity]

    # Messages per task
    msg_counts = conn.execute(
        "SELECT task_id, COUNT(*) as count FROM task_messages GROUP BY task_id"
    ).fetchall()
    message_counts = {row["task_id"]: row["count"] for row in msg_counts}

    # Dead letters
    try:
        dead_letters = conn.execute(
            "SELECT * FROM dead_letter_events"
        ).fetchall()
    except sqlite3.OperationalError:
        dead_letters = []

    conn.close()

    return {
        "node_states": dict(node_states),
        "items": dict(items),
        "fulfillment_links": fulfillment,
        "ambiguity_flags": ambiguity_flags,
        "message_counts": message_counts,
        "dead_letter_count": len(dead_letters),
    }


# ---------------------------------------------------------------------------
# Replay runner
# ---------------------------------------------------------------------------

def run_live_replay(trace: list[dict], seed: dict, run_linkage: bool = True,
                    max_messages: int | None = None) -> dict:
    """
    Run the full pipeline on a trace. Returns final state snapshot + run stats.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    _seed_db(db_path, seed)

    monitored = seed.get("monitored_groups", {})
    mock_redis = StreamCapture()

    messages_to_process = trace[:max_messages] if max_messages else trace

    stats = {
        "messages_total": len(messages_to_process),
        "messages_routed": 0,
        "messages_unrouted": 0,
        "update_agent_calls": 0,
        "update_agent_failures": 0,
        "linkage_events_processed": 0,
        "linkage_agent_failures": 0,
        "errors": [],
    }

    with patch("src.store.db.DB_PATH", db_path), \
         patch("src.config.DB_PATH", db_path), \
         patch("src.router.router.MONITORED_GROUPS", monitored):

        from src.router.worker import process_message
        from src.linkage.linkage_worker import process_event

        for i, msg in enumerate(messages_to_process):
            # Progress logging every 50 messages
            if (i + 1) % 50 == 0:
                log.info("Processing message %d/%d: %s",
                         i + 1, len(messages_to_process), msg["message_id"])

            try:
                process_message(msg, mock_redis)
            except Exception as e:
                stats["errors"].append({
                    "phase": "update_agent",
                    "message_id": msg["message_id"],
                    "error": str(e),
                })
                log.error("process_message failed for %s: %s", msg["message_id"], e)
                continue

            # Check if message was routed (by looking at captured stream events)
            new_events = mock_redis.drain_events()
            if new_events:
                stats["messages_routed"] += 1
                stats["update_agent_calls"] += len(new_events)
            else:
                stats["messages_unrouted"] += 1

            # Feed stream events to linkage worker
            if run_linkage:
                for stream_key, fields in new_events:
                    try:
                        process_event(f"replay-{i}", fields, mock_redis)
                        stats["linkage_events_processed"] += 1
                    except Exception as e:
                        stats["linkage_agent_failures"] += 1
                        stats["errors"].append({
                            "phase": "linkage_agent",
                            "message_id": msg["message_id"],
                            "event_fields": {k: v[:100] if isinstance(v, str) else v
                                             for k, v in fields.items()},
                            "error": str(e),
                        })
                        log.error("process_event failed for %s: %s", msg["message_id"], e)

    snapshot = _snapshot_state(db_path)

    # Keep the DB for manual inspection
    result_db = CASE_DIR / "replay_result.db"
    Path(db_path).rename(result_db)
    log.info("Result DB saved to: %s", result_db)

    return {
        "stats": stats,
        "state": snapshot,
        "db_path": str(result_db),
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestLiveReplay:

    def test_full_replay(self, request):
        if not request.config.getoption("--run-live"):
            pytest.skip("Live replay requires --run-live flag")

        skip_linkage = request.config.getoption("--skip-linkage")
        max_messages = request.config.getoption("--max-messages")

        trace = _load_json("replay_trace.json")
        seed = _load_json("seed_tasks.json")

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

        result = run_live_replay(
            trace, seed,
            run_linkage=not skip_linkage,
            max_messages=max_messages,
        )

        # Write full results to case dir
        output_path = CASE_DIR / "replay_result.json"
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        log.info("Results written to: %s", output_path)

        # Save publishable run record
        stats = result["stats"]
        state = result["state"]
        save_run_record("live", CASE_ID, {
            "messages_total": stats["messages_total"],
            "messages_routed": stats["messages_routed"],
            "messages_unrouted": stats["messages_unrouted"],
            "update_agent_calls": stats["update_agent_calls"],
            "update_agent_failures": stats["update_agent_failures"],
            "linkage_events_processed": stats["linkage_events_processed"],
            "linkage_agent_failures": stats["linkage_agent_failures"],
            "error_count": len(stats["errors"]),
            "dead_letter_count": state["dead_letter_count"],
            "ambiguity_flag_count": len(state["ambiguity_flags"]),
            "fulfillment_link_count": len(state["fulfillment_links"]),
            "node_summary": {
                task_id: {
                    "completed": sum(1 for n in nodes if n["status"] == "completed"),
                    "active": sum(1 for n in nodes if n["status"] in ("active", "in_progress")),
                    "pending": sum(1 for n in nodes if n["status"] == "pending"),
                    "blocked": sum(1 for n in nodes if n["status"] == "blocked"),
                    "provisional": sum(1 for n in nodes if n["status"] == "provisional"),
                    "total": len(nodes),
                }
                for task_id, nodes in state["node_states"].items()
            },
            "items_per_task": {tid: len(items) for tid, items in state["items"].items()},
            "messages_per_task": state["message_counts"],
            "skip_linkage": skip_linkage,
            "max_messages": max_messages,
        })

        # Basic sanity assertions
        stats = result["stats"]
        state = result["state"]

        # At least some messages should route
        assert stats["messages_routed"] > 0, "No messages routed"

        # Node states should exist for seeded tasks
        assert len(state["node_states"]) > 0, "No node states in DB"

        # Messages should be stored
        assert sum(state["message_counts"].values()) > 0, "No messages stored"

        # Print summary
        print(f"\n{'='*60}")
        print(f"LIVE REPLAY COMPLETE")
        print(f"{'='*60}")
        print(f"Messages: {stats['messages_total']} total, "
              f"{stats['messages_routed']} routed, "
              f"{stats['messages_unrouted']} unrouted")
        print(f"Update agent: {stats['update_agent_calls']} calls, "
              f"{stats['update_agent_failures']} failures")
        if not skip_linkage:
            print(f"Linkage agent: {stats['linkage_events_processed']} events, "
                  f"{stats['linkage_agent_failures']} failures")
        print(f"Dead letters: {state['dead_letter_count']}")
        print(f"Ambiguity flags: {len(state['ambiguity_flags'])}")
        print(f"Fulfillment links: {len(state['fulfillment_links'])}")
        print(f"\nNode states per task:")
        for task_id, nodes in state["node_states"].items():
            completed = sum(1 for n in nodes if n["status"] == "completed")
            active = sum(1 for n in nodes if n["status"] in ("active", "in_progress"))
            pending = sum(1 for n in nodes if n["status"] == "pending")
            print(f"  {task_id}: {completed} completed, {active} active, "
                  f"{pending} pending ({len(nodes)} total)")
        print(f"\nItems per task:")
        for task_id, task_items in state["items"].items():
            print(f"  {task_id}: {len(task_items)} items")
        print(f"\nMessages stored per task:")
        for task_id, count in state["message_counts"].items():
            print(f"  {task_id}: {count}")
        if stats["errors"]:
            print(f"\nErrors ({len(stats['errors'])}):")
            for err in stats["errors"][:5]:
                print(f"  [{err['phase']}] {err['message_id']}: {err['error'][:80]}")
        print(f"\nResult DB: {result['db_path']}")
        print(f"Full results: {CASE_DIR / 'replay_result.json'}")
