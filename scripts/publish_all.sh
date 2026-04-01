#!/usr/bin/env bash
# Regenerate all static pages for the developer portal.
# Run after test scripts to update published results.
# Usage: ./scripts/publish_all.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "Running unit tests with coverage..."
PYTHONPATH=. python -m pytest tests/unit_tests/ --cov=src --cov-report=json:tests/runs/coverage.json -q --no-header 2>&1 | tail -3

echo "Publishing Allure report..."
python scripts/publish_allure.py

echo "Publishing test runs dashboard..."
PYTHONPATH=. python scripts/publish_runs.py

echo "Publishing system test detail..."
PYTHONPATH=. python scripts/publish_system.py

echo "Done. Commit static/developer/ and deploy to update the portal."
