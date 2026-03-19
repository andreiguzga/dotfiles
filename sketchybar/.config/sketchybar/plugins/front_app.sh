#!/bin/sh

DISPLAY_ID="$1"

APP_INFO="$(aerospace list-windows --monitor "$DISPLAY_ID" --format '%{app-name}|%{app-bundle-id}|%{workspace-is-visible}|%{workspace-is-focused}' 2>/dev/null | sed -n '/|true|/p' | sed -n '1p')"
APP_NAME="$(printf '%s' "$APP_INFO" | cut -d'|' -f1)"
APP_BUNDLE_ID="$(printf '%s' "$APP_INFO" | cut -d'|' -f2)"

if [ -z "$APP_NAME" ] && [ "$SENDER" = "front_app_switched" ]; then
  APP_NAME="$INFO"
fi

if [ -z "$APP_NAME" ]; then
  APP_NAME="Desktop"
fi

ICON_SCALE=0.52
MONITOR_NAME="$(aerospace list-monitors --format '%{monitor-id}|%{monitor-name}' 2>/dev/null | sed -n "/^$DISPLAY_ID|/s/^[^|]*|//p" | sed -n '1p')"

case "$MONITOR_NAME" in
  *[Bb]uilt-[Ii]n*) ICON_SCALE=0.58 ;;
esac

if [ -n "$APP_BUNDLE_ID" ]; then
  APP_ICON="app.$APP_BUNDLE_ID"
else
  APP_ICON="app.$APP_NAME"
fi

sketchybar --set "$NAME" label="$APP_NAME" background.drawing=off icon="" icon.background.drawing=on icon.background.color=0x00000000 icon.background.image="$APP_ICON" icon.background.image.scale="$ICON_SCALE"
