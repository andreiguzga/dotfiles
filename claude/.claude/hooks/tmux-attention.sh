#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
target_pane="${TMUX_PANE:-}"
stamp_file="${TMPDIR:-/tmp}/claude-code-tmux-attention-sound-${UID:-user}"

play_sound() {
  local now last

  now="$(date +%s)"
  last="0"
  if [[ -r "$stamp_file" ]]; then
    last="$(<"$stamp_file")"
  fi

  if (( now - last < 3 )); then
    return
  fi

  printf '%s' "$now" >"$stamp_file"
  afplay /System/Library/Sounds/Glass.aiff >/dev/null 2>&1 &
}

set_attention() {
  if [[ -n "${TMUX:-}" && -n "$target_pane" ]]; then
    tmux set -wq -t "$target_pane" @claude_attention 1
  fi

  play_sound
}

clear_attention() {
  if [[ -n "${TMUX:-}" && -n "$target_pane" ]]; then
    tmux set -wu -t "$target_pane" @claude_attention
  fi
}

case "$action" in
  set)
    set_attention
    ;;
  clear)
    clear_attention
    ;;
  *)
    printf 'usage: %s {set|clear}\n' "$0" >&2
    exit 2
    ;;
esac
