"""
LLM backward context matcher — assigns unassigned scraps to conversations
by asking Gemini Flash whether they belong to a nearby assigned conversation.

Called after scrap detection + conversation building. For each assigned scrap
with entity evidence, looks backward within 16 working hours for unassigned
scraps and asks the LLM to judge relevance.

Cost: ~$0.002 per replay on Gemini Flash free tier.
"""

import json
import logging
import os
import time
from dataclasses import dataclass

from src.conversation.working_hours import working_hours_between
from src.conversation.scrap_detector import Scrap

log = logging.getLogger(__name__)

LOOKBACK_WORKING_HOURS = 16.0
MIN_WORKING_HOURS = 1.0  # skip candidates within 1wh — handled by time-based backprop

CONTEXT_MATCH_PROMPT = """You are analyzing messages from an internal staff WhatsApp group for an Army supply business in Guwahati, India. Messages are in Hindi/Hinglish/English mix.

A message has been identified as belonging to this conversation:
ENTITY: {entity_ref}
ANCHOR MESSAGE: {anchor_text}

Below are earlier UNASSIGNED messages from the group. For each, judge whether it belongs to the same conversation (about the same order, entity, or topic).

CANDIDATES:
{candidates_text}

For each candidate, return true if it belongs to this conversation, false if not.
Return ONLY valid JSON array:
[{{"id": 0, "belongs": true/false, "reason": "brief reason"}}]
"""


@dataclass
class MatchResult:
    scrap_id: str
    belongs: bool
    reason: str


def match_backward_context(
    assigned_scraps: list[tuple[Scrap, str]],  # [(scrap, entity_ref)]
    all_scraps: list[Scrap],
    assigned_ids: set[str],
) -> list[tuple[str, str]]:
    """
    For each assigned scrap, look backward for unassigned scraps within
    16 working hours and ask Gemini Flash if they belong.

    Args:
        assigned_scraps: list of (scrap, entity_ref) for scraps with entity evidence
        all_scraps: all scraps sorted chronologically
        assigned_ids: set of already-assigned scrap IDs

    Returns: list of (scrap_id, entity_ref) for newly matched scraps
    """
    # Build index for fast lookup
    scrap_by_id = {s.id: s for s in all_scraps}
    sorted_scraps = sorted(all_scraps, key=lambda s: s.first_msg_ts)
    scrap_indices = {s.id: i for i, s in enumerate(sorted_scraps)}

    new_assignments = []
    total_calls = 0
    total_matched = 0

    for scrap, entity_ref in assigned_scraps:
        idx = scrap_indices.get(scrap.id)
        if idx is None:
            continue

        # Collect unassigned candidates within working hours window
        candidates = []
        for j in range(idx - 1, -1, -1):
            prev = sorted_scraps[j]
            wh = working_hours_between(prev.last_msg_ts, scrap.first_msg_ts)
            if wh > LOOKBACK_WORKING_HOURS:
                break
            if wh <= MIN_WORKING_HOURS:
                continue
            if prev.id in assigned_ids:
                continue
            # Get text content
            text = " ".join(
                (m.get("body") or "")[:50]
                for m in prev.messages
                if (m.get("body") or "").strip()
            ).strip()
            if not text:
                continue
            candidates.append({
                "idx": len(candidates),
                "scrap": prev,
                "wh_before": wh,
                "text": text[:100],
                "sender": prev.sender_jid[:25],
            })

        if not candidates:
            continue

        # Build anchor text
        anchor_text = " ".join(
            (m.get("body") or "")[:60]
            for m in scrap.messages
            if (m.get("body") or "").strip()
        ).strip()[:200]

        # Ask LLM
        results = _call_llm_judge(entity_ref, anchor_text, candidates)
        total_calls += 1

        for result in results:
            if result.belongs:
                cand = candidates[result.scrap_id] if result.scrap_id < len(candidates) else None
                if cand:
                    new_assignments.append((cand["scrap"].id, entity_ref))
                    assigned_ids.add(cand["scrap"].id)
                    total_matched += 1
                    log.info("LLM context match: scrap %s → %s (%s)",
                             cand["scrap"].id, entity_ref, result.reason)

    log.info("LLM backward context: %d calls, %d scraps matched, %d total candidates evaluated",
             total_calls, total_matched, sum(1 for _ in new_assignments))

    return new_assignments


def _call_llm_judge(entity_ref: str, anchor_text: str,
                     candidates: list[dict]) -> list[MatchResult]:
    """Call Gemini Flash to judge candidate scraps."""
    candidates_text = "\n".join(
        f"[{c['idx']}] ({c['sender']}, {c['wh_before']:.1f}wh before): \"{c['text']}\""
        for c in candidates
    )

    prompt = CONTEXT_MATCH_PROMPT.format(
        entity_ref=entity_ref,
        anchor_text=anchor_text,
        candidates_text=candidates_text,
    )

    result = _try_gemini(prompt)
    if result is None:
        return []

    results = []
    if isinstance(result, list):
        for item in result:
            results.append(MatchResult(
                scrap_id=item.get("id", -1),
                belongs=item.get("belongs", False),
                reason=item.get("reason", ""),
            ))
    return results


def _try_gemini(prompt: str, max_retries: int = 2) -> list | None:
    """Call Gemini Flash with JSON response."""
    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            log.warning("GOOGLE_API_KEY not set — skipping LLM context matching")
            return None

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            max_output_tokens=2000,
            temperature=0.0,
            response_mime_type="application/json",
        )
        if True:  # Gemini 2.5 Flash
            config.thinking_config = types.ThinkingConfig(thinking_budget=0)

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=config,
                )
                text = response.text or ""
                return json.loads(text)
            except Exception as e:
                log.warning("Gemini context match failed (attempt %d/%d): %s",
                            attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(1)
    except ImportError:
        log.warning("google-genai not installed — skipping LLM context matching")
    return None
