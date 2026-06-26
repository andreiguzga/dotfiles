#!/bin/bash

GRAPH_NAME="$1"
MAX_BPS="${2:-12500000}"

WIFI_IF="$(networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/ {getline; print $2; exit}')"

if [ -z "$WIFI_IF" ]; then
  sketchybar --set "$NAME" icon="󰖪" label="No Wi-Fi"
  if [ -n "$GRAPH_NAME" ]; then
    sketchybar --push "$GRAPH_NAME" 0
  fi
  exit 0
fi

SSID_RAW="$(networksetup -getairportnetwork "$WIFI_IF" 2>/dev/null)"
if [[ "$SSID_RAW" == "Current Wi-Fi Network: "* ]]; then
  SSID="${SSID_RAW#Current Wi-Fi Network: }"
  ONLINE=1
elif IP_ADDR="$(ipconfig getifaddr "$WIFI_IF" 2>/dev/null)" && [ -n "$IP_ADDR" ] && ifconfig "$WIFI_IF" | grep -q "status: active"; then
  SSID="$IP_ADDR"
  ONLINE=1
else
  SSID="Offline"
  ONLINE=0
fi

STATE_KEY="$(printf "%s" "$NAME" | tr -c '[:alnum:]' '_')"
STATE_FILE="/tmp/sketchybar_net_${STATE_KEY}.state"
NOW="$(date +%s)"

read_bytes() {
  netstat -bI "$WIFI_IF" -n | awk -v iface="$WIFI_IF" '$1 == iface {inb = $7; outb = $10} END {print inb + 0, outb + 0}'
}

read -r CUR_IN CUR_OUT <<EOF
$(read_bytes)
EOF

RATE=0

if [ -f "$STATE_FILE" ]; then
  read -r PREV_TS PREV_IN PREV_OUT < "$STATE_FILE"
  DT=$((NOW - PREV_TS))
  if [ "$DT" -gt 0 ]; then
    DIFF_IN=$((CUR_IN - PREV_IN))
    DIFF_OUT=$((CUR_OUT - PREV_OUT))

    if [ "$DIFF_IN" -lt 0 ]; then DIFF_IN=0; fi
    if [ "$DIFF_OUT" -lt 0 ]; then DIFF_OUT=0; fi

    RATE=$(((DIFF_IN + DIFF_OUT) / DT))
  fi
fi

printf "%s %s %s\n" "$NOW" "$CUR_IN" "$CUR_OUT" > "$STATE_FILE"

if [ "$ONLINE" -ne 1 ]; then
  RATE=0
fi

NORM="$(awk -v rate="$RATE" -v max="$MAX_BPS" 'BEGIN {
  if (max <= 0) max = 1
  value = rate / max
  if (value < 0) value = 0
  if (value > 1) value = 1
  printf "%.4f", value
}')"

if [ "$ONLINE" -eq 1 ]; then
  sketchybar --set "$NAME" icon="󰖩" label="$SSID"
else
  sketchybar --set "$NAME" icon="󰖪" label="Offline"
fi

if [ -n "$GRAPH_NAME" ]; then
  sketchybar --push "$GRAPH_NAME" "$NORM"
fi
