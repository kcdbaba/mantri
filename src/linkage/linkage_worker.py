"""
Linkage worker — subscribes to the task_events Redis stream, runs the linkage
agent on every non-noise message event, applies client_order_updates as direct
DB writes, and upserts fulfillment_links.

task_events stream key: "task_events"
Stream entry fields:
  event_type  : "message_processed"
  task_id     : source task_id
  message_id  : original WhatsApp message_id
  message_json: full enriched message as JSON string

Sprint 3: processes events from all task types (standard_procurement,
client_order, supplier_order). Skips linkage_task events to avoid loops.

Retry + dead-letter:
  Transient errors (Redis, DB lock) and agent failures are retried up to
  MAX_RETRY_ATTEMPTS times with linear backoff. After all retries are
  exhausted the event is written to dead_letter_events and acked to
  prevent the PEL from growing unbounded.

  Dead-letter alerts are logged at CRITICAL level and are intended for
  the developer only — Ashish receives business-level alerts via the
  ambiguity worker, not system health alerts.
"""

import json
import logging
import time
import uuid

import src.api_guard
src.api_guard.activate()

import redis

from src.config import REDIS_URL, CRON_INTERVAL_SECONDS, TASK_EVENTS_STREAM
from src.linkage.agent import run_linkage_agent
from src.router.worker import _handle_ambiguity
from src.store.task_store import (
    get_open_orders_summary,
    get_fulfillment_links,
    upsert_fulfillment_link,
    update_node_as_linkage_agent,
    reconcile_order_ready,
    prune_links_for_supplier_order,
    prune_links_for_client_order,
)
from src.store.db import transaction

log = logging.getLogger(__name__)

CONSUMER_GROUP = "linkage_worker_group"
CONSUMER_NAME = "linkage_worker_1"
MAX_RETRY_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_all_fulfillment_links(open_orders: dict) -> list[dict]:
    """Return current fulfillment links for all open client orders."""
    all_links = []
    for order in open_orders.get("client_orders", []):
        all_links.extend(get_fulfillment_links(order["task_id"]))
    return all_links


