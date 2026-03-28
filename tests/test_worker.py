"""
Unit tests for router/worker._check_post_confirmation_item_changes.
Verifies correct gate selection and that _handle_ambiguity is called
(or not) based on node state.
No LLM, no Redis, no DB writes.
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
