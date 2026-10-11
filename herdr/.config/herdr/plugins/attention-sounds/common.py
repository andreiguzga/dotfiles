import json
import os
import subprocess
from pathlib import Path

MUTED_ENV = "HERDR_ATTENTION_MUTED_AGENTS"


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
    for name in os.environ.get(MUTED_ENV, "").split(","):
        if name.strip():
            names.add(name.strip().lower())
    return names


def is_muted(agent):
    return bool(agent) and agent.lower() in muted_agents()


def herdr(*args):
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    return subprocess.run([binary, *args], capture_output=True, text=True, check=False)


def event_payload():
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
