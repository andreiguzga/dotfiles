#!/bin/bash
# Usage: display_watch.sh <state-file>
# Reloads the bar when displays are added, removed or rearranged. Polling is
# used because SketchyBar's display_change only reports the active display.

STATE_FILE="$1"

CURRENT="$(sketchybar --query displays 2>/dev/null)"
[ -n "$CURRENT" ] || exit 0

if [ "$CURRENT" != "$(cat "$STATE_FILE" 2>/dev/null)" ]; then
  sketchybar --reload
fi
