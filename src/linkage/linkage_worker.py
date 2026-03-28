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
"""

import json
import logging
import time
import uuid
from pathlib import Path

import redis

from src.config import REDIS_URL, CRON_INTERVAL_SECONDS, AGENT_ERROR_LOG_PATH
from src.linkage.agent import run_linkage_agent
from src.store.task_store import (
    get_open_orders_summary,
    get_fulfillment_links,
    upsert_fulfillment_link,
    update_node,
    reconcile_order_ready,
)
from src.store.db import transaction

log = logging.getLogger(__name__)

TASK_EVENTS_STREAM = "task_events"
CONSUMER_GROUP = "linkage_worker_group"
CONSUMER_NAME = "linkage_worker_1"


def _get_all_fulfillment_links() -> list[dict]:
    """Return current fulfillment links for all open client orders."""
    summary = get_open_orders_summary()
    all_links = []
    for order in summary.get("client_orders", []):
        all_links.extend(get_fulfillment_links(order["task_id"]))
    return all_links


def _ensure_consumer_group(r: redis.Redis):
    try:
        r.xgroup_create(TASK_EVENTS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def process_event(event_id: str, fields: dict, r: redis.Redis):
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
    except json.JSONDecodeError:
        log.error("Linkage worker: malformed message_json in event %s", event_id)
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    open_orders = get_open_orders_summary()
    # Skip if no M:N work to do (no client_order or supplier_order tasks open)
    if not open_orders["client_orders"] and not open_orders["supplier_orders"]:
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

    all_links = _get_all_fulfillment_links()

    output = run_linkage_agent(open_orders, all_links, message)
    if output is None:
        log.error("Linkage agent failed for event=%s message=%s",
                  event_id, message.get("message_id"))
        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
        return

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
        update_node(
            task_id=cu.order_id,
            node_id=cu.node_id,
            new_status=cu.new_status,
            confidence=cu.confidence,
            message_id=message.get("message_id"),
            updated_by="linkage_worker",
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

    # Log new task candidates
    for candidate in output.new_task_candidates:
        log.info("Linkage new task candidate: %s", candidate)
        _log_new_task_candidate(candidate, message)

    # Log ambiguity flags
    for flag in output.ambiguity_flags:
        log.warning(
            "LINKAGE AMBIGUITY [%s/%s] blocking=%s | %s",
            flag.severity, flag.category, flag.blocking_node_id, flag.description,
        )

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


def _log_new_task_candidate(candidate: dict, message: dict):
    Path("logs").mkdir(parents=True, exist_ok=True)
    with open("logs/linkage_new_tasks.log", "a") as f:
        f.write(json.dumps({"candidate": candidate, "source_message": message}) + "\n")


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
                    try:
                        process_event(event_id, fields, r)
                    except Exception as e:
                        log.exception("Error processing event %s: %s", event_id, e)
                        # Ack anyway to avoid infinite retry loop in Sprint 3
                        r.xack(TASK_EVENTS_STREAM, CONSUMER_GROUP, event_id)
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
