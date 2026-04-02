"""
Conversation manager — assigns scraps to conversations with forward propagation.

Core mechanism:
1. Process scraps chronologically
2. Scraps with entity evidence → find/create conversation for that entity
3. Scraps without evidence → buffer in sender's open strand
4. When evidence arrives, propagate backward to buffered scraps in the same burst
5. Subsequent messages from same sender continue in same conversation until evidence changes
"""

import logging
import uuid
import time
from dataclasses import dataclass, field

from src.conversation.scrap_detector import Scrap, is_payment_message
from src.conversation.item_matcher import resolve_scrap_entity_by_items

log = logging.getLogger(__name__)


@dataclass
class Conversation:
    """A task-oriented conversation in a shared group."""
    id: str
    group_id: str
    entity_ref: str           # entity_id or "unit:20_jak" or "supplier:arihant"
    conv_type: str = "order"  # "order", "singleton", "ephemeral"
    status: str = "active"
    scraps: list[Scrap] = field(default_factory=list)
    created_at: int = 0
    last_activity: int = 0

    def add_scrap(self, scrap: Scrap):
        self.scraps.append(scrap)
        self.last_activity = max(self.last_activity, scrap.last_msg_ts)


@dataclass
class SenderState:
    """Tracks the current conversation context for a sender."""
    sender_jid: str
    current_entity_ref: str | None = None   # what entity this sender is currently talking about
    buffered_scraps: list[Scrap] = field(default_factory=list)  # unassigned scraps


BOOKKEEPING_ENTITY_REF = "singleton:bookkeeping"


def build_conversations(scraps: list[Scrap], group_id: str,
                        task_items: dict[str, list[dict]] | None = None,
                        task_entities: dict[str, str] | None = None) -> list[Conversation]:
    """
    Assign scraps to conversations using forward propagation.

    Process scraps chronologically. When a scrap has entity evidence,
    it establishes (or continues) a conversation. Preceding unassigned
    scraps from the same sender are retroactively assigned.

    Subsequent scraps from the same sender without evidence continue
    in the same conversation until a different entity is detected.

    When a scrap has no entity evidence from regex/alias, falls back to
    item-based matching via resolve_scrap_entity_by_items().

    Payment messages are dual-assigned to both the order conversation
    (if identifiable) AND the bookkeeping singleton conversation.

    Returns list of Conversations with their assigned scraps.
    """
    task_items = task_items or {}
    task_entities = task_entities or {}

    conversations: dict[str, Conversation] = {}  # entity_ref → Conversation
    sender_states: dict[str, SenderState] = {}   # sender_jid → SenderState
    assignments: dict[str, list[str]] = {}       # scrap_id → [conversation_ids]

    # Sort scraps by first message timestamp
    sorted_scraps = sorted(scraps, key=lambda s: s.first_msg_ts)

    for scrap in sorted_scraps:
        sender = scrap.sender_jid
        if sender not in sender_states:
            sender_states[sender] = SenderState(sender_jid=sender)
        state = sender_states[sender]

        # ── Item-matcher fallback (Task 2) ───────────────────────
        # If scrap has no entity evidence, try item-based matching
        if not scrap.entity_matches and task_items:
            scrap_text = " ".join(
                (m.get("body") or "") for m in scrap.messages
            )
            resolved = resolve_scrap_entity_by_items(
                scrap_text, task_items, task_entities
            )
            if resolved:
                scrap.entity_matches = [resolved]
                log.debug("Item-match fallback: scrap %s → %s", scrap.id, resolved)

        if scrap.entity_matches:
            # Scrap has evidence — assign to conversation(s)
            for entity_ref in scrap.entity_matches:
                conv = _get_or_create_conversation(
                    conversations, entity_ref, scrap.group_id, scrap.first_msg_ts
                )
                conv.add_scrap(scrap)

                # Track assignment
                if scrap.id not in assignments:
                    assignments[scrap.id] = []
                assignments[scrap.id].append(conv.id)

            # Update sender's current context
            state.current_entity_ref = scrap.entity_matches[0]

            # Backward propagation: assign RECENT buffered scraps to this conversation
            # Only propagate to scraps within BACKPROP_MAX_S of the entity evidence.
            # Older buffered scraps stay unassigned — prevents junk conversations
            # from accumulating hours of unrelated messages.
            BACKPROP_MAX_S = 3600  # 1 hour — balance between coverage and coherence
            if state.buffered_scraps:
                primary_ref = scrap.entity_matches[0]
                conv = conversations[primary_ref]
                recent = []
                stale = []
                for buffered in state.buffered_scraps:
                    gap = scrap.first_msg_ts - buffered.last_msg_ts
                    if gap <= BACKPROP_MAX_S:
                        recent.append(buffered)
                    else:
                        stale.append(buffered)

                for buffered in recent:
                    conv.add_scrap(buffered)
                    if buffered.id not in assignments:
                        assignments[buffered.id] = []
                    assignments[buffered.id].append(conv.id)
                    buffered.status = "assigned"
                    log.debug("Backward propagation: scrap %s → conversation %s",
                              buffered.id, conv.id)

                if stale:
                    log.debug("Dropped %d stale buffered scraps (>%ds old)",
                              len(stale), BACKPROP_MAX_S)
                state.buffered_scraps.clear()

            scrap.status = "assigned"

        elif state.current_entity_ref:
            # No evidence, but sender has a current context — continue
            # Check time gap — if too long, don't carry forward
            if state.buffered_scraps:
                last_ts = state.buffered_scraps[-1].last_msg_ts
            else:
                # Find the last assigned scrap from this sender
                last_ts = 0
                for s in reversed(sorted_scraps):
                    if s.sender_jid == sender and s.status == "assigned":
                        last_ts = s.last_msg_ts
                        break

            gap = scrap.first_msg_ts - last_ts if last_ts else float("inf")

            if gap <= 1800:  # 30 min — still in same context
                conv = conversations[state.current_entity_ref]
                conv.add_scrap(scrap)
                if scrap.id not in assignments:
                    assignments[scrap.id] = []
                assignments[scrap.id].append(conv.id)
                scrap.status = "assigned"
            else:
                # Too much time passed — buffer for future evidence
                state.current_entity_ref = None
                state.buffered_scraps.append(scrap)

        else:
            # No evidence, no current context — buffer
            state.buffered_scraps.append(scrap)

        # ── Bookkeeping singleton (Task 3) ───────────────────────
        # Payment messages are dual-assigned to the bookkeeping conversation
        scrap_text = " ".join((m.get("body") or "") for m in scrap.messages)
        if is_payment_message(scrap_text):
            bk_conv = _get_or_create_conversation(
                conversations, BOOKKEEPING_ENTITY_REF, group_id, scrap.first_msg_ts
            )
            bk_conv.conv_type = "singleton"
            bk_conv.add_scrap(scrap)
            if scrap.id not in assignments:
                assignments[scrap.id] = []
            assignments[scrap.id].append(bk_conv.id)
            log.debug("Payment dual-assign: scrap %s → bookkeeping", scrap.id)

    # Stats
    assigned_count = sum(1 for s in sorted_scraps if s.status == "assigned")
    buffered_count = sum(len(ss.buffered_scraps) for ss in sender_states.values())
    log.info("Conversations: %d, scraps assigned: %d, buffered: %d, total: %d",
             len(conversations), assigned_count, buffered_count, len(sorted_scraps))

    return list(conversations.values())


