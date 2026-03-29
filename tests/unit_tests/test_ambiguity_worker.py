"""
Unit tests for ambiguity_worker._process_entry state machine.
No DB writes — patches transaction() and update_node.
"""

import time
import pytest
import allure
from unittest.mock import patch, MagicMock, call

from src.alerts.ambiguity_worker import _process_entry, _auto_resolve


def _make_profile(timeout_high=1800, timeout_low=14400, silent=False):
    return {
        "resolution_timeout_high_s": timeout_high,
        "resolution_timeout_low_s": timeout_low,
        "silent_resolution_allowed": silent,
        "escalation_target_high": ["ashish"],
        "escalation_target_low": ["senior_staff", "ashish"],
    }


def _make_entry(status="pending", severity="high", blocking=1,
                created_at=None, escalated_at=None, re_escalations=0):
    now = int(time.time())
    return {
        "id": "aq-001",
        "message_id": "msg-001",
        "task_id": "task_001",
        "node_id": "order_confirmation",
        "group_id": "group@g.us",
        "body": "test message",
        "description": "entity ambiguity",
        "severity": severity,
        "category": "entity",
        "escalation_target": '["ashish"]',
        "blocking": blocking,
        "status": status,
        "created_at": created_at or now,
        "escalated_at": escalated_at,
        "re_escalation_count": re_escalations,
    }


# ---------------------------------------------------------------------------
# pending → first escalation
# ---------------------------------------------------------------------------

@allure.feature("Ambiguity Handling")
@allure.story("First Escalation")
class TestFirstEscalation:

    def test_pending_entry_sends_escalation(self):
        entry = _make_entry(status="pending")
        with patch("src.alerts.ambiguity_worker._send_escalation") as mock_send, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _process_entry(entry, _make_profile(), int(time.time()))
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[1]["is_re_escalation"] is False

    def test_pending_entry_not_re_escalation(self):
        entry = _make_entry(status="pending")
        with patch("src.alerts.ambiguity_worker._send_escalation") as mock_send, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _process_entry(entry, _make_profile(), int(time.time()))
        _, kwargs = mock_send.call_args
        assert kwargs["is_re_escalation"] is False


# ---------------------------------------------------------------------------
# escalated + timeout → re-escalation
# ---------------------------------------------------------------------------

@allure.feature("Ambiguity Handling")
@allure.story("Re-Escalation")
class TestReEscalation:

    def test_high_severity_re_escalates_after_timeout(self):
        now = int(time.time())
        escalated_at = now - 1900  # past 1800s timeout
        entry = _make_entry(status="escalated", severity="high", escalated_at=escalated_at)
        with patch("src.alerts.ambiguity_worker._send_escalation") as mock_send, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _process_entry(entry, _make_profile(), now)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert kwargs["is_re_escalation"] is True

    def test_high_severity_does_not_re_escalate_before_timeout(self):
        now = int(time.time())
        escalated_at = now - 600  # only 10 min, under 30 min timeout
        entry = _make_entry(status="escalated", severity="high", escalated_at=escalated_at)
        with patch("src.alerts.ambiguity_worker._send_escalation") as mock_send, \
             patch("src.alerts.ambiguity_worker.transaction"):
            _process_entry(entry, _make_profile(), now)
        mock_send.assert_not_called()

    def test_medium_severity_re_escalates_after_timeout(self):
        now = int(time.time())
        escalated_at = now - 1900
        entry = _make_entry(status="escalated", severity="medium", escalated_at=escalated_at)
        with patch("src.alerts.ambiguity_worker._send_escalation") as mock_send, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _process_entry(entry, _make_profile(), now)
        mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# low severity → auto-resolve after timeout
# ---------------------------------------------------------------------------

@allure.feature("Ambiguity Handling")
@allure.story("Auto Resolve")
class TestAutoResolve:

    def test_low_severity_auto_resolves_after_timeout(self):
        now = int(time.time())
        escalated_at = now - 15000  # past 14400s timeout
        entry = _make_entry(status="escalated", severity="low", escalated_at=escalated_at)
        with patch("src.alerts.ambiguity_worker._auto_resolve") as mock_resolve, \
             patch("src.alerts.ambiguity_worker._send_escalation"):
            _process_entry(entry, _make_profile(), now)
        mock_resolve.assert_called_once_with(entry)

    def test_low_severity_does_not_auto_resolve_before_timeout(self):
        now = int(time.time())
        escalated_at = now - 3600  # 1 hour, under 4-hour timeout
        entry = _make_entry(status="escalated", severity="low", escalated_at=escalated_at)
        with patch("src.alerts.ambiguity_worker._auto_resolve") as mock_resolve, \
             patch("src.alerts.ambiguity_worker._send_escalation"):
            _process_entry(entry, _make_profile(), now)
        mock_resolve.assert_not_called()

    def test_auto_resolve_calls_update_node_when_blocking(self):
        entry = _make_entry(blocking=1)
        with patch("src.alerts.ambiguity_worker.update_node") as mock_update, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _auto_resolve(entry)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args[1]
        assert call_kwargs["new_status"] == "provisional"

    def test_auto_resolve_skips_update_node_when_not_blocking(self):
        entry = _make_entry(blocking=0)
        with patch("src.alerts.ambiguity_worker.update_node") as mock_update, \
             patch("src.alerts.ambiguity_worker.transaction") as mock_tx:
            mock_tx.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            _auto_resolve(entry)
        mock_update.assert_not_called()
