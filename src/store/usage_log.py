"""
Log external API call usage and compute cost.
"""

import time
import uuid

from src.store.db import transaction, compute_cost


def log_llm_call(
    call_type: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    duration_ms: int,
    message_id: str | None = None,
    task_id: str | None = None,
):
    cost = compute_cost(model, tokens_in, tokens_out)
    with transaction() as conn:
        conn.execute(
            """INSERT INTO usage_log
               (id, call_type, message_id, task_id, tokens_in, tokens_out,
                cost_usd, duration_ms, model, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), call_type, message_id, task_id,
             tokens_in, tokens_out, cost, duration_ms, model, int(time.time())),
        )
    return cost


def log_whisper_call(message_id: str, duration_secs: float, model: str = "large-v3"):
    with transaction() as conn:
        conn.execute(
            """INSERT INTO usage_log
               (id, call_type, message_id, task_id, tokens_in, tokens_out,
                cost_usd, duration_ms, model, ts)
               VALUES (?, 'whisper', ?, NULL, 0, 0, 0.0, ?, ?, ?)""",
            (str(uuid.uuid4()), message_id, int(duration_secs * 1000), model, int(time.time())),
        )
