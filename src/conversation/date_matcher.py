"""
Date-based deterministic matcher — matches unassigned scraps to conversations
by correlating message timestamps with known order timeline events.

Timeline events are extracted from task node_data:
  - delivery_deadline, required_by_date → client expects delivery around this date
  - dispatch_date → items were dispatched, delivery tracking expected
  - payment_date → payment made, confirmation expected
  - confirmed_at → order confirmed, fulfillment activity expected

If an unassigned scrap's timestamp falls within a window around a timeline
event, and the scrap contains delivery/status keywords, it's matched
deterministically — no LLM needed.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.conversation.scrap_detector import Scrap

log = logging.getLogger(__name__)

# Keywords that indicate a message is about delivery STATUS (not price quotes)
# Excluded: "transport" (matches transport charges), "tomorrow/today" (too generic)
DELIVERY_KEYWORDS = re.compile(
    r'\b(?:deliver(?:y|ed)?|dispatch(?:ed)?|aaya|nahi\s*aaya|pahuch|pahus|pahusa|'
    r'aa\s*gaya|aa\s*gya|bhej\s*diya|received|collect(?:ed)?|'
    r'builty|consignment|otp|packet|pkt|'
    r'nhi\s*aaya|nhi\s*pahusa|arrived|coming|'
    r'godown|installation)\b',
    re.IGNORECASE,
)

PAYMENT_KEYWORDS = re.compile(
    r'\b(?:paid|payment|paytm|phonepe|upi|money\s*sent|'
    r'bank\s*transfer|₹|\d+/-)\b',
    re.IGNORECASE,
)

# Window around a timeline event to consider a match
DELIVERY_WINDOW_DAYS = 2  # +/- 2 days around expected delivery
PAYMENT_WINDOW_DAYS = 1   # +/- 1 day around payment date


@dataclass
class TimelineEvent:
    """A known date-bound event from task node_data."""
    task_id: str
    entity_ref: str
    event_type: str       # "delivery", "dispatch", "payment", "confirmation"
    date: datetime
    node_id: str
    details: str = ""


@dataclass
class DateMatch:
    """Result of a date-based match."""
    scrap_id: str
    entity_ref: str
    event: TimelineEvent
    reason: str


def extract_timeline(tasks: list[dict], node_states: dict,
                     task_entities: dict[str, str]) -> list[TimelineEvent]:
    """
    Extract timeline events from task node_data.

    Args:
        tasks: list of task dicts from get_active_tasks()
        node_states: {task_id: [node_dicts]} from replay_result or DB
        task_entities: {task_id: entity_ref}
    """
    events = []

    for task_id, nodes in node_states.items():
        entity_ref = task_entities.get(task_id, task_id)

        for node in nodes:
            node_data_raw = node.get("node_data") or node.get("data")
            if not node_data_raw:
                continue

            if isinstance(node_data_raw, str):
                try:
                    node_data = json.loads(node_data_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                node_data = node_data_raw

            nid = node.get("id", "").split("_", 1)[-1] if "_" in node.get("id", "") else node.get("id", "")

            # Extract dates from known fields
            date_fields = {
                "delivery_deadline": "delivery",
                "required_by_date": "delivery",
                "dispatch_date": "dispatch",
                "payment_date": "payment",
                "confirmed_at": "confirmation",
                "expected_delivery_date": "delivery",
            }

            for field_name, event_type in date_fields.items():
                date_str = node_data.get(field_name)
                if not date_str:
                    continue
                dt = _parse_date(date_str)
                if dt:
                    events.append(TimelineEvent(
                        task_id=task_id,
                        entity_ref=entity_ref,
                        event_type=event_type,
                        date=dt,
                        node_id=nid,
                        details=f"{field_name}={date_str}",
                    ))

    log.info("Extracted %d timeline events from %d tasks", len(events), len(node_states))
    return events


def match_by_date(unassigned_scraps: list[Scrap],
                  timeline: list[TimelineEvent]) -> list[DateMatch]:
    """
    Match unassigned scraps to timeline events by date proximity + keywords.

    A scrap matches if:
    1. Its timestamp falls within the delivery/payment window of a timeline event
    2. Its text contains delivery or payment keywords
    """
    if not timeline:
        return []

    matches = []

    for scrap in unassigned_scraps:
        scrap_text = " ".join(
            (m.get("body") or "") for m in scrap.messages
        ).strip()

        if not scrap_text:
            continue

        scrap_date = datetime.fromtimestamp(scrap.first_msg_ts)
        has_delivery_kw = bool(DELIVERY_KEYWORDS.search(scrap_text))
        has_payment_kw = bool(PAYMENT_KEYWORDS.search(scrap_text))

        if not has_delivery_kw and not has_payment_kw:
            continue

        # Check against each timeline event
        matching_events = []

        for event in timeline:
            if event.event_type in ("delivery", "dispatch"):
                window = timedelta(days=DELIVERY_WINDOW_DAYS)
                if not has_delivery_kw:
                    continue
            elif event.event_type == "payment":
                window = timedelta(days=PAYMENT_WINDOW_DAYS)
                if not has_payment_kw:
                    continue
            else:
                window = timedelta(days=DELIVERY_WINDOW_DAYS)

            distance = abs((scrap_date - event.date).total_seconds())
            if distance <= window.total_seconds():
                matching_events.append((event, distance))

        # Only match if exactly ONE entity matches. Multiple entities on the
        # same date = ambiguous, leave for LLM to resolve.
        if matching_events:
            unique_entities = set(ev.entity_ref for ev, _ in matching_events)
            if len(unique_entities) > 1:
                log.debug("Date match AMBIGUOUS for scrap %s: %d entities match (%s)",
                          scrap.id, len(unique_entities),
                          ", ".join(unique_entities))
                continue
            best_match = min(matching_events, key=lambda x: x[1])[0]
        else:
            best_match = None

        if best_match:
            days_diff = (scrap_date - best_match.date).days
            reason = (f"Message on {scrap_date.strftime('%m/%d')} matches "
                      f"{best_match.event_type} event on {best_match.date.strftime('%m/%d')} "
                      f"for {best_match.entity_ref} ({days_diff:+d} days, "
                      f"node={best_match.node_id})")
            matches.append(DateMatch(
                scrap_id=scrap.id,
                entity_ref=best_match.entity_ref,
                event=best_match,
                reason=reason,
            ))
            log.info("Date match: scrap %s → %s (%s)", scrap.id, best_match.entity_ref, reason)

    log.info("Date matching: %d matches from %d unassigned scraps", len(matches), len(unassigned_scraps))
    return matches


def _parse_date(s: str) -> datetime | None:
    """Parse a date string in common formats."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
                "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None
