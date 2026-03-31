"""
Unit tests for cron_worker._evaluate_time_trigger + check_time_trigger_alerts.
No DB, no LLM, no Redis — all state is passed as arguments or mocked.
"""

import time
import json
import pytest
import allure
from unittest.mock import patch, MagicMock, mock_open

from src.alerts.cron_worker import (
    _evaluate_time_trigger, _node_status, _node_completed_at,
    check_time_trigger_alerts, _fire_alert,
)


def _make_node_states(task_id, overrides: dict) -> list[dict]:
    """Build minimal node state list from {node_id: status} dict."""
    now = int(time.time())
    return [
        {"id": f"{task_id}_{nid}", "task_id": task_id, "status": status, "updated_at": now}
        for nid, status in overrides.items()
    ]


def _make_task(task_id="t1"):
    return {"id": task_id, "order_type": "standard_procurement"}


# ---------------------------------------------------------------------------
# _node_status / _node_completed_at helpers
# ---------------------------------------------------------------------------

def test_node_status_found():
    states = _make_node_states("t1", {"client_quotation": "in_progress"})
    assert _node_status(states, "client_quotation") == "in_progress"

def test_node_status_missing_returns_pending():
    assert _node_status([], "anything") == "pending"

def test_node_completed_at_returns_timestamp():
    now = int(time.time())
    states = [{"id": "t1_delivery_confirmed", "status": "completed", "updated_at": now}]
    result = _node_completed_at(states, "delivery_confirmed")
    assert result == now

def test_node_completed_at_not_completed_returns_none():
    states = _make_node_states("t1", {"delivery_confirmed": "active"})
    assert _node_completed_at(states, "delivery_confirmed") is None


# ---------------------------------------------------------------------------
# quote_followup_48h
# ---------------------------------------------------------------------------

QUOTE_NODE = {
    "id": "quote_followup_48h",
    "type": "time_trigger",
    "activates_when": "client_quotation.status=in_progress AND hours_since(client_quotation.updated_at) >= 48",
}

def test_quote_followup_fires_after_48h():
    now = int(time.time())
    old_ts = now - (49 * 3600)  # 49 hours ago
    states = [{"id": "t1_client_quotation", "task_id": "t1", "status": "in_progress", "updated_at": old_ts}]
    keys = _evaluate_time_trigger(QUOTE_NODE, _make_task(), states)
    assert "elapsed_48h" in keys

def test_quote_followup_does_not_fire_before_48h():
    now = int(time.time())
    recent_ts = now - (24 * 3600)  # only 24 hours ago
    states = [{"id": "t1_client_quotation", "task_id": "t1", "status": "in_progress", "updated_at": recent_ts}]
    keys = _evaluate_time_trigger(QUOTE_NODE, _make_task(), states)
    assert "elapsed_48h" not in keys

def test_quote_followup_does_not_fire_if_not_in_progress():
    now = int(time.time())
    old_ts = now - (49 * 3600)
    states = [{"id": "t1_client_quotation", "task_id": "t1", "status": "completed", "updated_at": old_ts}]
    keys = _evaluate_time_trigger(QUOTE_NODE, _make_task(), states)
    assert keys == []


# ---------------------------------------------------------------------------
# payment_followup_30d
# ---------------------------------------------------------------------------

PAYMENT_NODE = {
    "id": "payment_followup_30d",
    "type": "time_trigger",
    "activates_when": "delivery_confirmed.status=completed AND days_since(delivery_confirmed.completed_at) >= 30",
}

def test_payment_followup_fires_after_30d():
    now = int(time.time())
    old_ts = now - (31 * 86400)  # 31 days ago
    states = [{"id": "t1_delivery_confirmed", "task_id": "t1", "status": "completed", "updated_at": old_ts}]
    keys = _evaluate_time_trigger(PAYMENT_NODE, _make_task(), states)
    assert "elapsed_30d" in keys

def test_payment_followup_does_not_fire_before_30d():
    now = int(time.time())
    recent_ts = now - (20 * 86400)
    states = [{"id": "t1_delivery_confirmed", "task_id": "t1", "status": "completed", "updated_at": recent_ts}]
    keys = _evaluate_time_trigger(PAYMENT_NODE, _make_task(), states)
    assert keys == []

