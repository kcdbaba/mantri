"""
Reply-tree conversation threading — reconstruct human conversations
from group chat messages.

Task-agnostic: doesn't try to identify entities or tasks. Just answers
"who is talking to whom" by scoring each message as a potential reply
to preceding messages.

Output: a reply-tree where each message points to its most likely parent.
Connected components of the tree = human conversations.
"""

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Reply signal weights
W_TEMPORAL_CLOSE = 3.0      # different sender, < 60s
W_TEMPORAL_MODERATE = 1.5   # different sender, < 300s
W_TEMPORAL_DISTANT = 0.5    # different sender, < 900s
W_SAME_SENDER_CONTINUE = 2.0  # same sender, < 120s (continuing own thought)
W_AFFIRMATION = 2.0         # "haan", "ok", "ji" — replying to someone
W_QUESTION_ANSWER = 1.5     # previous was question, this has answer shape
W_NUMERIC_CONTEXT = 1.0     # both messages have numbers/quantities
W_TOPIC_OVERLAP = 1.5       # shared keywords between messages

# Minimum score to consider a message as a reply
MIN_REPLY_SCORE = 2.0

# Max lookback window (messages, not time)
MAX_LOOKBACK = 15

# Max time gap to even consider a reply relationship
MAX_GAP_S = 900  # 15 minutes

# Affirmation patterns
_AFFIRMATION_RE = re.compile(
    r'^(?:ha+n?|ok|ji|done|theek|thik|acha|accha|sahi|bilkul|'
    r'yes|yeah|yep|sure|ho gaya|ho gya|kar diya|kar diyaa|'
    r'bhej diya|sent|delivered|received)[\s.!?]*$',
    re.IGNORECASE,
)

# Question patterns
_QUESTION_RE = re.compile(
    r'(?:\?|kya|kitna|kitne|kab|kahan|kaun|konsa|kaise|'
    r'hai na|hai kya|hoga|milega|doge|chahiye|bolo|batao)',
    re.IGNORECASE,
)

# Numeric pattern
_NUMERIC_RE = re.compile(r'\d+')

# Common stop words to exclude from topic overlap
_STOP_WORDS = {
    "hai", "ka", "ke", "ki", "se", "ko", "pe", "mein", "ye", "wo",
    "the", "is", "a", "an", "and", "or", "but", "in", "on", "for",
    "sir", "ji", "bhai", "ok", "ha", "haan", "nahi", "aur",
}


@dataclass
class ThreadedMessage:
    """A message with its reply-tree linkage."""
    idx: int                    # position in filtered message list
    message_id: str
    sender: str
    body: str
    timestamp: int
    timestamp_raw: str
    parent_idx: int | None = None   # index of parent message (None = root)
    reply_score: float = 0.0        # confidence in the parent link
    reply_reason: str = ""
    thread_id: int | None = None    # assigned after tree is built


def build_reply_tree(messages: list[dict]) -> list[ThreadedMessage]:
    """
    Build a reply-tree from chronological messages.

    For each message, scores it against preceding messages as a potential
    reply. The highest-scoring predecessor above MIN_REPLY_SCORE becomes
    the parent. Messages with no suitable parent are thread roots.

    Args:
        messages: chronological list of message dicts (empty messages
                  should be pre-filtered)

    Returns: list of ThreadedMessage with parent_idx and thread_id set
    """
    threaded = []

    for i, msg in enumerate(messages):
        body = (msg.get("body") or "").strip()
        sender = msg.get("sender_jid", "")
        ts = msg.get("timestamp", 0)

        tm = ThreadedMessage(
            idx=i,
            message_id=msg.get("message_id", f"msg_{i}"),
            sender=sender[:30],
            body=body,
            timestamp=ts,
            timestamp_raw=msg.get("timestamp_raw", ""),
        )

        # Score against preceding messages
        best_score = 0.0
        best_idx = None
        best_reason = ""

        start = max(0, i - MAX_LOOKBACK)
        for j in range(i - 1, start - 1, -1):
            prev = threaded[j]
            gap = ts - prev.timestamp

            if gap > MAX_GAP_S:
                break  # too far back

            score, reason = _score_reply(tm, prev, gap)

            if score > best_score:
                best_score = score
                best_idx = j
                best_reason = reason

        if best_score >= MIN_REPLY_SCORE:
            tm.parent_idx = best_idx
            tm.reply_score = best_score
            tm.reply_reason = best_reason

        threaded.append(tm)

    # Assign thread IDs via connected components
    _assign_thread_ids(threaded)

    return threaded


