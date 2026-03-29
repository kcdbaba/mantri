#!/usr/bin/env python3
"""
publish_allure.py

Generates the Allure report from tests/allure/results/ and saves it to
static/developer/tests/.  Preserves history across runs by committing
the tests/allure/history/ directory to git.

Usage:
    python scripts/publish_allure.py

Run after:
    pytest                          (populates tests/allure/results/unit/)
    python scripts/run_incremental_test.py   (populates tests/allure/results/inc/)
"""

import shutil
import subprocess
from pathlib import Path

RESULTS_DIR = Path("tests/allure/results")
REPORT_DIR  = Path("static/developer/tests")
HISTORY_DIR = Path("tests/allure/history")


def main():
    if not RESULTS_DIR.exists() or not any(RESULTS_DIR.rglob("*-result.json")):
        print("No allure results found — run tests first (pytest / run_incremental_test.py)")
        return

    # Inject previous history so Allure renders trend charts
    history_target = RESULTS_DIR / "history"
    if history_target.exists():
        shutil.rmtree(history_target)
    if HISTORY_DIR.exists() and any(f for f in HISTORY_DIR.iterdir() if f.name != ".gitkeep"):
        shutil.copytree(HISTORY_DIR, history_target)

    # Generate static report
    subprocess.run(
        ["allure", "generate", str(RESULTS_DIR), "-o", str(REPORT_DIR), "--clean"],
        check=True,
    )

    # Persist updated history for next run
    new_history = REPORT_DIR / "history"
    if new_history.exists():
        if HISTORY_DIR.exists():
            shutil.rmtree(HISTORY_DIR)
        shutil.copytree(new_history, HISTORY_DIR)

    print(f"Report : {REPORT_DIR}/index.html")
    print(f"History: {HISTORY_DIR}/  (commit this)")


if __name__ == "__main__":
    main()
