#!/bin/bash
set -euo pipefail

workspace_id="5"
name_on="spotify"
name_off="5"
interval_seconds="1"

lock_path="${XDG_RUNTIME_DIR:-/tmp}/spotify-workspace-icon.lock"
exec 9>"$lock_path" || exit 0
flock -n 9 || exit 0

while true; do
  has_spotify="$(
    hyprctl clients -j 2>/dev/null | python -c "import json,sys; data=json.load(sys.stdin); print('1' if any(c.get('class')=='spotify' for c in data) else '0')" 2>/dev/null || echo 0
  )"

  desired_name="$name_off"
  if [ "$has_spotify" = "1" ]; then
    desired_name="$name_on"
  fi

  current_name="$(
    hyprctl workspaces -j 2>/dev/null | python -c "import json,sys; ws=json.load(sys.stdin); w=[w for w in ws if int(w.get('id',-1))==5]; print(w[0].get('name','') if w else '')" 2>/dev/null || echo ""
  )"

  if [ -n "$current_name" ] && [ "$current_name" != "$desired_name" ]; then
    hyprctl dispatch renameworkspace "$workspace_id" "$desired_name" >/dev/null 2>&1 || true
  fi

  sleep "$interval_seconds"
done
