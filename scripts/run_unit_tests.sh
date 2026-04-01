#!/usr/bin/env bash
# Run unit tests with allure results and coverage.
# Usage: ./scripts/run_unit_tests.sh
#
# Respects MANTRI_OUTPUT_DIR env var for /tmp artifact isolation.
# When set, coverage.json writes to $MANTRI_OUTPUT_DIR/coverage.json
# instead of tests/runs/coverage.json.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

OUTPUT_DIR="${MANTRI_OUTPUT_DIR:-tests/runs}"
COV_PATH="${OUTPUT_DIR}/coverage.json"
mkdir -p "$(dirname "$COV_PATH")"

PYTHONPATH=. python -m pytest tests/unit_tests/ \
    --alluredir=tests/allure/results/unit \
    --clean-alluredir \
    --cov=src \
    --cov-report="json:${COV_PATH}" \
    --cov-report=term \
    -v "$@"
