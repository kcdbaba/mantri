"""
Task templates — node definitions for each order type.
Sprint 3: defined inline. Production: loaded from DB.
"""

STANDARD_PROCUREMENT_TEMPLATE = {
    "order_type": "standard_procurement",
    "nodes": [
        # --- Reactive nodes (triggered by messages) ---
        {"id": "enquiry_received",      "type": "real_world_milestone",
         "name": "Enquiry received from client",            "stage": "enquiry"},
        {"id": "quote_requested",       "type": "agent_action",
         "name": "Quote requested from supplier",           "stage": "quote"},
        {"id": "quote_received",        "type": "real_world_milestone",
         "name": "Quote received from supplier",            "stage": "quote"},
        {"id": "po_raised",             "type": "agent_action",
         "name": "Purchase order raised",                   "stage": "order_placed"},
        {"id": "dispatch_confirmed",    "type": "real_world_milestone",
         "name": "Dispatch confirmed by supplier",          "stage": "dispatch"},
        {"id": "delivery_confirmed",    "type": "real_world_milestone",
         "name": "Delivery confirmed at client site",       "stage": "delivered"},
        {"id": "invoice_received",      "type": "real_world_milestone",
         "name": "Invoice received from supplier",          "stage": "payment"},
        {"id": "payment_made",          "type": "real_world_milestone",
         "name": "Payment made to supplier",                "stage": "payment"},

        # --- Cadence nodes (activate on stage/time trigger, NOT from messages) ---
        {"id": "quote_followup_48h",    "type": "cadence",
         "name": "Follow up on quote if no response in 48h",
         "activates_when": "stage=quote AND hours_since(quote_requested.completed_at) >= 48"},
        {"id": "predispatch_checklist", "type": "cadence",
         "name": "Pre-dispatch checklist (quality, quantity, packaging)",
         "activates_when": "po_raised.status=completed"},
        {"id": "delivery_photo_check",  "type": "cadence",
         "name": "Confirm delivery photo / proof of receipt",
         "activates_when": "dispatch_confirmed.status=completed"},
        {"id": "payment_followup_30d",  "type": "cadence",
         "name": "Payment follow-up if outstanding after 30 days",
         "activates_when": "delivery_confirmed.status=completed AND days_since(delivery_confirmed.completed_at) >= 30"},
    ],
}

# Map order_type → template
TEMPLATES = {
    "standard_procurement": STANDARD_PROCUREMENT_TEMPLATE,
}


def get_template(order_type: str) -> dict:
    if order_type not in TEMPLATES:
        raise ValueError(f"Unknown order type: {order_type}")
    return TEMPLATES[order_type]


def get_cadence_nodes(order_type: str) -> list[dict]:
    return [n for n in get_template(order_type)["nodes"] if n["type"] == "cadence"]
