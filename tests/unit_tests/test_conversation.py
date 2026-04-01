"""
Unit tests for the conversation routing subsystem.

Tests scrap detection, entity extraction, conversation assignment,
item matching, and reply-tree threading.
"""

import pytest
from unittest.mock import patch


# ── Helpers ──────────────────────────────────────────────────────────

def _msg(sender, body, ts, message_id=None):
    """Build a minimal message dict."""
    return {
        "sender_jid": sender,
        "body": body,
        "timestamp": ts,
        "message_id": message_id or f"msg_{ts}",
        "timestamp_raw": str(ts),
    }


# ── Test 1: scrap detection basic ────────────────────────────────────

class TestScrapDetectionBasic:
    """Verify scraps partition by sender and burst."""

    def test_single_sender_single_scrap(self):
        """Messages within gap → one scrap."""
        from src.conversation.scrap_detector import detect_scraps

        msgs = [
            _msg("alice", "hello", 1000),
            _msg("alice", "how are you", 1010),
            _msg("alice", "ok sir", 1020),
        ]
        with patch("src.conversation.scrap_detector.extract_entity_refs", return_value=[]):
            scraps = detect_scraps(msgs, "grp1")

        assert len(scraps) == 1
        assert len(scraps[0].messages) == 3
        assert scraps[0].sender_jid == "alice"

    def test_two_senders_two_scraps(self):
        """Different senders → separate scraps."""
        from src.conversation.scrap_detector import detect_scraps

        msgs = [
            _msg("alice", "order for 20 jak", 1000),
            _msg("bob", "confirmed", 1005),
        ]
        with patch("src.conversation.scrap_detector.extract_entity_refs", return_value=[]):
            scraps = detect_scraps(msgs, "grp1")

        assert len(scraps) == 2
        senders = {s.sender_jid for s in scraps}
        assert senders == {"alice", "bob"}

    def test_burst_gap_splits_scrap(self):
        """Gap > BURST_GAP_S (900s) splits same sender into two scraps."""
        from src.conversation.scrap_detector import detect_scraps, BURST_GAP_S

        msgs = [
            _msg("alice", "first topic", 1000),
            _msg("alice", "still first", 1060),
            _msg("alice", "new topic after gap", 1000 + BURST_GAP_S + 100),
        ]
        with patch("src.conversation.scrap_detector.extract_entity_refs", return_value=[]):
            scraps = detect_scraps(msgs, "grp1")

        assert len(scraps) == 2

    def test_same_sender_continues_within_900s(self):
        """Messages within 900s from same sender → single scrap (not 120s)."""
        from src.conversation.scrap_detector import detect_scraps

        msgs = [
            _msg("alice", "first msg", 1000),
            _msg("alice", "second msg 5 min later", 1300),  # 300s gap
            _msg("alice", "third msg 10 min later", 1600),  # 600s total
        ]
        with patch("src.conversation.scrap_detector.extract_entity_refs", return_value=[]):
            scraps = detect_scraps(msgs, "grp1")

        # All within 900s of each other → one scrap
        assert len(scraps) == 1
        assert len(scraps[0].messages) == 3

    def test_entity_change_splits_scrap(self):
        """Different entity evidence → split even within time window."""
        from src.conversation.scrap_detector import detect_scraps

        msgs = [
            _msg("alice", "order for 20 jak", 1000),
            _msg("alice", "order for 107 bde", 1010),
        ]
        entity_calls = [
            [{"ref": "unit:20_jak", "confidence": 0.85}],
            [{"ref": "unit:107_bde", "confidence": 0.85}],
        ]
        with patch("src.conversation.scrap_detector.extract_entity_refs",
                    side_effect=entity_calls):
            scraps = detect_scraps(msgs, "grp1")

        assert len(scraps) == 2


# ── Test 2: scrap entity detection ───────────────────────────────────

