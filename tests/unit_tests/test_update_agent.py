"""
Unit tests for src/agent/update_agent — parse, model selection, LLMResponse.
No real LLM calls — tests the deterministic code paths.
"""

import json
import pytest
import allure
from unittest.mock import patch, MagicMock

from src.agent.update_agent import (
    AgentOutput, NodeUpdate, AmbiguityFlag, ItemExtraction, NodeDataExtraction,
    LLMResponse, _parse_raw, _is_gemini_model, _select_model, _is_complex_message,
)


def _valid_output_json(**task_output_overrides):
    """Build valid AgentOutput JSON with the new task_outputs schema."""
    task_output = {
        "task_assignment": "t1",
        "node_updates": [],
        "new_task_candidates": [],
        "ambiguity_flags": [],
        "item_extractions": [],
        "node_data_extractions": [],
        **task_output_overrides,
    }
    return json.dumps({"task_outputs": [task_output]})


# ---------------------------------------------------------------------------
# _parse_raw
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Parse Raw Output")
class TestParseRaw:

    def test_valid_json_parses(self):
        raw = _valid_output_json(node_updates=[
            {"node_id": "client_enquiry", "new_status": "completed",
             "confidence": 0.9, "evidence": "test"}
        ])
        result = _parse_raw(raw, "t1", "m1")
        assert result is not None
        assert len(result.task_outputs) == 1
        assert len(result.task_outputs[0].node_updates) == 1
        assert result.task_outputs[0].node_updates[0].node_id == "client_enquiry"

    def test_markdown_fences_stripped(self):
        raw = f"```json\n{_valid_output_json()}\n```"
        result = _parse_raw(raw, "t1", "m1")
        assert result is not None

    def test_array_wrapped_output_unwrapped(self):
        inner = json.loads(_valid_output_json())
        raw = json.dumps([inner])
        result = _parse_raw(raw, "t1", "m1")
        assert result is not None

    def test_invalid_json_returns_none(self):
        result = _parse_raw("not json at all", "t1", "m1")
        assert result is None

    def test_valid_json_wrong_schema_returns_none(self):
        result = _parse_raw('{"foo": "bar"}', "t1", "m1")
        assert result is None

    def test_empty_output_parses(self):
        raw = _valid_output_json()
        result = _parse_raw(raw, "t1", "m1")
        assert result is not None
        assert result.task_outputs[0].node_updates == []

    def test_full_output_with_all_fields(self):
        raw = _valid_output_json(
            node_updates=[{"node_id": "dispatched", "new_status": "completed",
                           "confidence": 0.95, "evidence": "goods sent"}],
            new_task_candidates=[{"type": "client_notification"}],
            ambiguity_flags=[{"description": "which entity?", "severity": "medium",
                              "category": "entity", "blocking_node_id": None}],
            item_extractions=[{"operation": "add", "description": "atta 50kg"}],
            node_data_extractions=[{"node_id": "dispatched",
                                    "data": {"dispatch_date": "2026-03-25"}}],
        )
        result = _parse_raw(raw, "t1", "m1")
        to = result.task_outputs[0]
        assert len(to.node_updates) == 1
        assert len(to.ambiguity_flags) == 1
        assert len(to.item_extractions) == 1
        assert len(to.node_data_extractions) == 1


# ---------------------------------------------------------------------------
# _is_gemini_model
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Model Detection")
class TestIsGeminiModel:

    def test_gemini_flash(self):
        assert _is_gemini_model("gemini-2.5-flash") is True

    def test_gemini_pro(self):
        assert _is_gemini_model("gemini-2.5-pro") is True

    def test_claude_sonnet(self):
        assert _is_gemini_model("claude-sonnet-4-6") is False

    def test_claude_haiku(self):
        assert _is_gemini_model("claude-haiku-4-5-20251001") is False


