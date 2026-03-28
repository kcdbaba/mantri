"""
Unit tests for router/worker:
  _check_post_confirmation_item_changes — gate selection, _handle_ambiguity call
  process_message — routing, node writes, provisional downgrade, redis publish
No LLM, no real Redis, no DB writes.
"""

import time
import pytest
from unittest.mock import patch, MagicMock

from src.agent.update_agent import ItemExtraction


def _make_node_states(task_id, overrides: dict) -> list[dict]:
    now = int(time.time())
    return [
        {"id": f"{task_id}_{nid}", "task_id": task_id, "status": status, "updated_at": now}
        for nid, status in overrides.items()
    ]


def _make_extractions(n=1):
    return [ItemExtraction(operation="update", description=f"item {i}", quantity=float(i))
            for i in range(n)]


def _run_check(task_id, order_type, node_statuses, extractions=None):
    from src.router.worker import _check_post_confirmation_item_changes
    node_states = _make_node_states(task_id, node_statuses)
    exts = extractions or _make_extractions()
    with patch("src.router.worker.get_node_states", return_value=node_states), \
         patch("src.router.worker._handle_ambiguity") as mock_handle:
        _check_post_confirmation_item_changes(task_id, order_type, exts, {"message_id": "m1"})
    return mock_handle


# ---------------------------------------------------------------------------
# Client order gate: order_confirmation
# ---------------------------------------------------------------------------

class TestClientOrderGate:

    def test_escalates_when_order_confirmed(self):
        mock = _run_check("t1", "standard_procurement",
                          {"order_confirmation": "completed"})
        mock.assert_called_once()
        flag = mock.call_args[0][0]
        assert flag.severity == "high"
        assert flag.category == "quantity"
        assert flag.blocking_node_id == "dispatched"

    def test_no_escalation_when_not_confirmed(self):
        mock = _run_check("t1", "standard_procurement",
                          {"order_confirmation": "active"})
        mock.assert_not_called()

    def test_no_escalation_when_order_pending(self):
        mock = _run_check("t1", "standard_procurement",
                          {"order_confirmation": "pending"})
        mock.assert_not_called()

    def test_client_order_type_uses_same_gate(self):
        mock = _run_check("t1", "client_order",
                          {"order_confirmation": "completed"})
        mock.assert_called_once()
        flag = mock.call_args[0][0]
        assert flag.blocking_node_id == "dispatched"


# ---------------------------------------------------------------------------
# Supplier order gate: supplier_collection
# ---------------------------------------------------------------------------

class TestSupplierOrderGate:

    def test_escalates_when_collection_completed(self):
        mock = _run_check("t1", "supplier_order",
                          {"supplier_collection": "completed"})
        mock.assert_called_once()
        flag = mock.call_args[0][0]
        assert flag.severity == "high"
        assert flag.blocking_node_id == "supplier_QC"

    def test_no_escalation_when_collection_not_completed(self):
        mock = _run_check("t1", "supplier_order",
                          {"supplier_collection": "active"})
        mock.assert_not_called()


# ---------------------------------------------------------------------------
# Description content
# ---------------------------------------------------------------------------

class TestEscalationDescription:

    def test_description_includes_operation(self):
        mock = _run_check("t1", "standard_procurement",
                          {"order_confirmation": "completed"},
                          extractions=[ItemExtraction(operation="remove", description="atta")])
        flag = mock.call_args[0][0]
        assert "remove" in flag.description

    def test_description_includes_item_name(self):
        mock = _run_check("t1", "standard_procurement",
                          {"order_confirmation": "completed"},
                          extractions=[ItemExtraction(operation="add", description="steel rods")])
        flag = mock.call_args[0][0]
        assert "steel rods" in flag.description


# ---------------------------------------------------------------------------
# process_message
# ---------------------------------------------------------------------------

def _make_agent_output(node_updates=None, ambiguity_flags=None,
                       item_extractions=None, node_data_extractions=None):
    from src.agent.update_agent import AgentOutput
    return AgentOutput(
        node_updates=node_updates or [],
        new_task_candidates=[],
        ambiguity_flags=ambiguity_flags or [],
        item_extractions=item_extractions or [],
        node_data_extractions=node_data_extractions or [],
    )


def _make_node_update(node_id="order_confirmation", status="completed", confidence=0.95):
    from src.agent.update_agent import NodeUpdate
    return NodeUpdate(node_id=node_id, new_status=status, confidence=confidence, evidence="test")


def _run_process_message(message, task, agent_output):
    from src.router.worker import process_message
    mock_r = MagicMock()
    with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
         patch("src.router.worker.get_task", return_value=task), \
         patch("src.router.worker.append_message"), \
         patch("src.router.worker.run_update_agent", return_value=agent_output), \
         patch("src.router.worker.update_node") as mock_update_node, \
         patch("src.router.worker.apply_item_extractions"), \
         patch("src.router.worker.apply_node_data_extractions"), \
         patch("src.router.worker._check_post_confirmation_item_changes"), \
         patch("src.router.worker._handle_ambiguity") as mock_ambiguity:
        process_message(message, mock_r)
    return mock_update_node, mock_ambiguity, mock_r


class TestProcessMessage:

    def test_node_update_written(self):
        msg = {"body": "order confirmed", "message_id": "m1"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(node_updates=[_make_node_update()])
        mock_update_node, _, _ = _run_process_message(msg, task, output)
        mock_update_node.assert_called_once()
        kwargs = mock_update_node.call_args[1]
        assert kwargs["node_id"] == "order_confirmation"
        assert kwargs["new_status"] == "completed"

    def test_low_confidence_downgraded_to_provisional(self):
        msg = {"body": "maybe confirmed", "message_id": "m2"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(node_updates=[_make_node_update(confidence=0.3)])
        mock_update_node, _, _ = _run_process_message(msg, task, output)
        mock_update_node.assert_called_once()
        assert mock_update_node.call_args[1]["new_status"] == "provisional"

    def test_high_confidence_not_downgraded(self):
        msg = {"body": "confirmed", "message_id": "m3"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(node_updates=[_make_node_update(confidence=0.95)])
        mock_update_node, _, _ = _run_process_message(msg, task, output)
        assert mock_update_node.call_args[1]["new_status"] == "completed"

    def test_agent_failure_does_not_crash(self):
        msg = {"body": "test", "message_id": "m4"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        from src.router.worker import process_message
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=None):
            process_message(msg, mock_r)  # should not raise

    def test_no_routes_does_not_call_agent(self):
        msg = {"body": "hi", "message_id": "m5"}
        from src.router.worker import process_message
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[]), \
             patch("src.router.worker.run_update_agent") as mock_agent:
            process_message(msg, mock_r)
        mock_agent.assert_not_called()

    def test_task_event_published_to_redis(self):
        msg = {"body": "dispatched", "message_id": "m6"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output()
        _, _, mock_r = _run_process_message(msg, task, output)
        mock_r.xadd.assert_called_once()
        call_args = mock_r.xadd.call_args[0]
        assert call_args[0] == "task_events"
        assert call_args[1]["task_id"] == "t1"

    def test_ambiguity_flag_handled(self):
        from src.agent.update_agent import AmbiguityFlag
        msg = {"body": "wrong entity", "message_id": "m7"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        flag = AmbiguityFlag(description="unknown entity", severity="high",
                             category="entity", blocking_node_id="dispatched")
        output = _make_agent_output(ambiguity_flags=[flag])
        _, mock_ambiguity, _ = _run_process_message(msg, task, output)
        mock_ambiguity.assert_called_once()
        assert mock_ambiguity.call_args[0][0].severity == "high"