def _ensure_consumer_group(r: redis.Redis):
    try:
        r.xgroup_create(TASK_EVENTS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def _write_dead_letter(
    event_id: str, fields: dict, failure_reason: str, attempts: int
):
    now = int(time.time())
    with transaction() as conn:
        conn.execute(
            """INSERT INTO dead_letter_events
               (id, stream_key, event_id, fields_json, failure_reason,
                attempts, first_failed_at, last_failed_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                TASK_EVENTS_STREAM,
                event_id,
                json.dumps(fields),
                failure_reason,
                attempts,
                now,
                now,
            ),
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
    log.info("Linkage new task candidate: %s", candidate)


# ---------------------------------------------------------------------------
# Core event processing — raises on retryable failures, returns on skips
# ---------------------------------------------------------------------------

def process_event(event_id: str, fields: dict, r: redis.Redis):
    """
    Process one stream event. Acks on success or deliberate skip.
    Raises RuntimeError for failures that should be retried / dead-lettered.
    """
    event_type = fields.get("event_type", "")
    if event_type != "message_processed":
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    raw_message = fields.get("message_json")
    if not raw_message:
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as e:
        # Permanent failure — malformed payload can never be fixed by retry
        log.error("Linkage worker: malformed message_json in event %s", event_id)
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    open_orders = get_open_orders_summary()
    # Skip if no M:N work to do — linkage requires at least one client order
    # to create links against. Supplier-only state produces only "no client orders"
    # ambiguity flags which are a constant observation, not per-message.
    if not open_orders["client_orders"]:
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    all_links = _get_all_fulfillment_links(open_orders)

    output = run_linkage_agent(open_orders, all_links, message)
    if output is None:
        raise RuntimeError(
            f"Linkage agent returned None for event={event_id} "
            f"message={message.get('message_id')}"
        )

    # Apply linkage_updates
    for upd in output.linkage_updates:
        upsert_fulfillment_link({
            "id": str(uuid.uuid4()),
            "client_order_id": upd.client_order_id,
            "client_item_description": upd.client_item_description,
            "supplier_order_id": upd.supplier_order_id,
            "supplier_item_description": upd.supplier_item_description,
            "quantity_allocated": upd.quantity_allocated,
            "match_confidence": upd.match_confidence,
            "match_reasoning": upd.match_reasoning,
            "status": upd.status,
        })
        log.info(
            "Linkage: %s → %s [%s] conf=%.2f | %s",
            upd.client_order_id, upd.supplier_order_id,
            upd.status, upd.match_confidence, upd.match_reasoning,
        )

    # Apply client_order_updates as direct DB writes (no agent call)
    for cu in output.client_order_updates:
        update_node_as_linkage_agent(
            task_id=cu.order_id,
            node_id=cu.node_id,
            new_status=cu.new_status,
            confidence=cu.confidence,
            message_id=message.get("message_id"),
        )
        log.info("Client order update: task=%s node=%s → %s (conf=%.2f)",
                 cu.order_id, cu.node_id, cu.new_status, cu.confidence)

    # Idempotent reconciliation: recheck order_ready for all affected client orders
    affected_clients = {upd.client_order_id for upd in output.linkage_updates}
    for cu in output.client_order_updates:
        affected_clients.add(cu.order_id)
    for client_task_id in affected_clients:
        result = reconcile_order_ready(client_task_id)
        if result:
            log.info("reconcile_order_ready: task=%s → order_ready=%s",
                     client_task_id, result)

    # Two-level pruning: check terminal states after all upserts
    affected_suppliers = {upd.supplier_order_id for upd in output.linkage_updates
                          if upd.status in ("fulfilled", "failed", "invalidated")}
    for supplier_id in affected_suppliers:
        if prune_links_for_supplier_order(supplier_id):
            log.info("Pruned supplier order: task=%s — all links terminal", supplier_id)

    completed_clients = {upd.client_order_id for upd in output.linkage_updates
                         if upd.status == "completed"}
    for client_id in completed_clients:
        if prune_links_for_client_order(client_id):
            log.info("Pruned client order: task=%s — all links completed", client_id)

    # Log new task candidates
    for candidate in output.new_task_candidates:
        _log_new_task_candidate(candidate, message, fields.get("task_id", ""))

    # Escalate ambiguity flags via same path as update_agent
    from src.agent.update_agent import AmbiguityFlag
    for flag in output.ambiguity_flags:
        amb = AmbiguityFlag(
            description=flag.description,
            severity=flag.severity,
            category=flag.category,
            blocking_node_id=flag.blocking_node_id,
        )
        if flag.affected_task_ids:
            for task_id in flag.affected_task_ids:
                _handle_ambiguity(amb, task_id, message)
        else:
            # Non-blocking flag with no task attribution — use event's source task_id
            _handle_ambiguity(amb, fields.get("task_id", ""), message)

    # Log event to audit table
    with transaction() as conn:
        conn.execute(
            """INSERT INTO task_event_log (id, task_id, event_type, payload, ts)
               VALUES (?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                fields.get("task_id", ""),
                "linkage_processed",
                json.dumps({
                    "linkage_updates": len(output.linkage_updates),
                    "client_order_updates": len(output.client_order_updates),
                    "ambiguity_flags": len(output.ambiguity_flags),
                }),
                int(time.time()),
            ),
        )

    r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)


# ---------------------------------------------------------------------------
# Retry wrapper — 3 attempts with linear backoff, dead-letter on exhaustion
# ---------------------------------------------------------------------------

def _process_with_retry(event_id: str, fields: dict, r: redis.Redis):
    last_exc = None
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            process_event(event_id, fields, r)
            return
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRY_ATTEMPTS:
                log.warning(
                    "Linkage worker: event=%s attempt=%d/%d failed (%s) — retrying in %ds",
                    event_id, attempt, MAX_RETRY_ATTEMPTS, e, attempt,
                )
                time.sleep(attempt)  # 1s, 2s

    # All attempts exhausted — dead-letter and ack
    _write_dead_letter(event_id, fields, str(last_exc), MAX_RETRY_ATTEMPTS)
    r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
    log.critical(
        "DEAD LETTER: event=%s written after %d failed attempts — %s. "
        "Inspect dead_letter_events table; replay by re-publishing fields_json "
        "to the %s stream. This alert is for the developer.",
        event_id, MAX_RETRY_ATTEMPTS, last_exc, TASK_EVENTS_STREAM,
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    log.info("Linkage worker started — consuming stream %s", TASK_EVENTS_STREAM)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    _ensure_consumer_group(r)

    while True:
        try:
            results = r.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {TASK_EVENTS_STREAM: ">"},
                count=10,
                block=5000,  # 5-second block
            )
            if not results:
                continue
            for stream_name, entries in results:
                for event_id, fields in entries:
                    _process_with_retry(event_id, fields, r)
        except redis.RedisError as e:
            log.error("Linkage worker Redis error: %s — retrying in 5s", e)
            time.sleep(5)
        except Exception as e:
            log.exception("Linkage worker unhandled error: %s", e)
            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
