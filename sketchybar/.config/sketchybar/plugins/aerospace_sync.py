#!/usr/bin/env python3
"""Read a bounded AeroSpace snapshot and send only changed bar properties."""

import argparse
from collections import defaultdict
import json
import os
import subprocess

from run_once import state_directory


MONITOR_ID = "%{monitor-appkit-nsscreen-screens-id}"
ACTIVE = "0xffe5e9f0"
INACTIVE = "0xffaeb6c5"


def query(*arguments, optional=False):
    result = subprocess.run(
        ["aerospace", *arguments], capture_output=True, text=True, timeout=2
    )
    if result.returncode:
        if optional:
            return ""
        raise RuntimeError("AeroSpace query failed")
    return result.stdout


def desired_items(display_count, workspaces, workspace_text, window_text, focused_text):
    monitors = {}
    visible_workspaces = {}
    windows = defaultdict(list)
    visible_apps = {}
    for line in workspace_text.splitlines():
        monitor, visible, workspace, name = line.split("|", 3)
        monitors[monitor] = name
        if visible == "true":
            visible_workspaces[monitor] = workspace

    for line in window_text.splitlines():
        monitor, visible, workspace, bundle, name = line.split("|", 4)
        app = (bundle, name)
        windows[monitor, workspace].append(app)
        if visible == "true":
            visible_apps.setdefault(monitor, app)

    # A monitor without focus keeps the first app on its visible workspace.
    for line in focused_text.splitlines():
        monitor, bundle, name = line.split("|", 2)
        visible_apps[monitor] = (bundle, name)

    items = {}
    for display in range(1, display_count + 1):
        monitor = str(display)
        for workspace in workspaces:
            apps = windows[monitor, workspace]
            active = workspace == visible_workspaces.get(monitor)
            properties = {
                "drawing": "on" if active or apps else "off",
                "background.drawing": "on" if active else "off",
                "icon.color": ACTIVE if active else INACTIVE,
                "label.drawing": "on" if apps else "off",
                "label": f"+{len(apps) - 1}" if len(apps) > 1 else "",
            }
            if apps:
                bundle, name = apps[0]
                properties["label.background.image"] = f"app.{bundle or name}"
            items[f"space.{display}.{workspace}"] = properties

        bundle, name = visible_apps.get(monitor, ("", "Desktop"))
        properties = {
            "label": name,
            "icon": "",
            "icon.background.drawing": "on" if bundle or name != "Desktop" else "off",
        }
        if properties["icon.background.drawing"] == "on":
            properties["icon.background.image"] = f"app.{bundle or name}"
            properties["icon.background.image.scale"] = (
                "0.58" if "built-in" in monitors.get(monitor, "").lower() else "0.52"
            )
        items[f"front_app.{display}"] = properties
    return items


def changed_arguments(previous, desired):
    arguments = []
    for name, properties in desired.items():
        changed = [
            f"{key}={value}"
            for key, value in properties.items()
            if previous.get(name, {}).get(key) != value
        ]
        if changed:
            arguments.extend(["--set", name, *changed])
    return arguments


def refresh(display_count, workspaces, generation):
    workspace_text = query(
        "list-workspaces", "--all", "--format",
        f"{MONITOR_ID}|%{{workspace-is-visible}}|%{{workspace}}|%{{monitor-name}}",
    )
    window_text = query(
        "list-windows", "--all", "--format",
        f"{MONITOR_ID}|%{{workspace-is-visible}}|%{{workspace}}|%{{app-bundle-id}}|%{{app-name}}",
    )
    focused_text = query(
        "list-windows", "--focused", "--format",
        f"{MONITOR_ID}|%{{app-bundle-id}}|%{{app-name}}", optional=True,
    )
    if not workspace_text.strip():
        return  # Preserve the bar if AeroSpace is unavailable during startup.
    desired = desired_items(display_count, workspaces, workspace_text, window_text, focused_text)

    cache_path = state_directory() / "aerospace-sync.json"
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict) or not isinstance(cache.get("items", {}), dict):
        cache = {}
    previous = cache.get("items", {}) if cache.get("generation") == generation else {}
    arguments = changed_arguments(previous, desired)
    if not arguments:
        return

    subprocess.run(["sketchybar", *arguments], check=True, timeout=3)
    # A new configuration generation must repaint items after a bar reload.
    temporary = cache_path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"generation": generation, "items": desired}))
        temporary.replace(cache_path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("display_count", type=int)
    parser.add_argument("workspaces", nargs="+")
    parser.add_argument("--generation", default="manual")
    args = parser.parse_args()
    try:
        refresh(args.display_count, args.workspaces, args.generation)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Queries can fail while AeroSpace restarts. The next event/poll retries.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
