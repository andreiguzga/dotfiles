#!/usr/bin/env python3
"""Herdr plugin event entry point for orchestrator attention events.

Herdr runs this for its own lifecycle events. It normalizes the bounded
evidence in the event payload into attention records, records them, and routes
the resulting actions through notify and triage exactly once. It never reads a
transcript, never sends keys and never answers a permission or question:
native dialogs stay with the user.

Invocation is idempotent. A repeated event deduplicates on the request
fingerprint, so a redelivered hook does not produce a second notice.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attention  # noqa: E402
import supervisor  # noqa: E402

MAX_EVENT_BYTES = 64 * 1024
MAX_TITLE = 120
MAX_MESSAGE = 240
MAX_LIST = 8


def payload():
    raw = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
    if not raw or len(raw) > MAX_EVENT_BYTES:
        return {}
    try:
        event = json.loads(raw)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def find(event, key):
    """First string value for key at any depth in the event payload."""
    stack = [event]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def flatten(value, limit=MAX_MESSAGE):
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, str):
        return attention.text(value, limit)
    if isinstance(value, dict):
        return attention.text(json.dumps(value, sort_keys=True), limit)
    if isinstance(value, list):
        return attention.text(json.dumps(value[:MAX_LIST]), limit)
    return None


def reset_evidence(event):
    """Bounded reset hint from any nested header-style map in the payload."""
    stack = [event]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            keys = {attention.text(k, 64).lower(): v for k, v in item.items()}
            for name in ("retry-after", "x-ratelimit-reset", "x-ratelimit-reset-requests",
                         "x-ratelimit-reset-tokens", "anthropic-ratelimit-requests-reset",
                         "anthropic-ratelimit-tokens-reset", "reset_at"):
                if name in keys:
                    hint = attention.reset_hint({"headers": {name: keys[name]}})
                    if hint:
                        return hint
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return {}


def event_name(event):
    """Herdr delivers {"event": "<name>", "data": {...}}. Accept a flat payload too."""
    for key in ("event", "type"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return attention.text(value, 64)
    return ""


def records(event):
    """Translate a Herdr event into bounded attention records."""
    if not event:
        return []
    pane = find(event, "pane_id")
    session = find(event, "agent_session_id") or find(event, "session_id")
    agent = find(event, "agent")
    if not pane:
        return []
    kind = event_name(event)
    status = attention.text(find(event, "agent_status") or "", 24)
    detail = flatten(find(event, "message") or find(event, "reason") or find(event, "title"))
    out = []
    # A blocked lifecycle frame on an owned worker is user-blocking even when the
    # harness never surfaced a native request id to us.
    if status == "blocked":
        out.append({
            "kind": "blocked", "pane": pane, "session": session, "agent": agent,
            "title": detail, "message": detail, "source": "herdr:" + kind,
            "headers": reset_evidence(event),
        })
    return out


def main():
    try:
        root = supervisor.state_root()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 0
    incoming = records(payload())
    if not incoming:
        return 0
    now = time.time()
    try:
        resolved = [session_of(record) for record in incoming]
        # record_events returns the actions the state machine computed. Delivering
        # only the computed actions is what makes this path work at all: the
        # records are already drained here, so no later pass would retry them.
        actions = supervisor.record_events(root, resolved, now)
        supervisor.deliver(root, actions, now)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"attention ingress: {error}", file=sys.stderr)
        return 0
    return 0


def session_of(record):
    """Herdr lifecycle events carry no session id. Resolve it from live state.

    Only a pane whose live agent session still matches the registered worker is
    attributed, so a replaced session in a reused pane can never inherit it.
    Coverage is degraded when Herdr skipped screen detection for the pane, which
    is recorded so the operator can see the fallback is status-derived only.
    """
    if record.get("session"):
        return record
    pane = record.get("pane")
    if not pane:
        return record
    live = next((a for a in supervisor.api("agent", "list")["agents"] if a["pane_id"] == pane), None)
    value = supervisor.identity(live) if live else None
    record = dict(record, session=value)
    record["session_source"] = "resolved"
    if live and live.get("screen_detection_skipped"):
        record["degraded"] = "screen_detection_skipped"
    return record


if __name__ == "__main__":
    sys.exit(main())