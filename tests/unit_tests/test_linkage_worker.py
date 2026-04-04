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


# ---------------------------------------------------------------------------
# process_event — skip paths
# ---------------------------------------------------------------------------

@allure.feature("Linkage Worker")
@allure.story("Event Skip Paths")
class TestProcessEventSkips:

    def test_skips_non_message_processed_events(self):
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        process_event("evt-1", {"event_type": "other_type"}, mock_r)
        mock_r.xack.assert_called_once()

    def test_skips_missing_message_json(self):
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        process_event("evt-1", {"event_type": "message_processed"}, mock_r)
        mock_r.xack.assert_called_once()

    def test_skips_malformed_json(self):
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        fields = {"event_type": "message_processed", "message_json": "not{json"}
        process_event("evt-1", fields, mock_r)
        mock_r.xack.assert_called_once()

    def test_skips_when_no_client_orders(self):
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        msg = json.dumps(_make_message())
        fields = {"event_type": "message_processed", "message_json": msg}
        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [], "supplier_orders": [{"task_id": "s1"}]}):
            process_event("evt-1", fields, mock_r)
        mock_r.xack.assert_called_once()

    def test_raises_on_agent_failure(self):
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        msg = json.dumps(_make_message())
        fields = {"event_type": "message_processed", "message_json": msg}
        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
             patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
             patch("src.linkage.linkage_worker.run_linkage_agent", return_value=None):
            with pytest.raises(RuntimeError, match="Linkage agent returned None"):
                process_event("evt-1", fields, mock_r)


@allure.feature("Linkage Worker")
@allure.story("Retry Wrapper")
class TestProcessWithRetry:

    def test_exhaustion_writes_dead_letter_and_acks(self):
        from src.linkage.linkage_worker import (
            CONSUMER_GROUP,
            TASK_EVENTS_STREAM,
            _process_with_retry,
        )

        mock_r = MagicMock()
        fields = {"event_type": "message_processed", "message_json": json.dumps(_make_message())}

        with patch("src.linkage.linkage_worker.process_event", side_effect=RuntimeError("boom")), \
             patch("src.linkage.linkage_worker._write_dead_letter") as mock_dead, \
             patch("src.linkage.linkage_worker.time.sleep") as mock_sleep:
            _process_with_retry("evt-1", fields, mock_r)

        # linear backoff between attempts: 1s then 2s
        assert mock_sleep.call_count == 2
        mock_dead.assert_called_once()
        dead_args = mock_dead.call_args[0]
        assert dead_args[0] == "evt-1"
        assert dead_args[1] == fields
        assert dead_args[3] == 3

        mock_r.xack.assert_called_once_with(TASK_EVENTS_STREAM, CONSUMER_GROUP, "evt-1")


# ---------------------------------------------------------------------------
# process_event — linkage updates, pruning, reconciliation
# ---------------------------------------------------------------------------

