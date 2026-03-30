"""
Stateful update agent — single LLM call per (message, task) pair.

Input:  enriched message + current node states + last N messages for task
Output: NodeUpdate list (validated via pydantic)
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

import re

from src.config import CLAUDE_MODEL, CLAUDE_MODEL_FAST, AGENT_MAX_TOKENS, MAX_CONTEXT_MESSAGES, AGENT_ERROR_LOG_PATH
from src.store.task_store import get_node_states, get_recent_messages, get_order_items
from src.store.usage_log import log_llm_call
from src.agent.prompt import build_system_prompt, build_user_section

log = logging.getLogger(__name__)
client = anthropic.Anthropic()


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


def _select_model(message: dict) -> str:
    """Choose Sonnet for complex messages, Haiku for trivial ones.

    Trivial: short body (≤40 chars), no numbers, no quantity/business keywords,
    no image. Everything else gets Sonnet to avoid missing state changes.
    """
    body = (message.get("body") or "").strip()
    has_image = bool(message.get("image_path") or message.get("image_bytes"))

    if has_image:
        return CLAUDE_MODEL  # vision messages need full reasoning
    if len(body) > _SIMPLE_MESSAGE_MAX_LEN:
        return CLAUDE_MODEL  # longer messages may contain items/quantities
    if _HAS_NUMBERS.search(body):
        return CLAUDE_MODEL  # numbers often mean quantities, prices, dates
    if _QUANTITY_WORDS.search(body):
        return CLAUDE_MODEL  # business-relevant keywords need full reasoning
    return CLAUDE_MODEL_FAST


def run_update_agent(
    task_id: str,
    message: dict,
    node_states_override: list[dict] | None = None,
    recent_messages_override: list[dict] | None = None,
    items_override: list[dict] | None = None,
    task_override: dict | None = None,
    routing_confidence: float = 1.0,
) -> AgentOutput | None:
    """
    Run the update agent for a single (task_id, message) pair.
    Returns AgentOutput on success, None on unrecoverable failure.

    *_override params: used by the eval framework to inject test state
    instead of reading from the DB.
    """
    node_states = node_states_override if node_states_override is not None else get_node_states(task_id)
    recent_messages = recent_messages_override if recent_messages_override is not None else get_recent_messages(task_id, limit=MAX_CONTEXT_MESSAGES)
    current_items = items_override if items_override is not None else get_order_items(task_id)

    system_prompt = build_system_prompt(task_id, task=task_override)
    user_section = build_user_section(node_states, recent_messages, message, current_items,
                                       routing_confidence=routing_confidence)

    # Load image if message carries one (vision path)
    image_bytes, image_media_type = _load_image(message)

    model = _select_model(message)

    t0 = time.time()
    response = _call_with_retry(
        system_prompt, user_section, message.get("message_id"), task_id,
        image_bytes=image_bytes, image_media_type=image_media_type,
        model=model,
    )
    duration_ms = int((time.time() - t0) * 1000)

    if response is None:
        return None

    log_llm_call(
        call_type="update_agent",
        model=model,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        duration_ms=duration_ms,
        message_id=message.get("message_id"),
        task_id=task_id,
        cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
    )

    raw = response.content[0].text
    # Strip markdown fences if model wrapped output despite instructions
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        parsed = AgentOutput.model_validate(json.loads(cleaned))
        return parsed
    except (json.JSONDecodeError, ValidationError) as e:
        log.error("Agent output validation failed for task=%s message=%s: %s",
                  task_id, message.get("message_id"), e)
        _log_error(raw, task_id, message.get("message_id"))
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


def _call_with_retry(system_prompt: str, user_section: str,
                     message_id: str | None, task_id: str,
                     image_bytes: bytes | None = None,
                     image_media_type: str = "",
                     model: str = CLAUDE_MODEL,
                     max_retries: int = 3) -> anthropic.types.Message | None:
    content = _build_user_content(user_section, image_bytes, image_media_type)
    system_with_cache = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
    ]
    delay = 1
    for attempt in range(max_retries):
        try:
            return client.messages.create(
                model=model,
                max_tokens=AGENT_MAX_TOKENS,
                system=system_with_cache,
                messages=[{"role": "user", "content": content}],
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


def _log_error(raw: str, task_id: str, message_id: str | None):
    with open(AGENT_ERROR_LOG_PATH, "a") as f:
        f.write(f"\n--- task={task_id} message={message_id} ---\n{raw}\n")
