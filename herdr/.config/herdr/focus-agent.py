#!/usr/bin/env python3
"""Cycle blocked/done agents, or all agents when none need attention."""

import argparse
import json
import os
import subprocess
import sys


ATTENTION = {"blocked", "done"}
# Match this config's ui.agent_panel_sort = "priority". Stable sorting keeps
# agents with equal priority in the API's workspace/tab/pane order.
PRIORITY = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}


def choose_agent(agents, direction):
    ordered = sorted(agents, key=lambda agent: PRIORITY.get(agent["agent_status"], 4))
    candidates = [agent for agent in ordered if agent["agent_status"] in ATTENTION]
    if not candidates:
        candidates = ordered
    if not candidates:
        return None

    step = 1 if direction == "next" else -1
    for index, agent in enumerate(candidates):
        if agent.get("focused"):
            return candidates[(index + step) % len(candidates)]
    return candidates[0 if step == 1 else -1]


def herdr(*args):
    # Inherit HERDR_SOCKET_PATH so shortcuts target the invoking session.
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    process = subprocess.run(
        [binary, *args], check=True, capture_output=True, text=True, timeout=10
    )
    response = json.loads(process.stdout)
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    return response["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("next", "previous"), default="next", nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="print the target without focusing it")
    args = parser.parse_args()
    try:
        # Read live focus rather than launch-time environment, which can be stale
        # when the user presses the shortcut repeatedly.
        target = choose_agent(herdr("agent", "list")["agents"], args.direction)
        if target is None:
            return
        if args.dry_run:
            print(json.dumps(target))
        elif not target.get("focused"):
            herdr("agent", "focus", target["pane_id"])
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Herdr agent navigation failed: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
