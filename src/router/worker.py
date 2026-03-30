"""
Router worker — pops messages from Redis queue, routes them,
calls update agent for each routed task, writes results to store.
"""

import json
import logging
import time
import uuid
from pathlib import Path

import redis

from src.config import (
    REDIS_URL, INGEST_STREAM,
    UNROUTED_LOG_PATH,
    PROVISIONAL_THRESHOLD,
    ESCALATION_PROFILES, ACTIVE_ESCALATION_PROFILE,
    ESCALATION_CATEGORY_OVERRIDES, GATE_NODES,
)
from src.router.router import route
from src.agent.update_agent import run_update_agent, AmbiguityFlag
from src.store.task_store import (
    update_node, update_node_as_update_agent,
    append_message, get_task, get_node_states,
    apply_item_extractions, apply_node_data_extractions,
    check_stock_path_order_ready,
)
from src.store.db import get_connection, transaction

log = logging.getLogger(__name__)


def process_message(message: dict, r: redis.Redis):
    """Process a single message — route, store, run update agent, publish event.
    For batched processing, use process_message_batch instead."""
    routes = route(message)

    if not routes:
        _log_unrouted(message)
        return

    for task_id, confidence in routes:
        task = get_task(task_id)
        order_type = task["order_type"] if task else "standard_procurement"

        append_message(task_id, message, routing_confidence=confidence)

        # Run update agent
        output = run_update_agent(task_id, [message], task_override=task,
                                   routing_confidence=confidence)
        if output is None:
            log.error("Update agent failed for task=%s message=%s",
                      task_id, message.get("message_id"))
            _log_dead_letter(task_id, message)
            continue

        # Write node updates
        for update in output.node_updates:
            status = update.new_status
            # Downgrade to provisional if agent confidence is low
            if update.confidence < PROVISIONAL_THRESHOLD and status not in ("pending", "provisional"):
                status = "provisional"
                log.debug("Downgraded node %s to provisional (confidence=%.2f)",
                          update.node_id, update.confidence)

            update_node_as_update_agent(
                task_id=task_id,
                node_id=update.node_id,
                new_status=status,
                confidence=update.confidence,
                message_id=message.get("message_id"),
            )
            log.info("Node update: task=%s node=%s → %s (conf=%.2f) | %s",
                     task_id, update.node_id, status, update.confidence, update.evidence)

        # Stock path → order_ready auto-trigger (deterministic, no LLM)
        stock_updated = any(u.node_id == "filled_from_stock" for u in output.node_updates)
        if stock_updated:
            result = check_stock_path_order_ready(task_id)
            if result:
                log.info("Stock path → order_ready=%s for task=%s", result, task_id)

        # Log new task candidates (no creation flow in Sprint 3)
        for candidate in output.new_task_candidates:
            _log_new_task_candidate(candidate, message, task_id)

        # Handle ambiguity flags — enqueue, escalate, block gate nodes
        for flag in output.ambiguity_flags:
            _handle_ambiguity(flag, task_id, message)

        # Apply node_data extractions (merge into task_nodes.node_data)
        if output.node_data_extractions:
            apply_node_data_extractions(task_id, output.node_data_extractions)
            log.debug("Node data extractions: task=%s nodes=%s",
                      task_id, [e.node_id for e in output.node_data_extractions])

        # Apply item extractions and check for post-confirmation changes
        if output.item_extractions:
            apply_item_extractions(task_id, order_type, output.item_extractions)
            _check_post_confirmation_item_changes(
                task_id, order_type, output.item_extractions, message
            )

        # Publish to task_events stream for linkage_worker consumption
        _publish_task_event(task_id, message, r)


