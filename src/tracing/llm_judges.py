"""
LLM-based judges — compare actual vs expected output using an LLM for
fuzzy/semantic evaluation. Uses Gemini Flash (free tier) by default.

Judge dimensions:
  - Item name matching (multilingual fuzzy)
  - Node update semantic correctness
  - Ambiguity quality (should have flagged / shouldn't have)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_gemini_client = None


def _get_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _gemini_client


GEMINI_JUDGE_MODEL = "gemini-2.5-flash"


@dataclass
class LLMJudgment:
    dimension: str
    message_id: str
    score: float          # 0.0 - 1.0
    verdict: str          # "PASS", "PARTIAL", "FAIL"
    reasoning: str
    details: dict = field(default_factory=dict)


# ── Item name matching judge ──────────────────────────────────────────

ITEM_MATCH_PROMPT = """You are evaluating an AI agent's item extraction from a WhatsApp message.
The message is in Hindi/Hinglish/English. Item names may appear in different forms.
{drift_section}
MESSAGE: {body}

AGENT EXTRACTED ITEMS (from the actual system):
{actual_items}

EXPECTED ITEMS (ground truth):
{expected_items}

For each expected item, determine if the agent extracted a matching item.
Items match if they refer to the same product even if named differently
(e.g., "atta" = "wheat flour", "daal" = "dal" = "pulses",
"AC" = "air conditioner", "fridge" = "refrigerator").

Quantity and unit matching: if expected has quantity/unit, the actual must
match within 10% tolerance. If expected has null quantity, skip quantity check.

Respond with ONLY valid JSON:
{{
  "matches": [
    {{"expected": "...", "actual": "...", "match": true/false, "reason": "..."}}
  ],
  "precision": <float 0-1>,
  "recall": <float 0-1>,
  "verdict": "PASS" or "PARTIAL" or "FAIL"
}}
"""


FUZZY_MATCH_THRESHOLD = 0.85


def _try_rapidfuzz_match(expected_items: list[dict],
                          actual_items: list[dict]) -> tuple[bool, list[dict]]:
    """
    Try to match expected items against actual items using rapidfuzz.
    Returns (all_matched, match_details).
    If all expected items match with score >= FUZZY_MATCH_THRESHOLD, skip LLM.
    """
    from rapidfuzz import fuzz

    matches = []
    all_matched = True

    for exp in expected_items:
        desc_contains = (exp.get("description_contains") or "").lower()
        if not desc_contains:
            continue

        best_score = 0
        best_actual = None
        for act in actual_items:
            act_desc = (act.get("description") or "").lower()
            # Try both partial ratio and token_set_ratio for robustness
            score = max(
                fuzz.partial_ratio(desc_contains, act_desc) / 100.0,
                fuzz.token_set_ratio(desc_contains, act_desc) / 100.0,
            )
            if score > best_score:
                best_score = score
                best_actual = act_desc

        matched = best_score >= FUZZY_MATCH_THRESHOLD
        if not matched:
            all_matched = False

        matches.append({
            "expected": desc_contains,
            "actual": best_actual or "(none)",
            "match": matched,
            "fuzzy_score": round(best_score, 3),
            "reason": "rapidfuzz" if matched else f"fuzzy_score={best_score:.2f} < {FUZZY_MATCH_THRESHOLD}",
        })

    return all_matched, matches


def judge_items(message_id: str, body: str,
                actual_items: list[dict],
                expected_items: list[dict],
                drift_context: str = "") -> LLMJudgment:
    """Match actual vs expected items. Uses rapidfuzz first, falls back to LLM."""
    if not expected_items:
        return LLMJudgment(
            dimension="item_match", message_id=message_id,
            score=1.0, verdict="PASS",
            reasoning="No items expected",
        )

    # Try rapidfuzz first — skip LLM if all items match
    all_matched, fuzzy_matches = _try_rapidfuzz_match(expected_items, actual_items)
    if all_matched:
        matched_count = sum(1 for m in fuzzy_matches if m["match"])
        return LLMJudgment(
            dimension="item_match", message_id=message_id,
            score=matched_count / len(fuzzy_matches) if fuzzy_matches else 1.0,
            verdict="PASS",
            reasoning=json.dumps(fuzzy_matches, ensure_ascii=False),
            details={"method": "rapidfuzz", "matches": fuzzy_matches},
        )

    # Fuzzy matching inconclusive — escalate to LLM
    log.debug("rapidfuzz inconclusive for %s, escalating to LLM judge", message_id)

    prompt = ITEM_MATCH_PROMPT.format(
        body=body,
        actual_items=json.dumps(actual_items, ensure_ascii=False),
        expected_items=json.dumps(expected_items, ensure_ascii=False),
        drift_section=drift_context,
    )

    result = _call_judge(prompt)
    if result is None:
        # LLM also failed — use rapidfuzz results as fallback
        matched_count = sum(1 for m in fuzzy_matches if m["match"])
        return LLMJudgment(
            dimension="item_match", message_id=message_id,
            score=matched_count / len(fuzzy_matches) if fuzzy_matches else 0.0,
            verdict="PARTIAL" if matched_count > 0 else "FAIL",
            reasoning=f"LLM failed, using rapidfuzz: {json.dumps(fuzzy_matches)}",
            details={"method": "rapidfuzz_fallback", "matches": fuzzy_matches},
        )

    return LLMJudgment(
        dimension="item_match",
        message_id=message_id,
        score=result.get("recall", 0.0),
        verdict=result.get("verdict", "FAIL"),
        reasoning=json.dumps(result.get("matches", []), ensure_ascii=False),
        details=result,
    )


# ── Node update semantic judge ────────────────────────────────────────

NODE_JUDGE_PROMPT = """You are evaluating an AI agent's node status updates for an operations management system.
{drift_section}
MESSAGE: {body}

