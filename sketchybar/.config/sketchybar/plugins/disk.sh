#!/bin/bash

DISK_USED="$(df / | awk 'NR==2 {
  gsub(/%/, "", $5)
  print $5
}')"

if [ -z "$DISK_USED" ]; then
  DISK_USED="0"
fi

sketchybar --set "$NAME" label="${DISK_USED}%"