class TestScrapEntityDetection:
    """Verify known units and regex patterns detected."""

    def test_orbat_known_unit_107_bde(self):
        """107 bde should match via ORBAT lookup at confidence 0.85."""
        from src.conversation.scrap_detector import extract_entity_refs

        with patch("src.conversation.scrap_detector.match_entities", return_value=[]):
            refs = extract_entity_refs("delivery to 107 bde tomorrow")

        # Should get ORBAT match (0.85) and/or regex match (0.6)
        ref_strs = [r["ref"] for r in refs]
        # Must contain a unit reference for 107
        unit_refs = [r for r in ref_strs if "107" in r]
        assert len(unit_refs) >= 1

        # Check confidence — ORBAT should give 0.85
        orbat_matches = [r for r in refs if "107" in r["ref"] and r["confidence"] >= 0.85]
        assert len(orbat_matches) >= 1

    def test_regex_fallback_unknown_unit(self):
        """999 bde (not in ORBAT) should match via regex at confidence 0.6."""
        from src.conversation.scrap_detector import extract_entity_refs

        with patch("src.conversation.scrap_detector.match_entities", return_value=[]):
            refs = extract_entity_refs("order from 999 bde")

        ref_strs = [r["ref"] for r in refs]
        unit_refs = [r for r in refs if "999" in r["ref"]]
        assert len(unit_refs) >= 1
        # Regex fallback → 0.6
        assert all(r["confidence"] <= 0.6 for r in unit_refs)

    def test_known_alias_highest_confidence(self):
        """Known alias dict match gets confidence 0.95."""
        from src.conversation.scrap_detector import extract_entity_refs

        with patch("src.conversation.scrap_detector.match_entities",
                    return_value=[("entity_rangia_csd", 0.9)]):
            refs = extract_entity_refs("rangia csd order")

        alias_refs = [r for r in refs if r["ref"] == "entity_rangia_csd"]
        assert len(alias_refs) == 1
        assert alias_refs[0]["confidence"] == 0.95

    def test_supplier_pattern(self):
        """'from arihant' should match supplier pattern."""
        from src.conversation.scrap_detector import extract_entity_refs

        with patch("src.conversation.scrap_detector.match_entities", return_value=[]):
            refs = extract_entity_refs("items from arihant traders")

        ref_strs = [r["ref"] for r in refs]
        supplier_refs = [r for r in ref_strs if r.startswith("supplier:")]
        assert len(supplier_refs) >= 1


# ── Test 3: conversation assignment ──────────────────────────────────

class TestConversationAssignment:
    """Verify scraps assigned to conversations."""

    def test_scrap_with_entity_creates_conversation(self):
        """Scrap with entity evidence → conversation created."""
        from src.conversation.scrap_detector import Scrap
        from src.conversation.conversation_manager import build_conversations

        scrap = Scrap(id="s1", group_id="grp1", sender_jid="alice")
        scrap.add_message(_msg("alice", "20 jak order", 1000))
        scrap.entity_matches = ["unit:20_jak"]

        convs = build_conversations([scrap], "grp1")

        assert len(convs) == 1
        assert convs[0].entity_ref == "unit:20_jak"
        assert len(convs[0].scraps) == 1

    def test_buffered_scrap_assigned_on_evidence(self):
        """Unassigned scrap gets assigned when later scrap provides evidence."""
        from src.conversation.scrap_detector import Scrap
        from src.conversation.conversation_manager import build_conversations

        # First scrap: no evidence
        s1 = Scrap(id="s1", group_id="grp1", sender_jid="alice")
        s1.add_message(_msg("alice", "check the order", 1000))

        # Second scrap: has evidence (same sender, within gap)
        s2 = Scrap(id="s2", group_id="grp1", sender_jid="alice")
        s2.add_message(_msg("alice", "20 jak delivery", 1010))
        s2.entity_matches = ["unit:20_jak"]

        convs = build_conversations([s1, s2], "grp1")

        assert len(convs) == 1
        # Both scraps should be in the conversation (backward propagation)
        assert len(convs[0].scraps) == 2

    def test_payment_dual_assignment(self):
        """Payment scrap is assigned to both order and bookkeeping."""
        from src.conversation.scrap_detector import Scrap
        from src.conversation.conversation_manager import (
            build_conversations, BOOKKEEPING_ENTITY_REF,
        )

        scrap = Scrap(id="s1", group_id="grp1", sender_jid="alice")
        scrap.add_message(_msg("alice", "20 jak payment done via paytm", 1000))
        scrap.entity_matches = ["unit:20_jak"]

        convs = build_conversations([scrap], "grp1")

        # Should have two conversations: order + bookkeeping
        assert len(convs) == 2
        conv_refs = {c.entity_ref for c in convs}
        assert "unit:20_jak" in conv_refs
        assert BOOKKEEPING_ENTITY_REF in conv_refs

        # Bookkeeping conv should be singleton type
        bk = next(c for c in convs if c.entity_ref == BOOKKEEPING_ENTITY_REF)
        assert bk.conv_type == "singleton"

    def test_item_matcher_fallback(self):
        """Scrap with no entity evidence uses item matcher as fallback."""
        from src.conversation.scrap_detector import Scrap
        from src.conversation.conversation_manager import build_conversations

        scrap = Scrap(id="s1", group_id="grp1", sender_jid="alice")
        scrap.add_message(_msg("alice", "need 50 boxes of ghee", 1000))

        task_items = {
            "task_001": [{"description": "Pure ghee 1kg", "quantity": 100}],
        }
        task_entities = {"task_001": "unit:20_jak"}

        with patch("src.conversation.conversation_manager.resolve_scrap_entity_by_items",
                    return_value="unit:20_jak") as mock_resolve:
            convs = build_conversations(
                [scrap], "grp1",
                task_items=task_items,
                task_entities=task_entities,
            )

        mock_resolve.assert_called_once()
        assert len(convs) == 1
        assert convs[0].entity_ref == "unit:20_jak"


