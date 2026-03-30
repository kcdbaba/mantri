#!/usr/bin/env bash
# Regenerate all static pages for the developer portal.
# Run after test scripts to update published results.
# Usage: ./scripts/publish_all.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "Publishing Allure report..."
python scripts/publish_allure.py

echo "Publishing test runs dashboard..."
PYTHONPATH=. python scripts/publish_runs.py

echo "Publishing integration replay detail..."
PYTHONPATH=. python scripts/publish_integration.py

echo "Done. Commit static/developer/ and deploy to update the portal."
