"""
Alert engine cron worker — runs every 15 minutes.
Handles time_trigger nodes and repeating pre-delivery enquiries.
Sprint 3: outputs to log file only (no WhatsApp send).
"""

import json
import logging
import time
import uuid
from pathlib import Path

from src.config import CRON_INTERVAL_SECONDS, ALERT_LOG_PATH
from src.store.task_store import get_active_tasks, get_node_states, get_node_data
from src.store.db import transaction
from src.agent.templates import get_time_trigger_nodes

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


def _alert_already_fired(task_id: str, node_id: str, alert_key: str) -> bool:
    """Check task_alerts_fired to avoid duplicate alerts."""
    from src.store.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM task_alerts_fired WHERE task_id=? AND node_id=? AND alert_key=?",
        (task_id, node_id, alert_key)
    ).fetchone()
    conn.close()
    return row is not None


def _record_alert_fired(task_id: str, node_id: str, alert_key: str):
    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO task_alerts_fired (id, task_id, node_id, alert_key, fired_at) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), task_id, node_id, alert_key, int(time.time()))
        )


def _evaluate_time_trigger(node: dict, task: dict, node_states: list[dict]) -> list[str]:
    """
    Evaluate a time_trigger node. Returns list of alert_keys that should fire now.
    Sprint 3: rule-based. Production: replace with expression evaluator.
    """
    condition = node.get("activates_when", "")
    now = int(time.time())
    keys_to_fire = []

    # quote_followup_48h
    if "hours_since(client_quotation.updated_at) >= 48" in condition:
        if _node_status(node_states, "client_quotation") == "in_progress":
            completed_at = _node_completed_at(node_states, "client_quotation")
            # Use updated_at from the node if available
            updated_at = completed_at  # fallback
            for n in node_states:
                if n["id"].endswith("_client_quotation"):
                    updated_at = n.get("updated_at")
                    break
            if updated_at and (now - updated_at) / 3600 >= 48:
                keys_to_fire.append("elapsed_48h")

    # payment_followup_30d
    if "days_since(delivery_confirmed.completed_at) >= 30" in condition:
        completed_at = _node_completed_at(node_states, "delivery_confirmed")
        if completed_at and (now - completed_at) / 86400 >= 30:
            keys_to_fire.append("elapsed_30d")

    # supplier_predelivery_enquiry — repeating, fires at T-7, T-3, T-1 before expected_delivery_date
    if "expected_delivery_date IS SET" in condition:
        if _node_status(node_states, "supplier_collection") in ("completed",):
            return []  # goods already received, no more enquiries needed
        # expected_delivery_date lives in supplier_indent node_data (extracted by update agent)
        expected_date = get_node_data(task["id"], "supplier_indent").get("expected_delivery_date")
        if expected_date:
            try:
                import datetime
                exp_ts = int(datetime.datetime.fromisoformat(expected_date).timestamp())
                days_before_list = node.get("alert_days_before", [7, 3, 1])
                for days in days_before_list:
                    fire_at = exp_ts - (days * 86400)
                    if now >= fire_at:
                        keys_to_fire.append(f"days_before_{days}")
            except (ValueError, TypeError):
                pass

    return keys_to_fire


def check_time_trigger_alerts():
    tasks = get_active_tasks()
    fired = 0

    for task in tasks:
        node_states = get_node_states(task["id"])
        time_trigger_nodes = get_time_trigger_nodes(task["order_type"])

        for node in time_trigger_nodes:
            current_status = _node_status(node_states, node["id"])
            if current_status == "completed":
                continue  # already done

            alert_keys = _evaluate_time_trigger(node, task, node_states)
            for alert_key in alert_keys:
                if _alert_already_fired(task["id"], node["id"], alert_key):
                    continue
                _fire_alert(task, node, alert_key)
                _record_alert_fired(task["id"], node["id"], alert_key)
                fired += 1

    if fired:
        log.info("Time-trigger check: fired %d alert(s)", fired)
    else:
        log.debug("Time-trigger check: no alerts")


def _fire_alert(task: dict, node: dict, alert_key: str = ""):
    alert = {
        "type": "time_trigger_alert",
        "task_id": task["id"],
        "node_id": node["id"],
        "node_name": node["name"],
        "alert_key": alert_key,
        "order_type": task["order_type"],
        "client_id": task["client_id"],
        "ts": int(time.time()),
    }
    Path(ALERT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")
    log.info("TIME-TRIGGER ALERT | task=%s | node=%s | key=%s | %s",
             task["id"], node["id"], alert_key, node["name"])


def run():
    log.info("Cron worker started — interval=%ds", CRON_INTERVAL_SECONDS)
    while True:
        try:
            check_time_trigger_alerts()
        except Exception as e:
            log.exception("Cron worker error: %s", e)
        time.sleep(CRON_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