def process_message_batch(task_id: str, messages: list[dict], r: redis.Redis):
    """Process a batch of messages for a single task_id — one LLM call for the batch."""
    task = get_task(task_id)
    order_type = task["order_type"] if task else "standard_procurement"

    # Store all messages
    for msg in messages:
        routes = route(msg)
        confidence = next((c for tid, c in routes if tid == task_id), 0.9)
        append_message(task_id, msg, routing_confidence=confidence)

    # Single LLM call for the batch
    output = run_update_agent(task_id, messages, task_override=task,
                               routing_confidence=0.9)
    last_msg = messages[-1]

    if output is None:
        log.error("Update agent failed for task=%s batch of %d messages",
                  task_id, len(messages))
        _log_dead_letter(task_id, last_msg)
        _publish_task_event(task_id, last_msg, r)
        return

    for update in output.node_updates:
        status = update.new_status
        if update.confidence < PROVISIONAL_THRESHOLD and status not in ("pending", "provisional"):
            status = "provisional"
        update_node_as_update_agent(
            task_id=task_id, node_id=update.node_id,
            new_status=status, confidence=update.confidence,
            message_id=last_msg.get("message_id"),
        )
        log.info("Node update: task=%s node=%s → %s (conf=%.2f) | %s",
                 task_id, update.node_id, status, update.confidence, update.evidence)

    # Stock path → order_ready auto-trigger (deterministic, no LLM)
    stock_updated = any(u.node_id == "filled_from_stock" for u in output.node_updates)
    if stock_updated:
        result = check_stock_path_order_ready(task_id)
        if result:
            log.info("Stock path → order_ready=%s for task=%s", result, task_id)

    for candidate in output.new_task_candidates:
        _log_new_task_candidate(candidate, last_msg, task_id)

    for flag in output.ambiguity_flags:
        _handle_ambiguity(flag, task_id, last_msg)

    if output.node_data_extractions:
        apply_node_data_extractions(task_id, output.node_data_extractions)

    if output.item_extractions:
        apply_item_extractions(task_id, order_type, output.item_extractions)
        _check_post_confirmation_item_changes(
            task_id, order_type, output.item_extractions, last_msg
        )

    _publish_task_event(task_id, last_msg, r)


CONSUMER_GROUP = "router_worker_group"
CONSUMER_NAME = "router_worker_1"
MAX_RETRY_ATTEMPTS = 3
BATCH_WINDOW_S = 60
BATCH_MAX_SIZE = 10


