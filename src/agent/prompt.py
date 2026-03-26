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
Rahul Das, Samsuel Haque (Haque Babu). Dev Babu retired. 2 new hires expected April 2026.

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
      "new_status": "<pending|active|completed|blocked|provisional>",
      "confidence": <0.0 to 1.0>,
      "evidence": "<brief message excerpt or reason>"
    }
  ],
  "new_task_candidates": [],
  "ambiguity_flags": []
}

Rules:
- Only include nodes whose status should change based on the messages.
- Use "provisional" if a status change is implied but not confirmed.
- confidence < 0.75 → use "provisional" status.
- Do not invent node_ids not in the template.
- Cadence nodes: mark "active" when their activation condition is met.
  Do not wait for a message to mention them — activate on stage/predecessor completion.
"""


def build_system_prompt(task_id: str) -> str:
    """Build the (mostly) static system prompt for a given task."""
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
                       new_message: dict) -> str:
    """Build the variable user section for a single update call."""
    nodes_summary = [
        {"node_id": n["name"].lower().replace(" ", "_"),
         "name": n["name"], "status": n["status"]}
        for n in node_states
    ]

    messages_summary = [
        f"[{m['timestamp']}] {m.get('sender_jid', 'unknown')} "
        f"(group: {m.get('group_id', '?')}): {m.get('body', '')}"
        for m in recent_messages
    ]

    new_msg_line = (
        f"[{new_message.get('timestamp', '?')}] "
        f"{new_message.get('sender_jid', 'unknown')} "
        f"(group: {new_message.get('group_id', '?')}): "
        f"{new_message.get('body', '')}"
    )

    return f"""## Current node states

{json.dumps(nodes_summary, indent=2, ensure_ascii=False)}

## Recent messages (last {len(recent_messages)})

{chr(10).join(messages_summary) if messages_summary else "(none)"}

## New message

{new_msg_line}

Update the task nodes based on the above. Remember to activate cadence nodes
when their conditions are met, even if no message explicitly mentions them."""
