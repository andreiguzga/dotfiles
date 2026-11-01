#!/bin/bash
# Usage: bluetooth.sh <display-count>

DISPLAY_COUNT="$1"

BLUETOOTH_INFO="$(system_profiler SPBluetoothDataType 2>/dev/null)"
BT_STATE="$(printf "%s\n" "$BLUETOOTH_INFO" | awk -F': ' '/State/ {print $2; exit}')"

if [ "$BT_STATE" = "On" ]; then
  CONNECTED_DEVICE="$(printf "%s\n" "$BLUETOOTH_INFO" | awk '
    /Connected: Yes/ {print last; exit}
    /^[[:space:]]*[[:alnum:]].*:/ {line = $0; gsub(/^[[:space:]]+/, "", line); gsub(/:$/, "", line); last = line}
  ')"

  if [ -n "$CONNECTED_DEVICE" ]; then
    ICON="󰂱"
    LABEL="$CONNECTED_DEVICE"
  else
    ICON="󰂯"
    LABEL="On"
  fi
else
  ICON="󰂲"
  LABEL="Off"
fi

args=()
for ((did = 1; did <= DISPLAY_COUNT; did++)); do
  args+=(--set "bluetooth.$did" icon="$ICON" label="$LABEL")
done

sketchybar "${args[@]}"
