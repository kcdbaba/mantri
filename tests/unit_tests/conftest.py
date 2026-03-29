"""
pytest session hook — saves a small run summary JSON to tests/runs/unit/ after each session.
These summaries feed the Test Results history page.
"""

import json
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("tests/runs/unit")


def pytest_sessionfinish(session, exitstatus):
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr is None:
        return

    passed  = len(tr.stats.get("passed",  []))
    failed  = len(tr.stats.get("failed",  []))
    error   = len(tr.stats.get("error",   []))
    skipped = len(tr.stats.get("skipped", []))
    total   = passed + failed + error + skipped

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "total":   total,
        "passed":  passed,
        "failed":  failed + error,
        "skipped": skipped,
    }
    (RUNS_DIR / f"{ts}_summary.json").write_text(json.dumps(summary, indent=2))
