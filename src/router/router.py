"""
Message router — 4-layer cascade.

Layer 1:  noise filter (reactions, stickers, system messages)
Layer 2a: direct group → task map (conf=0.90)
Layer 2b: entity keyword + alias matching via rapidfuzz (conf varies, threshold 0.80)
Layer 2c: MiniLM embedding similarity — STUB (deferred to post-Sprint 3)
          Implementation plan: encode message body with all-MiniLM-L6-v2,
          cosine-compare against per-task context_embedding stored in
          task_routing_context.context_embedding (serialised float32 numpy array).
          Threshold: 0.65. Updates task_routing_context.context_embedding
          with EMA on new routed messages.
Layer 2d: Gemini Flash 8B LLM routing call — STUB (fail-up from 2c)
          Implementation plan: if 2c confidence < 0.65, call Gemini Flash 8B
          with message body + all active task summaries, ask it to pick the
          best-matching task. Output: task_id + confidence + reasoning.
          Only invoked when 2c produces no result above threshold.
→ ambiguity queue if all layers below threshold

Returns: list of (task_id, confidence) tuples.
Empty list → push to dead letter queue (unrouted).
"""

import json
import logging
from src.config import (
    MONITORED_GROUPS, DIRECT_GROUP_CONFIDENCE,
    ENTITY_MATCH_CONFIDENCE, AMBIGUITY_THRESHOLD,
    ENABLE_CONVERSATION_ROUTING,
)
from src.router.alias_dict import match_entities
from src.store.task_store import get_active_tasks

log = logging.getLogger(__name__)

RUNTIME_TASK_CONFIDENCE = 0.85


def _resolve_to_entity(value: str) -> str:
    """Resolve a MONITORED_GROUPS value to entity_id.
    Supports both entity_ids (pass-through) and legacy task_ids (look up entity)."""
    if value.startswith("entity_"):
        return value
    # Legacy: value is a task_id → look up entity from task
    try:
        from src.store.task_store import get_task
        task = get_task(value)
        if task:
            return task["client_id"]
    except Exception:
        pass
    return value  # fallback — use as-is


def _get_runtime_entities(group_id: str) -> list[str]:
    """Get entity_ids from task_routing_context whose source_groups include this group."""
    try:
        from src.store.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT task_id, source_groups, entity_ids FROM task_routing_context"
        ).fetchall()
        conn.close()
        result = set()
        for row in rows:
            groups = json.loads(row[1] or "[]")
            if group_id in groups:
                eids = json.loads(row[2] or "[]")
                result.update(eids)
        return list(result)
    except Exception:
        return []

# Message types that carry no operational content
NOISE_TYPES = {"reaction", "sticker", "system", "revoked"}


def route(message: dict) -> list[tuple[str, float]]:
    """
    Route a single enriched message to one or more entities.
    Returns [(entity_id, confidence), ...].
    """
    # --- Layer 1: noise filter ---
    if message.get("media_type") in NOISE_TYPES:
        log.debug("Dropped noise message %s (%s)", message.get("message_id"), message.get("media_type"))
        return []

    # Drop empty messages (no text, no image) — zero information for the pipeline
    body = message.get("body") or ""
    has_image = bool(message.get("image_path") or message.get("image_bytes"))
    if not body.strip() and not has_image:
        log.debug("Dropped empty message %s", message.get("message_id"))
        return []
    group_id = message.get("group_id", "")

    # --- Layer 2a: direct group → entity map ---
    results = []
    seen = set()
    if group_id in MONITORED_GROUPS:
        value = MONITORED_GROUPS[group_id]
        if value is not None:
            entity_id = _resolve_to_entity(value)
            results.append((entity_id, DIRECT_GROUP_CONFIDENCE))
            seen.add(entity_id)
            log.debug("Layer 2a: %s → %s (conf=%.2f)", group_id, entity_id, DIRECT_GROUP_CONFIDENCE)
        elif ENABLE_CONVERSATION_ROUTING:
            # Shared group with null mapping — route to conversation system
            log.debug("Layer 2a-conv: %s → conversation routing", group_id)
            return [("__conv_pending__", 0.0)]

    # --- Layer 2a+: runtime entities from task_routing_context ---
    for eid in _get_runtime_entities(group_id):
        if eid not in seen:
            results.append((eid, RUNTIME_TASK_CONFIDENCE))
            seen.add(eid)
            log.debug("Layer 2a+: runtime entity %s for group %s (conf=%.2f)",
                      eid, group_id, RUNTIME_TASK_CONFIDENCE)

    if results:
        return results

    # --- Layer 2b: entity keyword + alias matching ---
    entity_matches = match_entities(body)
    if entity_matches:
        for entity_id, match_confidence in entity_matches:
            if entity_id not in seen:
                confidence = min(match_confidence, ENTITY_MATCH_CONFIDENCE)
                results.append((entity_id, confidence))
                seen.add(entity_id)
                log.debug("Layer 2b: entity=%s (conf=%.2f)", entity_id, confidence)
        if results:
            return results

    # --- Layer 2c: MiniLM embedding similarity (deferred — stub only) ---
    # When implemented: encode body, cosine-compare against task_routing_context.context_embedding
    # Threshold 0.65; update EMA on hit. See module docstring for full plan.

    # --- Layer 2d: Gemini Flash 8B LLM routing (deferred — stub only) ---
    # When implemented: call Gemini Flash 8B with message + all active task summaries.
    # Only invoked when Layer 2c produces no result above threshold.

    log.debug("No route found for message %s — unrouted", message.get("message_id"))
    return []
