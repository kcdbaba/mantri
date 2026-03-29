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
    REDIS_URL, INGEST_QUEUE_KEY,
    UNROUTED_LOG_PATH,
    PROVISIONAL_THRESHOLD,
    ESCALATION_PROFILES, ACTIVE_ESCALATION_PROFILE,
    ESCALATION_CATEGORY_OVERRIDES, GATE_NODES,
)
from src.router.router import route
from src.agent.update_agent import run_update_agent, AmbiguityFlag
from src.store.task_store import (
    update_node, append_message, get_task, get_node_states,
    apply_item_extractions, apply_node_data_extractions,
)
from src.store.db import transaction

log = logging.getLogger(__name__)


def process_message(message: dict, r: redis.Redis):
    routes = route(message)

    if not routes:
        _log_unrouted(message)
        return

    for task_id, confidence in routes:
        # Fetch task once — used for order_type and agent context
        task = get_task(task_id)
        order_type = task["order_type"] if task else "standard_procurement"

        # Store message against this task
        append_message(task_id, message, routing_confidence=confidence)

        # Run update agent
        output = run_update_agent(task_id, message, task_override=task,
                                   routing_confidence=confidence)
        if output is None:
            log.error("Update agent failed for task=%s message=%s",
                      task_id, message.get("message_id"))
            continue

        # Write node updates
        for update in output.node_updates:
            status = update.new_status
            # Downgrade to provisional if agent confidence is low
            if update.confidence < PROVISIONAL_THRESHOLD and status not in ("pending", "provisional"):
                status = "provisional"
                log.debug("Downgraded node %s to provisional (confidence=%.2f)",
                          update.node_id, update.confidence)

            update_node(
                task_id=task_id,
                node_id=update.node_id,
                new_status=status,
                confidence=update.confidence,
                message_id=message.get("message_id"),
            )
            log.info("Node update: task=%s node=%s → %s (conf=%.2f) | %s",
                     task_id, update.node_id, status, update.confidence, update.evidence)

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


def run():
    log.info("Router worker started — listening on %s", INGEST_QUEUE_KEY)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        try:
            result = r.brpop(INGEST_QUEUE_KEY, timeout=5)
            if result is None:
                continue
            _, raw = result
            message = json.loads(raw)
            process_message(message, r)
        except redis.RedisError as e:
            log.error("Redis error: %s — retrying in 5s", e)
            time.sleep(5)
        except Exception as e:
            log.exception("Unhandled error processing message: %s", e)
            time.sleep(1)  # brief pause, then continue


def _log_unrouted(message: dict):
    Path(UNROUTED_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(UNROUTED_LOG_PATH, "a") as f:
        f.write(json.dumps(message) + "\n")
    log.debug("Unrouted: %s", message.get("message_id"))


def _handle_ambiguity(flag: AmbiguityFlag, task_id: str, message: dict):
    """Enqueue ambiguity, block gate node if warranted, log alert."""
    profile = ESCALATION_PROFILES[ACTIVE_ESCALATION_PROFILE]

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

    now = int(time.time())

    # Write to ambiguity_queue — no deduplication at DB level; a single message
    # may legitimately raise multiple distinct flags (e.g. entity + quantity).
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
