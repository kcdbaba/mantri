"""
Tests derived directly from specs/ambiguity_escalation_router.md.
Each test maps to a named example or edge case in the spec.
"""

import pytest
from src.alerts.escalation_router import route_ambiguity, AmbiguityItem, RoutingContext


def item(**kwargs):
    defaults = dict(
        id="a1",
        description="test ambiguity",
        risk_level="medium",
        order_id=None,
        is_irreversible=False,
        message_snippet="maal ready hai",
    )
    defaults.update(kwargs)
    return AmbiguityItem(**defaults)


def ctx(**kwargs):
    defaults = dict(smita_available=True)
    defaults.update(kwargs)
    return RoutingContext(**defaults)


# ---------------------------------------------------------------------------
# Spec examples (verbatim)
# ---------------------------------------------------------------------------

def test_example_1_irreversible_low_risk_goes_to_ashish_immediately():
    decision = route_ambiguity(item(risk_level="low", is_irreversible=True), ctx())
    assert decision.target == "ashish"
    assert decision.urgency == "immediate"
    assert decision.escalate is True


def test_example_2_medium_risk_goes_to_ashish_immediately():
    decision = route_ambiguity(item(risk_level="medium", is_irreversible=False), ctx())
    assert decision.target == "ashish"
    assert decision.urgency == "immediate"


def test_example_3_low_risk_smita_available_goes_to_smita_digest():
    decision = route_ambiguity(item(risk_level="low", is_irreversible=False), ctx(smita_available=True))
    assert decision.target == "smita"
    assert decision.urgency == "next_digest"


def test_example_4_low_risk_smita_unavailable_falls_back_to_ashish():
    decision = route_ambiguity(item(risk_level="low", is_irreversible=False), ctx(smita_available=False))
    assert decision.target == "ashish"
    assert decision.urgency == "next_digest"


def test_example_5_unknown_risk_level_treated_as_high():
    decision = route_ambiguity(item(risk_level="unknown"), ctx())
    assert decision.target == "ashish"
    assert decision.urgency == "immediate"


# ---------------------------------------------------------------------------
# All risk levels
# ---------------------------------------------------------------------------

def test_critical_goes_to_ashish_immediately():
    assert route_ambiguity(item(risk_level="critical"), ctx()).target == "ashish"
    assert route_ambiguity(item(risk_level="critical"), ctx()).urgency == "immediate"


def test_high_goes_to_ashish_immediately():
    assert route_ambiguity(item(risk_level="high"), ctx()).target == "ashish"


# ---------------------------------------------------------------------------
# Invariant: escalate is always True
# ---------------------------------------------------------------------------

def test_escalate_is_always_true_for_all_risk_levels():
    for level in ("low", "medium", "high", "critical"):
        d = route_ambiguity(item(risk_level=level), ctx())
        assert d.escalate is True, f"escalate was False for risk_level={level}"
