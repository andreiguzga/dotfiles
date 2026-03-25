#!/bin/bash

GRAPH_NAME="$1"

CPU_USAGE="$(top -l 1 | awk -F'[:,]' '/CPU usage/ {
  for (i = 1; i <= NF; i++) {
    if ($i ~ /% idle/) {
      gsub(/[^0-9.]/, "", $i)
      idle = $i
    }
  }
}
END {
  if (idle == "") {
    print 0
  } else {
    printf "%.0f", 100 - idle
  }
}')"

CPU_NORM="$(awk -v cpu="$CPU_USAGE" 'BEGIN {
  value = cpu / 100
  if (value < 0) value = 0
  if (value > 1) value = 1
  printf "%.4f", value
}')"

sketchybar --set "$NAME" label="${CPU_USAGE}%"

if [ -n "$GRAPH_NAME" ]; then
  sketchybar --push "$GRAPH_NAME" "$CPU_NORM"
fi
