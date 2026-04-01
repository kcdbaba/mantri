#!/usr/bin/env bash
# Run a traced replay with eval and Phoenix push.
#
# Usage:
#   scripts/run_traced_replay.sh LINKAGE-01              # single case, remote only
#   scripts/run_traced_replay.sh R1-D --local             # single case, local+remote
#   scripts/run_traced_replay.sh R3-C --max-messages 50   # partial replay
#   scripts/run_traced_replay.sh all                      # all cases
#
# After replay:
#   - Runs eval (scorers + judges + DAG)
#   - Pushes eval results to Phoenix
#   - Publishes developer portal
#   - Commits results (optional, with --commit)

set -euo pipefail
cd "$(dirname "$0")/.."

CASE=""
MAX_MESSAGES=""
PHOENIX_ARGS="--phoenix-endpoint remote"
RUN_NOTE=""
COMMIT=false
SKIP_LINKAGE="--skip-linkage"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local)
            PHOENIX_ARGS="--phoenix-endpoint local --phoenix-endpoint remote"
            shift ;;
        --max-messages)
            MAX_MESSAGES="--max-messages $2"
            shift 2 ;;
        --run-note)
            RUN_NOTE="--run-note '$2'"
            shift 2 ;;
        --with-linkage)
            SKIP_LINKAGE=""
            shift ;;
        --commit)
            COMMIT=true
            shift ;;
        all)
            CASE="all"
            shift ;;
        *)
            CASE="$1"
            shift ;;
    esac
done

if [[ -z "$CASE" ]]; then
    echo "Usage: scripts/run_traced_replay.sh <CASE|all> [options]"
    echo "  Cases: LINKAGE-01, R1-D, R3-C, all"
    echo "  Options: --local, --max-messages N, --run-note TEXT, --with-linkage, --commit"
    exit 1
fi

# Build pytest filter
if [[ "$CASE" == "all" ]]; then
    FILTER=""
else
    FILTER="-k $CASE"
fi

echo "=========================================="
echo "  TRACED REPLAY: $CASE"
echo "=========================================="

# 1. Run replay
echo ""
echo ">>> Step 1: Running replay..."
PYTHONPATH=. python3 -m pytest tests/integration_tests/test_live_replay.py \
    -v -s --run-live --traced $SKIP_LINKAGE $FILTER \
    $PHOENIX_ARGS $MAX_MESSAGES $RUN_NOTE

# 2. Run eval
echo ""
echo ">>> Step 2: Running eval..."
PYTHONPATH=. python3 -c "
from pathlib import Path
from src.tracing.push_eval import push_eval_to_phoenix

cases = list(Path('tests/integration_tests').iterdir())
for case_dir in sorted(cases):
    if not case_dir.is_dir():
        continue
    result = case_dir / 'replay_result.json'
    if not result.exists():
        continue
    case_id = case_dir.name.split('_')[0]
    if '$CASE' != 'all' and '$CASE' not in case_id:
        continue

    # Find baselines
    baselines = list(case_dir.glob('eval_baselines*.json'))
    if baselines:
        bl = sorted(baselines, key=lambda p: len(p.name))[-1]  # longest name = most specific
        print(f'Eval: {case_id} with {bl.name}')
        push_eval_to_phoenix(case_dir, baselines_filename=bl.name,
                             phoenix_endpoint='http://localhost:6006',
                             project_name='mantri')
    else:
        print(f'Eval: {case_id} (scorers only, no baselines)')
        push_eval_to_phoenix(case_dir, phoenix_endpoint='http://localhost:6006',
                             project_name='mantri')
"

# 3. Publish portal
echo ""
echo ">>> Step 3: Publishing portal..."
scripts/publish_all.sh

# 4. Commit (optional)
if [[ "$COMMIT" == true ]]; then
    echo ""
    echo ">>> Step 4: Committing results..."
    git add tests/integration_tests/*/replay_result.json \
            tests/integration_tests/*/replay_result.db \
            tests/integration_tests/*/pipeline_score.json \
            tests/eval_issues.json \
            static/developer/ \
            tests/runs/ \
            tests/allure/history/
    git commit -m "Replay results: $CASE ($(date +%Y-%m-%d))"
fi

echo ""
echo "=========================================="
echo "  DONE"
echo "=========================================="
