"""
Linkage agent — single LLM call per non-noise message.

Coordinates M:N item allocation across all open client and supplier orders.
Input:  open orders summary + current fulfilment links + new message
Output: LinkageAgentOutput (validated via pydantic)

Model: defaults to Sonnet (quality risk 9/10 — false positive dispatch is
irreversible). Gemini Flash available via LINKAGE_MODEL config override for
benchmarking, but not recommended for production.
"""

import json
import logging
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from src.config import CLAUDE_MODEL, AGENT_MAX_TOKENS, AGENT_ERROR_LOG_PATH
from src.agent.update_agent import (
    LLMResponse, _is_gemini_model,
    _call_anthropic_with_retry, _call_gemini_with_retry,
)
from src.store.usage_log import log_llm_call
from src.linkage.prompt import build_system_prompt, build_user_section

log = logging.getLogger(__name__)

# Linkage always uses Sonnet — override only for benchmarking
LINKAGE_MODEL = CLAUDE_MODEL


class LinkageUpdate(BaseModel):
    client_order_id: str
    client_item_description: str
    supplier_order_id: str
    supplier_item_description: str
    quantity_allocated: float
    match_confidence: float
    match_reasoning: str
    status: Literal["confirmed", "candidate", "failed", "fulfilled", "completed", "invalidated"]


class ClientOrderUpdate(BaseModel):
    order_id: str
    node_id: str
    new_status: Literal["pending", "active", "completed", "partial", "blocked"]
    confidence: float
    evidence: str


class LinkageAmbiguityFlag(BaseModel):
    description: str
    severity: Literal["high", "medium", "low"]
    category: Literal["entity", "quantity", "status", "timing", "linkage"]
    blocking_node_id: str | None = None
    affected_task_ids: list[str] = []


class LinkageAgentOutput(BaseModel):
    linkage_updates: list[LinkageUpdate] = []
    client_order_updates: list[ClientOrderUpdate] = []
    new_task_candidates: list[dict] = []
    ambiguity_flags: list[LinkageAmbiguityFlag] = []


def run_linkage_agent(
    open_orders: dict,
    fulfillment_links: list[dict],
    message: dict,
) -> LinkageAgentOutput | None:
    """
    Run the linkage agent for a single message.
    Returns LinkageAgentOutput on success, None on unrecoverable failure.
    """
    system_prompt = build_system_prompt()
    user_section = build_user_section(open_orders, fulfillment_links, message)
    model = LINKAGE_MODEL
    message_id = message.get("message_id")

    t0 = time.time()
    if _is_gemini_model(model):
        resp = _call_gemini_with_retry(
            system_prompt, user_section, message_id, "linkage",
            model=model,
        )
    else:
        resp = _call_anthropic_with_retry(
            system_prompt, user_section, message_id, "linkage",
            model=model,
        )
    duration_ms = int((time.time() - t0) * 1000)

    if resp is None:
        return None

    log_llm_call(
        call_type="linkage_agent",
        model=model,
        tokens_in=resp.tokens_in,
        tokens_out=resp.tokens_out,
        duration_ms=duration_ms,
        message_id=message_id,
        task_id="linkage",
        cache_creation_tokens=resp.cache_creation_tokens,
        cache_read_tokens=resp.cache_read_tokens,
    )

    raw = resp.raw
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(cleaned)
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        return LinkageAgentOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        log.error("Linkage agent output validation failed for message=%s: %s",
                  message_id, e)
        _log_error(raw, message_id)
        return None


def _log_error(raw: str, message_id: str | None):
    with open(AGENT_ERROR_LOG_PATH, "a") as f:
        f.write(f"\n--- linkage_agent message={message_id} ---\n{raw}\n")
