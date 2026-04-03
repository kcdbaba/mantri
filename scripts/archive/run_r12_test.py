#!/usr/bin/env python3
"""
R12 test runner — conversation routing integration test.

Processes all groups chronologically with conversation routing enabled
for the Tasks group. Warmup messages build state but aren't scored.

Usage:
    PYTHONPATH=. python3 scripts/run_r12_test.py [--skip-warmup] [--dry-run]
"""

import argparse
import json
import logging
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

log = logging.getLogger(__name__)

CASE_DIR = Path("tests/integration_tests/R12-L3-01_internal_staff_conversation_routing")


def main():
    parser = argparse.ArgumentParser(description="R12 conversation routing test")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run conversation analysis only (no agent calls)")
    parser.add_argument("--skip-warmup", action="store_true",
                        help="Skip warmup messages (faster but less context)")
    parser.add_argument("--keep-db", action="store_true",
                        help="Keep the result DB for inspection")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Load test data
    trace_data = json.loads((CASE_DIR / "replay_trace.json").read_text())
    seed = json.loads((CASE_DIR / "seed_tasks.json").read_text())
    messages = trace_data["messages"]
    meta = trace_data["_meta"]
    warmup_end_ts = meta["warmup_end_ts"]

    log.info("R12 test: %d messages (%d warmup, %d test)",
             len(messages), meta["warmup_messages"], meta["test_messages"])

    # Create temp DB and seed
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    from tests.integration_tests.test_live_replay import _seed_db
    _seed_db(db_path, seed)
    log.info("DB seeded at %s", db_path)

    # Initialize agent cache
    from src.tracing.agent_cache import init_cache, save_cache, stats, make_key, get as cache_get, put as cache_put
    cache_path = str(CASE_DIR / "agent_cache.json")
    init_cache(cache_path)

    # Initialize conversation router
    from src.conversation.conversation_router import ConversationRouter, load_ocr_cache, set_ocr_cache

    # Load OCR cache if available
    ocr_path = str(CASE_DIR / "image_ocr_tasks.json")
    if Path(ocr_path).exists():
        ocr = load_ocr_cache(ocr_path)
        set_ocr_cache("Tasks", ocr)

    conv_router = ConversationRouter(enable_llm_matching=False)  # start without LLM to save cost

    monitored = seed.get("monitored_groups", {})
    mock_redis = _MockRedis()

    # Stats tracking
    stats_data = {
        "warmup_messages": 0,
        "test_messages": 0,
        "routed": 0,
        "unrouted": 0,
        "noise": 0,
        "conv_routed": 0,
        "agent_calls": 0,
        "agent_failures": 0,
        "conversations_created": 0,
        "entities_discovered": 0,
        "errors": [],
    }

    BATCH_WINDOW_S = 60
    BATCH_MAX_SIZE = 10

    with patch("src.store.db.DB_PATH", db_path), \
         patch("src.config.DB_PATH", db_path), \
         patch("src.config.ENABLE_CONVERSATION_ROUTING", True), \
         patch("src.router.router.MONITORED_GROUPS", monitored), \
         patch("src.router.router.ENABLE_CONVERSATION_ROUTING", True):

        from src.router.router import route
        from src.router.worker import (
            process_message, process_message_batch,
            _resolve_task_for_entity,
        )
        from src.store.task_store import get_tasks_for_entity
        from src.linkage.linkage_worker import process_event

        t_start = time.time()

        # Batch buffer: {entity_id: {"messages": [...], "last_ts": int}}
        batch_buf: dict[str, dict] = {}

        def _flush_batch(entity_id, batch_msgs):
            """Process a batch through update agent + linkage agent."""
            from src.store.task_store import get_task

            # Resolve entity to task_id
            try:
                entity_tasks = get_tasks_for_entity(entity_id)
                task_id = _resolve_task_for_entity(
                    entity_id, entity_tasks, batch_msgs[-1], mock_redis
                )
            except Exception as e:
                log.debug("Entity resolution failed for %s: %s", entity_id, e)
                task_id = None

            # Multi-task case: pick anchor task (first immature, or first)
            if task_id is None and entity_tasks:
                immature = [t for t in entity_tasks if not t.get("is_mature")]
                anchor = immature[0] if immature else entity_tasks[0]
                task_id = anchor["task_id"]
                log.debug("Multi-task entity %s: using anchor %s", entity_id, task_id)

            if not task_id:
                log.debug("No task for entity %s — skipping batch of %d msgs",
                          entity_id, len(batch_msgs))
                return

            # Check cache before calling update agent
            msg_ids = [m.get("message_id", "") for m in batch_msgs]
            cache_key = make_key(task_id, msg_ids)
            cached = cache_get(cache_key)
            if cached:
                log.info("Cache HIT: task=%s entity=%s (%d msgs)", task_id, entity_id, len(batch_msgs))
                return

            # Update agent
            stats_data["agent_calls"] += 1
            try:
                process_message_batch(task_id, batch_msgs, mock_redis)
                cache_put(cache_key, {"task_id": task_id, "message_count": len(batch_msgs)})
            except Exception as e:
                stats_data["agent_failures"] += 1
                stats_data["errors"].append({
                    "message_id": batch_msgs[-1].get("message_id"),
                    "phase": "update_agent",
                    "error": str(e)[:100],
                })
                log.error("Update agent failed: task=%s entity=%s: %s",
                          task_id, entity_id, e)
                return

            # Linkage agent — process stream events emitted by update agent
            new_events = mock_redis.drain_events()
            for stream_key, fields in new_events:
                try:
                    process_event(f"r12-{entity_id}", fields, mock_redis)
                    stats_data["linkage_events"] = stats_data.get("linkage_events", 0) + 1
                except Exception as e:
                    stats_data["linkage_failures"] = stats_data.get("linkage_failures", 0) + 1
                    log.error("Linkage failed: %s — %s", entity_id, e)

        def _check_and_flush_batches(current_ts):
            """Flush batches that have timed out or hit max size."""
            flushed = []
            for eid, buf in batch_buf.items():
                elapsed = current_ts - buf["last_ts"]
                if elapsed >= BATCH_WINDOW_S or len(buf["messages"]) >= BATCH_MAX_SIZE:
                    flushed.append((eid, list(buf["messages"])))
            for eid, msgs in flushed:
                del batch_buf[eid]
                _flush_batch(eid, msgs)

        for i, msg in enumerate(messages):
            ts = msg.get("timestamp", 0)
            is_warmup = ts < warmup_end_ts
            group_id = msg.get("group_id", "")
            phase = "warmup" if is_warmup else "test"

            if is_warmup:
                stats_data["warmup_messages"] += 1
                if args.skip_warmup:
                    continue
            else:
                stats_data["test_messages"] += 1

            if (i + 1) % 50 == 0:
                log.info("[%s] Processing message %d/%d: %s",
                         phase, i + 1, len(messages), msg.get("message_id"))

            # ── Dry run: no LLM calls ──
            if args.dry_run:
                if group_id == "Tasks":
                    result = conv_router.feed(msg)
                    if result:
                        stats_data["conversations_created"] += len(result.conversations)
                        stats_data["entities_discovered"] += len(result.discovered_entities)
                        if not is_warmup:
                            _log_conversation_result(result)
                    stats_data["conv_routed"] += 1
                else:
                    routes = route(msg)
                    if routes and routes != [("__conv_pending__", 0.0)]:
                        stats_data["routed"] += 1
                    elif not routes:
                        body = (msg.get("body") or "").strip()
                        if not body:
                            stats_data["noise"] += 1
                        else:
                            stats_data["unrouted"] += 1
                continue

            # ── Full run with batching ──

            # Tasks group → conversation router
            if group_id == "Tasks":
                result = conv_router.feed(msg)
                if result:
                    stats_data["conversations_created"] += len(result.conversations)
                    stats_data["entities_discovered"] += len(result.discovered_entities)
                    if not is_warmup:
                        _log_conversation_result(result)
                    # Process conversation results through agent batches
                    for conv in result.conversations:
                        conv_msgs = []
                        for s in conv.scraps:
                            conv_msgs.extend(s.messages)
                        if conv_msgs and conv.entity_ref != "singleton:bookkeeping":
                            _flush_batch(conv.entity_ref, conv_msgs)
                stats_data["conv_routed"] += 1
                continue

            # Direct-mapped groups → route and batch
            routes = route(msg)
            if not routes:
                body = (msg.get("body") or "").strip()
                if not body:
                    stats_data["noise"] += 1
                else:
                    stats_data["unrouted"] += 1
                continue

            stats_data["routed"] += 1

            for entity_id, confidence in routes:
                if entity_id == "__conv_pending__":
                    continue

                # Add to batch buffer
                if entity_id not in batch_buf:
                    batch_buf[entity_id] = {"messages": [], "last_ts": 0}
                batch_buf[entity_id]["messages"].append(msg)
                batch_buf[entity_id]["last_ts"] = ts

            # Check for batches to flush
            _check_and_flush_batches(ts)

        # Flush remaining batches
        for eid, buf in batch_buf.items():
            if buf["messages"]:
                _flush_batch(eid, buf["messages"])

        # Flush remaining conversation buffers
        remaining = conv_router.flush_all()
        for result in remaining:
            stats_data["conversations_created"] += len(result.conversations)
            stats_data["entities_discovered"] += len(result.discovered_entities)
            _log_conversation_result(result)

        elapsed = time.time() - t_start

    # Save cache
    save_cache()

    # Print results
    print(f"\n{'='*60}")
    print(f"R12 TEST COMPLETE ({elapsed:.1f}s)")
    print(f"{'='*60}")
    print(f"Warmup:  {stats_data['warmup_messages']} messages")
    print(f"Test:    {stats_data['test_messages']} messages")
    print(f"Routed:  {stats_data['routed']}")
    print(f"Conv:    {stats_data['conv_routed']}")
    print(f"Errors:  {len(stats_data['errors'])}")
    print(f"Agent calls:  {stats_data['agent_calls']}")
    print(f"Agent fails:  {stats_data['agent_failures']}")
    print(f"Linkage:      {stats_data.get('linkage_events', 0)} events, {stats_data.get('linkage_failures', 0)} failures")
    print(f"Conversations created: {stats_data['conversations_created']}")
    print(f"Entities discovered:   {stats_data['entities_discovered']}")
    print(f"Cache stats: {stats()}")

    if stats_data["errors"]:
        print(f"\nErrors:")
        for err in stats_data["errors"][:5]:
            print(f"  [{err['phase']}] {err['message_id']}: {err['error']}")

    # Write results
    results_path = CASE_DIR / "test_result.json"
    results_path.write_text(json.dumps(stats_data, indent=2, default=str))
    print(f"\nResults written to: {results_path}")

    # Cleanup or preserve DB
    if args.keep_db:
        result_db = CASE_DIR / "replay_result.db"
        Path(db_path).rename(result_db)
        print(f"DB saved to: {result_db}")
    else:
        Path(db_path).unlink(missing_ok=True)


def _log_conversation_result(result):
    """Log a conversation routing result."""
    for conv in result.conversations:
        n = sum(len(s.messages) for s in conv.scraps)
        log.info("CONVERSATION: %s → %d msgs (%d scraps)",
                 conv.entity_ref, n, len(conv.scraps))

    if result.discovered_entities:
        for ent in result.discovered_entities:
            log.info("DISCOVERED: %s (%s, %s)", ent.name, ent.entity_type, ent.source)

    log.info("Flush: %d conversations, %d unassigned/%d total",
             len(result.conversations),
             len(result.unassigned_messages),
             result.total_messages)


class _MockRedis:
    """Minimal Redis mock for testing."""
    def __init__(self):
        self.events = []
        self._seq = 0

    def xadd(self, stream_key, fields, **kwargs):
        self._seq += 1
        self.events.append((stream_key, fields))
        return f"test-{self._seq}"

    def xack(self, *args, **kwargs):
        pass

    def drain_events(self):
        events = list(self.events)
        self.events.clear()
        return events


if __name__ == "__main__":
    main()
