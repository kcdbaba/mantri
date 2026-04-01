#!/usr/bin/env bash
# Run eval on existing replay results (no new LLM calls).
#
# Usage:
#   scripts/run_eval.sh LINKAGE-01    # eval one case
#   scripts/run_eval.sh all           # eval all cases with results
#   scripts/run_eval.sh R1-D --push   # eval + push to Phoenix
#
# This is cheap — only deterministic scorers + judges + rapidfuzz.
# LLM judges only called for fuzzy item matching (Gemini free tier).

set -euo pipefail
cd "$(dirname "$0")/.."

CASE=""
PUSH=false
PHOENIX_EP="http://localhost:6006"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --push) PUSH=true; shift ;;
        --phoenix) PHOENIX_EP="$2"; shift 2 ;;
        *) CASE="$1"; shift ;;
    esac
done

if [[ -z "$CASE" ]]; then
    echo "Usage: scripts/run_eval.sh <CASE|all> [--push] [--phoenix URL]"
    exit 1
fi

PYTHONPATH=. python3 -c "
import json, logging
from pathlib import Path
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')

from src.tracing.scorers import score_replay
from src.tracing.judges import judge_replay
from src.tracing.deepeval_dag import run_eval_dag
from src.tracing.issue_tracker import update_issues_from_eval, print_changes, load_issues

cases = list(Path('tests/integration_tests').iterdir())
for case_dir in sorted(cases):
    if not case_dir.is_dir():
        continue
    result_path = case_dir / 'replay_result.json'
    if not result_path.exists():
        continue
    case_id = case_dir.name.split('_')[0]
    if '$CASE' != 'all' and '$CASE' not in case_id:
        continue

    print(f'\n{\"=\"*60}')
    print(f'  EVAL: {case_id}')
    print(f'{\"=\"*60}')

    replay = json.loads(result_path.read_text())
    stats = replay['stats']
    state = replay['state']

    # Deterministic scorers
    card = score_replay(stats, state)
    print(f'\nScorecard:')
    for k, v in card.summary().items():
        print(f'  {k:30s} {v}')

    # Judges (if baselines exist)
    baselines = list(case_dir.glob('eval_baselines*.json'))
    if baselines:
        bl = sorted(baselines, key=lambda p: len(p.name))[-1]
        eval_result = judge_replay(bl, result_path)
        print(f'\nJudge ({bl.name}):')
        for k, v in eval_result.summary().items():
            print(f'  {k:30s} {v}')

        # DAG eval
        dag = run_eval_dag(case_dir, baselines_filename=bl.name, run_llm=True)
        print(f'\nDAG eval:')
        print(f'  Overall: {dag.overall_score:.3f} ({\"PASS\" if dag.overall_pass else \"FAIL\"})')
        for node in dag.nodes:
            print(f'  {node.name:25s} {node.score:.2f} [{\"PASS\" if node.passed else \"FAIL\"}]')

        # Issue tracking
        changes = update_issues_from_eval(
            eval_result, json.loads(bl.read_text()), state,
            run_id=f'eval_{case_id}',
        )
        print_changes(changes)

    # Push to Phoenix
    if $PUSH:
        from src.tracing.push_eval import push_eval_to_phoenix
        bl_name = bl.name if baselines else 'eval_baselines.json'
        push_eval_to_phoenix(case_dir, baselines_filename=bl_name,
                             phoenix_endpoint='$PHOENIX_EP',
                             project_name='mantri')

print('\n\nAll issues:')
issues = load_issues()
for iid, issue in issues.items():
    print(f'  [{issue.status.upper():8s}] {iid}')
" 2>&1 | grep -v "DeprecationWarning\|httpx\|alembic\|UserWarning"
