#!/bin/bash

MEMORY_USED="$(memory_pressure 2>/dev/null | awk -F': ' '/System-wide memory free percentage/ {
  gsub(/%/, "", $2)
  if ($2 == "") {
    print 0
  } else {
    printf "%.0f", 100 - $2
  }
}')"

if [ -z "$MEMORY_USED" ]; then
  MEMORY_USED="0"
fi

sketchybar --set "$NAME" label="${MEMORY_USED}%"
