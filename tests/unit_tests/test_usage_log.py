"""
Unit tests for src/store/usage_log — LLM call logging and cost tracking.
"""

import json
import pytest
import allure
from unittest.mock import patch, MagicMock


@allure.feature("Cost Tracking")
@allure.story("LLM Call Logging")
class TestLogLLMCall:

    def _run(self, **kwargs):
        from src.store.usage_log import log_llm_call
        defaults = {
            "call_type": "update_agent",
            "model": "claude-sonnet-4-6",
            "tokens_in": 1000,
            "tokens_out": 200,
            "duration_ms": 5000,
            "message_id": "msg_001",
            "task_id": "task_001",
        }
        defaults.update(kwargs)
        mock_conn = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("src.store.usage_log.transaction", return_value=mock_cm), \
             patch("src.store.usage_log.compute_cost", return_value=0.006) as mock_cost:
            cost = log_llm_call(**defaults)
        return cost, mock_conn, mock_cost

    def test_returns_cost(self):
        cost, _, _ = self._run()
        assert cost == 0.006

    def test_inserts_to_usage_log(self):
        _, mock_conn, _ = self._run()
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO usage_log" in sql

    def test_passes_model_to_compute_cost(self):
        _, _, mock_cost = self._run(model="claude-haiku-4-5-20251001")
        mock_cost.assert_called_once()
        assert mock_cost.call_args[0][0] == "claude-haiku-4-5-20251001"

    def test_passes_cache_tokens(self):
        _, _, mock_cost = self._run(cache_creation_tokens=100, cache_read_tokens=500)
        args = mock_cost.call_args[0]
        assert args[3] == 100  # cache_creation
        assert args[4] == 500  # cache_read

    def test_default_cache_tokens_zero(self):
        _, _, mock_cost = self._run()
        args = mock_cost.call_args[0]
        assert args[3] == 0
        assert args[4] == 0


@allure.feature("Cost Tracking")
@allure.story("Whisper Call Logging")
class TestLogWhisperCall:

    def test_inserts_whisper_call(self):
        from src.store.usage_log import log_whisper_call
        mock_conn = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("src.store.usage_log.transaction", return_value=mock_cm):
            log_whisper_call("msg_001", 12.5, model="large-v3")
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO usage_log" in sql
        assert "'whisper'" in sql
