"""
Ambiguity escalation router.
Spec: specs/ambiguity_escalation_router.md
"""

from dataclasses import dataclass, field


VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}


@dataclass
class AmbiguityItem:
    id: str
    description: str
    risk_level: str
    message_snippet: str
    order_id: str | None = None
    is_irreversible: bool = False


@dataclass
class RoutingContext:
    smita_available: bool = True


@dataclass
class EscalationDecision:
    target: str
    urgency: str
    reason: str
    escalate: bool = True


def route_ambiguity(item: AmbiguityItem, ctx: RoutingContext) -> EscalationDecision:
    """Route an ambiguity item per specs/ambiguity_escalation_router.md."""

    # Rule 1 — irreversible overrides everything
    if item.is_irreversible:
        return EscalationDecision(
            target="ashish",
            urgency="immediate",
            reason="Irreversible action — overrides risk level, escalates to Ashish immediately",
        )

    level = item.risk_level
    if level not in VALID_RISK_LEVELS:
        return EscalationDecision(
            target="ashish",
            urgency="immediate",
            reason=f"Unrecognised risk level '{level}' — treating as high, escalating to Ashish immediately",
        )

    # Rules 2–4: medium and above → Ashish immediately
    if level in ("critical", "high", "medium"):
        return EscalationDecision(
            target="ashish",
            urgency="immediate",
            reason=f"{level.capitalize()} risk — routes to Ashish immediately per escalation policy",
        )

    # Rule 5–6: low risk
    if ctx.smita_available:
        return EscalationDecision(
            target="smita",
            urgency="next_digest",
            reason="Low risk — routes to senior staff (Smita) for next digest",
        )
    return EscalationDecision(
        target="ashish",
        urgency="next_digest",
        reason="Low risk — Smita unavailable, falls back to Ashish for next digest",
    )