def test_payment_followup_does_not_fire_if_not_completed():
    now = int(time.time())
    old_ts = now - (31 * 86400)
    states = [{"id": "t1_delivery_confirmed", "task_id": "t1", "status": "active", "updated_at": old_ts}]
    keys = _evaluate_time_trigger(PAYMENT_NODE, _make_task(), states)
    assert keys == []


# ---------------------------------------------------------------------------
# supplier_predelivery_enquiry
# ---------------------------------------------------------------------------

PREDELIVERY_NODE = {
    "id": "supplier_predelivery_enquiry",
    "type": "time_trigger",
    "activates_when": "supplier_indent.status=completed AND task.metadata.expected_delivery_date IS SET",
    "alert_days_before": [7, 3, 1],
}

def _predelivery_states(task_id, collection_status="pending"):
    return _make_node_states(task_id, {"supplier_collection": collection_status})

def test_predelivery_fires_at_t_minus_7():
    now = int(time.time())
    exp_date = (now + 6 * 86400)  # 6 days from now → past T-7 window
    import datetime
    exp_str = datetime.datetime.fromtimestamp(exp_date).strftime("%Y-%m-%d")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.alerts.cron_worker.get_node_data",
                   lambda task_id, node_id: {"expected_delivery_date": exp_str})
        keys = _evaluate_time_trigger(PREDELIVERY_NODE, _make_task(), _predelivery_states("t1"))
    assert "days_before_7" in keys

def test_predelivery_stops_after_collection():
    now = int(time.time())
    exp_date = now + 6 * 86400
    import datetime
    exp_str = datetime.datetime.fromtimestamp(exp_date).strftime("%Y-%m-%d")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.alerts.cron_worker.get_node_data",
                   lambda task_id, node_id: {"expected_delivery_date": exp_str})
        # collection already completed
        keys = _evaluate_time_trigger(
            PREDELIVERY_NODE, _make_task(),
            _predelivery_states("t1", collection_status="completed"),
        )
    assert keys == []

def test_predelivery_no_date_fires_nothing():
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.alerts.cron_worker.get_node_data", lambda t, n: {})
        keys = _evaluate_time_trigger(PREDELIVERY_NODE, _make_task(), _predelivery_states("t1"))
    assert keys == []

def test_predelivery_fires_multiple_windows():
    # T-1 day from now → T-7, T-3, T-1 should all fire
    now = int(time.time())
    exp_date = now + 0 * 86400  # today = past all windows
    import datetime
    exp_str = datetime.datetime.fromtimestamp(exp_date).strftime("%Y-%m-%d")
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr("src.alerts.cron_worker.get_node_data",
                   lambda task_id, node_id: {"expected_delivery_date": exp_str})
        keys = _evaluate_time_trigger(PREDELIVERY_NODE, _make_task(), _predelivery_states("t1"))
    assert "days_before_7" in keys
    assert "days_before_3" in keys
    assert "days_before_1" in keys


# ---------------------------------------------------------------------------
# _fire_alert — writes to log file
# ---------------------------------------------------------------------------

@allure.feature("Time Triggers")
@allure.story("Fire Alert")
class TestFireAlert:

    def test_writes_to_alert_log(self, tmp_path):
        log_path = tmp_path / "alerts.log"
        task = {"id": "t1", "order_type": "standard_procurement", "client_id": "e_sata"}
        node = {"id": "payment_followup_30d", "name": "Payment Follow-up"}
        with patch("src.alerts.cron_worker.ALERT_LOG_PATH", str(log_path)):
            _fire_alert(task, node, alert_key="elapsed_30d")
        content = log_path.read_text()
        alert = json.loads(content.strip())
        assert alert["type"] == "time_trigger_alert"
        assert alert["task_id"] == "t1"
        assert alert["node_id"] == "payment_followup_30d"
        assert alert["alert_key"] == "elapsed_30d"

    def test_creates_parent_directory(self, tmp_path):
        log_path = tmp_path / "subdir" / "alerts.log"
        task = {"id": "t1", "order_type": "standard_procurement", "client_id": "e_sata"}
        node = {"id": "quote_followup_48h", "name": "Quote Follow-up"}
        with patch("src.alerts.cron_worker.ALERT_LOG_PATH", str(log_path)):
            _fire_alert(task, node)
        assert log_path.exists()


