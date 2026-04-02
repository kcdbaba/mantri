"""
Agent output cache — avoids repeat LLM calls during testing.

Caches update_agent and linkage_agent responses keyed by input signature.
On cache hit, returns the stored response without API call.

Cache keys:
  - Update agent: (task_id, frozenset(message_ids))
  - Conversation agent: (scrap_id, entity_ref)

Cache storage: JSON file alongside the test case.
"""

import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path

log = logging.getLogger(__name__)

_cache: dict[str, dict] = {}
_cache_path: str | None = None
_cache_hits = 0
_cache_misses = 0


def init_cache(path: str):
    """Load cache from file. Create if doesn't exist."""
    global _cache, _cache_path
    _cache_path = path
    p = Path(path)
    if p.exists():
        try:
            _cache = json.loads(p.read_text())
            log.info("Agent cache loaded: %d entries from %s", len(_cache), path)
        except (json.JSONDecodeError, ValueError):
            _cache = {}
    else:
        _cache = {}


def save_cache():
    """Persist cache to file."""
    if _cache_path:
        Path(_cache_path).write_text(
            json.dumps(_cache, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        log.info("Agent cache saved: %d entries, %d hits, %d misses",
                 len(_cache), _cache_hits, _cache_misses)


def make_key(task_id: str, message_ids: list[str]) -> str:
    """Generate cache key for an update agent call."""
    sig = f"{task_id}:{','.join(sorted(message_ids))}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def make_scrap_key(scrap_id: str, entity_ref: str) -> str:
    """Generate cache key for a conversation agent call."""
    sig = f"scrap:{scrap_id}:{entity_ref}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def get(key: str) -> dict | None:
    """Get cached agent output. Returns None on miss."""
    global _cache_hits, _cache_misses
    if key in _cache:
        _cache_hits += 1
        log.debug("Cache HIT: %s", key)
        return _cache[key]
    _cache_misses += 1
    return None


def put(key: str, output: dict):
    """Store agent output in cache."""
    _cache[key] = output
    log.debug("Cache PUT: %s", key)


def stats() -> dict:
    """Return cache statistics."""
    return {
        "entries": len(_cache),
        "hits": _cache_hits,
        "misses": _cache_misses,
        "hit_rate": _cache_hits / max(_cache_hits + _cache_misses, 1),
    }


def load_from_replay_result(replay_result_path: str, task_entities: dict[str, str] | None = None):
    """
    Pre-populate cache from an existing replay result.

    Reads the replay_result.db usage_log to reconstruct which messages
    were sent to which tasks, then stores the final state as cached output.

    This allows re-running with conversation routing enabled without
    re-calling the update agent for direct-mapped group messages.
    """
    import sqlite3

    db_path = replay_result_path.replace(".json", ".db")
    p = Path(db_path)
    if not p.exists():
        log.warning("No replay DB at %s — cannot pre-populate cache", db_path)
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get task_messages to reconstruct which messages went to which tasks
    try:
        rows = conn.execute(
            "SELECT task_id, message_id FROM task_messages ORDER BY task_id, timestamp"
        ).fetchall()
    except sqlite3.OperationalError:
        log.warning("No task_messages table in %s", db_path)
        conn.close()
        return

    # Group by task_id
    task_msg_groups: dict[str, list[str]] = {}
    for row in rows:
        tid = row["task_id"]
        if tid not in task_msg_groups:
            task_msg_groups[tid] = []
        task_msg_groups[tid].append(row["message_id"])

    # For each task, store a "cached" entry indicating this task was processed
    # The actual agent output isn't available from the DB, but we can mark
    # these message combinations as "already processed" to skip re-calling
    pre_populated = 0
    for task_id, msg_ids in task_msg_groups.items():
        key = make_key(task_id, msg_ids)
        if key not in _cache:
            _cache[key] = {
                "_cached_from": "replay_result",
                "task_id": task_id,
                "message_count": len(msg_ids),
                "status": "pre_populated",
            }
            pre_populated += 1

    conn.close()
    log.info("Pre-populated cache with %d entries from %s", pre_populated, db_path)
