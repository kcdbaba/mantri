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
)
from src.router.alias_dict import match_entities
from src.store.task_store import get_active_tasks

log = logging.getLogger(__name__)

RUNTIME_TASK_CONFIDENCE = 0.85


def _get_runtime_tasks(group_id: str) -> list[str]:
    """Get task_ids from task_routing_context whose source_groups include this group."""
    try:
        from src.store.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT task_id, source_groups FROM task_routing_context"
        ).fetchall()
        conn.close()
        result = []
        for row in rows:
            groups = json.loads(row[1] or "[]")
            if group_id in groups:
                result.append(row[0])
        return result
    except Exception:
        return []

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

    # Drop empty messages (no text, no image) — zero information for the pipeline
    body = message.get("body") or ""
    has_image = bool(message.get("image_path") or message.get("image_bytes"))
    if not body.strip() and not has_image:
        log.debug("Dropped empty message %s", message.get("message_id"))
        return []
    group_id = message.get("group_id", "")

    # --- Layer 2a: direct group → task map ---
    results = []
    if group_id in MONITORED_GROUPS:
        task_id = MONITORED_GROUPS[group_id]
        if task_id is not None:
            results.append((task_id, DIRECT_GROUP_CONFIDENCE))
            log.debug("Layer 2a: %s → %s (conf=%.2f)", group_id, task_id, DIRECT_GROUP_CONFIDENCE)

    # --- Layer 2a+: runtime tasks from task_routing_context ---
    runtime_tasks = _get_runtime_tasks(group_id)
    seen_task_ids = {tid for tid, _ in results}
    for rt_id in runtime_tasks:
        if rt_id not in seen_task_ids:
            results.append((rt_id, RUNTIME_TASK_CONFIDENCE))
            seen_task_ids.add(rt_id)
            log.debug("Layer 2a+: runtime task %s for group %s (conf=%.2f)",
                      rt_id, group_id, RUNTIME_TASK_CONFIDENCE)

    if results:
        return results

    # --- Layer 2b: entity keyword + alias matching ---
    entity_matches = match_entities(body)
    if entity_matches:
        active_tasks = {t["id"]: t for t in get_active_tasks()}
        for entity_id, match_confidence in entity_matches:
            for task in active_tasks.values():
                supplier_ids = json.loads(task.get("supplier_ids") or "[]")
                if task["client_id"] == entity_id or entity_id in supplier_ids:
                    confidence = min(match_confidence, ENTITY_MATCH_CONFIDENCE)
                    if task["id"] not in seen_task_ids:
                        results.append((task["id"], confidence))
                        seen_task_ids.add(task["id"])
                        log.debug("Layer 2b: entity=%s → task=%s (conf=%.2f)",
                                  entity_id, task["id"], confidence)
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
