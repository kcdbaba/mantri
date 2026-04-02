"""
Item-based scrap assignment — match scrap content against existing order
items across active tasks.

In production, tasks accumulate items as messages are processed. When a
new scrap arrives without entity evidence, we check if the items/keywords
mentioned in the scrap match items already tracked in any active task.

Uses rapidfuzz for fuzzy matching (handles Hindi/Hinglish item names).
"""

import logging
import re
from dataclasses import dataclass
from rapidfuzz import fuzz

log = logging.getLogger(__name__)

# Minimum fuzzy match score to consider an item match
ITEM_MATCH_THRESHOLD = 85

# Minimum word length to consider for matching (skip "ok", "ha", etc.)
MIN_WORD_LEN = 3

# Common non-item words to skip
_SKIP_WORDS = {
    "sir", "bhai", "please", "karo", "kijiye", "chahiye", "bhejo",
    "check", "rate", "price", "cost", "total", "extra", "plus",
    "gst", "delivery", "transport", "packing", "done", "sent",
    "received", "money", "payment", "paytm", "from", "the",
    "hai", "hain", "nahi", "aur", "bhi", "yeh", "woh",
    "real", "this", "message", "was", "deleted", "table",
    "come", "gone", "there", "here", "main", "list", "nos",
    "file", "attached", "thanks", "thank", "pink", "paid",
    "bank", "transfer", "number", "approx", "also",
    "tomorrow", "today", "need", "will", "mein", "stand",
    "inka", "intra", "their",
    "inch", "size", "colour", "color", "type", "model",
    "quality", "professional", "brand", "make", "wooden",
    "hair", "salon", "electric",
}


@dataclass
class ItemMatch:
    """A match between scrap content and an existing task item."""
    task_id: str
    entity_id: str
    item_description: str   # the existing item
    matched_word: str       # the word from the scrap that matched
    score: float            # fuzzy match score (0-100)


def match_scrap_to_items(scrap_text: str,
                          task_items: dict[str, list[dict]],
                          task_entities: dict[str, str]) -> list[ItemMatch]:
    """
    Match words from a scrap against items in active tasks.

    Args:
        scrap_text: concatenated body text from all messages in the scrap
        task_items: {task_id: [{"description": ..., "quantity": ..., ...}]}
        task_entities: {task_id: entity_id}

    Returns: list of ItemMatch, sorted by score descending
    """
    # Extract candidate words from scrap
    words = _extract_candidate_words(scrap_text)
    if not words:
        return []

    matches = []

    for task_id, items in task_items.items():
        entity_id = task_entities.get(task_id, "")

        for item in items:
            desc = (item.get("description") or "").lower()
            if not desc or len(desc) < 4:  # skip very short items like "pen"
                continue

            # Extract keywords from item description
            item_words = _extract_candidate_words(desc)

            for scrap_word in words:
                # Short words (<=4 chars) use strict full ratio to avoid
                # substring false positives (e.g., "inch" in "winch",
                # "hair" in "chair")
                use_strict = len(scrap_word) <= 4

                # Try matching scrap word against item description directly
                if use_strict:
                    direct_score = fuzz.ratio(scrap_word, desc)
                else:
                    direct_score = fuzz.partial_ratio(scrap_word, desc)
                if direct_score >= ITEM_MATCH_THRESHOLD:
                    matches.append(ItemMatch(
                        task_id=task_id,
                        entity_id=entity_id,
                        item_description=desc,
                        matched_word=scrap_word,
                        score=direct_score,
                    ))
                    continue

                # Try matching against individual item words
                for iw in item_words:
                    word_score = fuzz.ratio(scrap_word, iw)
                    if word_score >= ITEM_MATCH_THRESHOLD:
                        matches.append(ItemMatch(
                            task_id=task_id,
                            entity_id=entity_id,
                            item_description=desc,
                            matched_word=scrap_word,
                            score=word_score,
                        ))
                        break  # one match per item is enough

    # Deduplicate: keep best score per (task_id, item_description)
    best = {}
    for m in matches:
        key = (m.task_id, m.item_description)
        if key not in best or m.score > best[key].score:
            best[key] = m

    return sorted(best.values(), key=lambda m: -m.score)


def resolve_scrap_entity_by_items(scrap_text: str,
                                   task_items: dict[str, list[dict]],
                                   task_entities: dict[str, str]) -> str | None:
    """
    Try to resolve a scrap's entity by matching against task items.

    Returns entity_id if a clear match is found (one task matches
    significantly better than others), None otherwise.
    """
    matches = match_scrap_to_items(scrap_text, task_items, task_entities)

    if not matches:
        return None

    # Group matches by entity
    entity_scores: dict[str, float] = {}
    for m in matches:
        entity_scores[m.entity_id] = max(
            entity_scores.get(m.entity_id, 0), m.score
        )

    if not entity_scores:
        return None

    # If only one entity matches, use it
    if len(entity_scores) == 1:
        entity_id = list(entity_scores.keys())[0]
        log.debug("Item match: scrap → %s (sole match, score=%.0f)",
                  entity_id, list(entity_scores.values())[0])
        return entity_id

    # If multiple entities match, only use if one is clearly better
    sorted_entities = sorted(entity_scores.items(), key=lambda x: -x[1])
    best_entity, best_score = sorted_entities[0]
    second_score = sorted_entities[1][1]

    if best_score >= 85 and best_score - second_score >= 15:
        log.debug("Item match: scrap → %s (clear winner, %.0f vs %.0f)",
                  best_entity, best_score, second_score)
        return best_entity

    # Ambiguous — don't guess
    log.debug("Item match: ambiguous — %s", sorted_entities[:3])
    return None


def _extract_candidate_words(text: str) -> list[str]:
    """Extract meaningful words from text for item matching."""
    # Split on whitespace and common delimiters
    tokens = re.findall(r'[a-zA-Z\u0900-\u097F]{3,}', text.lower())
    return [t for t in tokens if t not in _SKIP_WORDS]
