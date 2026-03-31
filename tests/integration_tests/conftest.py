"""Integration test configuration and shared fixtures."""

import json
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("tests/runs/integration")


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="Run live replay tests (requires API key, costs money)")
    parser.addoption("--skip-linkage", action="store_true", default=False,
                     help="Skip linkage agent calls (update_agent only)")
    parser.addoption("--max-messages", type=int, default=None,
                     help="Process only the first N messages (for quick iteration)")
    parser.addoption("--run-note", type=str, default="",
                     help="Note to attach to the run record (e.g. 'testing new prompt rules')")
    parser.addoption("--traced", action="store_true", default=False,
                     help="Enable Phoenix tracing (entity-first pipeline, per-message processing)")
    parser.addoption("--phoenix-endpoint", type=str, default=None,
                     help="Phoenix OTEL endpoint (default: http://localhost:6006/v1/traces)")


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
