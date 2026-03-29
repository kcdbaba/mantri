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

RESULTS_DIR    = Path("tests/allure/results")
REPORT_DIR     = Path("static/developer/tests")
HISTORY_DIR    = Path("tests/allure/history")
CATEGORIES_SRC = Path("tests/allure/categories.json")


def main():
    # Collect subdirs that contain result files (allure generate doesn't recurse)
    source_dirs = [d for d in RESULTS_DIR.iterdir() if d.is_dir() and d.name != "history"
                   and any(d.glob("*-result.json"))] if RESULTS_DIR.exists() else []
    if not source_dirs:
        print("No allure results found — run tests first (pytest / run_incremental_test.py)")
        return

    # Inject previous history and categories into each results subdir
    for src in source_dirs:
        history_target = src / "history"
        if history_target.exists():
            shutil.rmtree(history_target)
        if HISTORY_DIR.exists() and any(f for f in HISTORY_DIR.iterdir() if f.name != ".gitkeep"):
            shutil.copytree(HISTORY_DIR, history_target)
        if CATEGORIES_SRC.exists():
            shutil.copy(CATEGORIES_SRC, src / "categories.json")

    # Generate static report — pass each subdir explicitly
    subprocess.run(
        ["allure", "generate", *[str(d) for d in source_dirs], "-o", str(REPORT_DIR), "--clean"],
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
