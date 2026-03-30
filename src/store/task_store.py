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


def update_node_as_update_agent(task_id: str, node_id: str, new_status: str,
                                confidence: float, message_id: str | None):
    """Wrapper — makes update_agent ownership visible at call site."""
    update_node(task_id, node_id, new_status, confidence, message_id,
                updated_by="update_agent")


def update_node_as_linkage_agent(task_id: str, node_id: str, new_status: str,
                                 confidence: float, message_id: str | None):
    """Wrapper — makes linkage_agent ownership visible at call site."""
    update_node(task_id, node_id, new_status, confidence, message_id,
                updated_by="linkage_agent")


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


# ─────────────────────────────────────────────────────────────────────────────
# M:N linkage helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_node_data(task_id: str, node_id: str) -> dict:
    """Return the node_data JSON dict for a specific node, or {} if not set."""
    conn = get_connection()
    row = conn.execute(
        "SELECT node_data FROM task_nodes WHERE id = ?",
        (f"{task_id}_{node_id}",),
    ).fetchone()
    conn.close()
    if not row or not row["node_data"]:
        return {}
    try:
        return json.loads(row["node_data"])
    except (json.JSONDecodeError, TypeError):
        return {}


def apply_node_data_extractions(task_id: str, extractions: list) -> None:
    """
    Merge node_data extractions into task_nodes.node_data (JSON column).
    New keys overwrite existing values; keys absent from the extraction are preserved.
    """
    now = int(time.time())
    for ext in extractions:
        node_id = ext.node_id
        new_data = ext.data
        if not new_data:
            continue
        full_id = f"{task_id}_{node_id}"
        with transaction() as conn:
            row = conn.execute(
                "SELECT node_data FROM task_nodes WHERE id = ?", (full_id,)
            ).fetchone()
            if row is None:
                continue  # node doesn't exist for this task
            existing = {}
            if row["node_data"]:
                try:
                    existing = json.loads(row["node_data"])
                except (json.JSONDecodeError, TypeError):
                    existing = {}
            merged = {**existing, **new_data}
            conn.execute(
                "UPDATE task_nodes SET node_data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), now, full_id),
            )


def get_order_items(task_id: str) -> list[dict]:
    """Return items for any order type — picks the right table from task's order_type."""
    task = get_task(task_id)
    if not task:
        return []
    if task["order_type"] in ("supplier_order",):
        return get_supplier_order_items(task_id)
    return get_client_order_items(task_id)


