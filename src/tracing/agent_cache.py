"""
Dev test cache — caches raw LLM responses to avoid repeat API calls.

SQLite-backed for atomic upserts. Each entry is written immediately —
no data lost on crash or kill. Git-tracked per test case.

Cache key: SHA256(system_prompt + user_section)[:16]
Cache value: LLMResponse fields (raw, tokens_in, tokens_out, cache tokens)

Each test case gets its own cache DB: <case_dir>/dev_cache.db
"""

import hashlib
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None
_cache_path: str | None = None
_hits = 0
_misses = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key             TEXT PRIMARY KEY,
    raw             TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL,
    tokens_out      INTEGER NOT NULL,
    cache_creation  INTEGER DEFAULT 0,
    cache_read      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
"""


def init(path: str):
    """Open or create cache DB."""
    global _conn, _cache_path, _hits, _misses
    _cache_path = path
    _hits = 0
    _misses = 0
    _conn = sqlite3.connect(path)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA)
    count = _conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    log.info("Dev cache opened: %d entries in %s", count, path)


def close():
    """Close cache DB."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        log.info("Dev cache closed: %d hits, %d misses", _hits, _misses)


def make_key(system_prompt: str, user_section: str) -> str:
    """Cache key from prompt inputs."""
    sig = system_prompt + "\n---\n" + user_section
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def get(key: str) -> dict | None:
    """Get cached LLM response. Returns None on miss."""
    global _hits, _misses
    if not _conn:
        return None
    row = _conn.execute(
        "SELECT raw, tokens_in, tokens_out, cache_creation, cache_read "
        "FROM llm_cache WHERE key = ?", (key,)
    ).fetchone()
    if row:
        _hits += 1
        return {
            "raw": row[0],
            "tokens_in": row[1],
            "tokens_out": row[2],
            "cache_creation_tokens": row[3],
            "cache_read_tokens": row[4],
        }
    _misses += 1
    return None


def put(key: str, raw: str, tokens_in: int, tokens_out: int,
        cache_creation_tokens: int = 0, cache_read_tokens: int = 0):
    """Store LLM response — atomic upsert, written immediately."""
    if not _conn:
        return
    _conn.execute(
        "INSERT OR REPLACE INTO llm_cache "
        "(key, raw, tokens_in, tokens_out, cache_creation, cache_read) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (key, raw, tokens_in, tokens_out, cache_creation_tokens, cache_read_tokens),
    )
    _conn.commit()


def stats() -> dict:
    count = 0
    if _conn:
        count = _conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    return {
        "entries": count,
        "hits": _hits,
        "misses": _misses,
        "hit_rate": _hits / max(_hits + _misses, 1),
    }
