"""
Alert engine cron worker — runs every 15 minutes, detects overdue cadence nodes.
Sprint 3: outputs to log file only (no WhatsApp send).
"""

import json
import logging
import time
from pathlib import Path

from src.config import CRON_INTERVAL_SECONDS, ALERT_LOG_PATH
from src.store.task_store import get_active_tasks, get_node_states
from src.agent.templates import get_cadence_nodes

log = logging.getLogger(__name__)


def _node_completed_at(node_states: list[dict], node_id_suffix: str) -> int | None:
    """Return updated_at timestamp for a completed node, or None."""
    for n in node_states:
        if n["id"].endswith(f"_{node_id_suffix}") and n["status"] == "completed":
            return n.get("updated_at")
    return None


def _node_status(node_states: list[dict], node_id_suffix: str) -> str:
    for n in node_states:
        if n["id"].endswith(f"_{node_id_suffix}"):
            return n.get("status", "pending")
    return "pending"


def _evaluate_cadence_node(node: dict, task: dict, node_states: list[dict]) -> bool:
    """
    Evaluate whether a cadence node's activation condition is met.
    Returns True if the node should be activated.

    Sprint 3: simple rule-based evaluation against the activation_when string.
    Production: replace with a proper expression evaluator.
    """
    condition = node.get("activates_when", "")
    now = int(time.time())

    if "po_raised.status=completed" in condition:
        return _node_status(node_states, "po_raised") == "completed"

    if "hours_since(quote_requested.completed_at) >= 48" in condition:
        completed_at = _node_completed_at(node_states, "quote_requested")
        if completed_at:
            hours_elapsed = (now - completed_at) / 3600
            return hours_elapsed >= 48
        return False

    if "dispatch_confirmed.status=completed" in condition:
        return _node_status(node_states, "dispatch_confirmed") == "completed"

    if "days_since(delivery_confirmed.completed_at) >= 30" in condition:
        completed_at = _node_completed_at(node_states, "delivery_confirmed")
        if completed_at:
            days_elapsed = (now - completed_at) / 86400
            return days_elapsed >= 30
        return False

    return False


def check_cadence_alerts():
    tasks = get_active_tasks()
    fired = 0

    for task in tasks:
        node_states = get_node_states(task["id"])
        cadence_nodes = get_cadence_nodes(task["order_type"])

        for node in cadence_nodes:
            current_status = _node_status(node_states, node["id"])
            if current_status != "pending":
                continue  # already active/completed

            if _evaluate_cadence_node(node, task, node_states):
                _fire_alert(task, node)
                fired += 1

    if fired:
        log.info("Cadence check: fired %d alert(s)", fired)
    else:
        log.debug("Cadence check: no alerts")


def _fire_alert(task: dict, node: dict):
    alert = {
        "type": "cadence_alert",
        "task_id": task["id"],
        "node_id": node["id"],
        "node_name": node["name"],
        "order_type": task["order_type"],
        "client_id": task["client_id"],
        "ts": int(time.time()),
    }
    Path(ALERT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")
    log.info("CADENCE ALERT | task=%s | node=%s | %s",
             task["id"], node["id"], node["name"])


def run():
    log.info("Cron worker started — interval=%ds", CRON_INTERVAL_SECONDS)
    while True:
        try:
            check_cadence_alerts()
        except Exception as e:
            log.exception("Cron worker error: %s", e)
        time.sleep(CRON_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
