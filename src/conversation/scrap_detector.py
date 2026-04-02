"""
Scrap detection — partition sender strands into topic-bounded scraps.

A scrap is a contiguous sequence of messages from one sender in a shared
group that addresses one task/topic. Scrap boundaries are detected by:

1. Entity/item mention change (strongest signal)
2. Time gap > BURST_GAP_S between consecutive messages from the sender
3. Explicit task reference change

Scraps start unassigned. When evidence arrives (entity mention, item
reference), the scrap is assigned to a conversation. Assignment propagates
backward to preceding unassigned messages in the same burst.

Scrap splitting uses a "default-continue, split on evidence" model for
same-sender messages: continue the current scrap unless a different entity
is detected or the gap exceeds 15 minutes.
"""

import json
import logging
import os
import re
import uuid
import time
from dataclasses import dataclass, field

from src.router.alias_dict import match_entities

log = logging.getLogger(__name__)

# Max gap between messages from same sender before starting a new scrap.
# Uses 15-minute gap (reply-tree informed) instead of the old 120s burst gap.
BURST_GAP_S = 900

# Army unit pattern: number + unit type abbreviation
_ARMY_UNIT_RE = re.compile(
    r'\b(\d+\s*(?:jak|bde|sub\s*area|regt|bty|fd|engr|sig|coy|bn|div|corps|'
    r'armd|inf|arty|ad|emd|med|ord|asc|aoc|eme|stn\s*hq|jak\s*rif))\b',
    re.IGNORECASE,
)

# "from <supplier>" pattern
_FROM_SUPPLIER_RE = re.compile(
    r'\b(?:from|se)\s+([a-zA-Z][\w\s]{2,20}?)(?:\s*[,.\n]|\s+@|\s*$)',
    re.IGNORECASE,
)

# Known non-entity words to filter out of pattern matches
_STOP_WORDS = {
    "ha", "ok", "ji", "sir", "ye", "wo", "is", "us", "ho", "the", "and",
    "confirm", "kijiye", "here", "there", "this", "that", "main",
    "confirm kijiye", "order", "delivery", "payment", "dispatch",
    "tomorrow", "today", "done", "ok sir", "ji sir",
    "chh", "prop", "pvt", "ltd", "pvt ltd",
}

# Principal entities — OUR business, never route TO these.
# Mentions of these confirm a business transaction but the routing target
# is the OTHER party (the supplier or client on the document).
PRINCIPAL_ENTITIES = {
    "uttam enterprise", "uttam enterprises",
    "army stores", "army store",
    "ashish chhabra", "ashish chh", "ashish ch",
    "prop ashish", "prop. ashish",
    "army stores prop", "prop ashish chh",
}

# Payment keywords for bookkeeping singleton detection
PAYMENT_KEYWORDS = {
    "money sent", "paytm", "payment", "paid", "bank transfer",
    "phonepe", "upi", "\u20b9",
}

# ── ORBAT numbered units lookup ─────────────────────────────────────
# alias_text (lowercase) → (full_name, unit_key)
_ORBAT_LOOKUP: dict[str, tuple[str, str]] = {}


