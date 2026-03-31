"""
Entity alias matching for Layer 2b routing.
Combines exact lookup and rapidfuzz fuzzy matching.
Reads aliases from both config (hardcoded) and entity_aliases DB table (runtime).
"""

import re
import time
from rapidfuzz import fuzz

from src.config import ENTITY_ALIASES, ENTITY_MATCH_THRESHOLD

# DB alias cache — refreshed every 30 seconds
_db_alias_cache: dict[str, str] = {}
_db_alias_cache_ts: float = 0
_DB_ALIAS_CACHE_TTL = 30.0


def _load_db_aliases() -> dict[str, str]:
    """Load aliases from entity_aliases DB table, with 30s cache."""
    global _db_alias_cache, _db_alias_cache_ts
    now = time.time()
    if now - _db_alias_cache_ts < _DB_ALIAS_CACHE_TTL and _db_alias_cache_ts > 0:
        return _db_alias_cache
    try:
        from src.store.db import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT alias, entity_id FROM entity_aliases").fetchall()
        conn.close()
        _db_alias_cache = {row[0]: row[1] for row in rows}
    except Exception:
        pass  # DB not available — use cache or empty
    _db_alias_cache_ts = now
    return _db_alias_cache


def invalidate_alias_cache():
    """Force refresh on next call (e.g. after inserting new aliases)."""
    global _db_alias_cache, _db_alias_cache_ts
    _db_alias_cache = {}
    _db_alias_cache_ts = 0


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_all_aliases() -> dict[str, str]:
    """Return merged alias dict: config + DB (DB takes precedence)."""
    return {**ENTITY_ALIASES, **_load_db_aliases()}


def match_entities(body: str) -> list[tuple[str, float]]:
    """
    Return [(entity_id, confidence), ...] for all entities matched in body.
    Uses exact substring match first, then rapidfuzz partial_ratio fallback.
    Multiple entities can match (M:N routing).
    Reads from both config aliases and entity_aliases DB table.
    """
    if not body:
        return []

    normalised = _normalise(body)
    matched: dict[str, float] = {}  # entity_id → best confidence
    all_aliases = get_all_aliases()

    for alias, entity_id in all_aliases.items():
        alias_norm = _normalise(alias)

        # Exact substring
        if alias_norm in normalised:
            score = 100.0
        else:
            score = fuzz.partial_ratio(alias_norm, normalised)
            # Short aliases produce noisy fuzzy matches — require higher score
            min_score = 90 if len(alias_norm) < 8 else ENTITY_MATCH_THRESHOLD
            if score < min_score:
                continue

        if score >= ENTITY_MATCH_THRESHOLD:
            confidence = score / 100.0 * 0.9  # scale to 0–0.90 range
            if entity_id not in matched or matched[entity_id] < confidence:
                matched[entity_id] = confidence

    return list(matched.items())
