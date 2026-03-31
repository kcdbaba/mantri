"""
Stateful update agent — single LLM call per (message, task) pair.

Input:  enriched message + current node states + last N messages for task
Output: NodeUpdate list (validated via pydantic)
"""

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

import re

from src.config import (
    CLAUDE_MODEL, CLAUDE_MODEL_FAST, GEMINI_MODEL,
    AGENT_MAX_TOKENS, MAX_CONTEXT_MESSAGES, AGENT_ERROR_LOG_PATH,
)
from src.store.task_store import get_node_states, get_recent_messages, get_order_items
from src.store.usage_log import log_llm_call
from src.agent.prompt import build_system_prompt, build_user_section

log = logging.getLogger(__name__)
_anthropic_client = anthropic.Anthropic()
_gemini_client = None  # lazy init


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _gemini_client


class NodeUpdate(BaseModel):
    node_id: str
    new_status: Literal["pending", "active", "in_progress", "completed", "blocked",
                        "provisional", "skipped", "failed", "partial"]
    confidence: float
    evidence: str


class AmbiguityFlag(BaseModel):
    description: str
    severity: Literal["high", "medium", "low"]
    category: Literal["entity", "quantity", "status", "timing", "linkage"]
    blocking_node_id: str | None = None  # gate node to block until resolved


class NodeDataExtraction(BaseModel):
    node_id: str
    data: dict  # merged (not replaced) into task_nodes.node_data for this node


class ItemExtraction(BaseModel):
    operation: Literal["add", "update", "remove"]
    description: str                        # new description (add/update) or item to remove
    unit: str | None = None
    quantity: float | None = None
    specs: str | None = None
    existing_description: str | None = None  # for update/remove: which existing item to match


class AgentOutput(BaseModel):
    task_assignment: str = ""  # existing task_id, "new", or "" (use current task)
    new_task_order_type: str | None = None  # only when task_assignment == "new"
    node_updates: list[NodeUpdate]
    new_task_candidates: list[dict] = []
    ambiguity_flags: list[AmbiguityFlag] = []
    item_extractions: list[ItemExtraction] = []
    node_data_extractions: list[NodeDataExtraction] = []


_HAS_NUMBERS = re.compile(r'\d')
_QUANTITY_WORDS = re.compile(
    r'\b(one|two|three|four|five|six|seven|eight|nine|ten|'
    r'ek|do|teen|char|panch|che|saat|aath|nau|das|'
    r'order|cancel|confirm|deliver|dispatch|payment|paid|rate|price|'
    r'kitna|kitne|kितna)\b', re.IGNORECASE
)
_SIMPLE_MESSAGE_MAX_LEN = 40


def _is_complex_message(message: dict) -> bool:
    """Return True if a message needs Sonnet-level reasoning."""
    body = (message.get("body") or "").strip()
    has_image = bool(message.get("image_path") or message.get("image_bytes"))
    if has_image:
        return True
    if len(body) > _SIMPLE_MESSAGE_MAX_LEN:
        return True
    if _HAS_NUMBERS.search(body):
        return True
    if _QUANTITY_WORDS.search(body):
        return True
    return False


def _select_model(messages: list[dict]) -> str:
    """Three-tier model selection:
      - Complex messages (numbers, items, images, long) → Sonnet (best accuracy)
      - Simple messages (acks, short, no business content) → Gemini Flash (cheapest, fastest)
    """
    if any(_is_complex_message(m) for m in messages):
        return CLAUDE_MODEL
    return GEMINI_MODEL


def _is_gemini_model(model: str) -> bool:
    return model.startswith("gemini")


