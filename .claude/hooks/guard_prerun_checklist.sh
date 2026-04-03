#!/usr/bin/env bash
# Hook: before any pytest or test script run, force Claude to prove
# it has done a pre-run checklist by including a magic comment.
#
# The command must contain "# VERIFIED:" followed by checklist items.
# Without it, the command is blocked.
set -euo pipefail

CMD=$(jq -r '.tool_input.command // ""')
FIRST_LINE=$(echo "$CMD" | head -1)

# Only gate actual test runner invocations (not git add/commit mentioning test files)
GATE=false
echo "$FIRST_LINE" | grep -qw 'pytest' && GATE=true
echo "$FIRST_LINE" | grep -q 'run_incremental' && GATE=true
echo "$FIRST_LINE" | grep -q 'run_unit_test' && GATE=true

if [ "$GATE" = false ]; then
    exit 0
fi

# Require verification comment
if ! echo "$CMD" | grep -q '# VERIFIED:'; then
    cat <<'BLOCK'
{"decision":"block","reason":"BLOCKED: Test command requires pre-run verification. Before running, you MUST:\n1. Read all changed files and trace the execution path mentally\n2. Verify data consistency (timestamps, field names, config values)\n3. Confirm syntax via py_compile for every changed .py file\n4. Check that no cached/expensive assets will be deleted\n\nAppend '# VERIFIED: <what you checked>' to your command to proceed."}
BLOCK
    exit 0
fi
