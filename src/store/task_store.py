"""
Read/write operations for task instances, nodes, and messages.
"""

import json
import time
import uuid

from src.store.db import get_connection, transaction


def get_task(task_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM task_instances WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_node_states(task_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM task_nodes WHERE task_id = ? ORDER BY rowid", (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_messages(task_id: str, limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM task_messages
           WHERE task_id = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (task_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


def update_node(task_id: str, node_id: str, new_status: str,
                confidence: float, message_id: str | None, updated_by: str = "agent"):
    full_node_id = f"{task_id}_{node_id}"
    now = int(time.time())
    with transaction() as conn:
        conn.execute(
            """UPDATE task_nodes
               SET status = ?, confidence = ?, last_message_id = ?,
                   updated_at = ?, updated_by = ?
               WHERE id = ?""",
            (new_status, confidence, message_id, now, updated_by, full_node_id),
        )
        conn.execute(
            "UPDATE task_instances SET last_updated = ? WHERE id = ?",
            (now, task_id),
        )


def append_message(task_id: str, message: dict, routing_confidence: float):
    with transaction() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO task_messages
               (id, task_id, message_id, group_id, sender_jid, body,
                media_type, timestamp, routing_confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()), task_id,
                message.get("message_id"), message.get("group_id"),
                message.get("sender_jid"), message.get("body"),
                message.get("media_type", "text"),
                message.get("timestamp", int(time.time())),
                routing_confidence,
            ),
        )


def get_active_tasks() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM task_instances WHERE stage != 'completed'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
