"""
Unit tests for linkage_worker ambiguity escalation path.
No LLM, no real Redis, no DB writes.
"""

import json
import pytest
import allure
from unittest.mock import patch, MagicMock, call

from src.linkage.agent import (
    LinkageAmbiguityFlag, LinkageAgentOutput, LinkageUpdate, ClientOrderUpdate,
)


def _make_output(ambiguity_flags=None, linkage_updates=None,
                 client_order_updates=None):
    return LinkageAgentOutput(
        linkage_updates=linkage_updates or [],
        client_order_updates=client_order_updates or [],
        new_task_candidates=[],
        ambiguity_flags=ambiguity_flags or [],
    )


def _make_message(message_id="m1"):
    return {"message_id": message_id, "group_id": "grp@g.us",
            "body": "test", "sender_jid": "sender", "timestamp": 1000}


def _run_process_event(output, fields=None, message=None):
    """Run process_event with mocked dependencies, return _handle_ambiguity mock."""
    from src.linkage.linkage_worker import process_event

    msg = message or _make_message()
    if fields is None:
        fields = {
            "event_type": "message_processed",
            "task_id": "task_source",
            "message_id": msg["message_id"],
            "message_json": json.dumps(msg),
        }

    mock_r = MagicMock()

    with patch("src.linkage.linkage_worker.get_open_orders_summary",
               return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
         patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
         patch("src.linkage.linkage_worker.run_linkage_agent", return_value=output), \
         patch("src.linkage.linkage_worker.upsert_fulfillment_link"), \
         patch("src.linkage.linkage_worker.update_node_as_linkage_agent"), \
         patch("src.linkage.linkage_worker.reconcile_order_ready"), \
         patch("src.linkage.linkage_worker.prune_links_for_supplier_order"), \
         patch("src.linkage.linkage_worker.prune_links_for_client_order"), \
         patch("src.linkage.linkage_worker._handle_ambiguity") as mock_amb, \
         patch("src.linkage.linkage_worker.transaction") as mock_tx:
        mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_tx.return_value.__exit__ = MagicMock(return_value=False)
        process_event("evt-1", fields, mock_r)

    return mock_amb


# ---------------------------------------------------------------------------
# Ambiguity escalation via _handle_ambiguity
# ---------------------------------------------------------------------------

@allure.feature("Linkage Worker")
@allure.story("Ambiguity Escalation")
class TestLinkageAmbiguityEscalation:

    def test_flag_with_affected_task_ids_calls_per_task(self):
        flag = LinkageAmbiguityFlag(
            description="qty mismatch", severity="high", category="quantity",
            blocking_node_id="order_ready",
            affected_task_ids=["task_a", "task_b"],
        )
        mock_amb = _run_process_event(_make_output(ambiguity_flags=[flag]))
        assert mock_amb.call_count == 2
        task_ids = [c.args[1] for c in mock_amb.call_args_list]
        assert "task_a" in task_ids
        assert "task_b" in task_ids

    def test_flag_without_affected_task_ids_uses_event_task_id(self):
        flag = LinkageAmbiguityFlag(
            description="vague message", severity="low", category="status",
        )
        fields = {
            "event_type": "message_processed",
            "task_id": "task_source_event",
            "message_id": "m1",
            "message_json": json.dumps(_make_message()),
        }
        mock_amb = _run_process_event(_make_output(ambiguity_flags=[flag]),
                                       fields=fields)
        mock_amb.assert_called_once()
        assert mock_amb.call_args.args[1] == "task_source_event"

    def test_single_affected_task_id(self):
        flag = LinkageAmbiguityFlag(
            description="entity unclear", severity="medium", category="entity",
            affected_task_ids=["task_only"],
        )
        mock_amb = _run_process_event(_make_output(ambiguity_flags=[flag]))
        mock_amb.assert_called_once()
        assert mock_amb.call_args.args[1] == "task_only"

    def test_multiple_flags_all_escalated(self):
        flags = [
            LinkageAmbiguityFlag(
                description="flag 1", severity="high", category="linkage",
                affected_task_ids=["t1"],
            ),
            LinkageAmbiguityFlag(
                description="flag 2", severity="low", category="quantity",
            ),
        ]
        fields = {
            "event_type": "message_processed",
            "task_id": "fallback_task",
            "message_id": "m1",
            "message_json": json.dumps(_make_message()),
        }
        mock_amb = _run_process_event(_make_output(ambiguity_flags=flags),
                                       fields=fields)
        assert mock_amb.call_count == 2
        # First call: flag 1 with task_id t1
        assert mock_amb.call_args_list[0].args[1] == "t1"
        # Second call: flag 2 with fallback task_id
        assert mock_amb.call_args_list[1].args[1] == "fallback_task"

    def test_no_flags_no_escalation(self):
        mock_amb = _run_process_event(_make_output(ambiguity_flags=[]))
        mock_amb.assert_not_called()

    def test_ambiguity_flag_converted_to_update_agent_format(self):
        """The AmbiguityFlag passed to _handle_ambiguity has the right fields."""
        flag = LinkageAmbiguityFlag(
            description="test desc", severity="high", category="linkage",
            blocking_node_id="order_ready",
            affected_task_ids=["t1"],
        )
        mock_amb = _run_process_event(_make_output(ambiguity_flags=[flag]))
        amb_flag = mock_amb.call_args.args[0]
        assert amb_flag.description == "test desc"
        assert amb_flag.severity == "high"
        assert amb_flag.category == "linkage"
        assert amb_flag.blocking_node_id == "order_ready"


# ---------------------------------------------------------------------------
# Linkage worker uses update_node_as_linkage_agent
# ---------------------------------------------------------------------------

@allure.feature("Linkage Worker")
@allure.story("Node Ownership")
class TestLinkageWorkerNodeOwnership:

    def test_client_order_update_uses_linkage_wrapper(self):
        from src.linkage.linkage_worker import process_event

        cu = ClientOrderUpdate(
            order_id="c1", node_id="order_ready",
            new_status="completed", confidence=0.95,
            evidence="all items allocated",
        )
        output = _make_output(client_order_updates=[cu])
        msg = _make_message()
        fields = {
            "event_type": "message_processed",
            "task_id": "t1",
            "message_id": "m1",
            "message_json": json.dumps(msg),
        }
        mock_r = MagicMock()

        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
             patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
             patch("src.linkage.linkage_worker.run_linkage_agent", return_value=output), \
             patch("src.linkage.linkage_worker.upsert_fulfillment_link"), \
             patch("src.linkage.linkage_worker.update_node_as_linkage_agent") as mock_update, \
             patch("src.linkage.linkage_worker.reconcile_order_ready"), \
             patch("src.linkage.linkage_worker.prune_links_for_supplier_order"), \
             patch("src.linkage.linkage_worker.prune_links_for_client_order"), \
             patch("src.linkage.linkage_worker._handle_ambiguity"), \
             patch("src.linkage.linkage_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            process_event("evt-1", fields, mock_r)

        mock_update.assert_called_once()
        kwargs = mock_update.call_args[1]
        assert kwargs["task_id"] == "c1"
        assert kwargs["node_id"] == "order_ready"
        assert kwargs["new_status"] == "completed"
