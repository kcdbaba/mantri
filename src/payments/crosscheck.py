"""
Payment cross-group detection.

Detects payments that appear in a supplier/task group but have no
corresponding entry in the Payments group within a configurable window.
"""

from datetime import timedelta


PAYMENTS_GROUP = "Payments"


def find_unlogged_payments(messages: list[dict], window_hours: float = 2.0) -> list[dict]:
    """
    Return payment messages that are orphaned — posted in a non-Payments group
    with no matching payment screenshot in the Payments group within window_hours.

    A match only requires another screenshot in the Payments group within the
    time window; content matching is not attempted (screenshots are images).
    """
    window = timedelta(hours=window_hours)

    payments_group_times = [
        m["timestamp"]
        for m in messages
        if m["group"] == PAYMENTS_GROUP and m["has_payment_screenshot"]
    ]

    orphans = []
    for m in messages:
        if not m["has_payment_screenshot"]:
            continue
        if m["group"] == PAYMENTS_GROUP:
            continue
        # Check if any Payments group screenshot falls within the window after this message
        matched = any(
            timedelta(0) <= (pt - m["timestamp"]) <= window
            for pt in payments_group_times
        )
        if not matched:
            orphans.append(m)

    return orphans
