#!/usr/bin/env python3
"""Session-scoped Herdr worker monitor. No model calls until a worker needs triage."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def state_root():
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not socket:
        raise RuntimeError("HERDR_SOCKET_PATH is required; run inside Herdr")
    key = hashlib.sha256(socket.encode()).hexdigest()[:16]
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "herdr-orchestrator" / key


def api(*args):
    result = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), *args],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)["result"]


@contextmanager
def transaction(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "state.json"
        state = json.loads(path.read_text()) if path.exists() else {
            "paused": False, "workers": {}, "events": [], "projects": {}
        }
        yield state
        temp = root / "state.tmp"
        temp.write_text(json.dumps(state, indent=2) + "\n")
        temp.replace(path)


def identity(agent):
    return (agent.get("agent_session") or {}).get("value")


def matches(record, agent):
    return bool(agent and agent.get("agent") == record["kind"]
                and identity(agent) == record["session"])


def enqueue(state, pane, worker, status, now):
    state["events"].append({
        "id": uuid.uuid4().hex[:12], "pane": pane, "task": worker["task"],
        "project": worker["project"], "status": status, "time": now,
        "delivery": "pending"
    })


def observe(state, agents, now):
    """Only watched sessions may generate work; transitions are persisted/deduplicated."""
    for pane, worker in state["workers"].items():
        if worker.get("finished"):
            continue
        agent = agents.get(pane)
        status = agent.get("agent_status", "unknown") if matches(worker, agent) else "missing-or-replaced"
        if status != worker.get("observed"):
            worker.update(observed=status, since=now)
            worker["stall_reported"] = False
        # A short dwell avoids reporting transient idle frames as task completion.
        if status != worker.get("reported") and now - worker["since"] >= 5:
            worker["reported"] = status
            if status in {"idle", "done", "blocked", "missing-or-replaced"}:
                # idle -> done is a UI distinction, not another completion.
                if status not in {"idle", "done"} or not worker.get("settled"):
                    enqueue(state, pane, worker, status, now)
                worker["settled"] = status in {"idle", "done"}
            elif status == "working":
                worker["settled"] = False
        if status in {"working", "unknown"} and now - worker["since"] >= worker["stall_minutes"] * 60:
            if not worker.get("stall_reported"):
                enqueue(state, pane, worker, "check-progress", now)
                worker["stall_reported"] = True


def tick(root):
    agents = {a["pane_id"]: a for a in api("agent", "list")["agents"]}
    with transaction(root) as state:
        state["heartbeat"] = time.time()
        observe(state, agents, time.time())
        coordinator = state.get("coordinator")
        live = agents.get(coordinator["pane"]) if coordinator else None
        state["coordinator_live"] = bool(coordinator and matches(coordinator, live))
        if state["paused"] or not coordinator:
            return
        if not matches(coordinator, live) or live.get("agent_status") not in {"idle", "done"}:
            return
        # Wait until the last batch is explicitly acknowledged. Never pile up turns.
        if any(e["delivery"] in {"sent", "uncertain"} for e in state["events"]):
            return
        pending = [e for e in state["events"] if e["delivery"] == "pending"][:20]
        if not pending:
            return
        ids = [e["id"] for e in pending]
        prompt = ("Supervisor event batch " + ", ".join(ids) + ". Run the supervisor status command, "
                  "inspect the listed workers, triage according to your coordinator policy, "
                  "then acknowledge these event IDs. Worker idle/done is not proof of task success. "
                  "Do not start unrelated work. Events:\n" + json.dumps(pending))
        # Write ahead: a crash between terminal submission and response must not resend.
        for event in pending:
            event["delivery"] = "uncertain"
    try:
        api("agent", "prompt", coordinator["pane"], prompt)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        with transaction(root) as state:
            state["delivery_error"] = str(error)
        return
    with transaction(root) as state:
        for event in state["events"]:
            if event["id"] in ids and event["delivery"] == "uncertain":
                event["delivery"] = "sent"
        state.pop("delivery_error", None)


def daemon(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        failures = 0
        while True:
            try:
                tick(root)
                failures = 0
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                failures += 1
                print(f"{time.ctime()}: {error}", flush=True)
                # Exit when the server goes away; plugin startup restarts on next launch.
                if failures >= 12:
                    return
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["start", "daemon", "status", "pause", "resume", "unbind"]:
        sub.add_parser(name)
    bind = sub.add_parser("bind", help="Register this OpenCode session as coordinator")
    bind.add_argument("--pane", default=os.environ.get("HERDR_PANE_ID"))
    project = sub.add_parser("project", help="Record user-selected worker tools")
    project.add_argument("name")
    project.add_argument("--cwd", required=True)
    project.add_argument("--tools", nargs="+", choices=["opencode", "claude", "codex"], required=True)
    watch = sub.add_parser("watch", help="Register a new task after its prompt is submitted")
    watch.add_argument("pane")
    watch.add_argument("--project", required=True)
    watch.add_argument("--task", required=True)
    watch.add_argument("--stall-minutes", type=int, default=20)
    finish = sub.add_parser("finish", help="Stop monitoring a reviewed task; leave its pane open")
    finish.add_argument("pane")
    ack = sub.add_parser("ack", help="Acknowledge triaged events (also resolves uncertain delivery)")
    ack.add_argument("ids", nargs="+")
    args = parser.parse_args()
    root = state_root()
    if args.command == "daemon":
        daemon(root)
        return
    if args.command == "start":
        root.mkdir(parents=True, exist_ok=True)
        with (root / "supervisor.log").open("a") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(str(root))
        return
    live = None
    if args.command in {"bind", "watch"}:
        live = next((a for a in api("agent", "list")["agents"] if a["pane_id"] == args.pane), None)
        if not live or not identity(live):
            parser.error("Pane needs a detected agent with a session ID; check Herdr integration")
    with transaction(root) as state:
        if args.command == "bind":
            if live["agent"] != "opencode":
                parser.error("Coordinator must be OpenCode")
            if state.get("coordinator") and state["coordinator"]["pane"] != args.pane:
                parser.error("A different coordinator is already bound in this Herdr session")
            state["coordinator"] = {"pane": args.pane, "kind": live["agent"], "session": identity(live)}
        elif args.command == "unbind":
            state.pop("coordinator", None)
            state["coordinator_live"] = False
        elif args.command == "project":
            cwd = Path(args.cwd).expanduser().resolve()
            if not cwd.is_dir():
                parser.error("Project directory does not exist")
            state["projects"][args.name] = {"cwd": str(cwd), "tools": args.tools}
        elif args.command == "watch":
            project = state["projects"].get(args.project)
            if not project or live["agent"] not in project["tools"]:
                parser.error("Register the project and user-selected tools first")
            if args.pane == state.get("coordinator", {}).get("pane"):
                parser.error("Coordinator cannot watch itself")
            if args.stall_minutes < 1:
                parser.error("stall-minutes must be positive")
            state["workers"][args.pane] = {
                "kind": live["agent"], "session": identity(live), "project": args.project,
                "task": args.task, "stall_minutes": args.stall_minutes,
                "observed": live["agent_status"], "since": time.time(), "settled": False
            }
        elif args.command == "finish":
            state["workers"][args.pane]["finished"] = True
        elif args.command == "ack":
            known = {e["id"] for e in state["events"]}
            if set(args.ids) - known:
                parser.error("Unknown event IDs")
            for event in state["events"]:
                if event["id"] in args.ids:
                    event["delivery"] = "acknowledged"
        elif args.command in {"pause", "resume"}:
            state["paused"] = args.command == "pause"
        print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