TASK TYPE: {order_type}
TASK ID: {task_id}

AGENT'S NODE UPDATES (what the agent actually did):
{actual_updates}

EXPECTED NODE UPDATES (ground truth):
{expected_updates}

CURRENT NODE STATES (before this message):
{current_states}

For each expected update, determine if the agent's update is semantically correct:
- Is the right node being updated?
- Is the status appropriate given the message content?
- A status of "active" when "completed" was expected is PARTIAL (not wrong, just conservative)
- A status of "provisional" when "completed" was expected is also PARTIAL
- An update to a completely wrong node is FAIL

Respond with ONLY valid JSON:
{{
  "assessments": [
    {{"node_id": "...", "expected_status": "...", "actual_status": "...",
      "correct": true/false, "partial": true/false, "reason": "..."}}
  ],
  "score": <float 0-1>,
  "verdict": "PASS" or "PARTIAL" or "FAIL"
}}
"""


def judge_node_updates(message_id: str, body: str, task_id: str,
                       order_type: str, actual_updates: list[dict],
                       expected_updates: list[dict],
                       current_states: dict,
                       drift_context: str = "") -> LLMJudgment:
    """Use Gemini to semantically evaluate node update correctness."""
    if not expected_updates:
        return LLMJudgment(
            dimension="node_semantic", message_id=message_id,
            score=1.0, verdict="PASS",
            reasoning="No node updates expected",
        )

    prompt = NODE_JUDGE_PROMPT.format(
        body=body,
        order_type=order_type,
        task_id=task_id,
        actual_updates=json.dumps(actual_updates, ensure_ascii=False),
        expected_updates=json.dumps(expected_updates, ensure_ascii=False),
        current_states=json.dumps(current_states, ensure_ascii=False),
        drift_section=drift_context,
    )

    result = _call_judge(prompt)
    if result is None:
        return LLMJudgment(
            dimension="node_semantic", message_id=message_id,
            score=0.0, verdict="FAIL",
            reasoning="Judge call failed",
        )

    return LLMJudgment(
        dimension="node_semantic",
        message_id=message_id,
        score=result.get("score", 0.0),
        verdict=result.get("verdict", "FAIL"),
        reasoning=json.dumps(result.get("assessments", []), ensure_ascii=False),
        details=result,
    )


# ── Ambiguity quality judge ──────────────────────────────────────────

AMBIGUITY_JUDGE_PROMPT = """You are evaluating whether an AI agent correctly identified ambiguity in a WhatsApp message.
{drift_section}
MESSAGE: {body}

CONTEXT: This message is part of a {order_type} order. The task involves tracking procurement operations.

AGENT'S AMBIGUITY FLAGS:
{actual_flags}

EXPECTED AMBIGUITY FLAGS (ground truth — may be empty if no ambiguity expected):
{expected_flags}

Think step by step:
1. Is the message genuinely ambiguous in any way? (entity unclear, quantity unclear, timing unclear, etc.)
2. For each flag the agent raised, is it genuinely ambiguous?
3. Did the agent miss any ambiguity that should have been flagged?
4. Is the severity level appropriate?

