#!/bin/bash
# Usage: cpu.sh <display-count>
# Runs from one hidden item and updates every display. iostat's second sample
# covers the last second; its first is an average since boot.

DISPLAY_COUNT="$1"

CPU_IDLE="$(iostat -c 2 -w 1 -n 0 | awk 'END { print $3 }')"
[[ "$CPU_IDLE" =~ ^[0-9]+$ ]] || exit 0

CPU_USAGE=$((100 - CPU_IDLE))
CPU_NORM="$(awk -v cpu="$CPU_USAGE" 'BEGIN { printf "%.4f", cpu / 100 }')"

args=()
for ((did = 1; did <= DISPLAY_COUNT; did++)); do
  args+=(--set "cpu.$did" label="${CPU_USAGE}%" --push "cpu.graph.$did" "$CPU_NORM")
done

sketchybar "${args[@]}"
