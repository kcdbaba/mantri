"""
Conversation router — bridges conversation analysis to the pipeline.

Maintains per-group message buffers. When a burstiness gap is detected
(no new messages for FLUSH_GAP_S seconds), closes the buffer and runs
scrap detection + conversation building. Returns entity assignments
for the accumulated messages.

Used by the main router for shared groups (MONITORED_GROUPS value = null).

Usage:
    router = ConversationRouter()
    result = router.feed(message)
    # result is None (buffered) or ConversationResult (conversation closed)

    # Periodically flush idle buffers:
    stale = router.flush_stale()
"""

import logging
import time
from dataclasses import dataclass, field

from src.conversation.scrap_detector import detect_scraps
from src.conversation.reply_tree import build_reply_tree
from src.conversation.conversation_manager import (
    build_conversations, build_conversations_from_threads, Conversation,
)
from src.store.task_store import get_active_tasks

log = logging.getLogger(__name__)

# Gap after which a buffer is considered idle and flushed
FLUSH_GAP_S = 300  # 5 minutes — matches the burst analysis findings

# OCR cache files per case directory
_ocr_cache: dict[str, dict] = {}  # group_id → {message_id: ocr_data}


def load_ocr_cache(ocr_path: str) -> dict:
    """Load OCR results from a JSON file. Returns {message_id: ocr_data}."""
    try:
        import json
        with open(ocr_path) as f:
            data = json.load(f)
        return data.get("images", {})
    except Exception as e:
        log.debug("No OCR cache at %s: %s", ocr_path, e)
        return {}


def set_ocr_cache(group_id: str, cache: dict):
    """Set OCR cache for a group. Called externally before routing."""
    _ocr_cache[group_id] = cache


def _enrich_with_ocr(messages: list[dict], group_id: str) -> list[dict]:
    """
    Enrich empty messages with OCR-extracted text from cached results.

    For messages with no body but an OCR entry, injects the extracted_text
    and entities into the message dict so downstream scrap detection can
    use them. Does NOT modify the original message — creates a shallow copy.
    """
    cache = _ocr_cache.get(group_id, {})
    if not cache:
        return messages

    enriched = []
    n_enriched = 0
    for msg in messages:
        mid = msg.get("message_id", "")
        body = (msg.get("body") or "").strip()

        if mid in cache:
            ocr = cache[mid]
            ocr_text = ocr.get("extracted_text", "")
            ocr_desc = ocr.get("description", "")

            # Always combine: body text + OCR text + OCR description
            # Never drop either — both are valuable signals
            # Strip principal entity names from OCR text to prevent false routing
            from src.conversation.scrap_detector import PRINCIPAL_ENTITIES
            def _strip_principal(text):
                lower = text.lower()
                for p in PRINCIPAL_ENTITIES:
                    idx = lower.find(p)
                    if idx >= 0:
                        text = text[:idx] + text[idx + len(p):]
                        lower = text.lower()
                return text.strip()

            parts = []
            if body:
                parts.append(body)
            if ocr_text:
                parts.append(_strip_principal(ocr_text))
            if ocr_desc and ocr_desc != ocr_text:
                parts.append(_strip_principal(ocr_desc))

            if parts:
                combined = " | ".join(parts)
                if combined != body:
                    msg = dict(msg)
                    msg["body"] = combined
                    msg["_ocr_enriched"] = True
                    msg["_ocr_category"] = ocr.get("category", "")
                    n_enriched += 1

        enriched.append(msg)

    if n_enriched:
        log.info("OCR enrichment: %d messages enriched from cache", n_enriched)

    return enriched


@dataclass
class ConversationResult:
    """Result of closing a conversation buffer."""
    group_id: str
    conversations: list[Conversation]
    unassigned_messages: list[dict]
    total_messages: int
    discovered_entities: list = field(default_factory=list)


