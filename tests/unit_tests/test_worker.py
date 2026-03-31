"""
Unit tests for router/worker:
  _check_post_confirmation_item_changes — gate selection, _handle_ambiguity call
  process_message — routing, node writes, provisional downgrade, redis publish
No LLM, no real Redis, no DB writes.
"""

import json
import time
import pytest
import allure
from unittest.mock import patch, MagicMock

from src.agent.update_agent import ItemExtraction, AmbiguityFlag


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

@allure.feature("Message Routing")
@allure.story("Client Order Gate")
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

@allure.feature("Message Routing")
@allure.story("Supplier Order Gate")
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

@allure.feature("Message Routing")
@allure.story("Escalation Description")
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
         patch("src.router.worker.update_node_as_update_agent") as mock_update_node, \
         patch("src.router.worker.apply_item_extractions"), \
         patch("src.router.worker.apply_node_data_extractions"), \
         patch("src.router.worker._check_post_confirmation_item_changes"), \
         patch("src.router.worker._handle_ambiguity") as mock_ambiguity:
        process_message(message, mock_r)
    return mock_update_node, mock_ambiguity, mock_r


@allure.feature("Message Routing")
@allure.story("Process Message")
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

    def test_stock_path_triggers(self):
        from src.agent.update_agent import NodeUpdate
        from src.router.worker import process_message
        msg = {"body": "godown se de denge", "message_id": "m8"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(node_updates=[
            NodeUpdate(node_id="filled_from_stock", new_status="completed",
                       confidence=0.9, evidence="stock"),
        ])
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=output), \
             patch("src.router.worker.update_node_as_update_agent"), \
             patch("src.router.worker.check_stock_path_order_ready", return_value="completed") as mock_stock, \
             patch("src.router.worker.cascade_auto_triggers", return_value=["predispatch_checklist"]) as mock_cascade, \
             patch("src.router.worker._handle_ambiguity"):
            process_message(msg, mock_r)
        mock_stock.assert_called_once_with("t1")
        mock_cascade.assert_called_once_with("t1")

    def test_node_data_extractions_applied(self):
        from src.agent.update_agent import NodeDataExtraction
        from src.router.worker import process_message
        msg = {"body": "delivery 25 march", "message_id": "m9"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(node_data_extractions=[
            NodeDataExtraction(node_id="dispatched", data={"dispatch_date": "2026-03-25"}),
        ])
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=output), \
             patch("src.router.worker.update_node_as_update_agent"), \
             patch("src.router.worker.check_stock_path_order_ready", return_value=None), \
             patch("src.router.worker.cascade_auto_triggers", return_value=[]), \
             patch("src.router.worker.apply_node_data_extractions") as mock_nd, \
             patch("src.router.worker._handle_ambiguity"):
            process_message(msg, mock_r)
        mock_nd.assert_called_once()

    def test_item_extractions_applied_and_checked(self):
        from src.agent.update_agent import ItemExtraction
        from src.router.worker import process_message
        msg = {"body": "50 bags atta", "message_id": "m10"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output(item_extractions=[
            ItemExtraction(operation="add", description="atta 50kg", quantity=50),
        ])
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=output), \
             patch("src.router.worker.update_node_as_update_agent"), \
             patch("src.router.worker.check_stock_path_order_ready", return_value=None), \
             patch("src.router.worker.cascade_auto_triggers", return_value=[]), \
             patch("src.router.worker.apply_item_extractions") as mock_items, \
             patch("src.router.worker._check_post_confirmation_item_changes") as mock_check, \
             patch("src.router.worker._handle_ambiguity"):
            process_message(msg, mock_r)
        mock_items.assert_called_once()
        mock_check.assert_called_once()

    def test_new_task_candidates_logged(self):
        from src.router.worker import process_message
        msg = {"body": "QC failed", "message_id": "m11"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        output = _make_agent_output()
        output.new_task_candidates = [{"type": "client_notification", "context": "QC fail"}]
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=output), \
             patch("src.router.worker.update_node_as_update_agent"), \
             patch("src.router.worker.check_stock_path_order_ready", return_value=None), \
             patch("src.router.worker.cascade_auto_triggers", return_value=[]), \
             patch("src.router.worker._log_new_task_candidate") as mock_log, \
             patch("src.router.worker._handle_ambiguity"):
            process_message(msg, mock_r)
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_ambiguity — gate blocking, queue write, target routing
# ---------------------------------------------------------------------------