def _score_reply(current: ThreadedMessage, previous: ThreadedMessage,
                 gap_s: int) -> tuple[float, str]:
    """
    Score how likely 'current' is a reply to 'previous'.
    Returns (score, reason).
    """
    score = 0.0
    reasons = []
    same_sender = current.sender == previous.sender

    # ── Temporal signal ──────────────────────────────────────────
    if same_sender:
        if gap_s <= 120:
            score += W_SAME_SENDER_CONTINUE
            reasons.append(f"same_sender_continue({gap_s}s)")
    else:
        if gap_s <= 60:
            score += W_TEMPORAL_CLOSE
            reasons.append(f"close_reply({gap_s}s)")
        elif gap_s <= 300:
            score += W_TEMPORAL_MODERATE
            reasons.append(f"moderate_reply({gap_s}s)")
        elif gap_s <= 900:
            score += W_TEMPORAL_DISTANT
            reasons.append(f"distant_reply({gap_s}s)")

    # ── Affirmation signal ───────────────────────────────────────
    if not same_sender and _AFFIRMATION_RE.match(current.body):
        score += W_AFFIRMATION
        reasons.append("affirmation")

    # ── Question-answer signal ───────────────────────────────────
    if not same_sender:
        prev_is_question = bool(_QUESTION_RE.search(previous.body))
        curr_is_answer = not _QUESTION_RE.search(current.body)
        if prev_is_question and curr_is_answer and gap_s <= 300:
            score += W_QUESTION_ANSWER
            reasons.append("question_answer")

    # ── Numeric context ──────────────────────────────────────────
    if _NUMERIC_RE.search(current.body) and _NUMERIC_RE.search(previous.body):
        if gap_s <= 300:
            score += W_NUMERIC_CONTEXT
            reasons.append("numeric_context")

    # ── Topic overlap ────────────────────────────────────────────
    curr_words = _extract_keywords(current.body)
    prev_words = _extract_keywords(previous.body)
    overlap = curr_words & prev_words
    if overlap and not same_sender:
        score += W_TOPIC_OVERLAP
        reasons.append(f"topic_overlap({','.join(list(overlap)[:3])})")
    elif overlap and same_sender:
        score += W_TOPIC_OVERLAP * 0.5
        reasons.append(f"self_topic({','.join(list(overlap)[:3])})")

    return score, " + ".join(reasons) if reasons else ""


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text for topic overlap."""
    words = set(re.findall(r'[a-zA-Z\u0900-\u097F]{3,}', text.lower()))
    return words - _STOP_WORDS


def _assign_thread_ids(messages: list[ThreadedMessage]):
    """Assign thread_id to each message by finding connected components."""
    # Find roots (messages with no parent)
    # Then walk from each root to assign thread IDs

    # Build children lookup
    children: dict[int, list[int]] = {}
    for m in messages:
        if m.parent_idx is not None:
            children.setdefault(m.parent_idx, []).append(m.idx)

    # Find roots
    has_parent = {m.idx for m in messages if m.parent_idx is not None}
    roots = [m.idx for m in messages if m.idx not in has_parent]

    # BFS from each root
    thread_id = 0
    visited = set()

    for root in roots:
        if root in visited:
            continue
        thread_id += 1
        queue = [root]
        while queue:
            idx = queue.pop(0)
            if idx in visited:
                continue
            visited.add(idx)
            messages[idx].thread_id = thread_id
            # Add children
            for child_idx in children.get(idx, []):
                queue.append(child_idx)

    # Handle any orphans (shouldn't happen but safety)
    for m in messages:
        if m.thread_id is None:
            thread_id += 1
            m.thread_id = thread_id


def summarize_threads(messages: list[ThreadedMessage]) -> list[dict]:
    """
    Summarize the detected conversation threads.
    Returns list of thread summaries sorted by size.
    """
    threads: dict[int, list[ThreadedMessage]] = {}
    for m in messages:
        threads.setdefault(m.thread_id, []).append(m)

    summaries = []
    for tid, members in sorted(threads.items(), key=lambda x: -len(x[1])):
        senders = set(m.sender for m in members)
        first_ts = members[0].timestamp_raw
        last_ts = members[-1].timestamp_raw
        duration_s = members[-1].timestamp - members[0].timestamp

        # Thread shape: root message + replies
        root = next((m for m in members if m.parent_idx is None), members[0])

        summaries.append({
            "thread_id": tid,
            "message_count": len(members),
            "sender_count": len(senders),
            "senders": sorted(senders),
            "first_ts": first_ts,
            "last_ts": last_ts,
            "duration_s": duration_s,
            "root_body": root.body[:80],
            "sample_bodies": [m.body[:60] for m in members[:5] if m.body],
        })

    return summaries
