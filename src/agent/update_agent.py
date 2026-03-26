"""
Stateful update agent — single LLM call per (message, task) pair.

Input:  enriched message + current node states + last N messages for task
Output: NodeUpdate list (validated via pydantic)
"""

import json
import logging
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from src.config import CLAUDE_MODEL, AGENT_MAX_TOKENS, MAX_CONTEXT_MESSAGES, AGENT_ERROR_LOG_PATH
from src.store.task_store import get_node_states, get_recent_messages
from src.store.usage_log import log_llm_call
from src.agent.prompt import build_system_prompt, build_user_section

log = logging.getLogger(__name__)
client = anthropic.Anthropic()


class NodeUpdate(BaseModel):
    node_id: str
    new_status: Literal["pending", "active", "completed", "blocked", "provisional"]
    confidence: float
    evidence: str


class AgentOutput(BaseModel):
    node_updates: list[NodeUpdate]
    new_task_candidates: list[dict] = []
    ambiguity_flags: list[str] = []


def run_update_agent(task_id: str, message: dict) -> AgentOutput | None:
    """
    Run the update agent for a single (task_id, message) pair.
    Returns AgentOutput on success, None on unrecoverable failure.
    """
    node_states = get_node_states(task_id)
    recent_messages = get_recent_messages(task_id, limit=MAX_CONTEXT_MESSAGES)

    system_prompt = build_system_prompt(task_id)
    user_section = build_user_section(node_states, recent_messages, message)

    t0 = time.time()
    response = _call_with_retry(system_prompt, user_section, message.get("message_id"), task_id)
    duration_ms = int((time.time() - t0) * 1000)

    if response is None:
        return None

    log_llm_call(
        call_type="update_agent",
        model=CLAUDE_MODEL,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        duration_ms=duration_ms,
        message_id=message.get("message_id"),
        task_id=task_id,
    )

    raw = response.content[0].text
    try:
        parsed = AgentOutput.model_validate(json.loads(raw))
        return parsed
    except (json.JSONDecodeError, ValidationError) as e:
        log.error("Agent output validation failed for task=%s message=%s: %s",
                  task_id, message.get("message_id"), e)
        _log_error(raw, task_id, message.get("message_id"))
        return None


def _call_with_retry(system_prompt: str, user_section: str,
                     message_id: str | None, task_id: str,
                     max_retries: int = 3) -> anthropic.types.Message | None:
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
