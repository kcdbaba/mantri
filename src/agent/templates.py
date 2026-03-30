"""
Task templates — node definitions for each order type.
Sprint 3: defined inline. Production: loaded from DB.

Node types:
  real_world_milestone  — physical/financial event observed from messages
  agent_action          — action taken by staff or agent
  auto_trigger          — activates automatically on predecessor node completion
  time_trigger          — activates on elapsed time relative to a reference point

Node fields:
  id                    — unique within template
  type                  — see above
  name                  — human-readable label
  stage                 — logical grouping for display
  owner                 — 'update_agent' | 'linkage_agent' — which agent writes this node
  optional              — if True, defaults to 'skipped' unless subgraph is activated
  requires_all          — list of node IDs that must be 'completed' before this node;
                          violation → failure alert
  warns_if_incomplete   — list of node IDs whose incompleteness triggers warning only
  activates_when        — condition string (auto_trigger / time_trigger only)
  alert_days_before     — for repeating time_trigger: days before reference date to fire
  description           — used in prompts and dashboard tooltips
"""

STANDARD_PROCUREMENT_TEMPLATE = {
    "order_type": "standard_procurement",
    "nodes": [

        # ── Client subgraph ────────────────────────────────────────────────
        {
            "id": "client_enquiry",
            "type": "real_world_milestone",
            "name": "Client enquiry",
            "stage": "enquiry",
            "owner": "update_agent",
            "description": "Initial order discussion; accumulates items, quantities, delivery requirements until quotation is generated.",
        },
        {
            "id": "client_quotation",
            "type": "agent_action",
            "name": "Client quotation",
            "stage": "quotation",
            "owner": "update_agent",
            "description": "Quotation sent to client; may iterate multiple times. Status: pending → in_progress (first quote sent). Completes only when order_confirmation completes.",
        },
        {
            "id": "order_confirmation",
            "type": "auto_trigger",
            "name": "Order confirmation",
            "stage": "confirmation",
            "owner": "update_agent",
            "activates_when": "client_quotation.status=in_progress",
            "description": "Activated when quotation is sent. Completes on client confirmation message or staff/Ashish update stating order is confirmed or to be delivered.",
        },

        # ── Supplier subgraph (all optional) ──────────────────────────────
        {
            "id": "supplier_indent",
            "type": "real_world_milestone",
            "name": "Supplier indent",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "description": "Initial supplier discussion or enquiry; captures supplier name, items discussed, lead times mentioned. Activates entire supplier subgraph.",
        },
        {
            "id": "supplier_invoice",
            "type": "real_world_milestone",
            "name": "Supplier invoice",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "description": "Invoice received from supplier. May arrive before collection (advance billing is common).",
        },
        {
            "id": "supplier_collection",
            "type": "real_world_milestone",
            "name": "Supplier collection",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "description": "Goods received from supplier — via supplier delivery, self-collection, or transporter. Transporter handoff detail captured in node_data.",
        },
        {
            "id": "supplier_po",
            "type": "agent_action",
            "name": "Supplier PO",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "description": "Purchase order raised. May be done after collection in informal procurement.",
        },
        {
            "id": "supplier_payment",
            "type": "real_world_milestone",
            "name": "Supplier payment",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "requires_all": ["supplier_invoice"],
            "description": "Payment made to supplier. Advance payment before collection is normal.",
        },
        {
            "id": "supplier_QC",
            "type": "agent_action",
            "name": "Supplier QC",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "requires_all": ["supplier_collection"],
            "description": "Quality check of received items. 'failed' status blocks order_ready and triggers client_notification task. Resolution paths: replace (creates supplier_QC_replace subgraph), proceed with available (order_ready=completed), or split delivery (order_ready=partial).",
        },

        # ── Stock subgraph (optional) ──────────────────────────────────────
        {
            "id": "filled_from_stock",
            "type": "real_world_milestone",
            "name": "Filled from stock",
            "stage": "stock",
            "owner": "update_agent",
            "optional": True,
            "description": "Order fulfilled from existing stock without a supplier. Activates on message in internal or client group. Emits new_task_candidates update to current pending stock_taking task.",
        },

        # ── Merge node ─────────────────────────────────────────────────────
        {
            "id": "order_ready",
            "type": "auto_trigger",
            "name": "Order ready",
            "stage": "fulfillment",
            "owner": "linkage_agent",
            "activates_when": "supplier_QC.status=completed OR filled_from_stock.status=completed",
            "description": "Merge node: order is ready for dispatch from either the supplier path or the stock path. Can be 'partial' if supplier_QC passed for some items only.",
        },

        # ── Delivery subgraph ──────────────────────────────────────────────
        {
            "id": "dispatched",
            "type": "real_world_milestone",
            "name": "Dispatched",
            "stage": "delivery",
            "owner": "update_agent",
            "requires_all": ["order_confirmation", "order_ready"],
            "warns_if_incomplete": ["predispatch_checklist"],
            "description": "Goods dispatched to client. If order_ready=partial, dispatch covers available items only.",
        },
        {
            "id": "delivery_confirmed",
            "type": "real_world_milestone",
            "name": "Delivery confirmed",
            "stage": "delivery",
            "owner": "update_agent",
            "requires_all": ["dispatched"],
            "warns_if_incomplete": ["delivery_photo_check"],
            "description": "Client confirms receipt of goods.",
        },

        # ── Auto-trigger nodes ─────────────────────────────────────────────
        {
            "id": "quote_followup_48h",
            "type": "time_trigger",
            "name": "Quote follow-up (48h)",
            "stage": "quotation",
            "owner": "update_agent",
            "activates_when": "client_quotation.status=in_progress AND hours_since(client_quotation.updated_at) >= 48",
            "description": "Follow up with client if no order confirmation 48 hours after quotation sent.",
        },
        {
            "id": "supplier_predelivery_enquiry",
            "type": "time_trigger",
            "name": "Supplier pre-delivery enquiry",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "activates_when": "supplier_indent.status=completed AND task.metadata.expected_delivery_date IS SET",
            "alert_days_before": [7, 3, 1],
            "description": "Repeated enquiry to supplier before expected delivery date. Fires at T-7, T-3, T-1 days. Stops when supplier_collection=completed. expected_delivery_date extracted from supplier messages into task metadata.",
        },
        {
            "id": "predispatch_checklist",
            "type": "auto_trigger",
            "name": "Pre-dispatch checklist",
            "stage": "fulfillment",
            "owner": "update_agent",
            "activates_when": "order_ready.status=completed OR order_ready.status=partial",
            "description": "Quality, quantity, and packaging check before dispatch to client.",
        },
        {
            "id": "delivery_photo_check",
            "type": "auto_trigger",
            "name": "Delivery photo check",
            "stage": "delivery",
            "owner": "update_agent",
            "activates_when": "dispatched.status=completed",
            "description": "Confirm delivery photo or proof of receipt from client site.",
        },
        {
            "id": "payment_followup_30d",
            "type": "time_trigger",
            "name": "Payment follow-up (30d)",
            "stage": "payment",
            "owner": "update_agent",
            "activates_when": "delivery_confirmed.status=completed AND days_since(delivery_confirmed.completed_at) >= 30",
            "description": "Payment follow-up alert if outstanding 30 days after delivery confirmed. Note: Army clients have 60-90 day normal cycles — this threshold should be overridden for Army orders.",
        },
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# M:N split templates (Sprint 3+)
#
# client_order   — client subgraph + stock path + delivery nodes.
#                  order_ready is set by the linkage_worker for supplier-fulfilled
#                  orders; the update agent handles the filled_from_stock path.
# supplier_order — supplier subgraph only. No client nodes.
# linkage_task   — singleton, long-running. Owns all M:N item coordination.
#                  Has no procurement nodes; managed entirely by linkage_worker.
# ─────────────────────────────────────────────────────────────────────────────

CLIENT_ORDER_TEMPLATE = {
    "order_type": "client_order",
    "nodes": [

        # ── Client subgraph ────────────────────────────────────────────────
        {
            "id": "client_enquiry",
            "type": "real_world_milestone",
            "name": "Client enquiry",
            "stage": "enquiry",
            "owner": "update_agent",
            "description": "Initial order discussion; accumulates items, quantities, delivery requirements until quotation is generated.",
        },
        {
            "id": "client_quotation",
            "type": "agent_action",
            "name": "Client quotation",
            "stage": "quotation",
            "owner": "update_agent",
            "description": "Quotation sent to client; may iterate multiple times. Status: pending → in_progress (first quote sent).",
        },
        {
            "id": "order_confirmation",
            "type": "auto_trigger",
            "name": "Order confirmation",
            "stage": "confirmation",
            "owner": "update_agent",
            "activates_when": "client_quotation.status=in_progress",
            "description": "Activated when quotation is sent. Completes on client confirmation.",
        },

        # ── Stock subgraph (optional) ──────────────────────────────────────
        {
            "id": "filled_from_stock",
            "type": "real_world_milestone",
            "name": "Filled from stock",
            "stage": "stock",
            "owner": "update_agent",
            "optional": True,
            "description": "Order fulfilled from existing stock without a supplier. Activates order_ready directly.",
        },

        # ── Merge node ─────────────────────────────────────────────────────
        {
            "id": "order_ready",
            "type": "auto_trigger",
            "name": "Order ready",
            "stage": "fulfillment",
            "owner": "linkage_agent",
            "activates_when": "filled_from_stock.status=completed",
            "description": (
                "Order is ready for dispatch. "
                "For stock-fulfilled orders: activates when filled_from_stock completes. "
                "For supplier-fulfilled orders: set externally by the linkage_worker when "
                "all item allocations are confirmed across supplier_order tasks."
            ),
        },

        # ── Delivery subgraph ──────────────────────────────────────────────
        {
            "id": "dispatched",
            "type": "real_world_milestone",
            "name": "Dispatched",
            "stage": "delivery",
            "owner": "update_agent",
            "requires_all": ["order_confirmation", "order_ready"],
            "warns_if_incomplete": ["predispatch_checklist"],
            "description": "Goods dispatched to client.",
        },
        {
            "id": "delivery_confirmed",
            "type": "real_world_milestone",
            "name": "Delivery confirmed",
            "stage": "delivery",
            "owner": "update_agent",
            "requires_all": ["dispatched"],
            "warns_if_incomplete": ["delivery_photo_check"],
            "description": "Client confirms receipt of goods.",
        },

        # ── Auto-trigger nodes ─────────────────────────────────────────────
        {
            "id": "predispatch_checklist",
            "type": "auto_trigger",
            "name": "Pre-dispatch checklist",
            "stage": "fulfillment",
            "owner": "update_agent",
            "activates_when": "order_ready.status=completed OR order_ready.status=partial",
            "description": "Quality, quantity, and packaging check before dispatch.",
        },
        {
            "id": "delivery_photo_check",
            "type": "auto_trigger",
            "name": "Delivery photo check",
            "stage": "delivery",
            "owner": "update_agent",
            "activates_when": "dispatched.status=completed",
            "description": "Confirm delivery photo or proof of receipt from client site.",
        },

        # ── Time-trigger nodes ─────────────────────────────────────────────
        {
            "id": "quote_followup_48h",
            "type": "time_trigger",
            "name": "Quote follow-up (48h)",
            "stage": "quotation",
            "owner": "update_agent",
            "activates_when": "client_quotation.status=in_progress AND hours_since(client_quotation.updated_at) >= 48",
            "description": "Follow up with client if no order confirmation 48 hours after quotation sent.",
        },
        {
            "id": "payment_followup_30d",
            "type": "time_trigger",
            "name": "Payment follow-up (30d)",
            "stage": "payment",
            "owner": "update_agent",
            "activates_when": "delivery_confirmed.status=completed AND days_since(delivery_confirmed.completed_at) >= 30",
            "description": "Payment follow-up alert if outstanding 30 days after delivery confirmed.",
        },

        # ── Terminal node ──────────────────────────────────────────────────
        {
            "id": "task_closed",
            "type": "real_world_milestone",
            "name": "Task closed",
            "stage": "closed",
            "owner": "linkage_agent",
            "description": (
                "Terminal node. Set to completed by linkage_worker when all fulfilment "
                "links for this client order are completed (delivery confirmed), or "
                "manually when the order is cancelled or truncated."
            ),
        },
    ],
}


SUPPLIER_ORDER_TEMPLATE = {
    "order_type": "supplier_order",
    "nodes": [

        # ── Supplier subgraph ──────────────────────────────────────────────
        {
            "id": "supplier_indent",
            "type": "real_world_milestone",
            "name": "Supplier indent",
            "stage": "supplier",
            "owner": "update_agent",
            "description": "Initial supplier discussion; captures supplier name, items, lead times. Entry point for this task.",
        },
        {
            "id": "supplier_invoice",
            "type": "real_world_milestone",
            "name": "Supplier invoice",
            "stage": "supplier",
            "owner": "update_agent",
            "description": "Invoice received from supplier. May arrive before collection (advance billing is common).",
        },
        {
            "id": "supplier_collection",
            "type": "real_world_milestone",
            "name": "Supplier collection",
            "stage": "supplier",
            "owner": "update_agent",
            "description": "Goods received from supplier — via delivery, self-collection, or transporter.",
        },
        {
            "id": "supplier_QC",
            "type": "agent_action",
            "name": "Supplier QC",
            "stage": "supplier",
            "owner": "update_agent",
            "requires_all": ["supplier_collection"],
            "description": (
                "Quality check of received items. "
                "'failed' → linkage_worker creates reorder supplier_order task. "
                "'partial' → linkage_worker sets client order_ready=partial. "
                "'completed' → linkage_worker reconciles order_ready on linked client orders."
            ),
        },
        {
            "id": "supplier_po",
            "type": "agent_action",
            "name": "Supplier PO",
            "stage": "supplier",
            "owner": "update_agent",
            "description": "Purchase order raised. May be done after collection in informal procurement.",
        },
        {
            "id": "supplier_payment",
            "type": "real_world_milestone",
            "name": "Supplier payment",
            "stage": "supplier",
            "owner": "update_agent",
            "requires_all": ["supplier_invoice"],
            "description": "Payment made to supplier.",
        },

        # ── Time-trigger ───────────────────────────────────────────────────
        {
            "id": "supplier_predelivery_enquiry",
            "type": "time_trigger",
            "name": "Supplier pre-delivery enquiry",
            "stage": "supplier",
            "owner": "update_agent",
            "optional": True,
            "activates_when": "supplier_indent.status=completed AND task.metadata.expected_delivery_date IS SET",
            "alert_days_before": [7, 3, 1],
            "description": "Repeated enquiry to supplier before expected delivery. Fires at T-7, T-3, T-1 days.",
        },

        # ── Terminal node ──────────────────────────────────────────────────
        {
            "id": "task_closed",
            "type": "real_world_milestone",
            "name": "Task closed",
            "stage": "closed",
            "owner": "linkage_agent",
            "description": (
                "Terminal node. Set to completed by linkage_worker when all fulfilment "
                "links for this supplier order reach terminal state (fulfilled/failed/invalidated), "
                "or manually when the order is cancelled."
            ),
        },
    ],
}


LINKAGE_TASK_TEMPLATE = {
    "order_type": "linkage_task",
    "nodes": [
        # The linkage_task has a single status-tracking node.
        # All actual work (M:N coordination, item matching, order_ready reconciliation)
        # is performed by the linkage_worker process — not via the node graph.
        {
            "id": "linkage_active",
            "type": "agent_action",
            "name": "Linkage active",
            "stage": "linkage",
            "owner": "update_agent",
            "description": (
                "Singleton node tracking linkage task lifecycle. "
                "Set to active on creation, completed only when all open orders are closed."
            ),
        },
    ],
}


# Map order_type → template
TEMPLATES = {
    "standard_procurement": STANDARD_PROCUREMENT_TEMPLATE,
    "client_order":         CLIENT_ORDER_TEMPLATE,
    "supplier_order":       SUPPLIER_ORDER_TEMPLATE,
    "linkage_task":         LINKAGE_TASK_TEMPLATE,
}


def get_template(order_type: str) -> dict:
    if order_type not in TEMPLATES:
        raise ValueError(f"Unknown order type: {order_type}")
    return TEMPLATES[order_type]


def get_trigger_nodes(order_type: str) -> list[dict]:
    """Return all auto_trigger and time_trigger nodes for a given order type."""
    return [n for n in get_template(order_type)["nodes"]
            if n["type"] in ("auto_trigger", "time_trigger")]


def get_time_trigger_nodes(order_type: str) -> list[dict]:
    return [n for n in get_template(order_type)["nodes"] if n["type"] == "time_trigger"]


def get_auto_trigger_nodes(order_type: str) -> list[dict]:
    return [n for n in get_template(order_type)["nodes"] if n["type"] == "auto_trigger"]
