"""
Prompt assembly for the update agent.

System prompt = cached prefix + task template + business context + output spec
User section  = current node states + last N messages + new message
"""

import json
from src.agent.templates import get_template

# Business context block (static, read from data/business_context.md at startup
# for Sprint 3 just inline the key facts)
_BUSINESS_CONTEXT = """
## Business context

**Company**: Uttam Enterprise, Adabari, near KFC, Guwahati 781014, Assam.
Army Stores and Sun Traders are billing entities only — all purchases are in the name of Uttam Enterprise.

**Staff**: Samita (supplier liaison), Mousumi Banik, Abhisha, Pramod (senior, accounts/CDA),
Rahul Das, Samsuel Haque (Haque Babu). Dev Babu (retired). 2 new hires expected April 2026.

**Language**: Messages are in Hindi, English, or Hinglish. Interpret accordingly.
Entity names may appear in informal forms (e.g. "Kapoor ji", "army wale", "eastern command").
"""

_OUTPUT_SPEC = """
## Output format

Respond with valid JSON only. No prose, no markdown fences.

{
  "node_updates": [
    {
      "node_id": "<node id from template>",
      "new_status": "<status — see rules below>",
      "confidence": <0.0 to 1.0>,
      "evidence": "<brief message excerpt or reason>"
    }
  ],
  "new_task_candidates": [],
  "ambiguity_flags": [
    {
      "description": "<plain English description of the ambiguity>",
      "severity": "<high|medium|low>",
      "category": "<entity|quantity|status|timing|linkage>",
      "blocking_node_id": "<node_id to block, or null if non-blocking>"
    }
  ],
  "item_extractions": [
    {
      "operation": "<add|update|remove>",
      "description": "<item description as stated in this message>",
      "unit": "<unit, e.g. kg/pcs/bags/litres, or null if not mentioned>",
      "quantity": <number or null>,
      "specs": "<additional spec notes, or null>",
      "existing_description": "<copy exactly from current order items for update/remove; null for add>"
    }
  ],
  "node_data_extractions": [
    {
      "node_id": "<node_id from template>",
      "data": { "<field>": "<value>", "...": "..." }
    }
  ]
}

## Status values
- pending       : not yet started
- active        : triggered/unlocked, work should begin
- in_progress   : actively ongoing with iterating updates (e.g. quotation being negotiated)
- completed     : done and confirmed
- provisional   : implied but not confirmed
- blocked       : cannot proceed; requires resolution
- failed        : outcome was negative (e.g. QC failed); blocks downstream nodes
- partial       : some items ready, some not (e.g. partial QC pass)
- skipped       : node not applicable for this order (e.g. supplier nodes when filling from stock)

## Rules
- Only include nodes whose status should change based on the messages.
- Use "provisional" if a status change is implied but not confirmed.
- confidence < 0.75 → use "provisional" status.
- Do not invent node_ids not in the template.
- ambiguity_flags: raise one per distinct ambiguity detected. Never silently skip ambiguity.
  severity: high = could cause wrong delivery or payment; medium = affects task progression;
            low = minor uncertainty about timing or detail.
  category: entity (which client/supplier?), quantity (how many?), status (did this happen?),
            timing (when?), linkage (which order does this belong to?).
  blocking_node_id: set to the gate node that should be blocked until resolved
    (order_confirmation, order_ready, dispatched, supplier_QC). Null for non-gate ambiguity.
- new_task_candidates must be an empty list [] if no new tasks detected.

## Node data extraction rules

For each node that has new factual data in the current message, include a
node_data_extractions entry. Only extract facts explicitly stated in the message.
Omit any field not mentioned — existing data is preserved by merge.
All dates as ISO strings (YYYY-MM-DD). All amounts as numbers (no currency symbol).

node_id → extractable fields:

client_enquiry:
  delivery_location, required_by_date, officer_name, unit_name

client_quotation:
  quoted_price_total, quoted_price_per_unit, quotation_ref, valid_until, notes

order_confirmation:
  confirmed_by, confirmed_at, confirmed_price, delivery_deadline

supplier_indent:
  supplier_name, supplier_contact, expected_delivery_date, lead_time_days
  ⚠ expected_delivery_date is critical — used by pre-delivery alert triggers.
    Extract whenever a supplier delivery date is mentioned.

supplier_invoice:
  invoice_number, invoice_amount, invoice_date, payment_terms

supplier_collection:
  collection_date, transporter_name, vehicle_number, received_by

supplier_QC:
  passed_items (array of strings), failed_items (array of strings),
  failure_reason, qc_date

supplier_po:
  po_number, po_amount, po_date

supplier_payment:
  payment_amount, payment_mode, payment_date, utr_number

dispatched:
  dispatch_date, vehicle_number, driver_name, driver_contact, estimated_delivery_date

delivery_confirmed:
  confirmed_by, confirmed_at, delivery_photo_ref

filled_from_stock:
  stock_location, issued_by, issue_date

predispatch_checklist:
  checklist_notes, checked_by

delivery_photo_check:
  photo_received (true/false), photo_ref, checked_by

node_data_extractions must be an empty list [] if no factual data is present in
this message for any node.

## Item extraction rules

Extract items whenever the message adds, changes, or removes items from this order.

- **add**: a new item is mentioned as part of this order (not previously in the current items list).
- **update**: quantity, unit, or specs of an existing item are revised.
  Set existing_description to the exact description string from the current order items list.
- **remove**: an item is explicitly cancelled or removed from the order.
  Set existing_description to the exact description string from the current order items list.
- item_extractions must be an empty list [] if no item changes are in this message.
- Copy existing_description verbatim from the current order items shown in the user section —
  do not paraphrase. This is used to match the record in the database.
- If an item is mentioned but it's ambiguous whether it's new or an update, prefer "add"
  and raise an ambiguity_flag with category="quantity".
- Items are described in Hindi/Hinglish/English — preserve the original wording in description.

## Post-confirmation item change rule

If the current node states show order_confirmation=completed (client orders) or
supplier_collection=completed (supplier orders), and this message contains item_extractions,
raise an ambiguity_flag with severity="high", category="quantity", and the appropriate
blocking_node_id (dispatched for client, supplier_QC for supplier).
This signals that items are being changed after the order has been locked.

## Node activation rules by type

**auto_trigger nodes** (order_confirmation, order_ready, predispatch_checklist, delivery_photo_check):
  Activate automatically when their predecessor condition is met in the current node states.
  Do NOT wait for a message to mention them.

**time_trigger nodes** (quote_followup_48h, supplier_predelivery_enquiry, payment_followup_30d):
  DO NOT activate these — they are managed by the cron worker based on elapsed time.
  Only mark them "completed" if a message explicitly confirms the follow-up was done.

**Optional nodes** (entire supplier subgraph, filled_from_stock, supplier_predelivery_enquiry):
  Default status is "skipped". Activate the full supplier subgraph (set supplier_indent to "pending")
  the moment any message discusses a supplier in the context of this order.
  Set filled_from_stock to "active" if a message indicates stock is being used to fulfil the order.

## Cross-task candidates
If a QC failure (supplier_QC=failed), dispatch, or client-facing update is detected,
emit a new_task_candidates entry:
  {"type": "client_notification", "trigger_node": "<node_id>", "context": "<brief summary>"}
If filled_from_stock is activated, emit:
  {"type": "stock_taking_update", "items": "<items mentioned>", "source_task_id": "<task_id>"}
"""