def build_conversations_from_threads(
    threaded_messages: list,  # list[ThreadedMessage] from reply_tree
    messages: list[dict],     # original message dicts (for body/timestamp access)
    group_id: str,
    task_items: dict[str, list[dict]] | None = None,
    task_entities: dict[str, str] | None = None,
) -> list[Conversation]:
    """
    Build conversations from reply-tree threads instead of sender scraps.

    Each reply-tree thread becomes a candidate conversation. Entity evidence
    from ANY message in the thread covers the entire thread — this is the
    key advantage over per-sender scraps.

    For threads without entity evidence, falls back to item matching.
    """
    from src.conversation.reply_tree import ThreadedMessage
    from src.conversation.scrap_detector import extract_entity_refs, is_payment_message
    from src.conversation.item_matcher import resolve_scrap_entity_by_items

    task_items = task_items or {}
    task_entities = task_entities or {}

    # Sender carry-forward: once a sender mentions an entity, subsequent
    # threads involving that sender inherit the entity until:
    #   (a) a DIFFERENT entity is detected, or
    #   (b) a long idle gap passes (CARRY_RESET_S)
    # This matches the scrap model's carry-forward behavior.
    CARRY_RESET_S = 3600  # 1 hour — reset if sender idle for this long
    sender_ctx: dict[str, dict] = {}  # sender → {"entity_ref": str, "last_ts": int}

    # Group threaded messages by thread_id
    threads: dict[int, list[ThreadedMessage]] = {}
    for tm in threaded_messages:
        threads.setdefault(tm.thread_id, []).append(tm)

    # Build message dict lookup for original data
    msg_by_id = {m.get("message_id"): m for m in messages}

    conversations: dict[str, Conversation] = {}

    for tid, thread_msgs in sorted(threads.items()):
        # Collect entity evidence from ALL messages in the thread
        thread_entities: set[str] = set()
        thread_text_parts = []
        original_msgs = []

        for tm in thread_msgs:
            orig = msg_by_id.get(tm.message_id, {})
            original_msgs.append(orig)
            body = tm.body
            if body:
                thread_text_parts.append(body)
                for ref in extract_entity_refs(body):
                    thread_entities.add(ref["ref"])

        # If no entity from text, try item matching
        if not thread_entities and task_items:
            combined_text = " ".join(thread_text_parts)
            resolved = resolve_scrap_entity_by_items(
                combined_text, task_items, task_entities
            )
            if resolved:
                thread_entities.add(resolved)
                log.debug("Thread %d: item-match → %s", tid, resolved)

        # Create a scrap to wrap the thread (for conversation compatibility)
        scrap = Scrap(
            id=f"thread_{tid}",
            group_id=group_id,
            sender_jid=thread_msgs[0].sender,  # root sender
            entity_matches=list(thread_entities),
            status="assigned" if thread_entities else "open",
        )
        for orig in original_msgs:
            scrap.add_message(orig)

        if thread_entities:
            # Assign to conversation(s)
            for entity_ref in thread_entities:
                conv = _get_or_create_conversation(
                    conversations, entity_ref, group_id,
                    thread_msgs[0].timestamp,
                )
                conv.add_scrap(scrap)

            # Update sender context for ALL senders in this thread
            for tm in thread_msgs:
                sender_ctx[tm.sender] = {
                    "entity_ref": list(thread_entities)[0],
                    "last_ts": tm.timestamp,
                }

        elif not thread_entities:
            # No direct evidence — try sender carry-forward
            # If any sender in the thread has a current entity context
            # (not expired by CARRY_RESET_S), assign the whole thread
            best_carry = None
            for tm in thread_msgs:
                ctx = sender_ctx.get(tm.sender)
                if ctx and (tm.timestamp - ctx["last_ts"]) <= CARRY_RESET_S:
                    best_carry = ctx["entity_ref"]
                    break

            if best_carry:
                conv = _get_or_create_conversation(
                    conversations, best_carry, group_id,
                    thread_msgs[0].timestamp,
                )
                conv.add_scrap(scrap)
                scrap.status = "assigned"
                scrap.entity_matches = [best_carry]
                # Update context for all senders in thread
                for tm in thread_msgs:
                    sender_ctx[tm.sender] = {
                        "entity_ref": best_carry,
                        "last_ts": tm.timestamp,
                    }
                log.debug("Thread %d: carry-forward from sender → %s", tid, best_carry)

        # Payment dual-assign
        combined = " ".join(thread_text_parts)
        if is_payment_message(combined):
            bk_conv = _get_or_create_conversation(
                conversations, BOOKKEEPING_ENTITY_REF, group_id,
                thread_msgs[0].timestamp,
            )
            bk_conv.conv_type = "singleton"
            bk_conv.add_scrap(scrap)

    assigned_count = sum(
        1 for tid in threads
        if any(f"thread_{tid}" == s.id
               for c in conversations.values() for s in c.scraps)
    )

    log.info("Thread-based conversations: %d conversations from %d threads "
             "(%d assigned, %d unassigned)",
             len(conversations), len(threads), assigned_count,
             len(threads) - assigned_count)

    return list(conversations.values())


def _get_or_create_conversation(conversations: dict[str, Conversation],
                                 entity_ref: str, group_id: str,
                                 timestamp: int) -> Conversation:
    """Get existing conversation for entity_ref, or create a new one."""
    if entity_ref not in conversations:
        conv = Conversation(
            id=f"conv_{uuid.uuid4().hex[:8]}",
            group_id=group_id,
            entity_ref=entity_ref,
            conv_type=_infer_conv_type(entity_ref),
            created_at=timestamp,
            last_activity=timestamp,
        )
        conversations[entity_ref] = conv
        log.info("New conversation: %s for %s", conv.id, entity_ref)
    return conversations[entity_ref]


def _infer_conv_type(entity_ref: str) -> str:
    """Infer conversation type from entity reference."""
    if entity_ref.startswith("singleton:"):
        return "singleton"
    if entity_ref.startswith("unit:"):
        return "order"  # army unit → client order
    if entity_ref.startswith("supplier:"):
        return "order"  # supplier → supplier order
    return "order"  # default