def run_update_agent(
    task_id: str,
    messages: list[dict],
    node_states_override: list[dict] | None = None,
    recent_messages_override: list[dict] | None = None,
    items_override: list[dict] | None = None,
    task_override: dict | None = None,
    routing_confidence: float = 1.0,
    entity_tasks: list[dict] | None = None,
) -> AgentOutput | None:
    """
    Run the update agent for a batch of messages for a single task_id.
    One LLM call per batch. Returns AgentOutput on success, None on failure.

    *_override params: used by the eval framework to inject test state
    instead of reading from the DB.
    """
    # Normalise single message dict to list
    if isinstance(messages, dict):
        messages = [messages]

    node_states = node_states_override if node_states_override is not None else get_node_states(task_id)
    recent_messages = recent_messages_override if recent_messages_override is not None else get_recent_messages(task_id, limit=MAX_CONTEXT_MESSAGES)
    current_items = items_override if items_override is not None else get_order_items(task_id)

    system_prompt = build_system_prompt(task_id, task=task_override)
    user_section = build_user_section(node_states, recent_messages, messages, current_items,
                                       routing_confidence=routing_confidence,
                                       entity_tasks=entity_tasks)

    # Load image from the last message that has one (vision path)
    image_bytes, image_media_type = None, ""
    for msg in messages:
        ib, imt = _load_image(msg)
        if ib:
            image_bytes, image_media_type = ib, imt

    model = _select_model(messages)

    last_message_id = messages[-1].get("message_id")

    # Images force Anthropic (Gemini image path not yet implemented)
    if image_bytes and _is_gemini_model(model):
        model = CLAUDE_MODEL
        log.debug("Image present — overriding to %s", model)

    t0 = time.time()
    resp = _call_with_retry(
        system_prompt, user_section, last_message_id, task_id,
        image_bytes=image_bytes, image_media_type=image_media_type,
        model=model,
    )
    duration_ms = int((time.time() - t0) * 1000)

    if resp is None:
        return None

    log_llm_call(
        call_type="update_agent",
        model=model,
        tokens_in=resp.tokens_in,
        tokens_out=resp.tokens_out,
        duration_ms=duration_ms,
        message_id=last_message_id,
        task_id=task_id,
        cache_creation_tokens=resp.cache_creation_tokens,
        cache_read_tokens=resp.cache_read_tokens,
    )

    parsed = _parse_raw(resp.raw, task_id, last_message_id)
    if parsed is not None:
        return parsed

    # First parse failed — retry once with a correction prompt
    log.warning("Retrying with correction prompt for task=%s message=%s", task_id, last_message_id)
    retry_resp = _call_with_retry(
        system_prompt,
        f"Your previous response was not valid JSON. Here it is:\n\n{resp.raw[:2000]}\n\n"
        f"Please respond again with ONLY valid JSON matching the output format. "
        f"No markdown fences, no prose.\n\n{user_section}",
        last_message_id, task_id,
        image_bytes=image_bytes, image_media_type=image_media_type,
        model=model, max_retries=1,
    )
    if retry_resp is None:
        return None

    log_llm_call(
        call_type="update_agent_retry",
        model=model,
        tokens_in=retry_resp.tokens_in,
        tokens_out=retry_resp.tokens_out,
        duration_ms=int((time.time() - t0) * 1000),
        message_id=last_message_id,
        task_id=task_id,
        cache_creation_tokens=retry_resp.cache_creation_tokens,
        cache_read_tokens=retry_resp.cache_read_tokens,
    )

    return _parse_raw(retry_resp.raw, task_id, last_message_id)


@dataclass
class LLMResponse:
    """Unified response from any LLM backend."""
    raw: str
    tokens_in: int
    tokens_out: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


