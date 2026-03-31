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
    ENABLE_LIVE_TASK_CREATION,
)
from src.router.router import route
from src.agent.update_agent import run_update_agent, AmbiguityFlag
from src.store.task_store import (
    update_node, update_node_as_update_agent,
    append_message, get_task, get_node_states,
    apply_item_extractions, apply_node_data_extractions,
    check_stock_path_order_ready, cascade_auto_triggers,
)
from src.store.db import get_connection, transaction, create_task_live

log = logging.getLogger(__name__)


def _apply_output(task_id: str, order_type: str, output, message: dict, r):
    """Apply agent output (node updates, items, flags, etc.) to a specific task."""
    # Write node updates
    for update in output.node_updates:
        status = update.new_status
        if update.confidence < PROVISIONAL_THRESHOLD and status not in ("pending", "provisional"):
            status = "provisional"
            log.debug("Downgraded node %s to provisional (confidence=%.2f)",
                      update.node_id, update.confidence)
        update_node_as_update_agent(
            task_id=task_id, node_id=update.node_id,
            new_status=status, confidence=update.confidence,
            message_id=message.get("message_id"),
        )
        log.info("Node update: task=%s node=%s → %s (conf=%.2f) | %s",
                 task_id, update.node_id, status, update.confidence, update.evidence)

    # Stock path → order_ready auto-trigger
    if any(u.node_id == "filled_from_stock" for u in output.node_updates):
        result = check_stock_path_order_ready(task_id)
        if result:
            log.info("Stock path → order_ready=%s for task=%s", result, task_id)

    # Cascade downstream auto-triggers
    cascaded = cascade_auto_triggers(task_id)
    if cascaded:
        log.info("Auto-trigger cascade: task=%s → %s", task_id, cascaded)

    # New task candidates (log only — creation handled by entity routing)
    for candidate in output.new_task_candidates:
        _log_new_task_candidate(candidate, message, task_id)

    # Ambiguity flags
    for flag in output.ambiguity_flags:
        _handle_ambiguity(flag, task_id, message)

    # Node data extractions
    if output.node_data_extractions:
        apply_node_data_extractions(task_id, output.node_data_extractions)

    # Item extractions
    if output.item_extractions:
        apply_item_extractions(task_id, order_type, output.item_extractions)
        _check_post_confirmation_item_changes(
            task_id, order_type, output.item_extractions, message
        )

    # Publish to task_events stream for linkage_worker
    _publish_task_event(task_id, message, r)


def _resolve_task_for_entity(entity_id: str, entity_tasks: list[dict],
                              message: dict, r) -> str:
    """Determine which task to process for nil-task or single-task case.
    For multi-task, returns None — caller must use agent-based assignment."""
    if not entity_tasks:
        # No tasks — create default
        order_type = "client_order"
        task_id = create_task_live(
            order_type=order_type, client_id=entity_id,
            source_group_id=message.get("group_id"),
            source_message_id=message.get("message_id"),
        )
        from src.router.alias_dict import invalidate_alias_cache
        invalidate_alias_cache()
        log.info("ENTITY NIL CREATE: %s → new %s (%s)", entity_id, task_id, order_type)
        return task_id

    if len(entity_tasks) == 1:
        return entity_tasks[0]["task_id"]

    # Multiple tasks — return None, caller uses agent assignment
    return None


def _handle_task_assignment(output, entity_id: str, entity_tasks: list[dict],
                             primary_task_id: str, message: dict, r) -> str:
    """Handle agent's task_assignment decision. Returns actual task_id to apply output to."""
    assignment = output.task_assignment

    if assignment == "new" and ENABLE_LIVE_TASK_CREATION:
        order_type = output.new_task_order_type or "client_order"
        new_id = create_task_live(
            order_type=order_type, client_id=entity_id,
            source_group_id=message.get("group_id"),
            source_message_id=message.get("message_id"),
        )
        from src.router.alias_dict import invalidate_alias_cache
        invalidate_alias_cache()
        append_message(new_id, message, routing_confidence=0.85)
        log.info("AGENT CREATE: %s → new %s (%s) for entity %s",
                 new_id, order_type, output.task_assignment, entity_id)
        return new_id

    if assignment and assignment != primary_task_id:
        # Agent reassigned to a different existing task
        valid_ids = {t["task_id"] for t in entity_tasks}
        if assignment in valid_ids:
            append_message(assignment, message, routing_confidence=0.85)
            log.info("AGENT REASSIGN: %s → %s for entity %s",
                     primary_task_id, assignment, entity_id)
            return assignment
        log.warning("Agent assigned to unknown task %s — using primary %s",
                    assignment, primary_task_id)

    return primary_task_id


