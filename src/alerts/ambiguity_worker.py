"""
Ambiguity worker — polls ambiguity_queue, fires escalation alerts,
handles re-escalation on timeout, and auto-resolves low-severity items
that exceed resolution_timeout_low_s.

Sprint 3: outputs to log file only (no WhatsApp send).
Production: replace _send_escalation() with Meta Cloud API call.
"""

import json
import logging
import time
import uuid
from pathlib import Path

from src.config import (
    CRON_INTERVAL_SECONDS, ALERT_LOG_PATH,
    ESCALATION_PROFILES, ACTIVE_ESCALATION_PROFILE,
)
from src.store.db import get_connection, transaction
from src.store.task_store import update_node

log = logging.getLogger(__name__)


def check_ambiguity_queue():
    profile = ESCALATION_PROFILES[ACTIVE_ESCALATION_PROFILE]
    now = int(time.time())
    conn = get_connection()

    pending = conn.execute(
        "SELECT * FROM ambiguity_queue WHERE status IN ('pending', 'escalated')"
    ).fetchall()
    conn.close()

    for row in pending:
        row = dict(row)
        _process_entry(row, profile, now)


DIGEST_WINDOW_S = 900  # 15 min — batch medium-severity flags into digest windows


def _process_entry(entry: dict, profile: dict, now: int):
    severity = entry["severity"]
    status = entry["status"]
    created_at = entry["created_at"] or now
    escalated_at = entry["escalated_at"]
    re_escalations = entry["re_escalation_count"] or 0

    timeout_high = profile["resolution_timeout_high_s"]
    timeout_low = profile["resolution_timeout_low_s"]

    # --- New entry: escalate based on severity ---
    if status == "pending":
        if severity == "high":
            # High: escalate immediately
            _send_escalation(entry, is_re_escalation=False)
            with transaction() as conn:
                conn.execute(
                    """UPDATE ambiguity_queue
                       SET status='escalated', escalated_at=?
                       WHERE id=?""",
                    (now, entry["id"]),
                )
        elif severity == "medium":
            # Medium: hold for digest window before escalating
            age = now - created_at
            if age >= DIGEST_WINDOW_S:
                _send_escalation(entry, is_re_escalation=False)
                with transaction() as conn:
                    conn.execute(
                        """UPDATE ambiguity_queue
                           SET status='escalated', escalated_at=?
                           WHERE id=?""",
                        (now, entry["id"]),
                    )
            # else: still within digest window, leave as pending
        else:
            # Low: auto-resolve after timeout_low (handled below in escalated branch)
            # Low blocking flags still escalate immediately
            if entry["blocking"]:
                _send_escalation(entry, is_re_escalation=False)
                with transaction() as conn:
                    conn.execute(
                        """UPDATE ambiguity_queue
                           SET status='escalated', escalated_at=?
                           WHERE id=?""",
                        (now, entry["id"]),
                    )
            else:
                # Non-blocking low should already be auto-resolved at creation
                # (by _handle_ambiguity). If one slips through, resolve it now.
                _auto_resolve(entry)
        return

    # --- Already escalated: check for re-escalation or auto-resolution ---
    time_since_escalation = now - (escalated_at or created_at)

    if severity == "high":
        # Re-escalate if unresolved past timeout_high
        if time_since_escalation >= timeout_high:
            _send_escalation(entry, is_re_escalation=True)
            with transaction() as conn:
                conn.execute(
                    """UPDATE ambiguity_queue
                       SET escalated_at=?, re_escalation_count=?
                       WHERE id=?""",
                    (now, re_escalations + 1, entry["id"]),
                )
    elif severity == "medium":
        # Medium: re-escalate at 2x timeout_high (less aggressive)
        if time_since_escalation >= timeout_high * 2:
            _send_escalation(entry, is_re_escalation=True)
            with transaction() as conn:
                conn.execute(
                    """UPDATE ambiguity_queue
                       SET escalated_at=?, re_escalation_count=?
                       WHERE id=?""",
                    (now, re_escalations + 1, entry["id"]),
                )
    else:
        # Low severity: auto-resolve as provisional after timeout_low
        if time_since_escalation >= timeout_low:
            _auto_resolve(entry)


def _send_escalation(entry: dict, is_re_escalation: bool):
    alert = {
        "type": "ambiguity_escalation",
        "re_escalation": is_re_escalation,
        "re_escalation_count": entry.get("re_escalation_count", 0),
        "task_id": entry["task_id"],
        "node_id": entry["node_id"],
        "blocking": bool(entry["blocking"]),
        "severity": entry["severity"],
        "category": entry["category"],
        "description": entry["description"],
        "escalation_target": json.loads(entry["escalation_target"] or "[]"),
        "message_body": entry["body"],
        "ts": int(time.time()),
    }
    Path(ALERT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_LOG_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")

    with transaction() as conn:
        conn.execute(
            """INSERT INTO task_alerts_fired
               (id, task_id, node_id, alert_key, alert_type, severity, category,
                description, escalation_target, fired_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()),
                entry["task_id"],
                entry["node_id"],
                "re_escalation" if is_re_escalation else "escalation",
                "ambiguity_escalation",
                entry["severity"],
                entry["category"],
                entry["description"],
                entry["escalation_target"],
                int(time.time()),
            ),
        )

    prefix = "RE-ESCALATION" if is_re_escalation else "ESCALATION"
    log.warning(
        "%s [%s/%s] task=%s node=%s blocking=%s target=%s | %s",
        prefix, entry["severity"], entry["category"],
        entry["task_id"], entry["node_id"], entry["blocking"],
        alert["escalation_target"], entry["description"],
    )


def _auto_resolve(entry: dict):
    """Auto-resolve low-severity ambiguity as provisional after timeout."""
    log.info(
        "AUTO-RESOLVE (timeout) [low/%s] task=%s node=%s | %s",
        entry["category"], entry["task_id"], entry["node_id"], entry["description"],
    )
    # If blocking, unblock the node by setting it back to provisional
    if entry["blocking"] and entry["node_id"] and entry["task_id"]:
        update_node(
            task_id=entry["task_id"],
            node_id=entry["node_id"],
            new_status="provisional",
            confidence=0.5,
            message_id=entry["message_id"],
            updated_by="ambiguity_auto_resolve",
        )
    with transaction() as conn:
        conn.execute(
            """UPDATE ambiguity_queue
               SET status='expired', resolved_at=?, resolution_note=?
               WHERE id=?""",
            (int(time.time()), "auto-resolved after low-severity timeout", entry["id"]),
        )


def run():
    log.info("Ambiguity worker started — interval=%ds", CRON_INTERVAL_SECONDS)
    while True:
        try:
            check_ambiguity_queue()
        except Exception as e:
            log.exception("Ambiguity worker error: %s", e)
        time.sleep(CRON_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run()
