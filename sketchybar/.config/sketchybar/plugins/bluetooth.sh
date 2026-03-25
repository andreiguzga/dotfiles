#!/bin/bash

BLUETOOTH_INFO="$(system_profiler SPBluetoothDataType 2>/dev/null)"
BT_STATE="$(printf "%s\n" "$BLUETOOTH_INFO" | awk -F': ' '/State/ {print $2; exit}')"

if [ "$BT_STATE" = "On" ]; then
  CONNECTED_DEVICE="$(printf "%s\n" "$BLUETOOTH_INFO" | awk '
    /Connected: Yes/ {print last; exit}
    /^[[:space:]]*[[:alnum:]].*:/ {line = $0; gsub(/^[[:space:]]+/, "", line); gsub(/:$/, "", line); last = line}
  ')"

  if [ -n "$CONNECTED_DEVICE" ]; then
    sketchybar --set "$NAME" icon="󰂱" label="$CONNECTED_DEVICE"
  else
    sketchybar --set "$NAME" icon="󰂯" label="On"
  fi
else
  sketchybar --set "$NAME" icon="󰂲" label="Off"
fi
