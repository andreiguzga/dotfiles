#!/bin/sh

DISPLAY_ID="$1"
WORKSPACE_ID="$2"
VISIBLE_WORKSPACE="$(aerospace list-workspaces --monitor "$DISPLAY_ID" --visible 2>/dev/null | sed -n '1p')"
NONEMPTY_WORKSPACES="$(aerospace list-workspaces --monitor "$DISPLAY_ID" --empty no 2>/dev/null)"

DRAWING=off
for sid in $NONEMPTY_WORKSPACES
do
  if [ "$sid" = "$WORKSPACE_ID" ]; then
    DRAWING=on
    break
  fi
done

if [ "$WORKSPACE_ID" = "$VISIBLE_WORKSPACE" ]; then
  DRAWING=on
fi

if [ "$WORKSPACE_ID" = "$VISIBLE_WORKSPACE" ]; then
  sketchybar --set "$NAME" drawing="$DRAWING" background.drawing=on icon.color=0xffe5e9f0
else
  sketchybar --set "$NAME" drawing="$DRAWING" background.drawing=off icon.color=0xffaeb6c5
fi
