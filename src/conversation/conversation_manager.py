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

            # Backward propagation: assign buffered scraps to this conversation
            if state.buffered_scraps:
                primary_ref = scrap.entity_matches[0]
                conv = conversations[primary_ref]
                for buffered in state.buffered_scraps:
                    conv.add_scrap(buffered)
                    if buffered.id not in assignments:
                        assignments[buffered.id] = []
                    assignments[buffered.id].append(conv.id)
                    buffered.status = "assigned"
                    log.debug("Backward propagation: scrap %s → conversation %s",
                              buffered.id, conv.id)
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

            if gap <= 300:  # 5 min — still in same context
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
