"""
Dev test cache — caches raw LLM responses to avoid repeat API calls.

Used with --dev-test flag for short replay iterations. Caches at the
_call_with_retry level so all downstream parsing/application works identically.

Cache key: SHA256(system_prompt + user_section)[:16]
Cache value: LLMResponse fields (raw, tokens_in, tokens_out, cache tokens)

Each test case gets its own cache file: <case_dir>/dev_cache.json
"""

import hashlib
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_cache: dict[str, dict] = {}
_cache_path: str | None = None
_hits = 0
_misses = 0


def init(path: str):
    """Load cache from file."""
    global _cache, _cache_path, _hits, _misses
    _cache_path = path
    _hits = 0
    _misses = 0
    p = Path(path)
    if p.exists():
        try:
            _cache = json.loads(p.read_text())
            log.info("Dev cache loaded: %d entries from %s", len(_cache), path)
        except (json.JSONDecodeError, ValueError):
            _cache = {}
    else:
        _cache = {}


def save():
    """Persist cache to file."""
    if _cache_path:
        Path(_cache_path).write_text(
            json.dumps(_cache, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Dev cache saved: %d entries, %d hits, %d misses",
                 len(_cache), _hits, _misses)


def make_key(system_prompt: str, user_section: str) -> str:
    """Cache key from prompt inputs."""
    sig = system_prompt + "\n---\n" + user_section
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def get(key: str) -> dict | None:
    """Get cached LLM response. Returns None on miss."""
    global _hits, _misses
    if key in _cache:
        _hits += 1
        return _cache[key]
    _misses += 1
    return None


def put(key: str, raw: str, tokens_in: int, tokens_out: int,
        cache_creation_tokens: int = 0, cache_read_tokens: int = 0):
    """Store LLM response in cache."""
    _cache[key] = {
        "raw": raw,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
    }


def stats() -> dict:
    return {
        "entries": len(_cache),
        "hits": _hits,
        "misses": _misses,
        "hit_rate": _hits / max(_hits + _misses, 1),
    }
