"""
Linkage agent — single LLM call per non-noise message.

Coordinates M:N item allocation across all open client and supplier orders.
Input:  open orders summary + current fulfilment links + new message
Output: LinkageAgentOutput (validated via pydantic)
"""

import json
import logging
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from src.config import CLAUDE_MODEL, AGENT_MAX_TOKENS, AGENT_ERROR_LOG_PATH
from src.store.usage_log import log_llm_call
from src.linkage.prompt import build_system_prompt, build_user_section

log = logging.getLogger(__name__)
client = anthropic.Anthropic()


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

    t0 = time.time()
    response = _call_with_retry(system_prompt, user_section, message.get("message_id"))
    duration_ms = int((time.time() - t0) * 1000)

    if response is None:
        return None

    log_llm_call(
        call_type="linkage_agent",
        model=CLAUDE_MODEL,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        duration_ms=duration_ms,
        message_id=message.get("message_id"),
        task_id="linkage",
    )

    raw = response.content[0].text
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return LinkageAgentOutput.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as e:
        log.error("Linkage agent output validation failed for message=%s: %s",
                  message.get("message_id"), e)
        _log_error(raw, message.get("message_id"))
        return None


def _call_with_retry(
    system_prompt: str,
    user_section: str,
    message_id: str | None,
    max_retries: int = 3,
) -> anthropic.types.Message | None:
    delay = 1
    for attempt in range(max_retries):
        try:
            return client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=AGENT_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_section}],
            )
        except anthropic.APIStatusError as e:
            log.warning("Linkage agent API error (attempt %d/%d): %s",
                        attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 4
            else:
                log.error("Linkage agent API failed after %d attempts for message=%s",
                          max_retries, message_id)
                return None


def _log_error(raw: str, message_id: str | None):
    with open(AGENT_ERROR_LOG_PATH, "a") as f:
        f.write(f"\n--- linkage_agent message={message_id} ---\n{raw}\n")
