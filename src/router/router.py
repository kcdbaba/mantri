"""
Message router — 3-layer cascade.

Layer 1: noise filter (reactions, stickers, system messages)
Layer 2a: direct group → task map
Layer 2b: entity keyword + alias matching
Layer 2c: embedding similarity (deferred — stub only)

Returns: list of (task_id, confidence) tuples.
Empty list → push to dead letter queue (unrouted).
"""

import logging
from src.config import (
    MONITORED_GROUPS, DIRECT_GROUP_CONFIDENCE,
    ENTITY_MATCH_CONFIDENCE, AMBIGUITY_THRESHOLD,
)
from src.router.alias_dict import match_entities
from src.store.task_store import get_active_tasks

log = logging.getLogger(__name__)

# Message types that carry no operational content
NOISE_TYPES = {"reaction", "sticker", "system", "revoked"}


def route(message: dict) -> list[tuple[str, float]]:
    """
    Route a single enriched message to one or more task instances.
    Returns [(task_id, confidence), ...].
    """
    # --- Layer 1: noise filter ---
    if message.get("media_type") in NOISE_TYPES:
        log.debug("Dropped noise message %s (%s)", message.get("message_id"), message.get("media_type"))
        return []

    body = message.get("body") or ""
    group_id = message.get("group_id", "")

    # --- Layer 2a: direct group → task map ---
    if group_id in MONITORED_GROUPS:
        task_id = MONITORED_GROUPS[group_id]
        if task_id is not None:
            log.debug("Layer 2a: %s → %s (conf=%.2f)", group_id, task_id, DIRECT_GROUP_CONFIDENCE)
            return [(task_id, DIRECT_GROUP_CONFIDENCE)]
        # group is monitored but shared → fall through to 2b

    # --- Layer 2b: entity keyword + alias matching ---
    entity_matches = match_entities(body)
    if entity_matches:
        results = []
        active_tasks = {t["id"]: t for t in get_active_tasks()}
        for entity_id, match_confidence in entity_matches:
            # Find active tasks involving this entity
            for task in active_tasks.values():
                import json
                supplier_ids = json.loads(task.get("supplier_ids") or "[]")
                if task["client_id"] == entity_id or entity_id in supplier_ids:
                    confidence = min(match_confidence, ENTITY_MATCH_CONFIDENCE)
                    results.append((task["id"], confidence))
                    log.debug("Layer 2b: entity=%s → task=%s (conf=%.2f)",
                              entity_id, task["id"], confidence)
        if results:
            return results

    # --- Layer 2c: embedding similarity (deferred) ---
    # TODO: implement MiniLM embedding similarity routing
    log.debug("No route found for message %s — unrouted", message.get("message_id"))
    return []
