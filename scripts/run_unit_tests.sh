#!/usr/bin/env bash
# Run unit tests with allure results and coverage.
# Usage: ./scripts/run_unit_tests.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PYTHONPATH=. python -m pytest tests/unit_tests/ \
    --alluredir=tests/allure/results/unit \
    --clean-alluredir \
    --cov=src \
    --cov-report=json:tests/runs/coverage.json \
    --cov-report=term \
    -v "$@"
