#!/bin/bash
# Usage: mode.sh <display-count>
# Shows the current AeroSpace binding mode, hidden while in the main mode.

DISPLAY_COUNT="$1"

QUERY_TIMEOUT=()
command -v timeout >/dev/null && QUERY_TIMEOUT=(timeout 3)

MODE="$("${QUERY_TIMEOUT[@]}" aerospace list-modes --current 2>/dev/null)"

args=()
for ((did = 1; did <= DISPLAY_COUNT; did++)); do
  if [ -z "$MODE" ] || [ "$MODE" = "main" ]; then
    args+=(--set "mode.$did" drawing=off)
  else
    args+=(--set "mode.$did" drawing=on label="$(printf '%s' "$MODE" | tr '[:lower:]' '[:upper:]')")
  fi
done

sketchybar "${args[@]}"
