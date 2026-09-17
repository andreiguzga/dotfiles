#!/usr/bin/env python3
"""Report a mute icon token for panes whose agent is muted."""

import json
import os
import sys
import time

from common import event_payload, find_value, herdr, is_muted, muted_agents

TOKEN = "mute"
ICON = os.environ.get("HERDR_ATTENTION_MUTE_ICON", "🔇")
SOURCE = "gzg-mute-icon"


def report(pane_id, muted, seq):
    if not pane_id:
        return
    args = ["pane", "report-metadata", pane_id, "--source", SOURCE, "--seq", str(seq)]
    if muted:
        args += ["--token", f"{TOKEN}={ICON}"]
    else:
        args += ["--clear-token", TOKEN]
    herdr(*args)


def from_event(seq):
    event = event_payload()
    if event is None:
        return
    report(
        find_value(event, "pane_id"),
        is_muted(find_value(event, "agent") or find_value(event, "display_agent")),
        seq,
    )


def all_panes(seq):
    result = herdr("agent", "list")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return
    muted = muted_agents()
    for agent in data.get("result", {}).get("agents", []):
        name = (agent.get("agent") or agent.get("display_agent") or "").lower()
        report(agent.get("pane_id"), name in muted, seq)


def main():
    seq = int(time.time() * 1000)
    if "--all" in sys.argv:
        all_panes(seq)
    else:
        from_event(seq)


if __name__ == "__main__":
    main()
