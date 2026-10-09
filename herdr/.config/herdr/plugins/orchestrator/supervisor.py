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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attention


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


def record_events(root, records, now=None):
    """Append normalized attention records to a per-writer spool, then drain it.

    Writers are event hooks and companion harness plugins running as separate
    processes, so appends are atomic-ish and the state transaction stays the only
    authority for latches, dedup and ownership.
    """
    root.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    spool = root / "events-in.jsonl"
    with (root / "spool.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with spool.open("a") as handle:
            for record in records:
                handle.write(json.dumps({"at": now, "record": record}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return drain_events(root, now)


def claim_batch(root, limit=attention.MAX_INGRESS_BATCH):
    """Claim up to `limit` spooled records, oldest source order first.

    Every source is read: the in-flight file (an unconfirmed batch from a crashed
    drainer), then leftover claim files, then the live spool. The claimed batch
    is parked in the in-flight file and only removed by `confirm` once the state
    transaction has committed, so a crash between claim and apply replays the
    batch instead of losing it. Unconsumed lines stay on disk, so nothing is
    ever truncated away.
    """
    root.mkdir(parents=True, exist_ok=True)
    spool = root / "events-in.jsonl"
    inflight = root / "events-inflight.jsonl"
    with (root / "spool.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sources = sorted(root.glob("events-claim*.jsonl"))
        if spool.exists() and spool.stat().st_size:
            moved = root / f"events-claim-{os.getpid()}.jsonl"
            os.replace(spool, moved)
            sources.append(moved)
        if inflight.exists() and inflight.stat().st_size:
            sources.insert(0, inflight)
        if not sources:
            return []
        usable, malformed = [], []
        for source in sources:
            try:
                lines = source.read_text().splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    # An interrupted append stays unconsumed so the writer's
                    # retry can complete it instead of the record being lost.
                    malformed.append(line)
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("record"), dict):
                    usable.append((line, entry))
                else:
                    malformed.append(line)
        batch = usable[:limit]
        for source in sources:
            try:
                source.unlink()
            except OSError:
                pass
        if not batch:
            # Nothing usable left. Retain only a bounded tail of unusable lines
            # so a truncated append can still be completed by its writer's retry
            # without pinning the spool forever.
            tail = malformed[-attention.MAX_MALFORMED:]
            if tail:
                rest = root / f"events-claim-{os.getpid()}.part"
                rest.write_text("".join(line + "\n" for line in tail))
                os.replace(rest, root / "events-claim-corrupt.jsonl")
            return []
        temp = root / "events-inflight.tmp"
        temp.write_text("".join(line + "\n" for line, _ in batch))
        os.replace(temp, inflight)
        remainder = [line for line, _ in usable[limit:]] + malformed[-attention.MAX_MALFORMED:]
        if remainder:
            rest = root / f"events-claim-{os.getpid()}.part"
            rest.write_text("".join(line + "\n" for line in remainder))
            os.replace(rest, root / "events-claim-rest.jsonl")
    return [entry for _, entry in batch]


def confirm(root):
    """Release an in-flight batch after its records are durably applied."""
    try:
        (root / "events-inflight.jsonl").unlink()
    except OSError:
        pass


def drain_events(root, now=None, max_batches=attention.MAX_DRAIN_BATCHES):
    """Apply every spooled record in bounded batches and return the actions.

    The loop is what makes the batch limit safe: it bounds each state
    transaction, not how much of the queue is accepted.
    """
    now = time.time() if now is None else now
    actions = []
    for _ in range(max_batches):
        batch = claim_batch(root)
        if not batch:
            break
        with transaction(root) as state:
            state["heartbeat"] = now
            for entry in batch:
                actions.extend(attention.apply(state, entry["record"], now))
            attention.prune(attention.state(state), now)
        confirm(root)
    return actions


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


def pending_notices(state, now):
    """Re-emit notices that never reached the user, so a failed toast is retried.

    The latch is the durable record: dedup suppresses the event, not the retry.
    Once the backoff window has passed the same request is offered again.
    """
    section = attention.state(state)
    retrying = []
    for pane, slot in section["pending"].items():
        for entry in slot.values():
            key = attention.fingerprint(entry.get("kind"), pane, entry.get("session"),
                                        entry.get("request_id"))
            # `attempts` holds only keys whose last delivery was not confirmed, so
            # a still-pending but already-shown request is never re-toasted.
            if key not in section["attempts"] or section["next_attempt"].get(key, 0) > now:
                continue
            retrying.append(attention.notice("notify", entry, key))
    for pane, entry in section["quota"].items():
        key = entry.get("fingerprint")
        if key not in section["attempts"] or section["next_attempt"].get(key, 0) > now:
            continue
        retrying.append(attention.notice("notify", entry, key, entry.get("evidence")))
    return retrying


def notify(actions, root):
    """Show direct user notices. Independent of coordinator idle, ack and delivery.

    Herdr rate limits toasts, so a failure is recorded and retried with backoff
    instead of being retried hot. Never answers anything; it only tells the user.
    """
    notices = [a for a in actions if a.get("action") == "notify"]
    if not notices:
        return []
    outcomes = []
    for item in notices:
        key = item["fingerprint"]
        with transaction(root) as state:
            attention_section = attention.state(state)
            # Backoff first: a failed notice must wait for its retry window even
            # though the same fingerprint is inside the success cooldown.
            if attention_section["next_attempt"].get(key, 0) > time.time():
                attention.bump(attention_section, "notice_backoff")
                continue
            seen = attention_section["seen"]
            last = seen.get(key)
            # Cooldown guards the raw event rate; the latch itself is deduped above.
            if last is not None and time.time() - last < attention.NOTICE_COOLDOWN:
                attention.bump(attention_section, "notice_suppressed")
                continue
            attention_section["attempts"][key] = attention_section["attempts"].get(key, 0) + 1
        try:
            result = api("notification", "show", item["title"], "--body", item["body"],
                         "--sound", item.get("sound", "request"))
            shown = bool(result.get("shown"))
            reason = result.get("reason")
        except (RuntimeError, subprocess.TimeoutExpired, ValueError, OSError) as error:
            shown, reason = False, str(error)
        with transaction(root) as state:
            attention_section = attention.state(state)
            key = item["fingerprint"]
            if shown:
                # Only a shown toast starts the cooldown; a failure must stay
                # retryable as soon as its backoff window expires.
                attention_section["seen"][key] = time.time()
                attention_section["next_attempt"].pop(key, None)
                attention_section["attempts"].pop(key, None)
                attention.bump(attention_section, "notice_shown")
            else:
                attempts = attention_section["attempts"].get(key, 1)
                attention_section["next_attempt"][key] = time.time() + attention.backoff(attempts)
                attention.bump(attention_section, "notice_failed")
                reason = f"attention notice not shown ({reason})"
            state["notice_error"] = None if shown else reason
        outcomes.append({"fingerprint": key, "shown": shown, "reason": reason})
    return outcomes


def retire_turn(state, pane, now):
    """Drop every latch and queued notice belonging to a retired watch turn."""
    section = attention.state(state)
    dropped = 0
    for slot in (section["pending"].pop(pane, None), section["overflow"].pop(pane, None)):
        dropped += len(slot or {})
    attention.bump(section, "dropped_retired_turn", dropped)
    for event in state["events"]:
        if event["pane"] == pane and event["delivery"] == "pending" \
                and str(event.get("status", "")).startswith("attention-"):
            event["delivery"] = "retired"
    return dropped


def triage_events(root, actions, now):
    """Queue attention facts for the coordinator, never duplicating a notice."""
    queued = []
    with transaction(root) as state:
        for item in actions:
            if item.get("action") != "triage":
                continue
            worker = state["workers"].get(item.get("pane"))
            if not worker or worker.get("finished"):
                continue
            enqueue(state, item["pane"], worker, item["status"], now)
            state["events"][-1]["attention"] = item.get("detail")
            queued.append(state["events"][-1]["id"])
    return queued


def deliver(root, actions, now=None):
    """Route computed actions plus any unconfirmed retry to the user, then triage.

    Shared by the Herdr event hook and the `event` CLI so both deliver exactly
    once and both retry a toast Herdr refused to show.
    """
    now = time.time() if now is None else now
    with transaction(root) as state:
        # A repeat of an already-latched event yields no actions, but a toast
        # Herdr refused earlier must still be retried on this pass.
        actions = list(actions) + pending_notices(state, now)
    notices = notify(actions, root)
    triaged = triage_events(root, actions, now)
    return notices, triaged


def tick(root):
    agents = {a["pane_id"]: a for a in api("agent", "list")["agents"]}
    now = time.time()
    # Spooled event ingress runs first so a native pending request noticed here
    # is never reported as a plain lifecycle change in the same pass.
    actions = drain_events(root, now)
    with transaction(root) as state:
        state["heartbeat"] = now
        observe(state, agents, now)
        statuses = {p: a.get("agent_status", "unknown") for p, a in agents.items()}
        # Herdr skips screen detection for panes whose agent owns lifecycle
        # authority. Recording it makes the degraded fallback coverage visible
        # instead of leaving the operator to infer it.
        state["detection_skipped"] = sorted(
            p for p, a in agents.items() if a.get("screen_detection_skipped"))
        actions.extend(attention.reconcile(state, statuses, now))
        attention.prune(attention.state(state), now)
    # User notices never wait for coordinator idle, batching or acknowledgement.
    deliver(root, actions, now)
    with transaction(root) as state:
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


def code_sources():
    """Files whose content defines what this process actually runs."""
    here = Path(__file__).resolve().parent
    return [here / name for name in ("supervisor.py", "attention.py", "attention-event.py")
            if (here / name).exists()]


def startup_fingerprint():
    """Deterministic digest of the loaded helper sources.

    Recorded by the daemon process itself, so it describes the code that is
    running rather than whatever a later CLI invocation happens to read.
    """
    digest = hashlib.sha256()
    for path in sorted(code_sources(), key=lambda p: p.name):
        digest.update(path.name.encode())
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()[:32]


def daemon_identity(started_at=None):
    return {
        "pid": os.getpid(),
        "started_at": time.time() if started_at is None else started_at,
        "fingerprint": startup_fingerprint(),
        "sources": {p.name: p.stat().st_size for p in code_sources()},
        "python": sys.version.split()[0],
    }


def write_daemon_metadata(root, identity):
    """Publish the running daemon's own identity. Only ever called under the lock."""
    with transaction(root) as state:
        state["daemon"] = identity
    return identity


def lock_probe(root):
    """Independent check that a daemon really holds the lock right now.

    Stale metadata is not liveness: this asks the kernel whether any process
    holds the lock, and reports the recorded pid alongside it so the caller can
    corroborate both. A probe that succeeds releases immediately.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / "daemon.lock"
    recorded = {}
    holder = None
    try:
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                holder = True
            else:
                holder = False
                fcntl.flock(lock, fcntl.LOCK_UN)
    except OSError:
        return {"lock_held": None, "pid": None, "alive": False, "error": "lock unreadable"}
    if path.exists():
        try:
            fields = path.read_text().split()
        except OSError:
            fields = []
        if len(fields) >= 3:
            recorded = {"pid": int(fields[0]), "started_at": float(fields[1]),
                        "fingerprint": fields[2]}
    pid = recorded.get("pid")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    return {"lock_held": holder, "pid": pid, "alive": alive,
            "lock_file": recorded, "error": None}


def daemon(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "daemon.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another daemon owns this session. Its metadata stays authoritative;
            # a competing start must never overwrite it.
            return {"started": False, "reason": "lock held"}
        # Only the lock holder publishes metadata about itself.
        identity = daemon_identity()
        lock.seek(0)
        lock.truncate()
        lock.write(f"{identity['pid']} {identity['started_at']} {identity['fingerprint']}\n")
        lock.flush()
        os.fsync(lock.fileno())
        write_daemon_metadata(root, identity)
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
                    return {"started": True, "reason": "api failures"}
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
    sub.add_parser("pending", help="Show latched pending requests and captured provider evidence")
    ingest = sub.add_parser("event", help="Ingest attention records as JSON lines on stdin")
    ingest.add_argument("--dry-run", action="store_true",
                        help="Spool the records without applying or notifying")
    args = parser.parse_args()
    root = state_root()
    if args.command == "pending":
        with transaction(root) as state:
            report = attention.summary(state)
            report["detection_skipped"] = state.get("detection_skipped", [])
            print(json.dumps(report, indent=2))
        return
    if args.command == "status":
        # `daemon` is whatever the lock-holding process wrote about itself. It is
        # never recomputed here: this CLI may be reading newer sources than the
        # running daemon. Liveness comes from the lock probe, not from the file.
        probe = lock_probe(root)
        with transaction(root) as state:
            recorded = state.get("daemon")
            print(json.dumps({
                "daemon": recorded,
                "daemon_present": recorded is not None,
                "lock": probe,
                "running": bool(recorded and probe["lock_held"] and probe["alive"]
                                and recorded.get("pid") == probe["pid"]),
                "heartbeat": state.get("heartbeat"),
                "source_fingerprint_now": startup_fingerprint(),
                "state": state,
            }, indent=2))
        return
    if args.command == "event":
        records = []
        for line in sys.stdin.read().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        if not records:
            return
        if args.dry_run:
            print(json.dumps({"spooled": len(records)}))
            record_events(root, records)
            return
        actions = record_events(root, records)
        results = notify(actions, root)
        triage_events(root, actions, time.time())
        print(json.dumps({"records": len(records), "notices": results}, indent=2))
        return
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
            previous = state["workers"].get(args.pane)
            state["workers"][args.pane] = {
                "kind": live["agent"], "session": identity(live), "project": args.project,
                "task": args.task, "stall_minutes": args.stall_minutes,
                "observed": live["agent_status"], "since": time.time(), "settled": False
            }
            # Re-registering a pane starts a new turn. Latches and queued notices
            # from the previous task are retired here rather than left to produce
            # reminders that name a superseded task.
            if previous and (previous.get("session") != identity(live)
                             or previous.get("task") != args.task):
                retire_turn(state, args.pane, time.time())
        elif args.command == "finish":
            state["workers"][args.pane]["finished"] = True
            retire_turn(state, args.pane, time.time())
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
