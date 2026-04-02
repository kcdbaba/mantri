"""
Entity learner — discover new entities from conversation patterns.

Detects:
  1. Contact sharing: name + phone number or .vcf files
  2. "From X" supplier introductions
  3. Unnumbered military unit references

Discovered entities are queued for confirmation, not auto-added.
The queue is stored in the DB and surfaced via the dashboard/WhatsApp.
"""

import json
import logging
import re
import uuid
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Contact sharing patterns
_CONTACT_RE = re.compile(
    r'([A-Z][a-zA-Z\s\']{3,35}?)(?:\s*\n|\s+)(?:\+91\s*|0)?\d{5,}',
)
_VCF_RE = re.compile(r'([^/\\]+?)\.vcf', re.IGNORECASE)
_PHONE_RE = re.compile(r'(?:\+91\s*|0)?(\d{10,})')

# Words that are NOT entity names
_IGNORE_NAMES = {
    "this message was deleted", "money sent", "payment successful",
    "paytm", "phonepe", "bank transfer", "hey", "hello",
}


@dataclass
class DiscoveredEntity:
    """A newly discovered entity from conversation analysis."""
    id: str
    name: str
    entity_type: str         # "supplier", "client", "transport", "unknown"
    confidence: float
    source: str              # "contact_share", "from_pattern", "unit_type"
    discovered_at: int
    discovered_by: str       # sender_jid who introduced it
    message_id: str
    phone: str = ""
    aliases: list[str] = field(default_factory=list)
    context: str = ""        # surrounding message text for confirmation


def discover_entities(messages: list[dict],
                      known_aliases: set[str] | None = None) -> list[DiscoveredEntity]:
    """
    Scan messages for new entity introductions.

    Args:
        messages: chronological message list
        known_aliases: set of lowercase aliases already in the system

    Returns: list of DiscoveredEntity objects (not yet confirmed)
    """
    known = known_aliases or set()
    discovered = []
    seen_names = set()

    for msg in messages:
        body = (msg.get("body") or "").strip()
        if not body or len(body) < 5:
            continue

        sender = msg.get("sender_jid", "")
        mid = msg.get("message_id", "")
        ts = msg.get("timestamp", int(time.time()))

        # ── Contact sharing (name + phone / VCF) ──
        # VCF files
        for match in _VCF_RE.finditer(body):
            name = match.group(1).strip()
            if _should_skip(name, known, seen_names):
                continue
            seen_names.add(name.lower())

            entity_type = _classify_entity(name, body)
            phone = _extract_phone(body)
            aliases = _generate_aliases(name)

            discovered.append(DiscoveredEntity(
                id=f"discovered_{uuid.uuid4().hex[:8]}",
                name=name,
                entity_type=entity_type,
                confidence=0.8,
                source="contact_share_vcf",
                discovered_at=ts,
                discovered_by=sender,
                message_id=mid,
                phone=phone,
                aliases=aliases,
                context=body[:200],
            ))

        # Phone number contacts (Name\n+91...)
        for match in _CONTACT_RE.finditer(body):
            name = match.group(1).strip()
            if _should_skip(name, known, seen_names):
                continue
            # Skip if it looks like a number, date, or single common word
            if re.match(r'^\d', name) or len(name.split()) > 5:
                continue
            # Skip if it's a single geographic/generic word
            if name.lower() in {"bihar", "assam", "delhi", "kolkata", "guwahati",
                                  "india", "bengal", "mumbai", "chennai", "rangia",
                                  "rangiya", "tezpur", "jorhat", "shillong"}:
                continue
            seen_names.add(name.lower())

            entity_type = _classify_entity(name, body)
            phone = _extract_phone(body)
            aliases = _generate_aliases(name)

            discovered.append(DiscoveredEntity(
                id=f"discovered_{uuid.uuid4().hex[:8]}",
                name=name,
                entity_type=entity_type,
                confidence=0.7,
                source="contact_share_phone",
                discovered_at=ts,
                discovered_by=sender,
                message_id=mid,
                phone=phone,
                aliases=aliases,
                context=body[:200],
            ))

        # ── "From X" supplier introductions ──
        from_pattern = re.compile(
            r'\bfrom\s+([a-zA-Z][\w\s]{2,25}?)(?:\s*[,.\n<]|\s+@|\s*$)',
            re.IGNORECASE,
        )
        for match in from_pattern.finditer(body):
            name = match.group(1).strip()
            if _should_skip(name, known, seen_names):
                continue
            # Skip if it's a military unit (already handled by unit detection)
            from src.conversation.scrap_detector import _is_military_unit_type
            if _is_military_unit_type(name):
                continue
            seen_names.add(name.lower())

            aliases = _generate_aliases(name)

            discovered.append(DiscoveredEntity(
                id=f"discovered_{uuid.uuid4().hex[:8]}",
                name=name,
                entity_type="supplier",
                confidence=0.7,
                source="from_pattern",
                discovered_at=ts,
                discovered_by=sender,
                message_id=mid,
                aliases=aliases,
                context=body[:200],
            ))

    log.info("Discovered %d new entities from %d messages", len(discovered), len(messages))
    return discovered


