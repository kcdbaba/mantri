#!/usr/bin/env bash
# Hook: block bash commands that would make LLM API calls.
set -euo pipefail

CMD=$(jq -r '.tool_input.command // ""')

# Extract just the command part (first line, before any heredoc/comment)
FIRST_LINE=$(echo "$CMD" | head -1)

# Match actual test runner invocations, not strings inside commit messages
BLOCK=false
echo "$FIRST_LINE" | grep -qE '\-\-run-live' && BLOCK=true
echo "$FIRST_LINE" | grep -qE 'run_incremental_test' && BLOCK=true
echo "$FIRST_LINE" | grep -qE 'run_update_agent' && BLOCK=true

if [ "$BLOCK" = true ]; then
    # Allow cached dev-test runs (no --run-live)
    if echo "$FIRST_LINE" | grep -q '\-\-dev-test' && ! echo "$FIRST_LINE" | grep -q '\-\-run-live'; then
        exit 0
    fi

    echo '{"decision":"block","reason":"BLOCKED: LLM-costing command. This will spend real money. Describe what you verified by reading code before requesting approval."}'
    exit 0
fi
