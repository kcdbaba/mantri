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

CASES_DIR = Path(__file__).parent

log = logging.getLogger(__name__)


def _discover_cases() -> list[Path]:
    """Find all case directories with required files for live replay."""
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        if not d.is_dir():
            continue
        if (d / "replay_trace.json").exists() and (d / "seed_tasks.json").exists():
            cases.append(d)
    return cases


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

def _load_json(case_dir: Path, filename: str):
    path = case_dir / filename
    if not path.exists():
        pytest.skip(f"{filename} not found in {case_dir.name}")
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

    # Model usage from usage_log
    model_usage = []
    total_cost = 0.0
    try:
        model_rows = conn.execute(
            "SELECT model, COUNT(*) as calls, SUM(cost_usd) as cost "
            "FROM usage_log GROUP BY model ORDER BY calls DESC"
        ).fetchall()
        model_usage = [{"model": r["model"], "calls": r["calls"],
                        "cost": r["cost"] or 0} for r in model_rows]
        cost_row = conn.execute("SELECT SUM(cost_usd) FROM usage_log").fetchone()
        total_cost = cost_row[0] or 0 if cost_row else 0
    except sqlite3.OperationalError:
        pass

    conn.close()

    return {
        "node_states": dict(node_states),
        "items": dict(items),
        "fulfillment_links": fulfillment,
        "ambiguity_flags": ambiguity_flags,
        "message_counts": message_counts,
        "dead_letter_count": len(dead_letters),
        "model_usage": model_usage,
        "total_cost": total_cost,
    }


# ---------------------------------------------------------------------------
# Replay runner
# ---------------------------------------------------------------------------

