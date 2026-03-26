"""
Router worker — pops messages from Redis queue, routes them,
calls update agent for each routed task, writes results to store.
"""

import json
import logging
import time
from pathlib import Path

import redis

from src.config import (
    REDIS_URL, INGEST_QUEUE_KEY,
    UNROUTED_LOG_PATH, NEW_TASK_LOG_PATH,
    PROVISIONAL_THRESHOLD,
)
from src.router.router import route
from src.agent.update_agent import run_update_agent
from src.store.task_store import update_node, append_message

log = logging.getLogger(__name__)
redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def process_message(message: dict):
    routes = route(message)

    if not routes:
        _log_unrouted(message)
        return

    for task_id, confidence in routes:
        # Store message against this task
        append_message(task_id, message, routing_confidence=confidence)

        # Run update agent
        output = run_update_agent(task_id, message)
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
            _log_new_task_candidate(candidate, message)


def run():
    log.info("Router worker started — listening on %s", INGEST_QUEUE_KEY)
    while True:
        try:
            result = redis_client.brpop(INGEST_QUEUE_KEY, timeout=5)
            if result is None:
                continue
            _, raw = result
            message = json.loads(raw)
            process_message(message)
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


def _log_new_task_candidate(candidate: dict, message: dict):
    Path(NEW_TASK_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(NEW_TASK_LOG_PATH, "a") as f:
        f.write(json.dumps({"candidate": candidate, "source_message": message}) + "\n")
    log.info("New task candidate detected: %s", candidate)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
