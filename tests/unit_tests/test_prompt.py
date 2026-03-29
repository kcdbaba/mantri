"""
Unit tests for agent/prompt.py build functions.
No LLM, no DB.
"""

import json
import pytest
import allure
from unittest.mock import patch

from src.agent.prompt import build_user_section, build_system_prompt


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

@allure.feature("Prompt Building")
@allure.story("Node ID Derivation")
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

@allure.feature("Prompt Building")
@allure.story("Items Block")
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

@allure.feature("Prompt Building")
@allure.story("Image Flag")
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

@allure.feature("Prompt Building")
@allure.story("Messages Section")
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


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

_MOCK_TEMPLATE = {"nodes": [{"id": "order_confirmation", "type": "real_world_milestone"}]}


@allure.feature("Prompt Building")
@allure.story("System Prompt")
class TestBuildSystemPrompt:

    def test_raises_for_missing_task(self):
        with patch("src.store.task_store.get_task", return_value=None):
            with pytest.raises(ValueError, match="Task not found"):
                build_system_prompt("no_such_task")

    def test_accepts_task_dict_directly(self):
        task = {"id": "t1", "order_type": "standard_procurement"}
        with patch("src.agent.prompt.get_template", return_value=_MOCK_TEMPLATE):
            result = build_system_prompt("t1", task=task)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_order_type_in_prompt(self):
        task = {"id": "t1", "order_type": "supplier_order"}
        with patch("src.agent.prompt.get_template", return_value=_MOCK_TEMPLATE):
            result = build_system_prompt("t1", task=task)
        assert "supplier_order" in result

    def test_skips_db_lookup_when_task_provided(self):
        task = {"id": "t1", "order_type": "standard_procurement"}
        with patch("src.store.task_store.get_task") as mock_get_task, \
             patch("src.agent.prompt.get_template", return_value=_MOCK_TEMPLATE):
            build_system_prompt("t1", task=task)
        mock_get_task.assert_not_called()
