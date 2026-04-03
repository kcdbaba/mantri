"""Integration test configuration and shared fixtures."""

import json
import os
from datetime import datetime
from pathlib import Path

# Respect MANTRI_OUTPUT_DIR for /tmp artifact isolation
_OUTPUT_BASE = os.environ.get("MANTRI_OUTPUT_DIR")
RUNS_DIR = Path(_OUTPUT_BASE) / "integration" if _OUTPUT_BASE else Path("tests/runs/integration")


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="Run live replay tests (requires API key, costs money)")
    parser.addoption("--max-messages", type=int, default=None,
                     help="Process only the first N messages. Mutually exclusive with --dev-test.")
    parser.addoption("--run-note", type=str, default="",
                     help="Note to attach to the run record (e.g. 'testing new prompt rules')")
    parser.addoption("--traced", action="store_true", default=False,
                     help="Enable Phoenix tracing (entity-first pipeline, per-message processing)")
    parser.addoption("--phoenix-endpoint", type=str, action="append", default=None,
                     help="Phoenix OTEL endpoint(s). Repeat for dual-write. "
                          "Default: remote droplet. Use 'local' for http://localhost:6006/v1/traces")
    parser.addoption("--phoenix-user", type=str, default="guest",
                     help="Basic auth user for remote Phoenix (default: guest)")
    parser.addoption("--phoenix-password", type=str, default="guestpasswd",
                     help="Basic auth password for remote Phoenix")
    parser.addoption("--agents", type=str, default="AO,AL",
                     help="Comma-separated agents to run: AO (orders), AL (linkage). Default: AO,AL")
    parser.addoption("--dev-test", action="store_true", default=False,
                     help="Short replay (50 msgs) with LLM response caching and pre-seeded tasks. "
                          "First run requires --run-live to build cache; subsequent runs use cache. "
                          "Disables Phoenix tracing and conv router LLM matching.")
    parser.addoption("--backfill-cache", action="store_true", default=False,
                     help="One-time migration: replay from cache to populate model column. "
                          "Uses hash-only lookup. PERMIT_API=False. Remove after --run-cache verifies.")
    parser.addoption("--run-cache", action="store_true", default=False,
                     help="Cache-only replay. No API calls (PERMIT_API=False). "
                          "Strict (hash, model) lookup. Upserts prompts on hit. "
                          "Mutually exclusive with --run-live, --traced, --dev-test.")


def save_run_record(test_type: str, case_id: str, results: dict):
    """Save a run record to tests/runs/integration/ for publishing."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {
        "run_at": now.isoformat(),
        "test_type": test_type,
        "case_id": case_id,
        **results,
    }
    filename = f"{test_type}-{case_id}-{now.strftime('%Y%m%dT%H%M%S')}.json"
    (RUNS_DIR / filename).write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