def process_message(message: dict, r: redis.Redis):
    """Process a single message — route to entity, resolve task, run agent, apply output."""
    routes = route(message)

    if not routes:
        _log_unrouted(message)
        return

    for entity_id, confidence in routes:
        from src.store.task_store import get_tasks_for_entity
        entity_tasks = get_tasks_for_entity(entity_id)

        resolved = _resolve_task_for_entity(entity_id, entity_tasks, message, r)

        if resolved is not None:
            # Single task or nil-task — fast path, no assignment decision
            task_id = resolved
            task = get_task(task_id)
            order_type = task["order_type"] if task else "standard_procurement"

            append_message(task_id, message, routing_confidence=confidence)
            output = run_update_agent(task_id, [message], task_override=task,
                                       routing_confidence=confidence)
            if output is None:
                _log_dead_letter(task_id, message)
                continue
            _apply_output(task_id, order_type, output, message, r)

        else:
            # Multiple tasks — agent decides assignment
            # Use first immature task as primary (or first task if all mature)
            immature = [t for t in entity_tasks if not t["is_mature"]]
            primary = immature[0] if immature else entity_tasks[0]
            primary_task_id = primary["task_id"]
            task = get_task(primary_task_id)
            order_type = task["order_type"] if task else "standard_procurement"

            append_message(primary_task_id, message, routing_confidence=confidence)
            output = run_update_agent(
                primary_task_id, [message], task_override=task,
                routing_confidence=confidence,
                entity_tasks=entity_tasks,
            )
            if output is None:
                _log_dead_letter(primary_task_id, message)
                continue

            # Handle agent's task assignment decision
            actual_task_id = _handle_task_assignment(
                output, entity_id, entity_tasks, primary_task_id, message, r
            )
            actual_task = get_task(actual_task_id) if actual_task_id != primary_task_id else task
            actual_order_type = actual_task["order_type"] if actual_task else order_type

            _apply_output(actual_task_id, actual_order_type, output, message, r)


def process_message_batch(task_id: str, messages: list[dict], r: redis.Redis):
    """Process a batch of messages for a single task_id — one LLM call for the batch.
    Note: task_id here is resolved by the caller (run loop or integration test)."""
    task = get_task(task_id)
    order_type = task["order_type"] if task else "standard_procurement"

    # Store all messages
    for msg in messages:
        append_message(task_id, msg, routing_confidence=0.9)

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

    _apply_output(task_id, order_type, output, last_msg, r)


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


def _flush_entity_batch(entity_id: str, batch: list[dict], event_ids: list[str], r: redis.Redis):
    """Flush a batch of messages for an entity — resolve task, then process."""
    from src.store.task_store import get_tasks_for_entity
    entity_tasks = get_tasks_for_entity(entity_id)
    task_id = _resolve_task_for_entity(entity_id, entity_tasks, batch[-1], r)

    try:
        process_message_batch(task_id, batch, r)
    except Exception as e:
        log.error("Entity batch failed for entity=%s task=%s batch_size=%d: %s",
                  entity_id, task_id, len(batch), e)
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

                        # Add to batch buffer per entity
                        for entity_id, confidence in routes:
                            if entity_id not in batch_buffer:
                                batch_buffer[entity_id] = {
                                    "messages": [], "event_ids": [], "last_at": 0,
                                }
                            buf = batch_buffer[entity_id]
                            buf["messages"].append(message)
                            buf["event_ids"].append(event_id)
                            buf["last_at"] = time.time()

            # Flush batches that have timed out or hit max size
            now = time.time()
            flushed = []
            for entity_id, buf in batch_buffer.items():
                elapsed = now - buf["last_at"]
                if elapsed >= BATCH_WINDOW_S or len(buf["messages"]) >= BATCH_MAX_SIZE:
                    _flush_entity_batch(entity_id, buf["messages"], buf["event_ids"], r)
                    flushed.append(entity_id)

            for entity_id in flushed:
                del batch_buffer[entity_id]

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


def _create_task_from_candidate(candidate: dict, message: dict,
                                source_task_id: str, r) -> str | None:
    """Create a new task from a new_order candidate. Returns new task_id or None."""
    order_type = candidate.get("order_type")
    if order_type not in ("client_order", "supplier_order"):
        log.warning("Invalid order_type in new_order candidate: %s", order_type)
        _log_new_task_candidate(candidate, message, source_task_id)
        return None

    entity_id = candidate.get("entity_id") or f"entity_{uuid.uuid4().hex[:6]}"
    entity_name = candidate.get("entity_name", "")
    now = int(time.time())

    # Build aliases from candidate
    aliases = []
    if entity_name:
        aliases.append({"alias": entity_name.lower(), "entity_id": entity_id,
                        "entity_type": "client" if order_type == "client_order" else "supplier"})

    try:
        new_task_id = create_task_live(
            order_type=order_type,
            client_id=entity_id,
            source_group_id=message.get("group_id"),
            source_message_id=message.get("message_id"),
            aliases=aliases,
        )
    except Exception as e:
        log.error("Failed to create task from candidate: %s", e)
        _log_new_task_candidate(candidate, message, source_task_id)
        return None

    # Invalidate alias cache so new aliases are immediately routable
    from src.router.alias_dict import invalidate_alias_cache
    invalidate_alias_cache()

    # Route the triggering message to the new task
    append_message(new_task_id, message, routing_confidence=0.85)

    # Log creation event
    with transaction() as conn:
        conn.execute(
            "INSERT INTO task_event_log (id, task_id, event_type, payload, ts) VALUES (?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                new_task_id,
                "task_created",
                json.dumps({
                    "source_task_id": source_task_id,
                    "candidate": candidate,
                    "source_message_id": message.get("message_id"),
                }),
                now,
            ),
        )

    # Publish task event for linkage worker
    _publish_task_event(new_task_id, message, r)

    log.info("TASK CREATED: %s (%s) from candidate in task=%s | %s",
             new_task_id, order_type, source_task_id, candidate.get("context", ""))
    return new_task_id


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