def _should_skip(name: str, known: set, seen: set) -> bool:
    """Check if a name should be skipped."""
    lower = name.lower().strip()
    if lower in seen:
        return True
    if lower in known:
        return True
    if lower in _IGNORE_NAMES:
        return True
    if len(lower) <= 2:
        return True
    # Skip principal entities
    from src.conversation.scrap_detector import PRINCIPAL_ENTITIES
    if any(p in lower for p in PRINCIPAL_ENTITIES):
        return True
    return False


def _classify_entity(name: str, context: str) -> str:
    """Classify a discovered entity by type from name and context clues."""
    lower = name.lower()
    ctx_lower = context.lower()

    # Transport clues
    if any(w in lower for w in ("driver", "tempo", "transport", "auto")):
        return "transport"

    # Military unit clues
    from src.conversation.scrap_detector import _is_military_unit_type
    if _is_military_unit_type(name):
        return "client"

    # Supplier clues (business suffixes)
    if any(w in lower for w in ("steel", "furniture", "electronics", "enterprise",
                                  "marketing", "craft", "store", "shop", "dealer")):
        return "supplier"

    # Context clues
    if "supplier" in ctx_lower or "rate" in ctx_lower or "price" in ctx_lower:
        return "supplier"
    if "client" in ctx_lower or "order from" in ctx_lower:
        return "client"

    return "unknown"


def _extract_phone(text: str) -> str:
    """Extract first phone number from text."""
    match = _PHONE_RE.search(text)
    return match.group(1) if match else ""


def _generate_aliases(name: str) -> list[str]:
    """Generate alias variants from a name."""
    aliases = [name.lower()]
    words = name.split()

    # Single-word alias if multi-word
    if len(words) >= 2:
        # First word (e.g., "Baishya" from "Baishya Steel")
        if len(words[0]) > 3:
            aliases.append(words[0].lower())
        # Last word if it's a person name (e.g., "Mandal" from "Dhiren Mandal")
        if len(words[-1]) > 3 and words[-1][0].isupper():
            aliases.append(words[-1].lower())

    # Without common suffixes
    for suffix in ("ji", "sir", "da", "bhai"):
        clean = name.lower().rstrip().removesuffix(f" {suffix}").strip()
        if clean != name.lower() and len(clean) > 2:
            aliases.append(clean)

    return list(set(aliases))


def store_discovered_entities(entities: list[DiscoveredEntity], db_path: str = None):
    """Store discovered entities in the DB for confirmation queue."""
    if not entities:
        return

    if db_path is None:
        from src.config import DB_PATH
        db_path = DB_PATH

    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entity_type TEXT,
            confidence REAL,
            source TEXT,
            discovered_at INTEGER,
            discovered_by TEXT,
            message_id TEXT,
            phone TEXT,
            aliases TEXT,
            context TEXT,
            status TEXT DEFAULT 'pending',
            confirmed_entity_id TEXT,
            confirmed_at INTEGER
        )
    """)

    for ent in entities:
        conn.execute(
            """INSERT OR IGNORE INTO discovered_entities
               (id, name, entity_type, confidence, source, discovered_at,
                discovered_by, message_id, phone, aliases, context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ent.id, ent.name, ent.entity_type, ent.confidence, ent.source,
             ent.discovered_at, ent.discovered_by, ent.message_id, ent.phone,
             json.dumps(ent.aliases), ent.context),
        )

    conn.commit()
    conn.close()
    log.info("Stored %d discovered entities for confirmation", len(entities))
