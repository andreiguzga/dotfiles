import json
import hashlib
import os
import subprocess
import time
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


def is_supervised(pane_id):
    """Owned worker events go to the coordinator instead of making per-worker sounds."""
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not socket or not pane_id:
        return False
    key = hashlib.sha256(socket.encode()).hexdigest()[:16]
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    try:
        state = json.loads((root / "herdr-orchestrator" / key / "state.json").read_text())
    except (OSError, ValueError):
        return False
    worker = state.get("workers", {}).get(pane_id)
    # If supervision is paused or unavailable, preserve the normal attention sounds.
    return bool(worker and not worker.get("finished") and not state.get("paused")
                and worker.get("observed") != "missing-or-replaced"
                and state.get("coordinator_live")
                and not any(e.get("delivery") == "uncertain" for e in state.get("events", []))
                and time.time() - state.get("heartbeat", 0) < 30)


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