def _run_handle_ambiguity(flag, task_id="t1", message=None,
                          is_duplicate=False, rate_limited=False):
    from src.router.worker import _handle_ambiguity
    message = message or {"message_id": "m1", "group_id": "grp@g.us", "body": "test"}
    mock_conn = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=False)
    with patch("src.router.worker.transaction", return_value=mock_cm), \
         patch("src.router.worker.update_node") as mock_update_node, \
         patch("src.router.worker._is_duplicate_flag", return_value=is_duplicate), \
         patch("src.router.worker._check_rate_limit", return_value=rate_limited):
        _handle_ambiguity(flag, task_id, message)
    return mock_conn, mock_update_node


@allure.feature("Ambiguity Handling")
@allure.story("Handle Ambiguity")
class TestHandleAmbiguity:

    def test_gate_node_blocked(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="quantity", blocking_node_id="dispatched")
        _, mock_update_node = _run_handle_ambiguity(flag)
        mock_update_node.assert_called_once()
        assert mock_update_node.call_args[1]["new_status"] == "blocked"
        assert mock_update_node.call_args[1]["node_id"] == "dispatched"

    def test_non_gate_node_not_blocked(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="quantity", blocking_node_id="supplier_indent")
        _, mock_update_node = _run_handle_ambiguity(flag)
        mock_update_node.assert_not_called()

    def test_none_blocking_node_not_blocked(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="quantity", blocking_node_id=None)
        _, mock_update_node = _run_handle_ambiguity(flag)
        mock_update_node.assert_not_called()

    def test_low_non_blocking_auto_resolved(self):
        """Low non-blocking flags are auto-resolved immediately (status='expired')."""
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="low",
                             category="entity", blocking_node_id=None)
        mock_conn, _ = _run_handle_ambiguity(flag)
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO ambiguity_queue" in sql
        params = mock_conn.execute.call_args[0][1]
        # status field (index 11) should be 'expired' for auto-resolved
        assert params[11] == "expired"

    def test_high_severity_targets_ashish(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="quantity", blocking_node_id="dispatched")
        mock_conn, _ = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        target = json.loads(params[9])   # escalation_target is index 9
        assert "ashish" in target

    def test_low_severity_targets_senior_staff(self):
        """Low blocking flags still target senior_staff + ashish."""
        from src.agent.update_agent import AmbiguityFlag
        # Use a blocking low flag (on a gate node) — non-blocking are auto-resolved
        flag = AmbiguityFlag(description="test", severity="low",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, _ = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        target = json.loads(params[9])
        assert "senior_staff" in target


# ---------------------------------------------------------------------------
# Dedup, rate limiting, severity filtering
# ---------------------------------------------------------------------------

@allure.feature("Ambiguity Handling")
@allure.story("Deduplication")
class TestAmbiguityDedup:

    def test_duplicate_flag_skipped(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, mock_update_node = _run_handle_ambiguity(flag, is_duplicate=True)
        mock_conn.execute.assert_not_called()
        mock_update_node.assert_not_called()

    def test_non_duplicate_flag_enqueued(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, _ = _run_handle_ambiguity(flag, is_duplicate=False)
        mock_conn.execute.assert_called_once()


@allure.feature("Ambiguity Handling")
@allure.story("Rate Limiting")
class TestAmbiguityRateLimit:

    def test_rate_limited_non_blocking_skipped(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="medium",
                             category="quantity", blocking_node_id=None)
        mock_conn, _ = _run_handle_ambiguity(flag, rate_limited=True)
        mock_conn.execute.assert_not_called()

    def test_rate_limited_blocking_still_enqueued(self):
        """Blocking flags bypass rate limit — safety over noise reduction."""
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, mock_update_node = _run_handle_ambiguity(flag, rate_limited=True)
        # Blocking flags are not rate-limited (should_block=True skips rate check)
        mock_conn.execute.assert_called_once()
        mock_update_node.assert_called_once()


@allure.feature("Ambiguity Handling")
@allure.story("Severity Filtering")
class TestAmbiguitySeverityFiltering:

    def test_high_blocking_enqueued_as_pending(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="high",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, _ = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        assert params[11] == "pending"  # status

    def test_medium_non_blocking_enqueued_as_pending(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="medium",
                             category="quantity", blocking_node_id=None)
        mock_conn, _ = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        assert params[11] == "pending"

    def test_low_non_blocking_auto_resolved_immediately(self):
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="low",
                             category="timing", blocking_node_id=None)
        mock_conn, mock_update_node = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        assert params[11] == "expired"  # auto-resolved
        mock_update_node.assert_not_called()  # not blocking

    def test_low_blocking_enqueued_as_pending(self):
        """Low blocking on gate node still enqueues as pending for escalation."""
        from src.agent.update_agent import AmbiguityFlag
        flag = AmbiguityFlag(description="test", severity="low",
                             category="entity", blocking_node_id="dispatched")
        mock_conn, mock_update_node = _run_handle_ambiguity(flag)
        params = mock_conn.execute.call_args[0][1]
        assert params[11] == "pending"
        mock_update_node.assert_called_once()  # gate blocked


# ---------------------------------------------------------------------------
# _log_dead_letter — writes failed update_agent calls to dead_letter_events
# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Dead Letter Logging")
class TestDeadLetterLogging:

    def test_dead_letter_written_on_agent_failure(self):
        from src.router.worker import _log_dead_letter
        mock_conn = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("src.router.worker.transaction", return_value=mock_cm):
            _log_dead_letter("task_001", {"message_id": "m1", "body": "test"})
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO dead_letter_events" in sql

    def test_dead_letter_contains_task_id(self):
        from src.router.worker import _log_dead_letter
        mock_conn = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("src.router.worker.transaction", return_value=mock_cm):
            _log_dead_letter("task_xyz", {"message_id": "m2", "body": "hello"})
        params = mock_conn.execute.call_args[0][1]
        fields_json = params[3]  # fields_json is index 3
        assert "task_xyz" in fields_json

    def test_process_message_logs_dead_letter_on_agent_none(self):
        msg = {"body": "test", "message_id": "m99"}
        task = {"id": "t1", "order_type": "standard_procurement"}
        from src.router.worker import process_message
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value=task), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=None), \
             patch("src.router.worker._log_dead_letter") as mock_dl:
            process_message(msg, mock_r)
        mock_dl.assert_called_once_with("t1", msg)


# ---------------------------------------------------------------------------
# update_node wrappers — verify updated_by is set correctly
# ---------------------------------------------------------------------------

@allure.feature("Node Ownership")
@allure.story("Update Node Wrappers")
class TestUpdateNodeWrappers:

    def test_update_agent_wrapper_sets_updated_by(self):
        from src.store.task_store import update_node_as_update_agent
        with patch("src.store.task_store.update_node") as mock:
            update_node_as_update_agent("t1", "order_confirmation", "completed", 0.95, "m1")
        mock.assert_called_once_with("t1", "order_confirmation", "completed", 0.95, "m1",
                                     updated_by="update_agent")

    def test_linkage_agent_wrapper_sets_updated_by(self):
        from src.store.task_store import update_node_as_linkage_agent
        with patch("src.store.task_store.update_node") as mock:
            update_node_as_linkage_agent("t1", "order_ready", "completed", 0.98, "m2")
        mock.assert_called_once_with("t1", "order_ready", "completed", 0.98, "m2",
                                     updated_by="linkage_agent")


# ---------------------------------------------------------------------------
# Template ownership — all nodes have owner field
# ---------------------------------------------------------------------------

@allure.feature("Node Ownership")
@allure.story("Template Owner Field")
class TestTemplateOwnership:

    def test_all_nodes_have_owner(self):
        from src.agent.templates import TEMPLATES
        for order_type, tmpl in TEMPLATES.items():
            for node in tmpl["nodes"]:
                assert "owner" in node, f"{order_type}/{node['id']} missing owner"

    def test_owner_values_are_valid(self):
        from src.agent.templates import TEMPLATES
        valid = {"update_agent", "linkage_agent"}
        for order_type, tmpl in TEMPLATES.items():
            for node in tmpl["nodes"]:
                assert node["owner"] in valid, \
                    f"{order_type}/{node['id']} has invalid owner: {node['owner']}"

    def test_order_ready_owned_by_linkage(self):
        from src.agent.templates import TEMPLATES
        for order_type, tmpl in TEMPLATES.items():
            for node in tmpl["nodes"]:
                if node["id"] == "order_ready":
                    assert node["owner"] == "linkage_agent"

    def test_task_closed_owned_by_linkage(self):
        from src.agent.templates import TEMPLATES
        for order_type, tmpl in TEMPLATES.items():
            for node in tmpl["nodes"]:
                if node["id"] == "task_closed":
                    assert node["owner"] == "linkage_agent"

    def test_message_driven_nodes_owned_by_update_agent(self):
        from src.agent.templates import TEMPLATES
        linkage_nodes = {"order_ready", "task_closed"}
        for order_type, tmpl in TEMPLATES.items():
            for node in tmpl["nodes"]:
                if node["id"] not in linkage_nodes:
                    assert node["owner"] == "update_agent", \
                        f"{order_type}/{node['id']} should be update_agent"


# ---------------------------------------------------------------------------
# LinkageAmbiguityFlag — affected_task_ids field
# ---------------------------------------------------------------------------

@allure.feature("Linkage Agent")
@allure.story("Linkage Ambiguity Flag")
class TestLinkageAmbiguityFlag:

    def test_default_affected_task_ids_empty(self):
        from src.linkage.agent import LinkageAmbiguityFlag
        flag = LinkageAmbiguityFlag(description="test", severity="high",
                                     category="entity")
        assert flag.affected_task_ids == []

    def test_affected_task_ids_set(self):
        from src.linkage.agent import LinkageAmbiguityFlag
        flag = LinkageAmbiguityFlag(
            description="test", severity="high", category="linkage",
            blocking_node_id="order_ready",
            affected_task_ids=["task_001", "task_002"],
        )
        assert flag.affected_task_ids == ["task_001", "task_002"]

    def test_parses_from_json(self):
        from src.linkage.agent import LinkageAmbiguityFlag
        data = {
            "description": "qty mismatch",
            "severity": "medium",
            "category": "quantity",
            "blocking_node_id": None,
            "affected_task_ids": ["task_abc"],
        }
        flag = LinkageAmbiguityFlag.model_validate(data)
        assert flag.affected_task_ids == ["task_abc"]

    def test_parses_without_affected_task_ids(self):
        from src.linkage.agent import LinkageAmbiguityFlag
        data = {
            "description": "test",
            "severity": "low",
            "category": "status",
        }
        flag = LinkageAmbiguityFlag.model_validate(data)
        assert flag.affected_task_ids == []


# ---------------------------------------------------------------------------
# Empty message filter — dropped at router level
# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Empty Message Filter")
class TestEmptyMessageFilter:

    def test_empty_body_no_image_returns_no_routes(self):
        from src.router.router import route
        assert route({"body": "", "message_id": "m1", "group_id": "sata_jobs"}) == []

    def test_whitespace_only_returns_no_routes(self):
        from src.router.router import route
        assert route({"body": "   ", "message_id": "m1", "group_id": "sata_jobs"}) == []

    def test_body_with_text_routes(self):
        from src.router.router import route
        with patch("src.router.router.MONITORED_GROUPS", {"grp": "t1"}):
            result = route({"body": "hello", "message_id": "m1", "group_id": "grp"})
        assert len(result) == 1

    def test_empty_body_with_image_routes(self):
        from src.router.router import route
        with patch("src.router.router.MONITORED_GROUPS", {"grp": "t1"}):
            result = route({"body": "", "image_path": "/tmp/x.jpg",
                           "message_id": "m1", "group_id": "grp"})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _select_model — batch-level model tiering
# ---------------------------------------------------------------------------

@allure.feature("Cost Optimisation")
@allure.story("Model Tiering")
class TestSelectModel:

    def test_all_simple_uses_gemini(self):
        from src.agent.update_agent import _select_model
        msgs = [{"body": "ok"}, {"body": "thanks sir"}]
        assert _select_model(msgs) == "gemini-2.5-flash"

    def test_any_complex_uses_sonnet(self):
        from src.agent.update_agent import _select_model
        msgs = [{"body": "ok"}, {"body": "50 bags atta 28500/-"}]
        assert _select_model(msgs) == "claude-sonnet-4-6"

    def test_single_complex_message(self):
        from src.agent.update_agent import _select_model
        assert _select_model([{"body": "Sir kindly reshare the rate for 1.5 ton Split Ac and others"}]) == "claude-sonnet-4-6"

    def test_image_in_batch_uses_sonnet(self):
        from src.agent.update_agent import _select_model
        msgs = [{"body": "ok"}, {"body": "hi", "image_path": "/tmp/x.jpg"}]
        assert _select_model(msgs) == "claude-sonnet-4-6"

    def test_numbers_trigger_sonnet(self):
        from src.agent.update_agent import _select_model
        assert _select_model([{"body": "50 bags atta"}]) == "claude-sonnet-4-6"

    def test_order_keyword_triggers_sonnet(self):
        from src.agent.update_agent import _select_model
        assert _select_model([{"body": "order confirm ho gaya"}]) == "claude-sonnet-4-6"

    def test_hindi_quantity_triggers_sonnet(self):
        from src.agent.update_agent import _select_model
        assert _select_model([{"body": "do battery chahiye"}]) == "claude-sonnet-4-6"

    def test_pure_acknowledgements_use_gemini(self):
        from src.agent.update_agent import _select_model
        for body in ["thanks sir", "Increased", "..", "Sir kindly share", "Welcome"]:
            assert _select_model([{"body": body}]) == "gemini-2.5-flash", \
                f"'{body}' should use Gemini Flash"

    def test_payment_keyword_triggers_sonnet(self):
        from src.agent.update_agent import _select_model
        assert _select_model([{"body": "payment done"}]) == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Router worker stream processing
# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Stream Processing")
class TestRouterStreamProcessing:

    def test_malformed_json_acks_and_dead_letters(self):
        from src.router.worker import _process_with_retry
        mock_r = MagicMock()
        with patch("src.router.worker._write_ingest_dead_letter") as mock_dl:
            _process_with_retry("evt-1", {"message_json": "not{json"}, mock_r)
        mock_dl.assert_called_once()
        mock_r.xack.assert_called_once()

    def test_missing_message_json_acks_silently(self):
        from src.router.worker import _process_with_retry
        mock_r = MagicMock()
        _process_with_retry("evt-1", {}, mock_r)
        mock_r.xack.assert_called_once()

    def test_successful_processing_acks(self):
        from src.router.worker import _process_with_retry
        mock_r = MagicMock()
        msg = {"body": "test", "message_id": "m1"}
        fields = {"message_json": json.dumps(msg)}
        with patch("src.router.worker.process_message"):
            _process_with_retry("evt-1", fields, mock_r)
        mock_r.xack.assert_called_once()


# ---------------------------------------------------------------------------
# process_message_batch
# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Batch Processing")
class TestProcessMessageBatch:

    def _run_batch(self, messages, agent_output):
        from src.router.worker import process_message_batch
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value={"id": "t1", "order_type": "standard_procurement"}), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=agent_output), \
             patch("src.router.worker.update_node_as_update_agent") as mock_update, \
             patch("src.router.worker.apply_item_extractions"), \
             patch("src.router.worker.apply_node_data_extractions"), \
             patch("src.router.worker._check_post_confirmation_item_changes"), \
             patch("src.router.worker._handle_ambiguity") as mock_amb, \
             patch("src.router.worker.check_stock_path_order_ready", return_value=None), \
             patch("src.router.worker.cascade_auto_triggers", return_value=[]):
            process_message_batch("t1", messages, mock_r)
        return mock_update, mock_amb, mock_r

    def test_batch_writes_node_updates(self):
        msgs = [{"body": "ok", "message_id": "m1"}, {"body": "confirmed", "message_id": "m2"}]
        output = _make_agent_output(node_updates=[_make_node_update()])
        mock_update, _, _ = self._run_batch(msgs, output)
        mock_update.assert_called_once()

    def test_batch_publishes_task_event(self):
        msgs = [{"body": "test", "message_id": "m1"}]
        output = _make_agent_output()
        _, _, mock_r = self._run_batch(msgs, output)
        mock_r.xadd.assert_called_once()

    def test_batch_agent_failure_dead_letters(self):
        from src.router.worker import process_message_batch
        msgs = [{"body": "test", "message_id": "m1"}]
        mock_r = MagicMock()
        with patch("src.router.worker.route", return_value=[("t1", 0.9)]), \
             patch("src.router.worker.get_task", return_value={"id": "t1", "order_type": "standard_procurement"}), \
             patch("src.router.worker.append_message"), \
             patch("src.router.worker.run_update_agent", return_value=None), \
             patch("src.router.worker._log_dead_letter") as mock_dl:
            process_message_batch("t1", msgs, mock_r)
        mock_dl.assert_called_once()

    def test_batch_handles_ambiguity_flags(self):
        msgs = [{"body": "test", "message_id": "m1"}]
        flag = AmbiguityFlag(description="test", severity="high",
                             category="entity", blocking_node_id="dispatched")
        output = _make_agent_output(ambiguity_flags=[flag])
        _, mock_amb, _ = self._run_batch(msgs, output)
        mock_amb.assert_called_once()


