#!/bin/bash
# Refreshes every AeroSpace-driven bar item from a few shared queries.
# Usage: aerospace_sync.sh <display-count> <workspace>...
#
# SketchyBar starts this once per event. Only one run does the work at a time:
# events arriving meanwhile just leave a marker, so a burst collapses into one
# extra refresh instead of a pile of concurrent AeroSpace requests.

DISPLAY_COUNT="$1"
shift
WORKSPACES=("$@")

# Matches SketchyBar's display numbering, unlike the left-to-right %{monitor-id}.
MONITOR_ID='%{monitor-appkit-nsscreen-screens-id}'

COLOR_ACTIVE=0xffe5e9f0
COLOR_INACTIVE=0xffaeb6c5

STATE_PREFIX="${TMPDIR:-/tmp}/sketchybar-aerospace-sync"
LOCK="$STATE_PREFIX.lock"
PENDING="$STATE_PREFIX.pending"
STALE_LOCK_MINUTES=1
SETTLE_SECONDS=0.3

# A client whose server dies mid-request can hang forever, so bound each query.
QUERY_TIMEOUT=()
command -v timeout >/dev/null && QUERY_TIMEOUT=(timeout 3)

query() {
  "${QUERY_TIMEOUT[@]}" aerospace "$@" 2>/dev/null
}

refresh_bar() {
  local workspaces windows focused

  # On failure, keep showing the last known state rather than blanking the bar.
  workspaces="$(query list-workspaces --all --format "$MONITOR_ID|%{workspace-is-visible}|%{workspace}|%{monitor-name}")" || return
  windows="$(query list-windows --all --format "$MONITOR_ID|%{workspace-is-visible}|%{workspace}|%{app-bundle-id}|%{app-name}")" || return
  # Fails when nothing is focused, which is fine: the bar falls back below.
  focused="$(query list-windows --focused --format "$MONITOR_ID|%{app-bundle-id}|%{app-name}")"

  local args=() did sid
  local monitor visible workspace bundle_id name
  local monitor_name visible_workspace app_name app_bundle_id icon_scale
  local window_count workspace_icon extra_label app_args

  for ((did = 1; did <= DISPLAY_COUNT; did++)); do
    monitor_name=""
    visible_workspace=""
    while IFS='|' read -r monitor visible workspace name; do
      [ "$monitor" = "$did" ] || continue
      monitor_name="$name"
      [ "$visible" = "true" ] && visible_workspace="$workspace"
    done <<<"$workspaces"

    app_name=""
    app_bundle_id=""
    while IFS='|' read -r monitor visible workspace bundle_id name; do
      [ "$monitor" = "$did" ] || continue
      if [ "$visible" = "true" ] && [ -z "$app_name" ]; then
        app_name="$name"
        app_bundle_id="$bundle_id"
      fi
    done <<<"$windows"

    # Only one display holds focus; the others show the first visible window.
    IFS='|' read -r monitor bundle_id name <<<"$focused"
    if [ "$monitor" = "$did" ]; then
      app_name="$name"
      app_bundle_id="$bundle_id"
    fi

    for sid in "${WORKSPACES[@]}"; do
      window_count=0
      workspace_icon=""
      while IFS='|' read -r monitor visible workspace bundle_id name; do
        [ "$monitor" = "$did" ] && [ "$workspace" = "$sid" ] || continue
        window_count=$((window_count + 1))
        [ -n "$workspace_icon" ] || workspace_icon="app.${bundle_id:-$name}"
      done <<<"$windows"

      if [ "$window_count" -eq 0 ]; then
        app_args=(label.drawing=off)
      else
        extra_label=""
        [ "$window_count" -gt 1 ] && extra_label="+$((window_count - 1))"
        app_args=(label.drawing=on label="$extra_label" label.background.image="$workspace_icon")
      fi

      if [ "$sid" = "$visible_workspace" ]; then
        args+=(--set "space.$did.$sid" drawing=on background.drawing=on icon.color="$COLOR_ACTIVE" "${app_args[@]}")
      elif [ "$window_count" -gt 0 ]; then
        args+=(--set "space.$did.$sid" drawing=on background.drawing=off icon.color="$COLOR_INACTIVE" "${app_args[@]}")
      else
        args+=(--set "space.$did.$sid" drawing=off background.drawing=off icon.color="$COLOR_INACTIVE" "${app_args[@]}")
      fi
    done

    icon_scale=0.52
    case "$monitor_name" in
      *[Bb]uilt-[Ii]n*) icon_scale=0.58 ;;
    esac

    [ -n "$app_name" ] || app_name="Desktop"

    args+=(--set "front_app.$did"
      label="$app_name"
      background.drawing=off
      icon=""
      icon.background.drawing=on
      icon.background.color=0x00000000
      icon.background.image="app.${app_bundle_id:-$app_name}"
      icon.background.image.scale="$icon_scale")
  done

  [ "${#args[@]}" -eq 0 ] || sketchybar "${args[@]}"
}

# Recover from a run that was killed before it could release the lock.
if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +"$STALE_LOCK_MINUTES" 2>/dev/null)" ]; then
  rmdir "$LOCK" 2>/dev/null
fi

# Window events can arrive before AeroSpace has placed the window, and apps can
# fire them in bursts. Wait for those to settle, and rate-limit repeat refreshes.
refresh_delay=0
[ "$SENDER" = "space_windows_change" ] && refresh_delay="$SETTLE_SECONDS"

touch "$PENDING"

while [ -e "$PENDING" ]; do
  # Whoever holds the lock re-checks PENDING after releasing it, so an event
  # that loses this race is still picked up.
  mkdir "$LOCK" 2>/dev/null || exit 0
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT

  while [ -e "$PENDING" ]; do
    sleep "$refresh_delay"
    rm -f "$PENDING"
    refresh_bar
    refresh_delay="$SETTLE_SECONDS"
  done

  rmdir "$LOCK" 2>/dev/null
  trap - EXIT
done
