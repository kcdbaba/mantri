"""
Entity alias matching for Layer 2b routing.
Combines exact lookup and rapidfuzz fuzzy matching.
"""

import re
from rapidfuzz import fuzz

from src.config import ENTITY_ALIASES, ENTITY_MATCH_THRESHOLD


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_entities(body: str) -> list[tuple[str, float]]:
    """
    Return [(entity_id, confidence), ...] for all entities matched in body.
    Uses exact substring match first, then rapidfuzz partial_ratio fallback.
    Multiple entities can match (M:N routing).
    """
    if not body:
        return []

    normalised = _normalise(body)
    matched: dict[str, float] = {}  # entity_id → best confidence

    for alias, entity_id in ENTITY_ALIASES.items():
        alias_norm = _normalise(alias)

        # Exact substring
        if alias_norm in normalised:
            score = 100.0
        else:
            score = fuzz.partial_ratio(alias_norm, normalised)

        if score >= ENTITY_MATCH_THRESHOLD:
            confidence = score / 100.0 * 0.9  # scale to 0–0.90 range
            if entity_id not in matched or matched[entity_id] < confidence:
                matched[entity_id] = confidence

    return list(matched.items())