# ---------------------------------------------------------------------------
# _is_complex_message
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Message Complexity")
class TestIsComplexMessage:

    def test_short_ack_is_simple(self):
        assert _is_complex_message({"body": "ok"}) is False

    def test_long_message_is_complex(self):
        assert _is_complex_message({"body": "a" * 50}) is True

    def test_numbers_make_complex(self):
        assert _is_complex_message({"body": "50 bags"}) is True

    def test_image_alone_not_complex(self):
        """Images alone don't force Sonnet — Gemini Flash handles vision."""
        assert _is_complex_message({"body": "ok", "image_path": "/tmp/x.jpg"}) is False

    def test_image_with_numbers_is_complex(self):
        assert _is_complex_message({"body": "50 bags atta", "image_path": "/tmp/x.jpg"}) is True

    def test_order_keyword_makes_complex(self):
        assert _is_complex_message({"body": "order done"}) is True

    def test_payment_keyword_makes_complex(self):
        assert _is_complex_message({"body": "payment ho gaya"}) is True

    def test_hindi_quantity_makes_complex(self):
        assert _is_complex_message({"body": "do chahiye"}) is True

    def test_empty_body_is_simple(self):
        assert _is_complex_message({"body": ""}) is False

    def test_none_body_is_simple(self):
        assert _is_complex_message({}) is False


# ---------------------------------------------------------------------------
# _select_model
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Model Selection")
class TestSelectModelTiers:

    def test_simple_batch_uses_gemini(self):
        assert _select_model([{"body": "ok"}, {"body": "thanks"}]) == "gemini-2.5-flash"

    def test_any_complex_uses_sonnet(self):
        assert _select_model([{"body": "ok"}, {"body": "50 bags atta"}]) == "claude-sonnet-4-6"

    def test_single_simple_uses_gemini(self):
        assert _select_model([{"body": "ji"}]) == "gemini-2.5-flash"

    def test_single_complex_uses_sonnet(self):
        assert _select_model([{"body": "delivery 25 march ko"}]) == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# LLMResponse dataclass
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("LLM Response")
class TestLLMResponse:

    def test_basic_creation(self):
        r = LLMResponse(raw='{"test": true}', tokens_in=100, tokens_out=50)
        assert r.raw == '{"test": true}'
        assert r.tokens_in == 100
        assert r.tokens_out == 50
        assert r.cache_creation_tokens == 0
        assert r.cache_read_tokens == 0

    def test_with_cache_tokens(self):
        r = LLMResponse(raw="", tokens_in=100, tokens_out=50,
                         cache_creation_tokens=200, cache_read_tokens=800)
        assert r.cache_creation_tokens == 200
        assert r.cache_read_tokens == 800


