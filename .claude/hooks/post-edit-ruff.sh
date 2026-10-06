#!/usr/bin/env bash
# PostToolUse (Edit|Write): corre ruff check sobre el .py editado y devuelve los
# hallazgos a Claude como contexto. No modifica el archivo (el repo aún no está
# formateado con ruff; un auto-format mezclaría diffs grandes en cambios chicos).
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[[ "$f" == *.py && -f "$f" ]] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
out=$(uv run --quiet ruff check --output-format concise "$f" 2>&1) && exit 0
jq -n --arg ctx "ruff check en ${f}:"$'\n'"$out" \
  '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $ctx}}'
