"""
Unit tests for src/linkage/prompt — linkage agent prompt construction.
"""

import json
import pytest
import allure


@allure.feature("Linkage Agent")
@allure.story("Prompt Construction")
class TestLinkagePrompt:

    def test_system_prompt_returns_string(self):
        from src.linkage.prompt import build_system_prompt
        prompt = build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_system_prompt_contains_output_format(self):
        from src.linkage.prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "linkage_updates" in prompt
        assert "client_order_updates" in prompt
        assert "ambiguity_flags" in prompt

    def test_system_prompt_contains_status_lifecycle(self):
        from src.linkage.prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "confirmed" in prompt
        assert "candidate" in prompt
        assert "fulfilled" in prompt

    def test_user_section_contains_orders(self):
        from src.linkage.prompt import build_user_section
        open_orders = {
            "client_orders": [{"task_id": "t1", "items": ["atta 50kg"]}],
            "supplier_orders": [{"task_id": "t2", "items": ["atta 100kg"]}],
        }
        links = [{"client_order_id": "t1", "supplier_order_id": "t2", "status": "candidate"}]
        msg = {"body": "maal ready hai", "timestamp": 1000, "sender_jid": "s@w", "group_id": "g@g"}
        section = build_user_section(open_orders, links, msg)
        assert "atta 50kg" in section
        assert "atta 100kg" in section
        assert "maal ready hai" in section
        assert "candidate" in section

    def test_user_section_handles_empty_orders(self):
        from src.linkage.prompt import build_user_section
        section = build_user_section(
            {"client_orders": [], "supplier_orders": []},
            [],
            {"body": "test", "timestamp": 0, "sender_jid": "x", "group_id": "y"},
        )
        assert "Open client orders" in section
        assert "Open supplier orders" in section

    def test_user_section_contains_message(self):
        from src.linkage.prompt import build_user_section
        msg = {"body": "dispatch ho gaya", "timestamp": 9999, "sender_jid": "ash@w", "group_id": "grp@g"}
        section = build_user_section({"client_orders": [], "supplier_orders": []}, [], msg)
        assert "dispatch ho gaya" in section
        assert "ash@w" in section
