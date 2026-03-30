#!/usr/bin/env bash
# Build all integration test artifacts for an eval case.
#
# Usage:
#   ./scripts/build_integration_case.sh tests/evals/<case_dir>
#
# Generates:
#   tests/integration_tests/<case_name>/seed_tasks.json      (scaffold — review!)
#   tests/integration_tests/<case_name>/replay_trace.json
#   tests/integration_tests/<case_name>/expected_routing.json
#
# After running, review seed_tasks.json and regenerate routing if you made changes:
#   PYTHONPATH=. python scripts/build_expected_routing.py \
#       --trace tests/integration_tests/<case_name>/replay_trace.json \
#       --seed tests/integration_tests/<case_name>/seed_tasks.json

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <eval_case_dir>"
    echo "Example: $0 tests/evals/R1-D-L3-01_sata_multi_item_multi_supplier/"
    exit 1
fi

CASE_DIR="$1"
CASE_NAME="$(basename "$CASE_DIR")"
INT_DIR="tests/integration_tests/$CASE_NAME"

if [ ! -f "$CASE_DIR/metadata.json" ]; then
    echo "Error: No metadata.json in $CASE_DIR"
    exit 1
fi

mkdir -p "$INT_DIR"

echo "=== Step 1: Generate seed_tasks.json (scaffold) ==="
PYTHONPATH=. python scripts/build_seed_tasks.py --case "$CASE_DIR" --output "$INT_DIR/seed_tasks.json"

echo ""
echo "=== Step 2: Generate replay_trace.json ==="
PYTHONPATH=. python scripts/build_replay_trace.py --case "$CASE_DIR"

echo ""
echo "=== Step 3: Generate expected_routing.json ==="
PYTHONPATH=. python scripts/build_expected_routing.py \
    --trace "$INT_DIR/replay_trace.json" \
    --seed "$INT_DIR/seed_tasks.json"

echo ""
echo "=== Done ==="
echo "Output: $INT_DIR/"
echo ""
echo "⚠  Review seed_tasks.json — check aliases, client_id linkages, group mappings."
echo "   If you make changes, regenerate routing:"
echo "   PYTHONPATH=. python scripts/build_expected_routing.py \\"
echo "       --trace $INT_DIR/replay_trace.json \\"
echo "       --seed $INT_DIR/seed_tasks.json"