def _parse_raw(raw: str, task_id: str, message_id: str | None) -> AgentOutput | None:
    """Parse and validate raw LLM output into AgentOutput. Returns None on failure."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(cleaned)
        # Some models wrap output in an array
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        return AgentOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.error("Agent output validation failed for task=%s message=%s: %s",
                  task_id, message_id, e)
        _log_error(raw, task_id, message_id)
        return None


def _load_image(message: dict) -> tuple[bytes | None, str]:
    """
    Return (image_bytes, media_type) if the message carries an image, else (None, '').
    Supports:
      - message["image_path"]: local file path (Sprint 3 / eval)
      - message["image_bytes"]: already-loaded bytes (future: WhatsApp download)
    """
    if "image_bytes" in message:
        ext = (message.get("image_filename") or "").rsplit(".", 1)[-1].lower()
        media_type = "image/png" if ext == "png" else "image/jpeg"
        return message["image_bytes"], media_type

    image_path = message.get("image_path")
    if image_path:
        p = Path(image_path)
        if p.exists():
            ext = p.suffix.lower().lstrip(".")
            media_type = "image/png" if ext == "png" else "image/jpeg"
            return p.read_bytes(), media_type
        else:
            log.warning("image_path not found: %s", image_path)

    return None, ""


def _build_user_content(user_section: str, image_bytes: bytes | None, image_media_type: str):
    """Return content for the API call — plain string or multipart list with image."""
    if image_bytes is None:
        return user_section
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type,
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            },
        },
        {"type": "text", "text": user_section},
    ]


def _call_anthropic_with_retry(system_prompt: str, user_section: str,
                               message_id: str | None, task_id: str,
                               image_bytes: bytes | None = None,
                               image_media_type: str = "",
                               model: str = CLAUDE_MODEL,
                               max_retries: int = 3) -> LLMResponse | None:
    content = _build_user_content(user_section, image_bytes, image_media_type)
    system_with_cache = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
    ]
    delay = 1
    for attempt in range(max_retries):
        try:
            response = _anthropic_client.messages.create(
                model=model,
                max_tokens=AGENT_MAX_TOKENS,
                system=system_with_cache,
                messages=[{"role": "user", "content": content}],
            )
            return LLMResponse(
                raw=response.content[0].text,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            )
        except anthropic.APIStatusError as e:
            log.warning("Claude API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 4
            else:
                log.error("Claude API failed after %d attempts for task=%s message=%s",
                          max_retries, task_id, message_id)
                return None


def _call_gemini_with_retry(system_prompt: str, user_section: str,
                            message_id: str | None, task_id: str,
                            model: str = GEMINI_MODEL,
                            max_retries: int = 3) -> LLMResponse | None:
    from google.genai import types
    client = _get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=AGENT_MAX_TOKENS * 4,
        temperature=0.0,
        response_mime_type="application/json",
    )
    if "2.5" in model:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)

    delay = 1
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model, contents=user_section, config=config,
            )
            um = response.usage_metadata
            return LLMResponse(
                raw=response.text or "",
                tokens_in=getattr(um, "prompt_token_count", 0) or 0,
                tokens_out=getattr(um, "candidates_token_count", 0) or 0,
            )
        except Exception as e:
            log.warning("Gemini API error (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 4
            else:
                log.error("Gemini API failed after %d attempts for task=%s message=%s",
                          max_retries, task_id, message_id)
                return None


def _call_with_retry(system_prompt: str, user_section: str,
                     message_id: str | None, task_id: str,
                     image_bytes: bytes | None = None,
                     image_media_type: str = "",
                     model: str = CLAUDE_MODEL,
                     max_retries: int = 3) -> LLMResponse | None:
    """Dispatch to the correct backend based on model name."""
    if _is_gemini_model(model):
        # Gemini doesn't support image via this path yet
        return _call_gemini_with_retry(
            system_prompt, user_section, message_id, task_id,
            model=model, max_retries=max_retries,
        )
    return _call_anthropic_with_retry(
        system_prompt, user_section, message_id, task_id,
        image_bytes=image_bytes, image_media_type=image_media_type,
        model=model, max_retries=max_retries,
    )


def _log_error(raw: str, task_id: str, message_id: str | None):
    with open(AGENT_ERROR_LOG_PATH, "a") as f:
        f.write(f"\n--- task={task_id} message={message_id} ---\n{raw}\n")
