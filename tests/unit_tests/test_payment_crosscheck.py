"""
TDD practice: payment cross-group detection
Red → Green → Refactor cycle

Business rule (from Ashish Part 1 interview):
  Every payment has a two-step log protocol:
  1. Screenshot posted to supplier/task group (usually done)
  2. Same screenshot + narration posted to Payments group (frequently missed)

  If step 2 is missing within ~2 hours of step 1, flag it.

We are writing tests for a function:
    find_unlogged_payments(messages: list[dict], window_hours: float = 2.0) -> list[dict]

Each message dict has:
    {
        "id": str,
        "group": str,         # group name
        "sender": str,
        "text": str,
        "timestamp": datetime,
        "has_payment_screenshot": bool,  # True if message contains a payment image/UPI ref
    }

Returns list of "orphaned" payment messages — payments in non-payments groups
with no matching entry in the Payments group within window_hours.
"""

from datetime import datetime, timedelta
import pytest

# This import will FAIL until we implement the function — that's the Red step
from src.payments.crosscheck import find_unlogged_payments


def _msg(id, group, text, has_screenshot, minutes_offset=0):
    return {
        "id": id,
        "group": group,
        "sender": "Ashish",
        "text": text,
        "timestamp": datetime(2026, 3, 27, 10, 0) + timedelta(minutes=minutes_offset),
        "has_payment_screenshot": has_screenshot,
    }


# ---------------------------------------------------------------------------
# Basic detection
# ---------------------------------------------------------------------------

def test_payment_with_no_payments_group_entry_is_flagged():
    """Payment screenshot in supplier group, nothing in Payments group → flagged."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "payment done", has_screenshot=True, minutes_offset=0),
    ]
    result = find_unlogged_payments(messages)
    assert len(result) == 1
    assert result[0]["id"] == "m1"


def test_payment_logged_in_payments_group_within_window_is_not_flagged():
    """Payment in supplier group + matching entry in Payments group within 2h → clean."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "payment done", has_screenshot=True, minutes_offset=0),
        _msg("m2", "Payments",           "payment to Kapoor",  has_screenshot=True, minutes_offset=90),
    ]
    result = find_unlogged_payments(messages)
    assert result == []


def test_payment_logged_in_payments_group_outside_window_is_flagged():
    """Payment logged in Payments group but >2h later → still flagged."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "payment done", has_screenshot=True, minutes_offset=0),
        _msg("m2", "Payments",           "payment to Kapoor",  has_screenshot=True, minutes_offset=150),
    ]
    result = find_unlogged_payments(messages)
    assert len(result) == 1
    assert result[0]["id"] == "m1"


def test_non_screenshot_message_not_flagged():
    """A text message with no screenshot in a supplier group → ignored."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "bhai kab aayega maal", has_screenshot=False),
    ]
    result = find_unlogged_payments(messages)
    assert result == []


def test_payment_already_in_payments_group_not_double_flagged():
    """A screenshot posted directly to the Payments group is not an orphan."""
    messages = [
        _msg("m1", "Payments", "payment to Kapoor", has_screenshot=True),
    ]
    result = find_unlogged_payments(messages)
    assert result == []


# ---------------------------------------------------------------------------
# Multiple payments
# ---------------------------------------------------------------------------

def test_multiple_payments_only_unlogged_ones_flagged():
    """Two payments: one logged, one not → only the unlogged one flagged."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "payment 1", has_screenshot=True, minutes_offset=0),
        _msg("m2", "Payments",           "payment 1 logged", has_screenshot=True, minutes_offset=30),
        _msg("m3", "JRBK Group",         "payment 2", has_screenshot=True, minutes_offset=60),
        # no payments group entry for m3
    ]
    result = find_unlogged_payments(messages)
    assert len(result) == 1
    assert result[0]["id"] == "m3"


def test_configurable_window():
    """Window can be tightened — 30 min window should flag a 45-min-late entry."""
    messages = [
        _msg("m1", "Kapoor Steel Group", "payment done", has_screenshot=True, minutes_offset=0),
        _msg("m2", "Payments",           "payment logged",     has_screenshot=True, minutes_offset=45),
    ]
    result = find_unlogged_payments(messages, window_hours=0.5)
    assert len(result) == 1
    assert result[0]["id"] == "m1"