@allure.feature("Linkage Worker")
@allure.story("Linkage Update Processing")
class TestProcessEventLinkageUpdates:

    def test_upserts_linkage_updates(self):
        link = LinkageUpdate(
            client_order_id="c1", client_item_description="atta 50kg",
            supplier_order_id="s1", supplier_item_description="atta bags",
            quantity_allocated=50, match_confidence=0.95,
            match_reasoning="same item", status="confirmed",
        )
        output = _make_output(linkage_updates=[link])
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        fields = {
            "event_type": "message_processed",
            "task_id": "t1", "message_json": json.dumps(_make_message()),
        }
        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
             patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
             patch("src.linkage.linkage_worker.run_linkage_agent", return_value=output), \
             patch("src.linkage.linkage_worker.upsert_fulfillment_link") as mock_upsert, \
             patch("src.linkage.linkage_worker.update_node_as_linkage_agent"), \
             patch("src.linkage.linkage_worker.reconcile_order_ready"), \
             patch("src.linkage.linkage_worker.prune_links_for_supplier_order"), \
             patch("src.linkage.linkage_worker.prune_links_for_client_order"), \
             patch("src.linkage.linkage_worker._handle_ambiguity"), \
             patch("src.linkage.linkage_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            process_event("evt-1", fields, mock_r)
        mock_upsert.assert_called_once()

    def test_reconciles_order_ready_for_affected_clients(self):
        link = LinkageUpdate(
            client_order_id="c1", client_item_description="item",
            supplier_order_id="s1", supplier_item_description="item",
            quantity_allocated=10, match_confidence=0.95,
            match_reasoning="match", status="confirmed",
        )
        output = _make_output(linkage_updates=[link])
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        fields = {
            "event_type": "message_processed",
            "task_id": "t1", "message_json": json.dumps(_make_message()),
        }
        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
             patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
             patch("src.linkage.linkage_worker.run_linkage_agent", return_value=output), \
             patch("src.linkage.linkage_worker.upsert_fulfillment_link"), \
             patch("src.linkage.linkage_worker.update_node_as_linkage_agent"), \
             patch("src.linkage.linkage_worker.reconcile_order_ready") as mock_recon, \
             patch("src.linkage.linkage_worker.prune_links_for_supplier_order"), \
             patch("src.linkage.linkage_worker.prune_links_for_client_order"), \
             patch("src.linkage.linkage_worker._handle_ambiguity"), \
             patch("src.linkage.linkage_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            process_event("evt-1", fields, mock_r)
        mock_recon.assert_called_with("c1")

    def test_prunes_fulfilled_supplier_links(self):
        link = LinkageUpdate(
            client_order_id="c1", client_item_description="item",
            supplier_order_id="s1", supplier_item_description="item",
            quantity_allocated=10, match_confidence=0.95,
            match_reasoning="match", status="fulfilled",
        )
        output = _make_output(linkage_updates=[link])
        from src.linkage.linkage_worker import process_event
        mock_r = MagicMock()
        fields = {
            "event_type": "message_processed",
            "task_id": "t1", "message_json": json.dumps(_make_message()),
        }
        with patch("src.linkage.linkage_worker.get_open_orders_summary",
                   return_value={"client_orders": [{"task_id": "c1"}], "supplier_orders": []}), \
             patch("src.linkage.linkage_worker._get_all_fulfillment_links", return_value=[]), \
             patch("src.linkage.linkage_worker.run_linkage_agent", return_value=output), \
             patch("src.linkage.linkage_worker.upsert_fulfillment_link"), \
             patch("src.linkage.linkage_worker.update_node_as_linkage_agent"), \
             patch("src.linkage.linkage_worker.reconcile_order_ready"), \
             patch("src.linkage.linkage_worker.prune_links_for_supplier_order") as mock_prune_s, \
             patch("src.linkage.linkage_worker.prune_links_for_client_order"), \
             patch("src.linkage.linkage_worker._handle_ambiguity"), \
             patch("src.linkage.linkage_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            process_event("evt-1", fields, mock_r)
        mock_prune_s.assert_called_with("s1")


# ---------------------------------------------------------------------------
# run_linkage_agent (from linkage/agent.py)
# ---------------------------------------------------------------------------

@allure.feature("Linkage Agent")
@allure.story("Run Linkage Agent")
class TestRunLinkageAgent:

    def _valid_raw(self):
        return json.dumps({
            "linkage_updates": [], "client_order_updates": [],
            "new_task_candidates": [], "ambiguity_flags": [],
        })

    def test_success_returns_output(self):
        from src.linkage.agent import run_linkage_agent
        from src.agent.update_agent import LLMResponse
        resp = LLMResponse(raw=self._valid_raw(), tokens_in=100, tokens_out=50)
        with patch("src.linkage.agent._call_anthropic_with_retry", return_value=resp), \
             patch("src.linkage.agent.log_llm_call"):
            result = run_linkage_agent(
                {"client_orders": [], "supplier_orders": []},
                [], {"body": "test", "message_id": "m1"},
            )
        assert result is not None
        assert result.linkage_updates == []

    def test_api_failure_returns_none(self):
        from src.linkage.agent import run_linkage_agent
        with patch("src.linkage.agent._call_anthropic_with_retry", return_value=None), \
             patch("src.linkage.agent.log_llm_call"):
            result = run_linkage_agent(
                {"client_orders": [], "supplier_orders": []},
                [], {"body": "test", "message_id": "m1"},
            )
        assert result is None

    def test_parse_failure_returns_none(self):
        from src.linkage.agent import run_linkage_agent
        from src.agent.update_agent import LLMResponse
        resp = LLMResponse(raw="not valid json", tokens_in=100, tokens_out=50)
        with patch("src.linkage.agent._call_anthropic_with_retry", return_value=resp), \
             patch("src.linkage.agent.log_llm_call"):
            result = run_linkage_agent(
                {"client_orders": [], "supplier_orders": []},
                [], {"body": "test", "message_id": "m1"},
            )
        assert result is None

    def test_markdown_fences_stripped(self):
        from src.linkage.agent import run_linkage_agent
        from src.agent.update_agent import LLMResponse
        raw = f"```json\n{self._valid_raw()}\n```"
        resp = LLMResponse(raw=raw, tokens_in=100, tokens_out=50)
        with patch("src.linkage.agent._call_anthropic_with_retry", return_value=resp), \
             patch("src.linkage.agent.log_llm_call"):
            result = run_linkage_agent(
                {"client_orders": [], "supplier_orders": []},
                [], {"body": "test", "message_id": "m1"},
            )
        assert result is not None

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
