#!/usr/bin/env python3
"""Play a random request/done sound on a Herdr agent status change."""

import fcntl
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

POOLS = {"blocked": "request", "done": "done"}
COOLDOWN = float(os.environ.get("HERDR_ATTENTION_COOLDOWN", "3"))
VOLUME = os.environ.get("HERDR_ATTENTION_VOLUME", "0.25")


def find_value(payload, key):
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            value = item.get(key)
            if isinstance(value, str):
                return value
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def sounds_root():
    override = os.environ.get("HERDR_ATTENTION_SOUNDS")
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "sounds"
        if (candidate / "request").is_dir() and (candidate / "done").is_dir():
            return candidate
    return None


def muted_agents():
    names = set()
    path = Path(__file__).resolve().parent / "muted-agents"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        name = line.split("#", 1)[0].strip()
        if name:
            names.add(name.lower())
    extra = os.environ.get("HERDR_ATTENTION_MUTED_AGENTS", "")
    names.update(name.strip().lower() for name in extra.split(",") if name.strip())
    return names


def play(path):
    if sys.platform == "darwin":
        subprocess.run(["afplay", "-v", VOLUME, str(path)], check=False)
        return
    commands = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        ["pw-play", "--volume", VOLUME, str(path)],
        ["paplay", str(path)],
        ["aplay", str(path)],
    ]
    for command in commands:
        if subprocess.run(["sh", "-c", f"command -v {command[0]}"], capture_output=True).returncode == 0:
            subprocess.run(command, check=False)
            return


def state_dir():
    chosen = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    path = Path(chosen)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir())
    return path


def main():
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON")
    if not raw:
        return
    try:
        event = json.loads(raw)
    except ValueError:
        return

    status = find_value(event, "agent_status")
    pool = POOLS.get(status or "")
    if not pool:
        return

    agent = find_value(event, "agent") or find_value(event, "display_agent")
    if agent and agent.lower() in muted_agents():
        return

    root = sounds_root()
    if root is None:
        return
    sounds = sorted((root / pool).glob("*.mp3"))
    if not sounds:
        return

    lock_path = state_dir() / "herdr-attention-sounds.lock"
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return

        now = time.time()
        lock.seek(0)
        last = lock.read().strip()
        if last:
            try:
                if now - float(last) < COOLDOWN:
                    return
            except ValueError:
                pass

        lock.seek(0)
        lock.truncate()
        lock.write(str(now))
        lock.flush()

        play(random.choice(sounds))


if __name__ == "__main__":
    main()
