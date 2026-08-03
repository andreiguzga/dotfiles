#!/bin/sh

python3 - "$NAME" "$INFO" <<'PY'
import json
import shlex
import subprocess
import sys

name = sys.argv[1]
raw = sys.argv[2] if len(sys.argv) > 2 else ""

label = ""
click_script = ""
icon = ""

if raw:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
else:
    data = {}

state = str(data.get("state") or data.get("Player State") or "").lower()
title = (data.get("title") or data.get("Name") or "").strip()
artist = (data.get("artist") or data.get("Artist") or "").strip()
app = (data.get("app") or ("Spotify" if "Player State" in data else "")).strip()

# Startup and wake events have no payload. Query once so an already-playing
# Spotify track appears without waiting for the next playback notification.
if not raw and subprocess.run(
    ["pgrep", "-x", "Spotify"], stdout=subprocess.DEVNULL
).returncode == 0:
    result = subprocess.run([
        "osascript", "-e",
        'tell application "Spotify" to return (player state as text) & linefeed & '
        '(artist of current track) & linefeed & (name of current track)',
    ], capture_output=True, text=True, check=False)
    values = result.stdout.rstrip("\n").split("\n", 2)
    if len(values) == 3:
        state, artist, title = values
        state = state.lower()
        app = "Spotify"

lower_app = app.lower()
lower_title = title.lower()
browser_apps = {"safari", "google chrome", "chrome", "brave browser", "brave", "arc", "firefox", "microsoft edge"}

if state != "playing" or not title:
    subprocess.run([
        "sketchybar", "--set", name,
        "drawing=off",
        "label=",
        "icon=",
        "click_script=",
    ], check=False)
    raise SystemExit(0)

label = f"{artist} - {title}" if artist else title
verb = "open -a"
if app:
    click_script = f"{verb} {shlex.quote(app)}"

if "spotify" in lower_app:
    icon = ""
elif "youtube" in lower_app or "youtube" in lower_title or lower_app in browser_apps:
    icon = ""

subprocess.run([
    "sketchybar", "--set", name,
    "drawing=on",
    f"icon={icon}",
    f"label={label}",
    f"click_script={click_script}",
], check=False)
PY
