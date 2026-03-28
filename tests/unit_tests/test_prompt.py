"""
Unit tests for agent/prompt.py build functions.
No LLM, no DB.
"""

import json
import pytest

from src.agent.prompt import build_user_section


def _make_node(task_id, node_id, status="pending"):
    return {
        "id": f"{task_id}_{node_id}",
        "task_id": task_id,
        "name": node_id.replace("_", " ").title(),
        "status": status,
    }


# ---------------------------------------------------------------------------
# build_user_section — node_id derivation
# ---------------------------------------------------------------------------

class TestNodeIdDerivation:

    def test_simple_node_id_extracted(self):
        nodes = [_make_node("task_001", "supplier_QC", "active")]
        section = build_user_section(nodes, [], {"body": "test", "timestamp": 1})
        data = json.loads(section.split("## Current node states\n\n")[1].split("\n\n")[0])
        assert data[0]["node_id"] == "supplier_QC"

    def test_node_id_not_mangled_to_lowercase(self):
        # Regression: old code did name.lower().replace(" ", "_") which broke supplier_QC → supplier_qc
        nodes = [_make_node("task_001", "supplier_QC", "completed")]
        section = build_user_section(nodes, [], {"body": "x", "timestamp": 1})
        assert "supplier_QC" in section
        assert "supplier_qc" not in section

    def test_multiple_nodes_all_extracted(self):
        nodes = [
            _make_node("task_001", "client_enquiry", "completed"),
            _make_node("task_001", "order_confirmation", "active"),
            _make_node("task_001", "supplier_QC", "pending"),
        ]
        section = build_user_section(nodes, [], {"body": "x", "timestamp": 1})
        assert "client_enquiry" in section
        assert "order_confirmation" in section
        assert "supplier_QC" in section


# ---------------------------------------------------------------------------
# build_user_section — items block
# ---------------------------------------------------------------------------

class TestItemsBlock:

    def test_no_items_omits_section(self):
        section = build_user_section([], [], {"body": "x", "timestamp": 1})
        assert "Current order items" not in section

    def test_items_present_shows_section(self):
        items = [{"description": "atta 50kg", "unit": "bags", "quantity": 50, "specs": None}]
        section = build_user_section([], [], {"body": "x", "timestamp": 1}, current_items=items)
        assert "Current order items" in section
        assert "atta 50kg" in section

    def test_items_shows_quantity(self):
        items = [{"description": "dal", "unit": "bags", "quantity": 30, "specs": None}]
        section = build_user_section([], [], {"body": "x", "timestamp": 1}, current_items=items)
        assert "30" in section

    def test_empty_items_list_omits_section(self):
        section = build_user_section([], [], {"body": "x", "timestamp": 1}, current_items=[])
        assert "Current order items" not in section


# ---------------------------------------------------------------------------
# build_user_section — image flag
# ---------------------------------------------------------------------------

class TestImageFlag:

    def test_image_path_adds_note(self):
        msg = {"body": "", "timestamp": 1, "image_path": "data/example_screenshots/photo.jpg"}
        section = build_user_section([], [], msg)
        assert "IMAGE ATTACHED" in section

    def test_no_image_no_note(self):
        msg = {"body": "normal text", "timestamp": 1}
        section = build_user_section([], [], msg)
        assert "IMAGE ATTACHED" not in section

    def test_empty_body_with_image_shows_no_text(self):
        msg = {"body": "", "timestamp": 1, "image_path": "photo.jpg"}
        section = build_user_section([], [], msg)
        assert "(no text)" in section

    def test_body_with_text_shows_text(self):
        msg = {"body": "payment done", "timestamp": 1}
        section = build_user_section([], [], msg)
        assert "payment done" in section


# ---------------------------------------------------------------------------
# build_user_section — messages
# ---------------------------------------------------------------------------

class TestMessagesSection:

    def test_no_recent_messages_shows_none(self):
        section = build_user_section([], [], {"body": "x", "timestamp": 1})
        assert "(none)" in section

    def test_recent_messages_included(self):
        msgs = [{"timestamp": 1000, "sender_jid": "ashish@s.whatsapp.net",
                 "group_id": "grp@g.us", "body": "kapoor aayenge"}]
        section = build_user_section([], msgs, {"body": "x", "timestamp": 1})
        assert "kapoor aayenge" in section
        assert "ashish@s.whatsapp.net" in section
