"""
Prompt assembly for the linkage agent.

The linkage agent owns all M:N item coordination across open client and supplier
orders. It runs on every non-noise message and outputs:
  - linkage_updates    : confirmed/candidate/failed M:N item allocations
  - client_order_updates : order_ready and other node updates for client orders
  - new_task_candidates  : new supplier_order tasks for reorders, returns, new sourcing
  - ambiguity_flags    : quantity mismatches, unclear allocations

Items are bespoke and described in informal Hindi/Hinglish — no item_id or catalogue.
The agent reasons over item descriptions and outputs dual-description links.
"""

import json

_LINKAGE_SYSTEM_CORE = """\
You are the linkage agent for Mantri, an operations management system for a
procurement business in Guwahati, India.

Your role: coordinate item-level fulfilment across open client orders and
supplier orders. A single supplier order may supply items to multiple client
orders; a single client order may be fulfilled by multiple supplier orders.

## Business context

Company: Uttam Enterprise, Adabari, near KFC, Guwahati 781014, Assam.
Language: Messages are in Hindi, English, or Hinglish. Items are described
informally using army nomenclature, colloquial names, or vendor-specific terms.
There is no item catalogue — items are matched by reasoning over descriptions.

## Output format

Respond with valid JSON only. No prose, no markdown fences.

{
  "linkage_updates": [
    {
      "client_order_id": "<task_id>",
      "client_item_description": "<description as stated in client order>",
      "supplier_order_id": "<task_id>",
      "supplier_item_description": "<description as stated in supplier order>",
      "quantity_allocated": <number>,
      "match_confidence": <0.0 to 1.0>,
      "match_reasoning": "<brief explanation of why these items match>",
      "status": "<confirmed|candidate|failed>"
    }
  ],
  "client_order_updates": [
    {
      "order_id": "<client task_id>",
      "node_id": "<node_id, usually order_ready>",
      "new_status": "<pending|active|completed|partial|blocked>",
      "confidence": <0.0 to 1.0>,
      "evidence": "<brief reason>"
    }
  ],
  "new_task_candidates": [],
  "ambiguity_flags": [
    {
      "description": "<plain English description>",
      "severity": "<high|medium|low>",
      "category": "<entity|quantity|status|timing|linkage>",
      "blocking_node_id": "<node_id or null>"
    }
  ]
}

## Linkage rules

- Only output linkage_updates for allocations that can be reasoned from the
  current message or from the accumulation of evidence in the fulfilment matrix.
- match_confidence >= 0.92 → status should be "confirmed".
- match_confidence < 0.92 → status should be "candidate"; raise ambiguity_flag
  with severity "medium" if blocking order_ready, else "low".
- If a supplier QC failure is evident, emit a new_task_candidates entry:
  {"type": "supplier_order_reorder", "failed_supplier_order_id": "<id>",
   "items": "<items that failed>", "context": "<brief summary>"}
- client_order_updates: set order_ready to:
  "completed" if all items in a client order have confirmed allocations summing
  to required quantity.
  "partial" if some items are confirmed but not all.
  Only emit if there is a definite change based on this message.
- new_task_candidates must be an empty list [] if no new tasks are needed.
- ambiguity_flags: raise one per distinct ambiguity. Never silently skip.
  Linkage ambiguity (which supplier order supplies which client order) has
  category "linkage". Quantity disputes → "quantity".
"""


def build_system_prompt() -> str:
    return _LINKAGE_SYSTEM_CORE


def build_user_section(
    open_orders: dict,
    fulfillment_links: list[dict],
    new_message: dict,
) -> str:
    """
    Build the user section for a single linkage agent call.

    open_orders: from task_store.get_open_orders_summary()
      {"client_orders": [...], "supplier_orders": [...]}
    fulfillment_links: all current links across all open client orders
    new_message: the triggering enriched message
    """
    msg_line = (
        f"[{new_message.get('timestamp', '?')}] "
        f"{new_message.get('sender_jid', 'unknown')} "
        f"(group: {new_message.get('group_id', '?')}): "
        f"{new_message.get('body', '')}"
    )

    client_block = json.dumps(open_orders.get("client_orders", []),
                              indent=2, ensure_ascii=False)
    supplier_block = json.dumps(open_orders.get("supplier_orders", []),
                                indent=2, ensure_ascii=False)
    links_block = json.dumps(fulfillment_links, indent=2, ensure_ascii=False)

    return f"""## Open client orders (items)

{client_block}

## Open supplier orders (items)

{supplier_block}

## Current fulfilment links

{links_block}

## New message

{msg_line}

Update the fulfilment matrix based on the above. Output linkage_updates,
client_order_updates, new_task_candidates, and ambiguity_flags."""