Respond with ONLY valid JSON:
{{
  "correct_flags": ["description of correctly identified ambiguity"],
  "false_flags": ["description of incorrectly flagged ambiguity"],
  "missed_flags": ["description of ambiguity agent missed"],
  "severity_appropriate": true/false,
  "score": <float 0-1>,
  "verdict": "PASS" or "PARTIAL" or "FAIL"
}}
"""


def judge_ambiguity(message_id: str, body: str, order_type: str,
                    actual_flags: list[dict],
                    expected_flags: list[dict],
                    drift_context: str = "") -> LLMJudgment:
    """Use Gemini to evaluate ambiguity detection quality."""
    if not actual_flags and not expected_flags:
        return LLMJudgment(
            dimension="ambiguity", message_id=message_id,
            score=1.0, verdict="PASS",
            reasoning="No flags expected or raised",
        )

    prompt = AMBIGUITY_JUDGE_PROMPT.format(
        body=body,
        order_type=order_type,
        actual_flags=json.dumps(actual_flags, ensure_ascii=False),
        expected_flags=json.dumps(expected_flags, ensure_ascii=False),
        drift_section=drift_context,
    )

    result = _call_judge(prompt)
    if result is None:
        return LLMJudgment(
            dimension="ambiguity", message_id=message_id,
            score=0.0, verdict="FAIL",
            reasoning="Judge call failed",
        )

    return LLMJudgment(
        dimension="ambiguity",
        message_id=message_id,
        score=result.get("score", 0.0),
        verdict=result.get("verdict", "FAIL"),
        reasoning=json.dumps({
            "correct": result.get("correct_flags", []),
            "false": result.get("false_flags", []),
            "missed": result.get("missed_flags", []),
        }, ensure_ascii=False),
        details=result,
    )


# ── Gemini call helper ───────────────────────────────────────────────

def _call_judge(prompt: str, max_retries: int = 2) -> dict | None:
    """Call LLM judge with fallback chain: Gemini → Mistral → Anthropic Haiku.
    Returns parsed JSON or None."""
    # Try Gemini first (free tier, 1000 req/day)
    result = _try_gemini(prompt, max_retries)
    if result is not None:
        return result

    # Fallback: Mistral (1B free tokens/month)
    result = _try_mistral(prompt)
    if result is not None:
        return result

    # Last resort: Anthropic Haiku (cheap, uses existing client)
    result = _try_anthropic_haiku(prompt)
    if result is not None:
        return result

    log.error("All judge backends failed")
    return None


def _try_gemini(prompt: str, max_retries: int = 2) -> dict | None:
    """Try Gemini Flash judge."""
    try:
        from google.genai import types
        client = _get_client()

        config = types.GenerateContentConfig(
            max_output_tokens=2000,
            temperature=0.0,
            response_mime_type="application/json",
        )
        if "2.5" in GEMINI_JUDGE_MODEL:
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=GEMINI_JUDGE_MODEL,
                    contents=prompt,
                    config=config,
                )
                text = response.text or ""
                return json.loads(text)
            except Exception as e:
                log.warning("Gemini judge failed (attempt %d/%d): %s",
                            attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(1)
    except Exception as e:
        log.warning("Gemini judge unavailable: %s", e)
    return None


def _try_mistral(prompt: str) -> dict | None:
    """Try Mistral API judge (free tier: 1B tokens/month)."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        log.debug("MISTRAL_API_KEY not set, skipping Mistral fallback")
        return None

    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)
        response = client.chat.complete(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        return json.loads(text)
    except Exception as e:
        log.warning("Mistral judge failed: %s", e)
    return None


def _try_anthropic_haiku(prompt: str) -> dict | None:
    """Last resort: Anthropic Haiku. Only used if ALLOW_PAID_JUDGE=1 is set,
    since this costs money unlike Gemini/Mistral free tiers."""
    if not os.environ.get("ALLOW_PAID_JUDGE"):
        log.debug("Skipping paid Anthropic judge (set ALLOW_PAID_JUDGE=1 to enable)")
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        # Strip markdown fences if present
        if text.strip().startswith("```"):
            lines = text.strip().splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return json.loads(text)
    except Exception as e:
        log.warning("Anthropic Haiku judge failed: %s", e)
    return None


def run_llm_judges(baselines_path, replay_result_path,
                   trace_df=None,
                   staleness_report=None) -> list[LLMJudgment]:
    """
    Run LLM judges on messages that have non-trivial expected outputs.
    Only judges messages where deterministic checking is insufficient.

    If staleness_report is provided and baselines are stale, injects
    drift context into judge prompts so the judge can adjust scoring.

    Returns list of LLMJudgment objects.
    """
    from pathlib import Path
    from src.tracing.staleness import build_drift_prompt_section

    baselines = json.loads(Path(baselines_path).read_text())
    replay = json.loads(Path(replay_result_path).read_text())
    state = replay["state"]

    # Build drift context if baselines are stale
    drift_context = ""
    if staleness_report and staleness_report.stale:
        drift_context = build_drift_prompt_section(staleness_report)
        log.info("Baselines are stale — injecting drift context into judge prompts")

    # Build item lookup
    actual_items = {}
    for task_id, items in state.get("items", {}).items():
        actual_items[task_id] = items

    # Build ambiguity flags lookup
    actual_ambiguity = state.get("ambiguity_flags", [])

    judgments = []

    for msg_bl in baselines["messages"]:
        msg_id = msg_bl["message_id"]
        body = msg_bl.get("body_summary", "")
        task_id = msg_bl.get("expected_task_id")

        if task_id is None:
            continue  # skip noise/unrouted

        # ── Item matching (only if expected items exist) ──
        expected_items = msg_bl.get("expected_items", [])
        if expected_items:
            task_items = actual_items.get(task_id, [])
            j = judge_items(msg_id, body, task_items, expected_items,
                           drift_context=drift_context)
            judgments.append(j)

        # ── Ambiguity (only if flags expected or many flags raised) ──
        expected_flags = msg_bl.get("expected_ambiguity", [])
        task_flags = [f for f in actual_ambiguity if f.get("task_id") == task_id]
        if expected_flags or len(task_flags) > 2:
            order_type = "client_order"  # simplified for now
            j = judge_ambiguity(msg_id, body, order_type, task_flags, expected_flags,
                               drift_context=drift_context)
            judgments.append(j)

    return judgments
