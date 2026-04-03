#!/usr/bin/env bash
# Hook: detect when code is written/edited that introduces API calls.
# Fires on PostToolUse for Write|Edit. Scans the changed file for
# API call patterns and warns if found — the patterns must be added
# to guard_llm_cost.sh before running.
set -euo pipefail

FILE=$(jq -r '.tool_input.file_path // .tool_response.filePath // ""')

# Skip non-Python files and test data
[[ "$FILE" != *.py ]] && exit 0
[[ "$FILE" == */tests/evals/* ]] && exit 0
[[ "$FILE" == */dev_cache* ]] && exit 0

# Patterns that indicate new API-calling code
if grep -qE '(anthropic\.Anthropic|genai\.GenerativeModel|_call_with_retry|_call_anthropic|_call_gemini|requests\.post.*api\.anthropic|requests\.post.*generativelanguage)' "$FILE" 2>/dev/null; then
    # Check if this file is already covered by guard_llm_cost.sh
    HOOK_FILE=".claude/hooks/guard_llm_cost.sh"
    if [ -f "$HOOK_FILE" ]; then
        BASENAME=$(basename "$FILE" .py)
        if ! grep -q "$BASENAME" "$HOOK_FILE" 2>/dev/null; then
            echo "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"WARNING: File $FILE contains API-calling code. Verify guard_llm_cost.sh covers any new scripts/commands that invoke this file before running them.\"}}"
        fi
    fi
fi