@dataclass
class GroupBuffer:
    """Buffered messages for a shared group awaiting conversation analysis."""
    group_id: str
    messages: list[dict] = field(default_factory=list)
    last_msg_ts: int = 0

    def add(self, msg: dict):
        self.messages.append(msg)
        self.last_msg_ts = msg.get("timestamp", int(time.time()))


class ConversationRouter:
    """
    Stateful router for shared groups. Accumulates messages and runs
    conversation analysis when a burstiness gap is detected.
    """

    def __init__(self, flush_gap_s: int = FLUSH_GAP_S,
                 enable_llm_matching: bool = False,
                 preloaded_node_states: dict | None = None,
                 preloaded_task_entities: dict | None = None):
        self.flush_gap_s = flush_gap_s
        self.enable_llm_matching = enable_llm_matching
        self.preloaded_node_states = preloaded_node_states  # {task_id: [node_dicts]}
        self.preloaded_task_entities = preloaded_task_entities  # {task_id: entity_ref}
        self._buffers: dict[str, GroupBuffer] = {}  # group_id → buffer

    def feed(self, message: dict) -> ConversationResult | None:
        """
        Feed a message from a shared group.

        If adding this message triggers a flush (gap since last message
        exceeds flush_gap_s), returns a ConversationResult for the
        previous buffer. The new message starts a fresh buffer.

        If no flush is triggered, returns None (message buffered).
        """
        group_id = message.get("group_id", "")
        msg_ts = message.get("timestamp", int(time.time()))

        result = None

        if group_id in self._buffers:
            buf = self._buffers[group_id]
            gap = msg_ts - buf.last_msg_ts

            if gap > self.flush_gap_s and buf.messages:
                # Flush the previous buffer
                result = self._flush_buffer(buf)
                # Start fresh buffer with the new message
                self._buffers[group_id] = GroupBuffer(group_id=group_id)

        if group_id not in self._buffers:
            self._buffers[group_id] = GroupBuffer(group_id=group_id)

        self._buffers[group_id].add(message)
        return result

    def flush_stale(self, now: int | None = None) -> list[ConversationResult]:
        """
        Flush any buffers that have been idle for more than flush_gap_s.
        Called periodically by the worker loop.
        """
        now = now or int(time.time())
        results = []
        stale_groups = []

        for group_id, buf in self._buffers.items():
            if buf.messages and (now - buf.last_msg_ts) > self.flush_gap_s:
                stale_groups.append(group_id)

        for group_id in stale_groups:
            buf = self._buffers.pop(group_id)
            result = self._flush_buffer(buf)
            results.append(result)

        return results

    def flush_all(self) -> list[ConversationResult]:
        """Flush all buffers regardless of age. Used at end of replay."""
        results = []
        for group_id in list(self._buffers.keys()):
            buf = self._buffers.pop(group_id)
            if buf.messages:
                results.append(self._flush_buffer(buf))
        return results

    def _flush_buffer(self, buf: GroupBuffer) -> ConversationResult:
        """Run conversation analysis on a buffer and return results.

        Uses reply-tree threading as the primary grouping mechanism.
        Entity evidence from any message in a thread covers the whole thread.
        Falls back to scrap-based analysis if reply tree produces fewer results.
        """
        log.info("Flushing conversation buffer: group=%s, %d messages",
                 buf.group_id, len(buf.messages))

        # Enrich empty messages with OCR text if available
        buf.messages = _enrich_with_ocr(buf.messages, buf.group_id)

        # Get active task items for item-based matching
        task_items = {}
        task_entities = {}
        try:
            tasks = get_active_tasks()
            for task in tasks:
                tid = task["id"]
                from src.store.task_store import get_order_items
                items = get_order_items(tid)
                if items:
                    task_items[tid] = items
                task_entities[tid] = task.get("client_id", "")
        except Exception as e:
            log.warning("Could not load task items for conversation matching: %s", e)

        # Step 1: Scrap-based conversation building (per-sender, with carry-forward)
        # This gets ~77% coverage through sender context propagation
        scraps = detect_scraps(buf.messages, buf.group_id)
        conversations = build_conversations(
            scraps, buf.group_id,
            task_items=task_items,
            task_entities=task_entities,
        )

        # Step 2: Reply-tree cross-sender enhancement
        # Find unassigned messages, check if reply tree links them to assigned messages
        text_msgs = [m for m in buf.messages if (m.get("body") or "").strip()]
        if text_msgs:
            threaded = build_reply_tree(text_msgs)
            conversations = self._enhance_with_reply_tree(
                conversations, scraps, threaded, buf.messages, buf.group_id
            )

        # Step 2.5: Date-based deterministic matching
        # Match unassigned scraps to conversations by correlating message
        # timestamps with known order timeline events (delivery dates, etc.)
        conversations = self._enhance_with_date_matching(
            conversations, scraps, task_entities, buf.group_id
        )

        # Step 3: LLM backward context matching
        # For each assigned scrap with entity evidence, look backward within
        # 16 working hours for unassigned scraps and ask LLM to judge relevance
        if self.enable_llm_matching:
            conversations = self._enhance_with_llm_context(
                conversations, scraps, buf.group_id
            )

        # Step 4: Entity learning — discover new entities from all messages
        # (runs on ALL messages, not just unassigned, because entity
        # introductions often appear in assigned contexts too)
        from src.conversation.entity_learner import discover_entities
        try:
            from src.router.alias_dict import get_all_aliases
            known = set(get_all_aliases().keys())
        except Exception:
            known = set()
        discovered = discover_entities(buf.messages, known_aliases=known)
        if discovered:
            log.info("Entity learning: %d new entities discovered", len(discovered))

        # Collect unassigned messages
        assigned_msg_ids = set()
        for conv in conversations:
            for scrap in conv.scraps:
                for msg in scrap.messages:
                    assigned_msg_ids.add(msg.get("message_id"))

        unassigned = [
            m for m in buf.messages
            if m.get("message_id") not in assigned_msg_ids
        ]

        log.info("Conversation flush: %d conversations, %d assigned msgs, %d unassigned",
                 len(conversations), len(assigned_msg_ids), len(unassigned))

        return ConversationResult(
            group_id=buf.group_id,
            conversations=conversations,
            unassigned_messages=unassigned,
            total_messages=len(buf.messages),
            discovered_entities=discovered,
        )

    @staticmethod
    def _enhance_with_reply_tree(conversations, scraps, threaded, all_messages, group_id):
        """
        Use reply tree to assign unassigned messages via cross-sender threading.

        If an unassigned message is in the same reply-tree thread as an assigned
        message, inherit the assignment. This captures cross-sender context that
        per-sender scraps miss (e.g., Ashish replies to Sammsul's entity mention).
        """
        from src.conversation.scrap_detector import Scrap

        # Build message_id → entity_ref lookup from current assignments
        msg_to_entity = {}
        for conv in conversations:
            for scrap in conv.scraps:
                for msg in scrap.messages:
                    mid = msg.get("message_id")
                    if mid:
                        msg_to_entity[mid] = conv.entity_ref

        # Build thread_id → message_ids from reply tree
        thread_msgs = {}
        for tm in threaded:
            thread_msgs.setdefault(tm.thread_id, []).append(tm)

        # For each thread, check if some messages are assigned and some aren't
        newly_assigned = 0
        msg_by_id = {m.get("message_id"): m for m in all_messages}

        for tid, tms in thread_msgs.items():
            assigned_entities = set()
            unassigned_mids = []

            for tm in tms:
                if tm.message_id in msg_to_entity:
                    assigned_entities.add(msg_to_entity[tm.message_id])
                else:
                    unassigned_mids.append(tm.message_id)

            if assigned_entities and unassigned_mids:
                # Thread has both assigned and unassigned — propagate
                entity_ref = list(assigned_entities)[0]  # pick the first
                # Find the conversation for this entity
                target_conv = None
                for conv in conversations:
                    if conv.entity_ref == entity_ref:
                        target_conv = conv
                        break

                if target_conv:
                    # Create a scrap for the newly assigned messages
                    new_scrap = Scrap(
                        id=f"reply_thread_{tid}",
                        group_id=group_id,
                        sender_jid="(cross-sender)",
                        entity_matches=[entity_ref],
                        status="assigned",
                    )
                    for mid in unassigned_mids:
                        orig = msg_by_id.get(mid, {})
                        if orig:
                            new_scrap.add_message(orig)
                            msg_to_entity[mid] = entity_ref
                            newly_assigned += 1

                    if new_scrap.messages:
                        target_conv.add_scrap(new_scrap)

        if newly_assigned:
            log.info("Reply-tree enhancement: %d messages newly assigned via cross-sender threads",
                     newly_assigned)

        return conversations

    @staticmethod
    def _build_order_context(conversations) -> dict[str, str]:
        """Build compact order context summaries for each entity's conversation.

        Pulls items, stage info, and key messages from the conversation scraps
        to give the LLM context about what this order is about.
        """
        ctx = {}
        for conv in conversations:
            if conv.conv_type == "singleton":
                continue

            parts = []
            parts.append(f"Entity: {conv.entity_ref}")
            parts.append(f"Type: {conv.conv_type}")

            # Extract key content from conversation scraps
            items_mentioned = set()
            key_messages = []
            for s in conv.scraps:
                for m in s.messages:
                    body = (m.get("body") or "").strip()
                    if body and len(body) > 5:
                        key_messages.append(body[:60])
                        # Look for item-like mentions
                        words = body.lower().split()
                        for w in words:
                            if len(w) > 3 and w not in {"from", "sir", "haan", "bhai",
                                                         "main", "this", "that", "item"}:
                                items_mentioned.add(w)

            if key_messages:
                parts.append(f"Messages so far: {' | '.join(key_messages[:5])}")

            # Try to get task items from DB
            try:
                from src.store.task_store import get_active_tasks, get_order_items, get_node_states
                tasks = get_active_tasks()
                for task in tasks:
                    if task.get("client_id") == conv.entity_ref or conv.entity_ref in str(task.get("supplier_ids", [])):
                        tid = task["id"]
                        items = get_order_items(tid)
                        if items:
                            item_descs = [it.get("description", "")[:40] for it in items[:5]]
                            parts.append(f"Order items: {', '.join(item_descs)}")
                        # Get delivery-related node states
                        nodes = get_node_states(tid)
                        for n in nodes:
                            nid = n["id"].split("_", 1)[-1] if "_" in n["id"] else n["id"]
                            if nid in ("dispatched", "delivery_confirmed", "supplier_collection",
                                       "order_ready") and n["status"] not in ("pending", "skipped"):
                                parts.append(f"Stage: {nid}={n['status']}")
                        break
            except Exception:
                pass

            ctx[conv.entity_ref] = "\n".join(parts)

        return ctx

    def _enhance_with_date_matching(self, conversations, scraps, task_entities, group_id):
        """
        Match unassigned scraps to conversations by date proximity to
        known order timeline events (delivery dates, required-by dates).
        """
        from src.conversation.date_matcher import extract_timeline, match_by_date
        from src.conversation.scrap_detector import Scrap

        # Build assigned set
        assigned_ids = set()
        conv_by_entity = {}
        for conv in conversations:
            conv_by_entity[conv.entity_ref] = conv
            for s in conv.scraps:
                assigned_ids.add(s.id)

        # Get unassigned scraps with text
        unassigned = [s for s in scraps if s.id not in assigned_ids]
        if not unassigned:
            return conversations

        # Extract timeline from task node_data
        node_states = {}
        if self.preloaded_node_states:
            node_states = self.preloaded_node_states
        else:
            try:
                from src.store.task_store import get_node_states
                for task_id in task_entities:
                    nodes = get_node_states(task_id)
                    if nodes:
                        node_states[task_id] = [dict(n) for n in nodes]
            except Exception as e:
                log.debug("Could not load node states for date matching: %s", e)
                return conversations

        # Use preloaded entity mapping if available (has all tasks including
        # those created during replay), fall back to _flush_buffer's task_entities
        effective_entities = self.preloaded_task_entities or task_entities
        timeline = extract_timeline([], node_states, effective_entities)
        if not timeline:
            return conversations

        # Run date matching
        date_matches = match_by_date(unassigned, timeline)

        # Apply matches
        scrap_by_id = {s.id: s for s in scraps}
        for dm in date_matches:
            matched_scrap = scrap_by_id.get(dm.scrap_id)
            if not matched_scrap:
                continue

            # Find or create conversation for this entity
            conv = conv_by_entity.get(dm.entity_ref)
            if not conv:
                from src.conversation.conversation_manager import Conversation
                import uuid
                conv = Conversation(
                    id=f"conv_{uuid.uuid4().hex[:8]}",
                    group_id=group_id,
                    entity_ref=dm.entity_ref,
                    conv_type="order",
                    created_at=matched_scrap.first_msg_ts,
                    last_activity=matched_scrap.last_msg_ts,
                )
                conversations.append(conv)
                conv_by_entity[dm.entity_ref] = conv

            conv.add_scrap(matched_scrap)
            matched_scrap.status = "assigned"
            assigned_ids.add(matched_scrap.id)

        if date_matches:
            log.info("Date matching: %d scraps newly assigned", len(date_matches))

        return conversations

    def _enhance_with_llm_context(self, conversations, scraps, group_id):
        """
        Use LLM to match unassigned scraps to conversations by backward context.

        For each assigned scrap with entity evidence, looks backward within
        16 working hours for unassigned scraps and asks Gemini Flash to judge.
        """
        from src.conversation.llm_context_matcher import match_backward_context
        from src.conversation.scrap_detector import Scrap

        # Build assigned scrap list with entity refs
        assigned_ids = set()
        assigned_with_entity = []
        conv_by_entity = {}
        for conv in conversations:
            conv_by_entity[conv.entity_ref] = conv
            for s in conv.scraps:
                assigned_ids.add(s.id)
                if s.entity_matches:
                    assigned_with_entity.append((s, conv.entity_ref))

        if not assigned_with_entity:
            return conversations

        # Build order context summaries for each entity
        order_ctx = self._build_order_context(conversations)

        # Run LLM matching
        new_matches = match_backward_context(
            assigned_with_entity, scraps, assigned_ids,
            order_context=order_ctx,
        )

        # Apply matches — add matched scraps to their conversations
        scrap_by_id = {s.id: s for s in scraps}
        for scrap_id, entity_ref in new_matches:
            matched_scrap = scrap_by_id.get(scrap_id)
            if not matched_scrap:
                continue
            conv = conv_by_entity.get(entity_ref)
            if conv:
                conv.add_scrap(matched_scrap)
                matched_scrap.status = "assigned"
                log.info("LLM context: scrap %s → %s", scrap_id, entity_ref)

        if new_matches:
            log.info("LLM context matching: %d scraps newly assigned", len(new_matches))

        return conversations

    def get_entity_routes(self, result: ConversationResult) -> list[tuple[str, float]]:
        """
        Extract entity routing from a ConversationResult.
        Returns [(entity_ref, confidence)] — same format as router.route().
        """
        routes = []
        seen = set()
        for conv in result.conversations:
            if conv.entity_ref not in seen:
                # Infer confidence from the conversation's scraps
                max_conf = 0.5  # default
                for scrap in conv.scraps:
                    for ref_info in (scrap.entity_matches or []):
                        if isinstance(ref_info, str):
                            # Simple string ref — use default confidence
                            max_conf = max(max_conf, 0.7)
                routes.append((conv.entity_ref, max_conf))
                seen.add(conv.entity_ref)

        return routes
