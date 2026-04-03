"""
LLM response cache — avoids repeat API calls across replay runs.

SQLite-backed with atomic upserts. Each entry written immediately —
no data lost on crash or kill. Git-tracked per test case.

Schema:
  - Lookup: (hash, model) — fast indexed composite key
  - Response: raw LLM output text
  - Meta: JSON blob for prompts, tokens, message/task IDs, image hash.
    Schema-free so new fields don't need migrations.

Each test case gets its own cache DB: <case_dir>/dev_cache.db
"""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None
_cache_path: str | None = None
_hits = 0
_misses = 0

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS llm_cache_v2 (
    hash        TEXT NOT NULL,
    model       TEXT NOT NULL,
    raw         TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (hash, model)
);
"""


def init(path: str):
    """Open or create cache DB. Migrates v1 schema if present."""
    global _conn, _cache_path, _hits, _misses
    _cache_path = path
    _hits = 0
    _misses = 0
    _conn = sqlite3.connect(path)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript(SCHEMA_V2)

    # Check if old v1 table exists (migration source)
    tables = {r[0] for r in _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "llm_cache" in tables and "llm_cache_v2" in tables:
        # Migrate v1 → v2: copy entries with model=NULL in meta
        v1_count = _conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        if v1_count > 0:
            _conn.execute("""
                INSERT OR IGNORE INTO llm_cache_v2 (hash, model, raw, meta)
                SELECT key, '__v1__', raw,
                    json_object(
                        'tokens_in', tokens_in,
                        'tokens_out', tokens_out,
                        'cache_creation_tokens', cache_creation,
                        'cache_read_tokens', cache_read
                    )
                FROM llm_cache
            """)
            _conn.commit()
            log.info("Migrated %d v1 cache entries to v2 (model='__v1__')", v1_count)
        # Rename old table to preserve it
        _conn.execute("ALTER TABLE llm_cache RENAME TO llm_cache_v1_backup")
        _conn.commit()
        log.info("Renamed llm_cache → llm_cache_v1_backup")

    count = _conn.execute("SELECT COUNT(*) FROM llm_cache_v2").fetchone()[0]
    log.info("Cache opened: %d entries in %s", count, path)


def close():
    """Close cache DB."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
        log.info("Cache closed: %d hits, %d misses", _hits, _misses)


def make_key(system_prompt: str, user_section: str) -> str:
    """Cache key from prompt inputs (model-independent)."""
    sig = system_prompt + "\n---\n" + user_section
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def get(key: str, model: str) -> dict | None:
    """Get cached LLM response by (hash, model). Returns None on miss."""
    global _hits, _misses
    if not _conn:
        return None
    row = _conn.execute(
        "SELECT raw, meta FROM llm_cache_v2 WHERE hash = ? AND model = ?",
        (key, model),
    ).fetchone()
    if row:
        _hits += 1
        meta = json.loads(row[1]) if row[1] else {}
        return {
            "raw": row[0],
            "tokens_in": meta.get("tokens_in", 0),
            "tokens_out": meta.get("tokens_out", 0),
            "cache_creation_tokens": meta.get("cache_creation_tokens", 0),
            "cache_read_tokens": meta.get("cache_read_tokens", 0),
        }
    _misses += 1
    return None


def get_backfill(key: str) -> dict | None:
    """Get cached response by hash only, ignoring model. For --backfill-cache migration."""
    global _hits, _misses
    if not _conn:
        return None
    row = _conn.execute(
        "SELECT raw, meta, model FROM llm_cache_v2 WHERE hash = ?",
        (key,),
    ).fetchone()
    if row:
        _hits += 1
        meta = json.loads(row[1]) if row[1] else {}
        return {
            "raw": row[0],
            "tokens_in": meta.get("tokens_in", 0),
            "tokens_out": meta.get("tokens_out", 0),
            "cache_creation_tokens": meta.get("cache_creation_tokens", 0),
            "cache_read_tokens": meta.get("cache_read_tokens", 0),
            "_current_model": row[2],
        }
    _misses += 1
    return None


def put(key: str, model: str, raw: str, meta: dict):
    """Store LLM response — atomic upsert, written immediately."""
    if not _conn:
        return
    _conn.execute(
        "INSERT OR REPLACE INTO llm_cache_v2 (hash, model, raw, meta) "
        "VALUES (?, ?, ?, ?)",
        (key, model, raw, json.dumps(meta, ensure_ascii=False)),
    )
    _conn.commit()


def update_meta(key: str, model: str, meta_updates: dict):
    """Update meta fields for an existing entry (upsert into JSON)."""
    if not _conn:
        return
    row = _conn.execute(
        "SELECT meta FROM llm_cache_v2 WHERE hash = ? AND model = ?",
        (key, model),
    ).fetchone()
    if row:
        existing = json.loads(row[0]) if row[0] else {}
        existing.update(meta_updates)
        _conn.execute(
            "UPDATE llm_cache_v2 SET meta = ? WHERE hash = ? AND model = ?",
            (json.dumps(existing, ensure_ascii=False), key, model),
        )
        _conn.commit()


def update_model(key: str, old_model: str, new_model: str):
    """Change the model for an entry (used by --backfill-cache)."""
    if not _conn:
        return
    _conn.execute(
        "UPDATE llm_cache_v2 SET model = ? WHERE hash = ? AND model = ?",
        (new_model, key, old_model),
    )
    _conn.commit()


def stats() -> dict:
    count = 0
    if _conn:
        count = _conn.execute("SELECT COUNT(*) FROM llm_cache_v2").fetchone()[0]
    return {
        "entries": count,
        "hits": _hits,
        "misses": _misses,
        "hit_rate": _hits / max(_hits + _misses, 1),
    }
