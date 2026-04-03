#!/usr/bin/env bash
# Hook: LLM-costing commands require explicit user approval.
set -euo pipefail

CMD=$(jq -r '.tool_input.command // ""')
FIRST_LINE=$(echo "$CMD" | head -1)

# Match actual test runner invocations
BLOCK=false
echo "$FIRST_LINE" | grep -qE '\-\-run-live' && BLOCK=true
echo "$FIRST_LINE" | grep -qE 'run_incremental_test' && BLOCK=true
echo "$FIRST_LINE" | grep -qE 'run_update_agent' && BLOCK=true

if [ "$BLOCK" = true ]; then
    # Allow cached dev-test runs (no --run-live)
    if echo "$FIRST_LINE" | grep -q '\-\-dev-test' && ! echo "$FIRST_LINE" | grep -q '\-\-run-live'; then
        exit 0
    fi

    echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"LLM-costing command: this will spend real money on API calls."}}'
    exit 0
fi