def get_client_order_items(task_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM client_order_items WHERE task_id = ? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_supplier_order_items(task_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM supplier_order_items WHERE task_id = ? ORDER BY created_at",
        (task_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_fulfillment_links(client_order_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM fulfillment_links WHERE client_order_id = ? ORDER BY created_at",
        (client_order_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_fulfillment_links_by_supplier(supplier_order_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM fulfillment_links WHERE supplier_order_id = ? ORDER BY created_at",
        (supplier_order_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_task(task_id: str) -> None:
    """Mark a task as completed — removes it from all open-order queries."""
    now = int(time.time())
    with transaction() as conn:
        conn.execute(
            "UPDATE task_instances SET stage='completed', last_updated=? WHERE id=?",
            (now, task_id),
        )


_SUPPLIER_TERMINAL = {"fulfilled", "failed", "invalidated"}


def prune_links_for_supplier_order(supplier_order_id: str) -> bool:
    """
    If all links for supplier_order_id are in terminal states
    {fulfilled, failed, invalidated}, delete them and close the supplier task.
    Returns True if pruned.
    """
    links = get_fulfillment_links_by_supplier(supplier_order_id)
    if not links:
        return False
    if all(lnk["status"] in _SUPPLIER_TERMINAL for lnk in links):
        with transaction() as conn:
            conn.execute(
                "DELETE FROM fulfillment_links WHERE supplier_order_id=?",
                (supplier_order_id,),
            )
        update_node(
            task_id=supplier_order_id,
            node_id="task_closed",
            new_status="completed",
            confidence=1.0,
            message_id=None,
            updated_by="linkage_worker",
        )
        close_task(supplier_order_id)
        return True
    return False


def prune_links_for_client_order(client_order_id: str) -> bool:
    """
    If all links for client_order_id are completed, delete them and close the client task.
    Returns True if pruned.
    """
    links = get_fulfillment_links(client_order_id)
    if not links:
        return False
    if all(lnk["status"] == "completed" for lnk in links):
        with transaction() as conn:
            conn.execute(
                "DELETE FROM fulfillment_links WHERE client_order_id=?",
                (client_order_id,),
            )
        update_node(
            task_id=client_order_id,
            node_id="task_closed",
            new_status="completed",
            confidence=1.0,
            message_id=None,
            updated_by="linkage_worker",
        )
        close_task(client_order_id)
        return True
    return False


def upsert_fulfillment_link(link: dict):
    """
    Insert or update a fulfillment link.
    Auto-confirms if match_confidence >= 0.92.
    link keys: id, client_order_id, client_item_description, supplier_order_id,
               supplier_item_description, quantity_allocated, match_confidence,
               match_reasoning, status, resolution_note (optional)
    """
    now = int(time.time())
    status = link.get("status", "candidate")
    if link.get("match_confidence", 0) >= 0.92 and status == "candidate":
        status = "confirmed"

    with transaction() as conn:
        conn.execute(
            """INSERT INTO fulfillment_links
               (id, client_order_id, client_item_description,
                supplier_order_id, supplier_item_description,
                quantity_allocated, match_confidence, match_reasoning,
                status, resolution_note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 quantity_allocated=excluded.quantity_allocated,
                 match_confidence=excluded.match_confidence,
                 match_reasoning=excluded.match_reasoning,
                 status=excluded.status,
                 resolution_note=excluded.resolution_note,
                 updated_at=excluded.updated_at""",
            (
                link["id"],
                link["client_order_id"], link["client_item_description"],
                link["supplier_order_id"], link["supplier_item_description"],
                link["quantity_allocated"], link["match_confidence"],
                link["match_reasoning"], status,
                link.get("resolution_note"), now, now,
            ),
        )


def apply_item_extractions(task_id: str, order_type: str, extractions: list) -> list[str]:
    """
    Apply item add/update/remove operations to the appropriate items table.
    Returns list of descriptions that were changed (for post-confirmation check).

    For update/remove: matches on extraction.existing_description.
    If no existing_description, falls back to description.
    """
    now = int(time.time())
    table = (
        "supplier_order_items"
        if order_type == "supplier_order"
        else "client_order_items"
    )
    changed: list[str] = []

    with transaction() as conn:
        for ext in extractions:
            op = ext.operation
            if op == "add":
                conn.execute(
                    f"INSERT INTO {table} (id, task_id, description, unit, quantity, specs, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), task_id,
                     ext.description, ext.unit, ext.quantity, ext.specs, now),
                )
                changed.append(ext.description)
            elif op == "update":
                match = ext.existing_description or ext.description
                conn.execute(
                    f"UPDATE {table} SET description=?, unit=?, quantity=?, specs=?"
                    " WHERE task_id=? AND description=?",
                    (ext.description, ext.unit, ext.quantity, ext.specs, task_id, match),
                )
                changed.append(ext.description)
            elif op == "remove":
                match = ext.existing_description or ext.description
                conn.execute(
                    f"DELETE FROM {table} WHERE task_id=? AND description=?",
                    (task_id, match),
                )
                changed.append(match)

    return changed


def reconcile_order_ready(client_task_id: str) -> str | None:
    """
    Examine confirmed fulfillment_links for client_task_id against
    client_order_items to determine order_ready status.

    Returns the new status set ('completed', 'partial'), or None if no change.
    Idempotent — safe to call multiple times.
    """
    items = get_client_order_items(client_task_id)
    if not items:
        return None  # no items registered yet

    links = [
        lnk for lnk in get_fulfillment_links(client_task_id)
        if lnk["status"] in ("confirmed", "auto_allocated")
    ]

    # Build per-description allocated quantity map
    allocated: dict[str, float] = {}
    for lnk in links:
        key = lnk["client_item_description"]
        allocated[key] = allocated.get(key, 0.0) + lnk["quantity_allocated"]

    fully_met = 0
    for item in items:
        required = item["quantity"] or 0.0
        got = allocated.get(item["description"], 0.0)
        if got >= required:
            fully_met += 1

    if fully_met == len(items):
        new_status = "completed"
    elif fully_met > 0:
        new_status = "partial"
    else:
        return None  # no confirmed allocations yet

    update_node(
        task_id=client_task_id,
        node_id="order_ready",
        new_status=new_status,
        confidence=0.95,
        message_id=None,
        updated_by="linkage_worker",
    )
    return new_status


def check_stock_path_order_ready(task_id: str) -> str | None:
    """
    Check if the stock path (filled_from_stock) makes the order ready.
    Called after update_agent sets filled_from_stock to active/completed.

    If filled_from_stock is active or completed and the supplier subgraph is
    skipped (no supplier involvement), sets order_ready to the same status.

    Returns the new status set, or None if no change needed.
    Idempotent — safe to call multiple times.
    """
    nodes = {
        n["id"][len(task_id) + 1:]: n["status"]
        for n in get_node_states(task_id)
    }

    stock_status = nodes.get("filled_from_stock")
    if stock_status not in ("active", "completed"):
        return None

    # Only trigger if supplier path is not active
    supplier_active = nodes.get("supplier_indent", "skipped") not in ("skipped", "pending")
    if supplier_active:
        return None  # supplier path is in play — let linkage agent handle order_ready

    current_ready = nodes.get("order_ready")
    if current_ready in ("completed", "active"):
        return None  # already set

    new_status = "completed" if stock_status == "completed" else "active"
    update_node(
        task_id=task_id,
        node_id="order_ready",
        new_status=new_status,
        confidence=0.95,
        message_id=None,
        updated_by="linkage_worker",
    )
    return new_status


def get_open_orders_summary() -> dict:
    """
    Return a compact summary of all open client and supplier orders for
    the linkage agent context window.
    """
    conn = get_connection()
    client_tasks = conn.execute(
        "SELECT id, order_type FROM task_instances WHERE order_type='client_order' AND stage != 'completed'"
    ).fetchall()
    supplier_tasks = conn.execute(
        "SELECT id, order_type FROM task_instances WHERE order_type='supplier_order' AND stage != 'completed'"
    ).fetchall()
    conn.close()

    result: dict = {"client_orders": [], "supplier_orders": []}
    for row in client_tasks:
        tid = row["id"]
        result["client_orders"].append({
            "task_id": tid,
            "items": get_client_order_items(tid),
        })
    for row in supplier_tasks:
        tid = row["id"]
        result["supplier_orders"].append({
            "task_id": tid,
            "items": get_supplier_order_items(tid),
        })
    return result