# ── Test 4: item matching ────────────────────────────────────────────

class TestItemMatching:
    """Verify fuzzy item matching."""

    def test_exact_item_match(self):
        """Exact item name matches with high score."""
        from src.conversation.item_matcher import match_scrap_to_items

        task_items = {
            "task_001": [{"description": "Mustard Oil 15kg"}],
        }
        task_entities = {"task_001": "unit:20_jak"}

        matches = match_scrap_to_items(
            "mustard oil needed urgently", task_items, task_entities
        )
        assert len(matches) >= 1
        assert matches[0].task_id == "task_001"
        assert matches[0].score >= 85

    def test_no_match_for_unrelated(self):
        """Unrelated text should not match."""
        from src.conversation.item_matcher import match_scrap_to_items

        task_items = {
            "task_001": [{"description": "Mustard Oil 15kg"}],
        }
        task_entities = {"task_001": "unit:20_jak"}

        matches = match_scrap_to_items(
            "vehicle inspection scheduled", task_items, task_entities
        )
        assert len(matches) == 0

    def test_resolve_entity_sole_match(self):
        """resolve_scrap_entity_by_items returns entity when sole match."""
        from src.conversation.item_matcher import resolve_scrap_entity_by_items

        task_items = {
            "task_001": [{"description": "Basmati Rice 25kg"}],
        }
        task_entities = {"task_001": "unit:20_jak"}

        result = resolve_scrap_entity_by_items(
            "basmati rice delivery", task_items, task_entities
        )
        assert result == "unit:20_jak"

    def test_resolve_entity_none_when_no_match(self):
        """resolve_scrap_entity_by_items returns None when nothing matches."""
        from src.conversation.item_matcher import resolve_scrap_entity_by_items

        task_items = {
            "task_001": [{"description": "Basmati Rice 25kg"}],
        }
        task_entities = {"task_001": "unit:20_jak"}

        result = resolve_scrap_entity_by_items(
            "vehicle scheduled", task_items, task_entities
        )
        assert result is None


# ── Test 5: reply tree basic ─────────────────────────────────────────

class TestReplyTreeBasic:
    """Verify parent-child linkage in reply tree."""

    def test_close_reply_links(self):
        """Messages close in time from different senders → parent-child."""
        from src.conversation.reply_tree import build_reply_tree

        msgs = [
            _msg("alice", "20 jak ka order bhejo", 1000),
            _msg("bob", "haan sir bhej diya", 1010),
        ]

        tree = build_reply_tree(msgs)

        assert len(tree) == 2
        assert tree[0].parent_idx is None  # root
        assert tree[1].parent_idx == 0     # reply to alice

    def test_same_sender_continuation(self):
        """Same sender within 120s → continues (parent link)."""
        from src.conversation.reply_tree import build_reply_tree

        msgs = [
            _msg("alice", "packing list check karo", 1000),
            _msg("alice", "aur rate bhi confirm karo", 1060),
        ]

        tree = build_reply_tree(msgs)

        assert len(tree) == 2
        assert tree[1].parent_idx == 0  # same sender continuation

    def test_thread_ids_assigned(self):
        """All messages get thread IDs via connected components."""
        from src.conversation.reply_tree import build_reply_tree

        msgs = [
            _msg("alice", "hello", 1000),
            _msg("bob", "hi", 1010),
            _msg("charlie", "unrelated topic", 5000),  # far away → new thread
        ]

        tree = build_reply_tree(msgs)

        assert all(m.thread_id is not None for m in tree)
        # First two should be in same thread, third separate
        assert tree[0].thread_id == tree[1].thread_id
        assert tree[2].thread_id != tree[0].thread_id

    def test_affirmation_reply(self):
        """Short affirmation from different sender links as reply."""
        from src.conversation.reply_tree import build_reply_tree

        msgs = [
            _msg("alice", "107 bde ka order dispatch karo", 1000),
            _msg("bob", "ok", 1005),
        ]

        tree = build_reply_tree(msgs)

        assert tree[1].parent_idx == 0
        assert tree[1].reply_score >= 2.0  # should score well


# ── Test: payment detection ──────────────────────────────────────────

class TestPaymentDetection:
    """Verify payment keyword detection."""

    def test_payment_keywords(self):
        from src.conversation.scrap_detector import is_payment_message

        assert is_payment_message("money sent via paytm")
        assert is_payment_message("Payment done")
        assert is_payment_message("paid 5000 via UPI")
        assert is_payment_message("bank transfer completed")
        assert is_payment_message("sent via PhonePe")
        assert is_payment_message("₹5000 transferred")

    def test_non_payment(self):
        from src.conversation.scrap_detector import is_payment_message

        assert not is_payment_message("20 jak order confirmed")
        assert not is_payment_message("delivery tomorrow")
