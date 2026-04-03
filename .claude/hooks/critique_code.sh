#!/usr/bin/env bash
# Hook: merciless code critique on every non-tmp file edit.
# Fires PostToolUse on Write|Edit. Returns context that forces
# Claude to self-review before proceeding.
set -euo pipefail

FILE=$(jq -r '.tool_input.file_path // .tool_response.filePath // ""')

# Skip tmp files, data files, static assets, configs, docs
[[ "$FILE" == /tmp/* ]] && exit 0
[[ "$FILE" == /var/* ]] && exit 0
[[ "$FILE" == /private/tmp/* ]] && exit 0
[[ "$FILE" == *.json ]] && exit 0
[[ "$FILE" == *.md ]] && exit 0
[[ "$FILE" == *.html ]] && exit 0
[[ "$FILE" == *.css ]] && exit 0
[[ "$FILE" == *.yml ]] && exit 0
[[ "$FILE" == *.yaml ]] && exit 0
[[ "$FILE" == *.txt ]] && exit 0
[[ "$FILE" == *.csv ]] && exit 0
[[ "$FILE" == */static/* ]] && exit 0
[[ "$FILE" == */.claude/* ]] && exit 0

cat <<CRITIQUE
{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"MANDATORY SELF-REVIEW for $FILE. Before proceeding, answer ALL of these:\n1. TRACE: Walk through every code path that touches this change. What functions call it? What calls it? What data flows through?\n2. DATA: Are all field names, timestamps, config keys, file paths correct? Cross-check against the actual data files and schemas.\n3. EDGE CASES: What happens with empty input, None, missing keys, multi-item lists, zero-length collections?\n4. SIDE EFFECTS: Does this change break any caller? Any downstream consumer? Any test assertion?\n5. ASSETS: Will this delete, overwrite, or invalidate any cached/expensive assets (dev_cache.db, replay results, LLM outputs)?\n6. SYNTAX: Did you py_compile this file?\nIf ANY answer is uncertain, read the relevant code before proceeding. Do NOT run tests to find out."}}
CRITIQUE