def build_system_prompt(task_id: str, task: dict | None = None) -> str:
    """Build the (mostly) static system prompt for a given task.
    Pass task dict directly to avoid a DB lookup (e.g. in eval framework)."""
    if task is None:
        from src.store.task_store import get_task
        task = get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    template = get_template(task["order_type"])
    nodes_block = json.dumps(template["nodes"], indent=2, ensure_ascii=False)

    return f"""You are an update agent for Mantri, an operations management system for
a procurement business in Guwahati, India. Your job is to update the status of task nodes
based on incoming WhatsApp messages.
{_BUSINESS_CONTEXT}
## Task template: {task["order_type"]}

{nodes_block}
{_OUTPUT_SPEC}"""


def build_user_section(node_states: list[dict], recent_messages: list[dict],
                       new_message: dict, current_items: list[dict] | None = None) -> str:
    """Build the variable user section for a single update call."""
    nodes_summary = [
        {"node_id": n["id"][len(n["task_id"]) + 1:] if n.get("task_id") else n["id"],
         "name": n["name"], "status": n["status"]}
        for n in node_states
    ]

    messages_summary = [
        f"[{m['timestamp']}] {m.get('sender_jid', 'unknown')} "
        f"(group: {m.get('group_id', '?')}): {m.get('body', '')}"
        for m in recent_messages
    ]

    has_image = bool(new_message.get("image_path") or new_message.get("image_bytes"))
    image_note = " [IMAGE ATTACHED — extract data from image above]" if has_image else ""
    new_msg_line = (
        f"[{new_message.get('timestamp', '?')}] "
        f"{new_message.get('sender_jid', 'unknown')} "
        f"(group: {new_message.get('group_id', '?')}): "
        f"{new_message.get('body', '') or '(no text)'}{image_note}"
    )

    items_block = ""
    if current_items:
        compact = [
            {"description": it["description"], "unit": it.get("unit"),
             "quantity": it.get("quantity"), "specs": it.get("specs")}
            for it in current_items
        ]
        items_block = f"\n## Current order items\n\n{json.dumps(compact, indent=2, ensure_ascii=False)}\n"

    return f"""## Current node states

{json.dumps(nodes_summary, indent=2, ensure_ascii=False)}
{items_block}
## Recent messages (last {len(recent_messages)})

{chr(10).join(messages_summary) if messages_summary else "(none)"}

## New message

{new_msg_line}

Update the task nodes based on the above. Remember to activate auto_trigger nodes
when their predecessor conditions are met, even if no message explicitly mentions them."""
