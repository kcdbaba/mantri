#!/usr/bin/env python3
"""
Generate baseline conversation tags for Tasks group messages.

Walks through all messages chronologically, using contextual reasoning
to assign each message to a task/conversation. Uses:
- Entity mentions (army units, suppliers)
- Temporal proximity (burst continuity)
- Sender context (what was this sender recently talking about)
- Item/topic continuity

Outputs a JSON file with per-message tags for eval comparison.
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

# Load ORBAT reference for comprehensive unit detection
_ORBAT_PATH = Path("data/indian_military_units_reference.json")
_ORBAT = json.loads(_ORBAT_PATH.read_text()) if _ORBAT_PATH.exists() else {}
_ORBAT_REGEX = _ORBAT.get("regex_patterns", {})

# Build comprehensive unit regex from ORBAT reference
_numbered_unit_pattern = _ORBAT_REGEX.get("numbered_unit", "")
_named_unit_pattern = _ORBAT_REGEX.get("named_unit_no_number", "")
_service_pattern = _ORBAT_REGEX.get("service_corps", "")
_capf_pattern = _ORBAT_REGEX.get("capf_units", "")
_artillery_pattern = _ORBAT_REGEX.get("artillery_specific", "")
_ne_location_pattern = _ORBAT_REGEX.get("ne_location_military", "")

UNIT_RE = re.compile(
    _numbered_unit_pattern or
    r'\b(\d+\s*(?:jak|bde|sub\s*area|regt|bty|fd|engr|sig|coy|bn|div|'
    r'armd|inf|arty|ad|emd|med|ord|asc|aoc|eme|stn\s*hq|jak\s*rif|'
    r'fd\s*wk\s*sp))\b',
    re.IGNORECASE,
)

# Named units without numbers (stn hq, sub area, etc.)
NAMED_UNIT_RE = re.compile(
    _named_unit_pattern or r'\b(?:stn\s*hq|sub\s*area)\b',
    re.IGNORECASE,
)

# CAPF units
CAPF_RE = re.compile(
    _capf_pattern or r'\b(?:bsf|crpf|cisf|itbp|ssb|assam\s*rifles?)\b',
    re.IGNORECASE,
)

# NE military locations
NE_LOCATION_RE = re.compile(
    _ne_location_pattern or r'\b(?:narangi|tezpur|missamari|rangapahar)\b',
    re.IGNORECASE,
)

# Known supplier/entity patterns from the chat
KNOWN_ENTITIES = {
    "20 jak": "unit:20_jak",
    "20 jak rif": "unit:20_jak_rif",
    "107 bde": "unit:107_bde",
    "857 fd": "unit:857_fd",
    "857 fd wk sp": "unit:857_fd_wk_sp",
    "621 eme": "unit:621_eme",
    "264 fd": "unit:264_fd",
    "264 fd wk sp": "unit:264_fd_wk_sp",
    "11 bde": "unit:11_bde",
    "stn hq daranga": "unit:stn_hq_daranga",
    "stn hq darranga": "unit:stn_hq_daranga",
    "csd canteen": "singleton:csd_canteen",
    "canon camera": "supplier:canon_camera",
    "ahuja": "supplier:ahuja_manoj",
    "arihant": "supplier:arihant",
    "nilkamal": "supplier:nilkamal",
    "modren living": "supplier:modren_living",
    "modern living": "supplier:modren_living",
    "assam motor": "supplier:assam_motor",
    "bajaj": "supplier:bajaj",
    "solo": "supplier:solo",
    "faber castel": "supplier:faber_castel",
    "faber castle": "supplier:faber_castel",
    "glass shop": "supplier:glass_shop",
    "saikia electronic": "supplier:saikia_electronic",
    "biswanath": "supplier:biswanath",
    "sanju da": "supplier:sanju_da",
    "arty bde": "unit:arty_bde",
    "mukesh ji": "entity:mukesh_ji",
}

# Item keywords that hint at task context
ITEM_KEYWORDS = {
    "printer": "item:printer",
    "camera": "item:camera",
    "fan": "item:fan",
    "battery": "item:battery",
    "ac": "item:ac",
    "refrigerator": "item:fridge",
    "fridge": "item:fridge",
    "dustbin": "item:dustbin",
    "ups": "item:ups",
    "dosa bhatti": "item:dosa_bhatti",
    "stabilizer": "item:stabilizer",
}


def detect_entity(body: str) -> str | None:
    """Detect the primary entity reference in a message."""
    body_lower = body.lower()

    # Check known entities (longest match first)
    for pattern, entity_id in sorted(KNOWN_ENTITIES.items(), key=lambda x: -len(x[0])):
        if pattern in body_lower:
            return entity_id

    # Check numbered army unit regex (e.g., "20 jak", "107 bde")
    match = UNIT_RE.search(body)
    if match:
        unit = re.sub(r'\s+', '_', match.group(0).lower().strip())
        return f"unit:{unit}"

    # Check named units without numbers (stn hq, sub area, etc.)
    match = NAMED_UNIT_RE.search(body)
    if match:
        # Need additional context (location name usually follows)
        # e.g., "stn hq daranga" → grab following word(s)
        start = match.end()
        following = body[start:start + 30].strip().split()[0] if start < len(body) else ""
        if following and following.lower() not in _STOP_WORDS:
            return f"unit:{match.group(0).lower().replace(' ', '_')}_{following.lower()}"
        return f"unit:{match.group(0).lower().replace(' ', '_')}"

    # Check CAPF units
    match = CAPF_RE.search(body)
    if match:
        return f"unit:{match.group(0).lower().replace(' ', '_')}"

    # Check NE military locations
    match = NE_LOCATION_RE.search(body)
    if match:
        return f"location:{match.group(0).lower()}"

    return None

_STOP_WORDS = {
    "ha", "ok", "ji", "sir", "ye", "wo", "is", "us", "ho", "the", "and",
    "confirm", "kijiye", "here", "there", "this", "that", "main",
    "ka", "ke", "ki", "se", "ko", "pe", "mein", "wala",
}


def tag_messages(messages: list[dict]) -> list[dict]:
    """
    Tag each message with a conversation/task assignment.

    Returns list of {message_id, idx, tag, confidence, reasoning} dicts.
    """
    tags = []
    sender_context: dict[str, str] = {}  # sender → last known entity
    sender_last_ts: dict[str, int] = {}  # sender → last message timestamp

    for i, msg in enumerate(messages):
        body = (msg.get("body") or "").strip()
        sender = msg.get("sender_jid", "")
        ts = msg.get("timestamp", 0)
        media = msg.get("media_type", "text")

        tag = None
        confidence = 0.0
        reasoning = ""

        # Try direct entity detection
        if body:
            entity = detect_entity(body)
            if entity:
                tag = entity
                confidence = 0.9
                reasoning = f"direct mention: '{body[:50]}'"
                sender_context[sender] = entity
                sender_last_ts[sender] = ts

        # If no direct entity, check sender context (forward propagation)
        if tag is None and sender in sender_context:
            last_ts = sender_last_ts.get(sender, 0)
            gap = ts - last_ts if last_ts else float("inf")

            if gap <= 300:  # 5 min continuity
                tag = sender_context[sender]
                confidence = 0.6
                reasoning = f"sender continuity ({gap}s gap)"
                sender_last_ts[sender] = ts
            else:
                # Too much gap — clear context
                del sender_context[sender]

        # If still no tag, check if this is an empty message (likely image)
        if tag is None and not body:
            # Check if sender has recent context
            if sender in sender_context:
                last_ts = sender_last_ts.get(sender, 0)
                gap = ts - last_ts if last_ts else float("inf")
                if gap <= 120:  # tighter window for empty messages
                    tag = sender_context[sender]
                    confidence = 0.4
                    reasoning = f"empty msg, sender context ({gap}s gap)"
                    sender_last_ts[sender] = ts

        # Check if a nearby sender (within 60s) provides context
        if tag is None and body:
            for j in range(max(0, i - 5), i):
                other = messages[j]
                other_ts = other.get("timestamp", 0)
                if ts - other_ts <= 60:
                    other_body = (other.get("body") or "").strip()
                    if other_body:
                        other_entity = detect_entity(other_body)
                        if other_entity:
                            # Check if this message is a reply/continuation
                            tag = other_entity
                            confidence = 0.4
                            reasoning = f"nearby message context from msg {j}"
                            break

        # Special cases
        if body and any(kw in body.lower() for kw in ["money sent", "paytm", "payment"]):
            tag = tag or "singleton:payment"
            confidence = max(confidence, 0.5)
            reasoning = reasoning or "payment related"

        if body and "deleted" in body.lower():
            tag = "noise:deleted"
            confidence = 1.0
            reasoning = "deleted message"

        tags.append({
            "message_id": msg.get("message_id", f"msg_{i}"),
            "idx": i,
            "sender": sender[:25],
            "timestamp_raw": msg.get("timestamp_raw", ""),
            "body_preview": (body[:60] if body else "(empty)"),
            "tag": tag,
            "confidence": confidence,
            "reasoning": reasoning,
        })

    return tags


def main():
    trace_path = Path("tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/replay_trace.json")
    trace = json.loads(trace_path.read_text())
    tasks_msgs = [m for m in trace if m["group_id"] == "Tasks"]

    print(f"Tagging {len(tasks_msgs)} Tasks group messages...")
    tags = tag_messages(tasks_msgs)

    # Save baseline
    output_path = Path("tests/integration_tests/R1-D-L3-01_sata_multi_item_multi_supplier/tasks_baseline_tags.json")
    output_path.write_text(json.dumps({
        "case_id": "R1-D-L3-01",
        "group_id": "Tasks",
        "generated_by": "claude_code_baseline",
        "message_count": len(tags),
        "tags": tags,
    }, indent=2, ensure_ascii=False))

    # Stats
    tagged = sum(1 for t in tags if t["tag"] is not None)
    untagged = len(tags) - tagged

    entity_counts = defaultdict(int)
    for t in tags:
        if t["tag"]:
            entity_counts[t["tag"]] += 1

    print(f"\nTagged: {tagged}/{len(tags)} ({100*tagged/len(tags):.0f}%)")
    print(f"Untagged: {untagged}")
    print(f"\nTags by entity:")
    for entity, count in sorted(entity_counts.items(), key=lambda x: -x[1]):
        print(f"  {entity:35s} {count}")

    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