# ---------------------------------------------------------------------------
# _publish_task_event
# ---------------------------------------------------------------------------
# _create_task_from_candidate
# ---------------------------------------------------------------------------

@allure.feature("Task Creation")
@allure.story("Create Task From Candidate")
class TestCreateTaskFromCandidate:

    def test_creates_task_for_valid_new_order(self):
        from src.router.worker import _create_task_from_candidate
        candidate = {
            "type": "new_order",
            "order_type": "client_order",
            "entity_id": "entity_new_client",
            "entity_name": "New Client",
            "context": "new AC order",
        }
        msg = {"message_id": "m1", "group_id": "grp@g.us", "body": "test"}
        mock_r = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None  # no dedup hit
        with patch("src.router.worker.get_connection", return_value=mock_conn), \
             patch("src.router.worker.create_task_live", return_value="task_abc123") as mock_create, \
             patch("src.router.worker.append_message"), \
             patch("src.router.alias_dict.invalidate_alias_cache"), \
             patch("src.router.worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            result = _create_task_from_candidate(candidate, msg, "t1", mock_r)
        assert result == "task_abc123"
        mock_create.assert_called_once()

    def test_dedup_prevents_duplicate_creation(self):
        from src.router.worker import _create_task_from_candidate
        candidate = {"type": "new_order", "order_type": "client_order",
                     "entity_id": "entity_dup"}
        msg = {"message_id": "m1", "group_id": "grp@g.us"}
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("task_existing",)
        with patch("src.router.worker.get_connection", return_value=mock_conn):
            result = _create_task_from_candidate(candidate, msg, "t1", MagicMock())
        assert result is None

    def test_invalid_order_type_falls_back_to_log(self):
        from src.router.worker import _create_task_from_candidate
        candidate = {"type": "new_order", "order_type": "invalid_type"}
        msg = {"message_id": "m1"}
        with patch("src.router.worker._log_new_task_candidate") as mock_log:
            result = _create_task_from_candidate(candidate, msg, "t1", MagicMock())
        assert result is None
        mock_log.assert_called_once()

    def test_publishes_task_event_after_creation(self):
        from src.router.worker import _create_task_from_candidate
        candidate = {"type": "new_order", "order_type": "supplier_order",
                     "entity_id": "entity_s"}
        msg = {"message_id": "m1", "group_id": "grp@g.us"}
        mock_r = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("src.router.worker.get_connection", return_value=mock_conn), \
             patch("src.router.worker.create_task_live", return_value="task_new"), \
             patch("src.router.worker.append_message"), \
             patch("src.router.alias_dict.invalidate_alias_cache"), \
             patch("src.router.worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _create_task_from_candidate(candidate, msg, "t1", mock_r)
        mock_r.xadd.assert_called_once()


# ---------------------------------------------------------------------------

@allure.feature("Message Routing")
@allure.story("Task Event Publishing")
class TestPublishTaskEvent:

    def test_publishes_to_task_events_stream(self):
        from src.router.worker import _publish_task_event
        mock_r = MagicMock()
        msg = {"message_id": "m1", "body": "test"}
        _publish_task_event("t1", msg, mock_r)
        mock_r.xadd.assert_called_once()
        call_args = mock_r.xadd.call_args[0]
        assert call_args[0] == "task_events"
        fields = call_args[1]
        assert fields["task_id"] == "t1"
        assert fields["event_type"] == "message_processed"

    def test_redis_error_does_not_raise(self):
        from src.router.worker import _publish_task_event
        import redis
        mock_r = MagicMock()
        mock_r.xadd.side_effect = redis.RedisError("connection refused")
        _publish_task_event("t1", {"message_id": "m1"}, mock_r)  # should not raise


# ---------------------------------------------------------------------------
# _is_duplicate_flag and _check_rate_limit (DB-backed)
# ---------------------------------------------------------------------------

@allure.feature("Ambiguity Handling")
@allure.story("Dedup DB Query")
class TestDedupDBQuery:

    def test_is_duplicate_returns_true_when_match(self):
        from src.router.worker import _is_duplicate_flag
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"id": "existing"}
        with patch("src.router.worker.get_connection", return_value=mock_conn):
            result = _is_duplicate_flag("t1", "entity", "dispatched", int(time.time()))
        assert result is True

    def test_is_duplicate_returns_false_when_no_match(self):
        from src.router.worker import _is_duplicate_flag
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        with patch("src.router.worker.get_connection", return_value=mock_conn):
            result = _is_duplicate_flag("t1", "entity", None, int(time.time()))
        assert result is False

    def test_check_rate_limit_unlimited(self):
        from src.router.worker import _check_rate_limit
        profile = {"escalation_rate_limit": None}
        assert _check_rate_limit("t1", profile, int(time.time())) is False

    def test_check_rate_limit_under(self):
        from src.router.worker import _check_rate_limit
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (3,)
        profile = {"escalation_rate_limit": 10}
        with patch("src.router.worker.get_connection", return_value=mock_conn):
            assert _check_rate_limit("t1", profile, int(time.time())) is False

    def test_check_rate_limit_over(self):
        from src.router.worker import _check_rate_limit
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (10,)
        profile = {"escalation_rate_limit": 10}
        with patch("src.router.worker.get_connection", return_value=mock_conn):
            assert _check_rate_limit("t1", profile, int(time.time())) is True