def _ensure_consumer_group(r: redis.Redis):
    try:
        r.xgroup_create(INGEST_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def _process_with_retry(event_id: str, fields: dict, r: redis.Redis):
    raw = fields.get("message_json")
    if not raw:
        r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
        return

    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Malformed message_json in event %s — dead-lettering", event_id)
        _write_ingest_dead_letter(event_id, fields, "malformed JSON")
        r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
        return

    last_exc = None
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            process_message(message, r)
            r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
            return
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRY_ATTEMPTS:
                log.warning(
                    "Router worker: event=%s attempt=%d/%d failed (%s) — retrying in %ds",
                    event_id, attempt, MAX_RETRY_ATTEMPTS, e, attempt,
                )
                time.sleep(attempt)

    # All attempts exhausted
    _write_ingest_dead_letter(event_id, fields, str(last_exc))
    r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
    log.critical(
        "DEAD LETTER: router event=%s after %d attempts — %s",
        event_id, MAX_RETRY_ATTEMPTS, last_exc,
    )


def _write_ingest_dead_letter(event_id: str, fields: dict, failure_reason: str):
    now = int(time.time())
    with transaction() as conn:
        conn.execute(
            """INSERT INTO dead_letter_events
               (id, stream_key, event_id, fields_json, failure_reason,
                attempts, first_failed_at, last_failed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                INGEST_STREAM,
                event_id,
                json.dumps(fields),
                failure_reason,
                MAX_RETRY_ATTEMPTS,
                now,
                now,
            ),
        )


def _flush_batch(task_id: str, batch: list[dict], event_ids: list[str], r: redis.Redis):
    """Flush a batch of messages for a single task_id — one LLM call."""
    try:
        process_message_batch(task_id, batch, r)
    except Exception as e:
        log.error("Batch processing failed for task=%s batch_size=%d: %s",
                  task_id, len(batch), e)
    # ACK all events in the batch regardless (messages are stored in DB already)
    for eid in event_ids:
        r.xack(INGEST_STREAM, CONSUMER_GROUP, eid)


def run():
    log.info("Router worker started — consuming stream %s (batch_window=%ds)",
             INGEST_STREAM, BATCH_WINDOW_S)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    _ensure_consumer_group(r)

    # Batch buffer: {task_id: {"messages": [...], "event_ids": [...], "last_at": timestamp}}
    batch_buffer: dict[str, dict] = {}

    while True:
        try:
            # Short block time to check batch timeouts frequently
            results = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {INGEST_STREAM: ">"},
                count=10,
                block=2000,
            )

            # Route incoming messages into batch buffer
            if results:
                for stream_name, entries in results:
                    for event_id, fields in entries:
                        raw = fields.get("message_json")
                        if not raw:
                            r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
                            continue
                        try:
                            message = json.loads(raw)
                        except json.JSONDecodeError:
                            _write_ingest_dead_letter(event_id, fields, "malformed JSON")
                            r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
                            continue

                        routes = route(message)
                        if not routes:
                            _log_unrouted(message)
                            r.xack(INGEST_STREAM, CONSUMER_GROUP, event_id)
                            continue

                        # Add to batch buffer for each routed task
                        for task_id, confidence in routes:
                            if task_id not in batch_buffer:
                                batch_buffer[task_id] = {
                                    "messages": [], "event_ids": [], "last_at": 0,
                                }
                            buf = batch_buffer[task_id]
                            buf["messages"].append(message)
                            buf["event_ids"].append(event_id)
                            buf["last_at"] = time.time()

            # Flush batches that have timed out or hit max size
            now = time.time()
            flushed = []
            for task_id, buf in batch_buffer.items():
                elapsed = now - buf["last_at"]
                if elapsed >= BATCH_WINDOW_S or len(buf["messages"]) >= BATCH_MAX_SIZE:
                    _flush_batch(task_id, buf["messages"], buf["event_ids"], r)
                    flushed.append(task_id)

            for task_id in flushed:
                del batch_buffer[task_id]

        except redis.RedisError as e:
            log.error("Router worker Redis error: %s — retrying in 5s", e)
            time.sleep(5)
        except Exception as e:
            log.exception("Router worker unhandled error: %s", e)
            time.sleep(1)


def _log_unrouted(message: dict):
    Path(UNROUTED_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(UNROUTED_LOG_PATH, "a") as f:
        f.write(json.dumps(message) + "\n")
    log.debug("Unrouted: %s", message.get("message_id"))


DEDUP_WINDOW_S = 3600  # 1 hour — same (task, category, node) within this window is a duplicate


def _is_duplicate_flag(task_id: str, category: str, node_id: str | None, now: int) -> bool:
    """Check if the same (task_id, category, node_id) was already raised within DEDUP_WINDOW_S."""
    conn = get_connection()
    cutoff = now - DEDUP_WINDOW_S
    row = conn.execute(
        """SELECT id FROM ambiguity_queue
           WHERE task_id=? AND category=? AND node_id IS ?
             AND created_at >= ? AND status IN ('pending', 'escalated')
           LIMIT 1""",
        (task_id, category, node_id, cutoff),
    ).fetchone()
    conn.close()
    return row is not None


def _check_rate_limit(task_id: str, profile: dict, now: int) -> bool:
    """Return True if the task has hit the per-task per-hour escalation rate limit."""
    limit = profile.get("escalation_rate_limit")
    if limit is None:
        return False
    conn = get_connection()
    cutoff = now - 3600
    count = conn.execute(
        "SELECT COUNT(*) FROM ambiguity_queue WHERE task_id=? AND created_at >= ?",
        (task_id, cutoff),
    ).fetchone()[0]
    conn.close()
    return count >= limit


def _handle_ambiguity(flag: AmbiguityFlag, task_id: str, message: dict):
    """Enqueue ambiguity, block gate node if warranted, log alert.

    Deduplication: same (task_id, category, node_id) within 1 hour is skipped.
    Rate limiting: respects profile escalation_rate_limit per task per hour.
    Low non-blocking flags: auto-resolved immediately (never enqueued as pending).
    """
    profile = ESCALATION_PROFILES[ACTIVE_ESCALATION_PROFILE]
    now = int(time.time())

    # Per-category threshold override
    cat_override = ESCALATION_CATEGORY_OVERRIDES.get(flag.category, {})
    blocking_threshold = cat_override.get("blocking_threshold",
                                          profile["blocking_threshold"])

    # Determine escalation target from severity
    if flag.severity in ("high", "medium"):
        target = profile["escalation_target_high"]
    else:
        target = profile["escalation_target_low"]

    # Should we block the gate node?
    should_block = (
        flag.blocking_node_id is not None
        and flag.blocking_node_id in GATE_NODES
        and not profile["silent_resolution_allowed"]
    )

    # --- Dedup: skip if same (task, category, node) raised within window ---
    if _is_duplicate_flag(task_id, flag.category, flag.blocking_node_id, now):
        log.debug("DEDUP skip [%s/%s] task=%s node=%s | %s",
                  flag.severity, flag.category, task_id,
                  flag.blocking_node_id, flag.description)
        return

    # --- Rate limit: skip non-blocking flags if task hit hourly limit ---
    if not should_block and _check_rate_limit(task_id, profile, now):
        log.debug("RATE-LIMITED [%s/%s] task=%s | %s",
                  flag.severity, flag.category, task_id, flag.description)
        return

    # --- Low non-blocking: auto-resolve immediately, don't escalate ---
    if flag.severity == "low" and not should_block:
        with transaction() as conn:
            conn.execute(
                """INSERT INTO ambiguity_queue
                   (id, message_id, task_id, node_id, group_id, body, description,
                    severity, category, escalation_target, blocking, status,
                    created_at, resolved_at, resolution_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    message.get("message_id"),
                    task_id,
                    flag.blocking_node_id,
                    message.get("group_id"),
                    message.get("body", "")[:500],
                    flag.description,
                    flag.severity,
                    flag.category,
                    json.dumps(target),
                    0,
                    "expired",
                    now,
                    now,
                    "auto-resolved: low non-blocking",
                ),
            )
        log.debug("AUTO-RESOLVED low non-blocking [%s] task=%s | %s",
                  flag.category, task_id, flag.description)
        return

    # --- Enqueue as pending ---
    with transaction() as conn:
        conn.execute(
            """INSERT INTO ambiguity_queue
               (id, message_id, task_id, node_id, group_id, body, description,
                severity, category, escalation_target, blocking, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                message.get("message_id"),
                task_id,
                flag.blocking_node_id,
                message.get("group_id"),
                message.get("body", "")[:500],
                flag.description,
                flag.severity,
                flag.category,
                json.dumps(target),
                1 if should_block else 0,
                "pending",
                now,
            ),
        )

    # Block the gate node immediately if warranted
    if should_block:
        update_node(
            task_id=task_id,
            node_id=flag.blocking_node_id,
            new_status="blocked",
            confidence=0.0,
            message_id=message.get("message_id"),
            updated_by="ambiguity_block",
        )
        log.warning("BLOCKED node=%s task=%s — ambiguity: %s",
                    flag.blocking_node_id, task_id, flag.description)

    log.info("AMBIGUITY [%s/%s] task=%s target=%s blocking=%s | %s",
             flag.severity, flag.category, task_id, target,
             flag.blocking_node_id if should_block else "none",
             flag.description)


def _check_post_confirmation_item_changes(
    task_id: str, order_type: str, extractions: list, message: dict
):
    """
    Raise an immediate high-severity escalation if items are changed after
    the order has been locked:
      - client_order / standard_procurement: locked at order_confirmation=completed
      - supplier_order: locked at supplier_collection=completed
    """
    # Determine lock gate and blocking node for this order type
    if order_type == "supplier_order":
        lock_gate = "supplier_collection"
        blocking_node = "supplier_QC"
    else:
        lock_gate = "order_confirmation"
        blocking_node = "dispatched"

    # Read current node states (post-update)
    node_status = {
        n["id"][len(task_id) + 1:]: n["status"]
        for n in get_node_states(task_id)
    }

    if node_status.get(lock_gate) != "completed":
        return  # order not yet locked — changes are expected

    ops = list({e.operation for e in extractions})
    changed = [e.description for e in extractions]
    description = (
        f"Items changed after {lock_gate} completed — "
        f"operations: {ops}; items: {changed[:5]}"
        + (" (+ more)" if len(changed) > 5 else "")
    )
    flag = AmbiguityFlag(
        description=description,
        severity="high",
        category="quantity",
        blocking_node_id=blocking_node,
    )
    _handle_ambiguity(flag, task_id, message)
    log.warning(
        "POST-CONFIRMATION ITEM CHANGE: task=%s gate=%s ops=%s items=%s",
        task_id, lock_gate, ops, changed[:3],
    )


def _publish_task_event(task_id: str, message: dict, r: redis.Redis):
    """Publish a message_processed event to the task_events Redis stream."""
    try:
        r.xadd(
            "task_events",
            {
                "event_type": "message_processed",
                "task_id": task_id,
                "message_id": message.get("message_id", ""),
                "message_json": json.dumps(message),
            },
            maxlen=10_000,  # approximate trim — keeps ~10k events in memory
            approximate=True,
        )
    except redis.RedisError as e:
        log.warning("Failed to publish task_event for task=%s: %s", task_id, e)


def _log_dead_letter(task_id: str, message: dict, failure_reason: str = ""):
    """Record a failed update_agent call to dead_letter_events for review."""
    reason = failure_reason or "update_agent returned None (API failure or parse error after retry)"
    now = int(time.time())
    with transaction() as conn:
        conn.execute(
            """INSERT INTO dead_letter_events
               (id, stream_key, event_id, fields_json, failure_reason,
                attempts, first_failed_at, last_failed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                "update_agent",
                message.get("message_id", ""),
                json.dumps({"task_id": task_id, "message": message}),
                reason,
                1,
                now,
                now,
            ),
        )
    log.critical(
        "DEAD LETTER: update_agent failed for task=%s message=%s reason=%s",
        task_id, message.get("message_id"), reason,
    )


def _log_new_task_candidate(candidate: dict, message: dict, task_id: str):
    with transaction() as conn:
        conn.execute(
            "INSERT INTO task_event_log (id, task_id, event_type, payload, ts) VALUES (?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                task_id,
                "new_task_candidate",
                json.dumps({"candidate": candidate, "source_message_id": message.get("message_id")}),
                int(time.time()),
            ),
        )
    log.info("New task candidate detected: %s", candidate)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