def run_live_replay(case_dir: Path, trace: list[dict], seed: dict,
                    run_linkage: bool = True, max_messages: int | None = None) -> dict:
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
    progress_path = case_dir / "replay_progress.json"
    t_start = time.time()

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

    def _write_progress(phase: str, detail: str = ""):
        elapsed = time.time() - t_start
        progress = {
            "phase": phase,
            "detail": detail,
            "elapsed_s": round(elapsed, 1),
            "stats": {k: v for k, v in stats.items() if k != "errors"},
            "error_count": len(stats["errors"]),
        }
        try:
            progress_path.write_text(json.dumps(progress, indent=2, default=str))
        except Exception:
            pass

    with patch("src.store.db.DB_PATH", db_path), \
         patch("src.config.DB_PATH", db_path), \
         patch("src.router.router.MONITORED_GROUPS", monitored):

        from src.router.router import route
        from src.router.worker import process_message_batch
        from src.linkage.linkage_worker import process_event

        # Build batches: group by task_id with 60s window
        BATCH_WINDOW = 60
        # {task_id: {"messages": [...], "last_ts": int}}
        batch_buf: dict[str, dict] = {}
        batches_to_flush: list[tuple[str, list[dict]]] = []

        def _flush_all():
            for tid, buf in batch_buf.items():
                if buf["messages"]:
                    batches_to_flush.append((tid, list(buf["messages"])))
            batch_buf.clear()

        _write_progress("routing", f"0/{len(messages_to_process)} messages")
        for i, msg in enumerate(messages_to_process):
            if (i + 1) % 50 == 0:
                log.info("Processing message %d/%d: %s",
                         i + 1, len(messages_to_process), msg["message_id"])
                _write_progress("routing", f"{i+1}/{len(messages_to_process)} messages")

            routes = route(msg)
            if not routes:
                stats["messages_unrouted"] += 1
                continue

            stats["messages_routed"] += 1
            msg_ts = msg.get("timestamp", 0)

            for task_id, confidence in routes:
                # Check if existing batch for this task should flush (window expired)
                if task_id in batch_buf:
                    elapsed = msg_ts - batch_buf[task_id]["last_ts"]
                    if elapsed > BATCH_WINDOW or len(batch_buf[task_id]["messages"]) >= 10:
                        batches_to_flush.append(
                            (task_id, list(batch_buf[task_id]["messages"]))
                        )
                        del batch_buf[task_id]

                if task_id not in batch_buf:
                    batch_buf[task_id] = {"messages": [], "last_ts": 0}

                batch_buf[task_id]["messages"].append(msg)
                batch_buf[task_id]["last_ts"] = msg_ts

        # Flush remaining
        _flush_all()

        # Process all batches
        _write_progress("batches", f"0/{len(batches_to_flush)} batches")
        for batch_idx, (task_id, batch_msgs) in enumerate(batches_to_flush):
            if (batch_idx + 1) % 20 == 0:
                log.info("Processing batch %d/%d: task=%s size=%d",
                         batch_idx + 1, len(batches_to_flush), task_id, len(batch_msgs))

            stats["update_agent_calls"] += 1
            try:
                process_message_batch(task_id, batch_msgs, mock_redis)
            except Exception as e:
                stats["errors"].append({
                    "phase": "update_agent",
                    "message_id": batch_msgs[-1]["message_id"],
                    "error": str(e),
                })
                log.error("Batch failed for task=%s: %s", task_id, e)
                continue
            finally:
                if (batch_idx + 1) % 5 == 0 or batch_idx == len(batches_to_flush) - 1:
                    _write_progress("batches",
                                    f"{batch_idx+1}/{len(batches_to_flush)} batches, "
                                    f"task={task_id}, size={len(batch_msgs)}")

            # Feed stream events to linkage worker
            new_events = mock_redis.drain_events()
            if run_linkage:
                for stream_key, fields in new_events:
                    try:
                        process_event(f"replay-batch-{batch_idx}", fields, mock_redis)
                        stats["linkage_events_processed"] += 1
                    except Exception as e:
                        stats["linkage_agent_failures"] += 1
                        stats["errors"].append({
                            "phase": "linkage_agent",
                            "message_id": batch_msgs[-1]["message_id"],
                            "error": str(e),
                        })
                        log.error("Linkage failed for batch %d: %s", batch_idx, e)

    _write_progress("snapshot", "computing final state")
    snapshot = _snapshot_state(db_path)

    # Keep the DB for manual inspection
    result_db = case_dir / "replay_result.db"
    Path(db_path).rename(result_db)
    _write_progress("complete", f"done in {time.time()-t_start:.0f}s")
    log.info("Result DB saved to: %s", result_db)

    return {
        "stats": stats,
        "state": snapshot,
        "db_path": str(result_db),
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def _get_run_metadata() -> dict:
    """Capture config state and git version at the time of the run."""
    import subprocess
    from src.config import (
        ENABLE_LIVE_TASK_CREATION, CLAUDE_MODEL, CLAUDE_MODEL_FAST,
        GEMINI_MODEL, ACTIVE_ESCALATION_PROFILE,
    )
    git_hash = ""
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        pass

    return {
        "git_commit": git_hash,
        "live_task_creation": ENABLE_LIVE_TASK_CREATION,
        "models": {
            "complex": CLAUDE_MODEL,
            "simple": GEMINI_MODEL,
        },
        "escalation_profile": ACTIVE_ESCALATION_PROFILE,
    }


def _compute_pipeline_score(stats: dict, state: dict, skip_linkage: bool) -> dict:
    """
    Compute a metric-based pipeline quality score from replay results.
    Dimensions:
      - reliability: dead letter rate, failure rate
      - routing: routed/total ratio
      - extraction: items extracted per routed message
      - node_progression: completed nodes / total nodes
      - ambiguity_quality: flag rate (lower is better, but 0 is suspicious)
      - linkage: fulfillment links created (if applicable)
    """
    total_msgs = stats["messages_total"]
    routed = stats["messages_routed"]
    failures = stats["update_agent_failures"] + stats.get("linkage_agent_failures", 0)
    dead_letters = state["dead_letter_count"]
    n_flags = len(state["ambiguity_flags"])
    n_links = len(state["fulfillment_links"])
    n_errors = len(stats.get("errors", []))
    agent_calls = stats["update_agent_calls"]

    # Total nodes across all tasks
    all_nodes = [n for nodes in state["node_states"].values() for n in nodes]
    completed = sum(1 for n in all_nodes if n["status"] == "completed")
    active = sum(1 for n in all_nodes if n["status"] in ("active", "in_progress"))
    total_nodes = len(all_nodes)
    progressed = completed + active

    # Total items
    total_items = sum(len(items) for items in state["items"].values())

    # --- Scoring ---

    # Reliability (0-100): penalise dead letters and failures
    if agent_calls == 0:
        reliability = 50
    else:
        dead_rate = dead_letters / agent_calls
        fail_rate = failures / agent_calls
        reliability = max(0, int(100 - dead_rate * 500 - fail_rate * 300 - n_errors * 10))

    # Routing (0-100): % of messages routed
    routing = int(100 * routed / total_msgs) if total_msgs else 0

    # Extraction (0-100): items per 100 routed messages (expect ~5-20 items per 100 msgs)
    if routed == 0:
        extraction = 0
    else:
        items_per_100 = total_items / routed * 100
        extraction = min(100, int(items_per_100 * 5))  # 20 items/100msgs = 100

    # Node progression (0-100): % of nodes progressed from pending
    if total_nodes == 0:
        node_prog = 0
    else:
        node_prog = int(100 * progressed / total_nodes)

    # Ambiguity quality (0-100): moderate flag rate is good
    # 0 flags = suspicious (50), 1-3 per call = too many (penalise), sweet spot ~0.1-0.5/call
    if agent_calls == 0:
        ambiguity_q = 50
    else:
        flag_rate = n_flags / agent_calls
        if flag_rate == 0:
            ambiguity_q = 60  # suspiciously low
        elif flag_rate <= 0.5:
            ambiguity_q = 95  # ideal range
        elif flag_rate <= 1.0:
            ambiguity_q = 80
        elif flag_rate <= 2.0:
            ambiguity_q = 60
        else:
            ambiguity_q = max(20, int(100 - flag_rate * 20))

    # Linkage (0-100): only if linkage was run
    linkage_score = None
    if not skip_linkage:
        if n_links > 0:
            linkage_score = min(100, 70 + n_links * 5)  # baseline 70, +5 per link
        else:
            linkage_score = 40  # no links created

    # Overall: weighted average
    weights = {
        "reliability": 30,
        "routing": 15,
        "extraction": 20,
        "node_progression": 15,
        "ambiguity_quality": 10,
    }
    if linkage_score is not None:
        weights["linkage"] = 10
    else:
        weights["extraction"] += 5
        weights["node_progression"] += 5

    scores = {
        "reliability": reliability,
        "routing": routing,
        "extraction": extraction,
        "node_progression": node_prog,
        "ambiguity_quality": ambiguity_q,
    }
    if linkage_score is not None:
        scores["linkage"] = linkage_score

    total_weight = sum(weights.values())
    overall = sum(scores[k] * weights[k] for k in scores) / total_weight

    dimensions = {}
    for k in ["reliability", "routing", "extraction", "node_progression",
              "ambiguity_quality", "linkage"]:
        if k in scores:
            dimensions[k] = {"score": scores[k], "notes": ""}
        else:
            dimensions[k] = {"score": None, "notes": ""}

    # Add notes
    dimensions["reliability"]["notes"] = (
        f"{dead_letters} dead letters, {failures} failures, {n_errors} errors "
        f"out of {agent_calls} agent calls"
    )
    dimensions["routing"]["notes"] = f"{routed}/{total_msgs} messages routed"
    dimensions["extraction"]["notes"] = f"{total_items} items extracted across {len(state['items'])} tasks"
    dimensions["node_progression"]["notes"] = (
        f"{completed} completed, {active} active out of {total_nodes} total nodes"
    )
    dimensions["ambiguity_quality"]["notes"] = (
        f"{n_flags} flags from {agent_calls} calls "
        f"({n_flags/max(agent_calls,1):.2f}/call)"
    )
    if linkage_score is not None:
        dimensions["linkage"]["notes"] = f"{n_links} fulfillment links created"

    return {
        "verdict": "PASS" if overall >= 60 else "PARTIAL" if overall >= 40 else "FAIL",
        "overall_score": round(overall),
        "dimensions": dimensions,
        "metrics": {
            "total_messages": total_msgs,
            "routed": routed,
            "agent_calls": agent_calls,
            "dead_letters": dead_letters,
            "failures": failures,
            "errors": n_errors,
            "ambiguity_flags": n_flags,
            "fulfillment_links": n_links,
            "total_items": total_items,
            "total_nodes": total_nodes,
            "completed_nodes": completed,
            "active_nodes": active,
        },
    }


LIVE_CASES = _discover_cases()
LIVE_CASE_IDS = [d.name for d in LIVE_CASES]


@pytest.mark.parametrize("case_dir", LIVE_CASES, ids=LIVE_CASE_IDS)
class TestLiveReplay:

    def test_full_replay(self, case_dir, request):
        if not request.config.getoption("--run-live"):
            pytest.skip("Live replay requires --run-live flag")

        skip_linkage = request.config.getoption("--skip-linkage")
        max_messages = request.config.getoption("--max-messages")
        run_note = request.config.getoption("--run-note")
        case_id = case_dir.name.split("_")[0]

        trace = _load_json(case_dir, "replay_trace.json")
        seed = _load_json(case_dir, "seed_tasks.json")

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

        result = run_live_replay(
            case_dir, trace, seed,
            run_linkage=not skip_linkage,
            max_messages=max_messages,
        )

        # Write full results to case dir
        output_path = case_dir / "replay_result.json"
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        log.info("Results written to: %s", output_path)

        # Compute pipeline score
        stats = result["stats"]
        state = result["state"]
        score = _compute_pipeline_score(stats, state, skip_linkage)
        score["case_id"] = case_id
        score["case_name"] = case_dir.name
        score["evaluated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        score_path = case_dir / "pipeline_score.json"
        score_path.write_text(
            json.dumps(score, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # Save publishable run record
        save_run_record("live", case_id, {
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
            "model_usage": state.get("model_usage", []),
            "total_cost": state.get("total_cost", 0),
            "tasks_created": len(state["node_states"]),
            "pipeline_score": score.get("overall_score") if score else None,
            "run_metadata": _get_run_metadata(),
            "run_notes": run_note or "",
        })

        # Basic sanity assertions
        assert stats["messages_routed"] > 0, "No messages routed"
        assert len(state["node_states"]) > 0, "No node states in DB"
        assert sum(state["message_counts"].values()) > 0, "No messages stored"

        # Print summary
        print(f"\n{'='*60}")
        print(f"LIVE REPLAY COMPLETE — {case_dir.name}")
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
        print(f"Full results: {case_dir / 'replay_result.json'}")

        # --- Pipeline quality score ---
        print(f"\nPIPELINE SCORE: {score['overall_score']}/100")
        for dim, data in score["dimensions"].items():
            if data["score"] is not None:
                bar = "█" * (data["score"] // 10) + "░" * (10 - data["score"] // 10)
                print(f"  {dim:25s} {bar} {data['score']}")
        print(f"Score written to: {score_path}")
