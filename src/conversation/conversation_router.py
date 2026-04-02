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


@dataclass
class ConversationResult:
    """Result of closing a conversation buffer."""
    group_id: str
    conversations: list[Conversation]
    unassigned_messages: list[dict]
    total_messages: int


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

    def __init__(self, flush_gap_s: int = FLUSH_GAP_S):
        self.flush_gap_s = flush_gap_s
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