# ---------------------------------------------------------------------------
# check_time_trigger_alerts — integration with mocks
# ---------------------------------------------------------------------------

@allure.feature("Time Triggers")
@allure.story("Check Alerts")
class TestCheckTimeTrigggerAlerts:

    def test_skips_completed_nodes(self):
        task = {"id": "t1", "order_type": "standard_procurement", "client_id": "e_sata"}
        nodes = [{"id": "t1_payment_followup_30d", "task_id": "t1",
                  "status": "completed", "updated_at": int(time.time())}]
        trigger_nodes = [{"id": "payment_followup_30d", "name": "Payment Follow-up",
                          "type": "time_trigger", "trigger_rule": {"type": "elapsed_since_node"}}]
        with patch("src.alerts.cron_worker.get_active_tasks", return_value=[task]), \
             patch("src.alerts.cron_worker.get_node_states", return_value=nodes), \
             patch("src.alerts.cron_worker.get_time_trigger_nodes", return_value=trigger_nodes), \
             patch("src.alerts.cron_worker._fire_alert") as mock_fire:
            check_time_trigger_alerts()
        mock_fire.assert_not_called()

    def test_fires_for_triggered_node(self):
        now = int(time.time())
        task = {"id": "t1", "order_type": "standard_procurement", "client_id": "e_sata"}
        nodes = [
            {"id": "t1_payment_followup_30d", "task_id": "t1",
             "status": "pending", "updated_at": now},
            {"id": "t1_delivery_confirmed", "task_id": "t1",
             "status": "completed", "updated_at": now - 40 * 86400},
        ]
        trigger_nodes = [{"id": "payment_followup_30d", "name": "Payment Follow-up",
                          "type": "time_trigger",
                          "trigger_rule": {"type": "elapsed_since_node",
                                          "reference_node": "delivery_confirmed",
                                          "days": 30}}]
        with patch("src.alerts.cron_worker.get_active_tasks", return_value=[task]), \
             patch("src.alerts.cron_worker.get_node_states", return_value=nodes), \
             patch("src.alerts.cron_worker.get_time_trigger_nodes", return_value=trigger_nodes), \
             patch("src.alerts.cron_worker._evaluate_time_trigger", return_value=["elapsed_30d"]), \
             patch("src.alerts.cron_worker._alert_already_fired", return_value=False), \
             patch("src.alerts.cron_worker._fire_alert") as mock_fire, \
             patch("src.alerts.cron_worker._record_alert_fired"):
            check_time_trigger_alerts()
        mock_fire.assert_called_once()

    def test_dedup_prevents_double_fire(self):
        task = {"id": "t1", "order_type": "standard_procurement", "client_id": "e_sata"}
        nodes = [{"id": "t1_quote_followup_48h", "task_id": "t1",
                  "status": "pending", "updated_at": int(time.time())}]
        trigger_nodes = [{"id": "quote_followup_48h", "name": "Quote Follow-up",
                          "type": "time_trigger",
                          "trigger_rule": {"type": "elapsed_since_node"}}]
        with patch("src.alerts.cron_worker.get_active_tasks", return_value=[task]), \
             patch("src.alerts.cron_worker.get_node_states", return_value=nodes), \
             patch("src.alerts.cron_worker.get_time_trigger_nodes", return_value=trigger_nodes), \
             patch("src.alerts.cron_worker._evaluate_time_trigger", return_value=["elapsed_48h"]), \
             patch("src.alerts.cron_worker._alert_already_fired", return_value=True), \
             patch("src.alerts.cron_worker._fire_alert") as mock_fire:
            check_time_trigger_alerts()
        mock_fire.assert_not_called()
