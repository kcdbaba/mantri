"""
pytest session hook — saves a small run summary JSON to tests/runs/unit/ after each session.
Injects Allure environment/executor metadata at session start.
These summaries feed the Test Results history page.
"""

import json
import platform
import sys
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path("tests/runs/unit")


def pytest_configure(config):
    alluredir = getattr(config.option, "allure_report_dir", None) or "tests/allure/results/unit"
    p = Path(alluredir)
    p.mkdir(parents=True, exist_ok=True)

    (p / "environment.properties").write_text(
        "\n".join([
            f"Python={sys.version.split()[0]}",
            f"OS={platform.system()} {platform.release()}",
            "Project=Mantri",
            "Sprint=3",
            "Model=claude-sonnet-4-6",
        ]),
        encoding="utf-8",
    )

    (p / "executor.json").write_text(
        json.dumps({"name": "Local", "type": "local", "buildName": "pytest"}, indent=2),
        encoding="utf-8",
    )


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