def _load_orbat_lookup():
    """Load numbered units from indian_military_units_reference.json."""
    global _ORBAT_LOOKUP
    if _ORBAT_LOOKUP:
        return

    ref_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "data",
        "indian_military_units_reference.json",
    )
    ref_path = os.path.normpath(ref_path)

    try:
        with open(ref_path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("Could not load ORBAT reference from %s", ref_path)
        return

    numbered = data.get("numbered_units", {})

    # Process list-type sections (ne_india_units, other_india_units, sub_areas,
    # station_hqs, ne_military_hospitals)
    for section_key in ("ne_india_units", "other_india_units", "sub_areas",
                        "station_hqs", "ne_military_hospitals"):
        entries = numbered.get(section_key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            full_name = entry.get("full_name") or entry.get("name", "")
            # Build a stable unit key from the full_name
            unit_key = re.sub(r'\s+', '_', full_name.lower().strip())
            for alias in entry.get("aliases", []):
                _ORBAT_LOOKUP[alias.lower()] = (full_name, unit_key)

    log.info("Loaded %d ORBAT aliases", len(_ORBAT_LOOKUP))


# Load at module init
_load_orbat_lookup()


def is_payment_message(body: str) -> bool:
    """Return True if body contains payment-related keywords."""
    lower = body.lower()
    return any(kw in lower for kw in PAYMENT_KEYWORDS)


def extract_entity_refs(body: str) -> list[dict]:
    """
    Extract entity references from a message body.

    Uses four layers (checked in priority order):
    1. Known entities (alias dict) → confidence 0.95
    2. ORBAT numbered units lookup → confidence 0.85
    3. Regex fallback (army unit pattern) → confidence 0.6
    4. "from <supplier>" regex pattern → confidence 0.6

    Returns list of dicts: [{"ref": str, "confidence": float}, ...]
    The "ref" may be an entity_id (from alias dict) or a "unit:..." /
    "supplier:..." raw reference.
    """
    refs: dict[str, float] = {}  # ref → best confidence

    def _is_principal(text: str) -> bool:
        """Check if text matches a principal entity (our business)."""
        lower = text.lower().strip()
        return any(p in lower for p in PRINCIPAL_ENTITIES)

    def _add(ref: str, conf: float):
        if ref not in refs or refs[ref] < conf:
            refs[ref] = conf

    # 1. Known aliases → confidence 0.95
    matches = match_entities(body)
    for eid, confidence in matches:
        if confidence >= 0.8:
            _add(eid, 0.95)

    # Normalise body for ORBAT / regex matching
    body_lower = body.lower()

    # 2. ORBAT numbered units lookup → confidence 0.85
    for alias, (full_name, unit_key) in _ORBAT_LOOKUP.items():
        if alias in body_lower:
            _add(f"unit:{unit_key}", 0.85)

    # 3. Army unit regex fallback → confidence 0.6
    for match in _ARMY_UNIT_RE.finditer(body):
        unit_ref = re.sub(r'\s+', '_', match.group(1).lower().strip())
        _add(f"unit:{unit_ref}", 0.6)

    # 4. "from X" supplier pattern → confidence 0.6
    for match in _FROM_SUPPLIER_RE.finditer(body):
        supplier = match.group(1).strip().lower()
        if supplier in _STOP_WORDS or len(supplier) <= 2:
            continue
        if _is_principal(supplier):
            continue  # skip our own business name
        if _ARMY_UNIT_RE.search(supplier):
            unit_ref = re.sub(r'\s+', '_', supplier)
            _add(f"unit:{unit_ref}", 0.6)
        else:
            _add(f"supplier:{supplier}", 0.6)

    # Filter out any refs that match principal entities
    filtered = {
        ref: conf for ref, conf in refs.items()
        if not _is_principal(ref.replace("supplier:", "").replace("unit:", ""))
    }

    return [{"ref": ref, "confidence": conf} for ref, conf in filtered.items()]


@dataclass
class Scrap:
    """A topic-bounded sequence from one sender."""
    id: str
    group_id: str
    sender_jid: str
    messages: list[dict] = field(default_factory=list)
    first_msg_ts: int = 0
    last_msg_ts: int = 0
    entity_matches: list[str] = field(default_factory=list)  # entity_ids found
    status: str = "open"  # "open", "assigned", "processed"

    def add_message(self, msg: dict):
        self.messages.append(msg)
        ts = msg.get("timestamp", 0)
        if not self.first_msg_ts:
            self.first_msg_ts = ts
        self.last_msg_ts = ts


def detect_scraps(messages: list[dict], group_id: str) -> list[Scrap]:
    """
    Detect scraps from a chronological list of messages in a shared group.

    Groups messages into sender strands, then partitions each strand into
    scraps based on burst gaps and entity mention changes.

    Returns list of Scraps in chronological order (by first_msg_ts).
    """
    # Group into sender strands (chronological per sender)
    strands: dict[str, list[dict]] = {}
    for msg in messages:
        sender = msg.get("sender_jid", "")
        if sender not in strands:
            strands[sender] = []
        strands[sender].append(msg)

    all_scraps = []

    for sender, strand_msgs in strands.items():
        scraps = _partition_strand(strand_msgs, group_id, sender)
        all_scraps.extend(scraps)

    # Sort all scraps by first message timestamp
    all_scraps.sort(key=lambda s: s.first_msg_ts)

    log.info("Detected %d scraps from %d senders in group %s",
             len(all_scraps), len(strands), group_id)

    return all_scraps


def _partition_strand(messages: list[dict], group_id: str,
                      sender: str) -> list[Scrap]:
    """Partition a single sender's messages into scraps."""
    scraps = []
    current_scrap = None
    current_entities: set[str] = set()

    for msg in messages:
        body = (msg.get("body") or "").strip()
        ts = msg.get("timestamp", 0)

        # Detect entity references in this message
        # Body may include OCR text appended by conversation_router enrichment
        msg_entities = set()
        if body:
            msg_entities = {r["ref"] for r in extract_entity_refs(body)}

        # Decide: continue current scrap or start new one
        should_break = False

        if current_scrap is None:
            should_break = True
        else:
            # Time gap check
            gap = ts - current_scrap.last_msg_ts
            if gap > BURST_GAP_S:
                should_break = True

            # Entity change check (if both have entity evidence)
            if msg_entities and current_entities and not msg_entities & current_entities:
                should_break = True

        if should_break:
            if current_scrap and current_scrap.messages:
                scraps.append(current_scrap)
            current_scrap = Scrap(
                id=f"scrap_{uuid.uuid4().hex[:8]}",
                group_id=group_id,
                sender_jid=sender,
            )
            current_entities = set()

        current_scrap.add_message(msg)

        # Accumulate entity evidence for the scrap
        if msg_entities:
            current_entities.update(msg_entities)
            current_scrap.entity_matches = list(current_entities)

            # Propagate backward — assign entity to earlier messages
            # in this scrap that had no evidence
            if not current_scrap.entity_matches:
                current_scrap.entity_matches = list(msg_entities)

    # Don't forget the last scrap
    if current_scrap and current_scrap.messages:
        scraps.append(current_scrap)

    return scraps


def assign_scraps_to_conversations(scraps: list[Scrap],
                                     existing_conversations: list[dict]) -> dict:
    """
    Assign scraps to conversations based on entity evidence.

    Returns: {scrap_id: [conversation_ids]} (many-to-many)

    Scraps with no entity evidence remain unassigned.
    Scraps with entity evidence are matched to existing conversations
    or flagged for new conversation creation.
    """
    # Build entity → conversation lookup
    entity_to_convos = {}
    for conv in existing_conversations:
        eid = conv.get("entity_id")
        if eid:
            if eid not in entity_to_convos:
                entity_to_convos[eid] = []
            entity_to_convos[eid].append(conv["id"])

    assignments = {}  # scrap_id → [conversation_ids]
    unassigned = []
    new_entities = set()  # entities that need new conversations

    for scrap in scraps:
        if not scrap.entity_matches:
            unassigned.append(scrap.id)
            continue

        conv_ids = []
        for eid in scrap.entity_matches:
            if eid in entity_to_convos:
                conv_ids.extend(entity_to_convos[eid])
            else:
                new_entities.add(eid)

        if conv_ids:
            assignments[scrap.id] = list(set(conv_ids))
            scrap.status = "assigned"
        else:
            unassigned.append(scrap.id)

    log.info("Assigned %d scraps, %d unassigned, %d new entities need conversations",
             len(assignments), len(unassigned), len(new_entities))

    return assignments