# ---------------------------------------------------------------------------
# run_update_agent — input normalization
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Input Normalization")
class TestRunUpdateAgentInputNorm:

    def _valid_raw(self):
        return _valid_output_json()

    _SENTINEL = object()

    def _run_agent(self, messages, resp_raw=None, resp=None, call_returns=_SENTINEL):
        from src.agent.update_agent import run_update_agent
        if resp is None and call_returns is self._SENTINEL:
            resp = LLMResponse(raw=resp_raw or self._valid_raw(), tokens_in=100, tokens_out=50)
        ret = call_returns if call_returns is not self._SENTINEL else resp
        with patch("src.agent.update_agent._call_with_retry", return_value=ret), \
             patch("src.agent.update_agent.log_llm_call"), \
             patch("src.agent.update_agent.build_system_prompt", return_value="sys"), \
             patch("src.agent.update_agent.build_user_section", return_value="user"):
            return run_update_agent(
                "t1", messages,
                node_states_override=[], recent_messages_override=[],
                task_override={"id": "t1", "order_type": "standard_procurement"},
            )

    def test_single_dict_normalized_to_list(self):
        result = self._run_agent({"body": "test", "message_id": "m1"})
        assert result is not None

    def test_api_failure_returns_none(self):
        result = self._run_agent(
            [{"body": "test", "message_id": "m1"}],
            call_returns=None,
        )
        assert result is None

    def test_parse_failure_triggers_retry(self):
        """When first parse fails, retry with correction prompt."""
        from src.agent.update_agent import run_update_agent
        bad_resp = LLMResponse(raw="not valid json", tokens_in=100, tokens_out=50)
        good_resp = LLMResponse(raw=self._valid_raw(), tokens_in=100, tokens_out=50)
        call_count = [0]

        def mock_call(*args, **kwargs):
            call_count[0] += 1
            return bad_resp if call_count[0] == 1 else good_resp

        with patch("src.agent.update_agent._call_with_retry", side_effect=mock_call), \
             patch("src.agent.update_agent.log_llm_call"), \
             patch("src.agent.update_agent.build_system_prompt", return_value="sys"), \
             patch("src.agent.update_agent.build_user_section", return_value="user"):
            result = run_update_agent(
                "t1", [{"body": "test", "message_id": "m1"}],
                node_states_override=[], recent_messages_override=[],
                task_override={"id": "t1", "order_type": "standard_procurement"},
            )
        assert result is not None
        assert call_count[0] == 2  # original + retry

    def test_retry_also_fails_returns_none(self):
        from src.agent.update_agent import run_update_agent
        bad_resp = LLMResponse(raw="bad json", tokens_in=100, tokens_out=50)
        with patch("src.agent.update_agent._call_with_retry", return_value=bad_resp), \
             patch("src.agent.update_agent.log_llm_call"), \
             patch("src.agent.update_agent.build_system_prompt", return_value="sys"), \
             patch("src.agent.update_agent.build_user_section", return_value="user"):
            result = run_update_agent(
                "t1", [{"body": "test", "message_id": "m1"}],
                node_states_override=[], recent_messages_override=[],
                task_override={"id": "t1", "order_type": "standard_procurement"},
            )
        assert result is None

    def test_image_uses_gemini_for_simple_text(self):
        """When image is present but text is simple, Gemini handles vision."""
        from src.agent.update_agent import run_update_agent
        resp = LLMResponse(raw=self._valid_raw(), tokens_in=100, tokens_out=50)
        captured_model = []

        def mock_call(*args, model="", **kwargs):
            captured_model.append(model)
            return resp

        with patch("src.agent.update_agent._call_with_retry", side_effect=mock_call), \
             patch("src.agent.update_agent.log_llm_call"), \
             patch("src.agent.update_agent.build_system_prompt", return_value="sys"), \
             patch("src.agent.update_agent.build_user_section", return_value="user"), \
             patch("src.agent.update_agent._load_image", return_value=(b"img", "image/jpeg")):
            run_update_agent(
                "t1", [{"body": "ok", "message_id": "m1"}],  # simple msg → gemini
                node_states_override=[], recent_messages_override=[],
                task_override={"id": "t1", "order_type": "standard_procurement"},
            )
        assert captured_model[0] == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# _load_image
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Image Loading")
class TestLoadImage:

    def test_no_image_returns_none(self):
        from src.agent.update_agent import _load_image
        result, media = _load_image({"body": "test"})
        assert result is None
        assert media == ""

    def test_image_bytes_returns_jpeg(self):
        from src.agent.update_agent import _load_image
        result, media = _load_image({"image_bytes": b"\xff\xd8", "image_filename": "photo.jpg"})
        assert result == b"\xff\xd8"
        assert media == "image/jpeg"

    def test_image_bytes_png(self):
        from src.agent.update_agent import _load_image
        result, media = _load_image({"image_bytes": b"\x89PNG", "image_filename": "img.png"})
        assert result == b"\x89PNG"
        assert media == "image/png"

    def test_image_path_exists(self, tmp_path):
        from src.agent.update_agent import _load_image
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0")
        result, media = _load_image({"image_path": str(img)})
        assert result == b"\xff\xd8\xff\xe0"
        assert media == "image/jpeg"

    def test_image_path_not_found(self):
        from src.agent.update_agent import _load_image
        result, media = _load_image({"image_path": "/nonexistent/photo.jpg"})
        assert result is None

    def test_image_path_png(self, tmp_path):
        from src.agent.update_agent import _load_image
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")
        result, media = _load_image({"image_path": str(img)})
        assert media == "image/png"


