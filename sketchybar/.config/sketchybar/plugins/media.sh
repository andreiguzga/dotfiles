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

state = str(data.get("state", "")).lower()
title = (data.get("title") or "").strip()
artist = (data.get("artist") or "").strip()
app = (data.get("app") or "").strip()
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
