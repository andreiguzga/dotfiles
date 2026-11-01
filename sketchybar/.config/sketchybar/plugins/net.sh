#!/bin/bash
# Usage: net.sh <display-count> <max-bytes-per-second> <wifi-interface>
# macOS redacts the SSID from command-line tools, so the label shows the IP.

DISPLAY_COUNT="$1"
MAX_BPS="${2:-12500000}"
WIFI_IF="$3"

ICON_ONLINE="󰖩"
ICON_OFFLINE="󰖪"

update_bar() {
  local icon="$1" label="$2" graph_value="$3" did
  local args=()
  for ((did = 1; did <= DISPLAY_COUNT; did++)); do
    args+=(--set "net.$did" icon="$icon" label="$label" --push "net.graph.$did" "$graph_value")
  done
  sketchybar "${args[@]}"
}

if [ -z "$WIFI_IF" ]; then
  update_bar "$ICON_OFFLINE" "No Wi-Fi" 0
  exit 0
fi

ADDRESS="$(ipconfig getifaddr "$WIFI_IF" 2>/dev/null)"
if [ -n "$ADDRESS" ] && ifconfig "$WIFI_IF" | grep -q "status: active"; then
  ONLINE=1
else
  ONLINE=0
fi

STATE_FILE="/tmp/sketchybar_net.state"
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
  update_bar "$ICON_ONLINE" "$ADDRESS" "$NORM"
else
  update_bar "$ICON_OFFLINE" "Offline" "$NORM"
fi