# ---------------------------------------------------------------------------
# _build_user_content
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("User Content Building")
class TestBuildUserContent:

    def test_no_image_returns_string(self):
        from src.agent.update_agent import _build_user_content
        result = _build_user_content("user text", None, "")
        assert result == "user text"

    def test_with_image_returns_list(self):
        from src.agent.update_agent import _build_user_content
        result = _build_user_content("user text", b"\xff\xd8", "image/jpeg")
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "image"
        assert result[1]["type"] == "text"


# ---------------------------------------------------------------------------
# _call_with_retry — dispatch
# ---------------------------------------------------------------------------

@allure.feature("Update Agent")
@allure.story("Call Dispatch")
class TestCallWithRetry:

    def test_gemini_model_dispatches_to_gemini(self):
        from src.agent.update_agent import _call_with_retry
        mock_resp = LLMResponse(raw="{}", tokens_in=10, tokens_out=5)
        with patch("src.agent.update_agent._call_gemini_with_retry", return_value=mock_resp) as mock_g:
            result = _call_with_retry("sys", "user", "m1", "t1", model="gemini-2.5-flash")
        mock_g.assert_called_once()
        assert result == mock_resp

    def test_claude_model_dispatches_to_anthropic(self):
        from src.agent.update_agent import _call_with_retry
        mock_resp = LLMResponse(raw="{}", tokens_in=10, tokens_out=5)
        with patch("src.agent.update_agent._call_anthropic_with_retry", return_value=mock_resp) as mock_a:
            result = _call_with_retry("sys", "user", "m1", "t1", model="claude-sonnet-4-6")
        mock_a.assert_called_once()

    def test_anthropic_success(self):
        from src.agent.update_agent import _call_anthropic_with_retry
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"test": true}')]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_response.usage.cache_creation_input_tokens = 0
        mock_response.usage.cache_read_input_tokens = 0
        with patch("src.agent.update_agent._anthropic_client") as mock_client:
            mock_client.messages.create.return_value = mock_response
            result = _call_anthropic_with_retry("sys", "user", "m1", "t1", max_retries=1)
        assert result is not None
        assert result.raw == '{"test": true}'
        assert result.tokens_in == 100

    def test_anthropic_api_error_retries_and_fails(self):
        from src.agent.update_agent import _call_anthropic_with_retry
        import anthropic
        with patch("src.agent.update_agent._anthropic_client") as mock_client, \
             patch("time.sleep"):
            mock_client.messages.create.side_effect = anthropic.APIStatusError(
                "rate limited", response=MagicMock(status_code=429), body=None)
            result = _call_anthropic_with_retry("sys", "user", "m1", "t1", max_retries=2)
        assert result is None
        assert mock_client.messages.create.call_count == 2

    def test_gemini_success(self):
        from src.agent.update_agent import _call_gemini_with_retry
        mock_response = MagicMock()
        mock_response.text = '{"result": true}'
        mock_response.usage_metadata.prompt_token_count = 200
        mock_response.usage_metadata.candidates_token_count = 30
        with patch("src.agent.update_agent._get_gemini_client") as mock_get:
            mock_get.return_value.models.generate_content.return_value = mock_response
            result = _call_gemini_with_retry("sys", "user", "m1", "t1", max_retries=1)
        assert result is not None
        assert result.raw == '{"result": true}'

    def test_gemini_api_error_retries_and_fails(self):
        from src.agent.update_agent import _call_gemini_with_retry
        from google.genai import errors as _genai_errors
        with patch("src.agent.update_agent._get_gemini_client") as mock_get, \
             patch("time.sleep"):
            mock_get.return_value.models.generate_content.side_effect = _genai_errors.APIError(
                429, {"message": "quota exceeded"})
            result = _call_gemini_with_retry("sys", "user", "m1", "t1", max_retries=2)
        assert result is None
