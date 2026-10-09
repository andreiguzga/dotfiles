#!/usr/bin/env python3
"""Attention-event state machine for the orchestrator supervisor.

Pure logic only: no Herdr calls, no model calls, no terminal reads and no
replies. Native approval and question requests are latched here until their own
reply event arrives, bounded quota and provider-error evidence is recorded for
triage, and notices are returned for the supervisor to show the user directly.

Event text is data. Nothing read here is ever treated as an instruction, and a
pending native request is never answered on the user's behalf.
"""

import hashlib
import re
import time
import uuid

MAX_TEXT = 240
MAX_TASK = 48
MAX_SEEN = 256
# Visible latches per pane. A real worker raises a handful of simultaneous
# dialogs; anything beyond this is retained exactly in `overflow`, not dropped.
MAX_PENDING_PER_PANE = 32
MAX_OVERFLOW_PER_PANE = 256
MAX_QUOTA_PANES = 32
MAX_INGRESS_BATCH = 100
MAX_DRAIN_BATCHES = 512
MAX_MALFORMED = 32
NOTICE_COOLDOWN = 90.0
PENDING_REMINDER = 600.0
PENDING_MAX_REMINDERS = 3
# One pass may only nag about this many latches, oldest first. Without it a burst
# of simultaneous dialogs would turn a single reconcile into dozens of toasts.
PENDING_REMINDERS_PER_PASS = 2
PENDING_STALE_AFTER = 20.0
QUOTA_STALE_AFTER = 900.0
RETRY_QUOTA_ATTEMPTS = 2
SEEN_TTL = 86400.0
BACKOFF_STEPS = (5.0, 15.0, 45.0, 120.0, 300.0)

PENDING_KINDS = ("permission", "question")
ALERT_KINDS = ("quota", "error", "blocked")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACE = re.compile(r"\s+")

# Narrow, case-insensitive markers for a provider limit reached. Used only to
# decide whether a retry is worth showing, never to derive an action.
QUOTA_MARKERS = re.compile(
    r"(usage limit|quota|rate[ _-]?limit|ratelimit|429|insufficient (credit|quota|balance)"
    r"|billing|spend|credits? exhausted|resource[_ ]exhausted|exceeded your current"
    r"|monthly limit|spend limit|out of credit)",
    re.IGNORECASE,
)

RESET_HEADERS = (
    "retry-after",
    "x-ratelimit-reset",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "ratelimit-reset",
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-reset",
    "openai-ratelimit-requests-reset",
    "openai-ratelimit-tokens-reset",
)


def text(value, limit=MAX_TEXT):
    """Sanitize untrusted event text: strip escapes and control bytes, cap size."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = _ANSI.sub(" ", value)
    value = _CONTROL.sub(" ", value)
    value = _SPACE.sub(" ", value).strip()
    return value if len(value) <= limit else value[:max(1, limit - 3)] + "..."


def fingerprint(*parts):
    return hashlib.sha256("|".join(text(p, 512) for p in parts).encode()).hexdigest()[:16]


def epoch_seconds(value):
    """Normalize a provider reset hint. Heuristic: large values are milliseconds."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 1e11:
        number /= 1000.0
    return int(number)


def reset_hint(record):
    """Bounded reset evidence from status/headers. Returns {} when absent."""
    headers = record.get("headers")
    lowered = {}
    if isinstance(headers, dict):
        for key, value in list(headers.items())[:16]:
            lowered[text(key, 64).lower()] = text(value, 48)
    for name in ("retry-after", "retry_after"):
        value = lowered.get(name)
        if value and value.isdigit():
            return {"reset_in_seconds": int(value), "reset_source": name}
    for name in RESET_HEADERS:
        value = lowered.get(name)
        if value and value.isdigit():
            seconds = epoch_seconds(value)
            if seconds:
                return {"reset_at": seconds, "reset_source": name}
    for name in ("next", "reset_at", "resetAt", "reset"):
        if record.get(name) is not None:
            seconds = epoch_seconds(record.get(name))
            if seconds:
                return {"reset_at": seconds, "reset_source": f"status.{name}"}
    return {}


def evidence(record):
    """Bounded, sanitized evidence for a quota, provider-error or blocked signal."""
    data = {"signal": text(record.get("kind"), 24)}
    if record.get("error_name"):
        data["error_name"] = text(record.get("error_name"), 48)
    if isinstance(record.get("status_code"), int):
        data["status_code"] = record["status_code"]
    if isinstance(record.get("is_retryable"), bool):
        data["is_retryable"] = record["is_retryable"]
    if record.get("provider"):
        data["provider"] = text(record.get("provider"), 40)
    attempt = record.get("attempt")
    if isinstance(attempt, int) and attempt > 0:
        data["attempt"] = min(attempt, 1000)
    message = text(record.get("message"), MAX_TEXT)
    if message:
        data["message"] = message
    data.update(reset_hint(record))
    return data


def actionable(record, data):
    """Decide whether a captured signal is worth interrupting the user for.

    Errors are actionable. Retries only become actionable once they persist or
    carry a provider limit marker, so ordinary transient retries stay silent.
    """
    if record.get("kind") == "blocked":
        return True
    if record.get("kind") == "error":
        return True
    attempt = data.get("attempt", 0)
    return attempt >= RETRY_QUOTA_ATTEMPTS or bool(QUOTA_MARKERS.search(data.get("message", "")))


def owned(workers, pane, session, agent=None, allow_finished=False):
    """Exact-session ownership; finished workers may only contribute resolution."""
    if not pane or not session:
        return None
    worker = workers.get(pane)
    if not worker or (worker.get("finished") and not allow_finished):
        return None
    if worker.get("session") != session:
        return None
    if agent and worker.get("kind") != agent:
        return None
    return worker


def counts(attention):
    return attention.setdefault("counts", {})


def bump(attention, name, amount=1):
    bucket = counts(attention)
    bucket[name] = bucket.get(name, 0) + amount


def state(store):
    attention = store.setdefault("attention", {})
    for key in ("pending", "overflow", "quota", "seen", "resolved", "attempts", "next_attempt", "counts"):
        attention.setdefault(key, {})
    return attention


def overflow(attention, pane):
    """Exact unresolved request ids kept after the visible latch cap.

    A request is never silently forgotten: ids land here with their kind and
    task, replies clear them by the same id, and nothing here is notified, so a
    large burst cannot turn into an unbounded toast loop.
    """
    return attention["overflow"].setdefault(pane, {})


def retain(attention, pane, entry):
    """Record an unresolved request id beyond the visible cap. Returns True."""
    dropped = overflow(attention, pane)
    request_id = entry.get("request_id")
    if request_id not in dropped:
        dropped[request_id] = {
            "request_id": request_id, "kind": entry.get("kind"), "task": entry.get("task"),
            "project": entry.get("project"), "session": entry.get("session"),
            "turn": entry.get("turn"),
            "since": entry.get("since"), "title": entry.get("title", "")[:80],
        }
        bump(attention, "overflowed_pending")
    while len(dropped) > MAX_OVERFLOW_PER_PANE:
        oldest = min(dropped, key=lambda k: (dropped[k].get("since", 0), k))
        del dropped[oldest]
        bump(attention, "pruned_overflow")
    for pane_id in [p for p in attention["overflow"] if not attention["overflow"][p]]:
        del attention["overflow"][pane_id]
    return request_id not in attention["pending"].get(pane, {})


def prune(attention, now):
    """Bound retained data: latches, overflow, notices, retries and dedup keys."""
    for pane, slot in attention["pending"].items():
        # The oldest unresolved request stays visible: it is the one the user has
        # been asked about longest. Newer ids beyond the cap are retained exactly
        # in the overflow map rather than discarded.
        while len(slot) > MAX_PENDING_PER_PANE:
            newest = max(slot, key=lambda k: (slot[k].get("since", 0), k))
            entry = slot.pop(newest)
            retain(attention, pane, entry)
    for pane in [p for p in attention["pending"] if not attention["pending"][p]]:
        del attention["pending"][pane]
    for pane in list(attention["overflow"]):
        while len(attention["overflow"][pane]) > MAX_OVERFLOW_PER_PANE:
            oldest = min(attention["overflow"][pane],
                         key=lambda k: (attention["overflow"][pane][k].get("since", 0), k))
            del attention["overflow"][pane][oldest]
            bump(attention, "pruned_overflow")
        if not attention["overflow"][pane]:
            del attention["overflow"][pane]
    for pane in sorted(attention["quota"], key=lambda p: attention["quota"][p].get("since", 0)):
        if len(attention["quota"]) > MAX_QUOTA_PANES:
            del attention["quota"][pane]
            bump(attention, "pruned_quota")
    for key in [k for k, seen in attention["seen"].items() if now - seen > SEEN_TTL]:
        del attention["seen"][key]
    if len(attention["seen"]) > MAX_SEEN:
        ordered = sorted(attention["seen"], key=lambda k: attention["seen"][k])
        for key in ordered[: len(attention["seen"]) - MAX_SEEN]:
            del attention["seen"][key]
    # Resolution cache is separate from toast cooldowns: a shown notice alone
    # cannot establish that a request was answered. Durable event audit backs
    # this bounded cache after expiration/eviction.
    for key in [k for k, resolved in attention["resolved"].items() if now - resolved > SEEN_TTL]:
        del attention["resolved"][key]
    ordered = sorted(attention["resolved"], key=lambda k: attention["resolved"][k])
    for key in ordered[:max(0, len(ordered) - MAX_SEEN)]:
        del attention["resolved"][key]


def notice_title(record):
    task = text(record.get("task"), MAX_TASK) or "worker"
    kind = record.get("kind")
    if kind == "permission":
        return f"Approval waiting: {task}"
    if kind == "question":
        return f"Question waiting: {task}"
    if kind == "quota":
        return f"Provider limit: {task}"
    if kind == "error":
        return f"Provider error: {task}"
    return f"Worker needs you: {task}"


def notice_body(record, data=None, reminder=False):
    """Concise and actionable. States where to act and that nothing was answered."""
    parts = [text(record.get("pane"), 24), text(record.get("task"), MAX_TASK)]
    if record.get("kind") == "permission":
        detail = text(record.get("title"), 60) or text(record.get("permission"), 40) or "request"
        parts.append(detail)
    elif record.get("kind") == "question":
        parts.append(text(record.get("title"), 60) or "question")
    data = data or {}
    if data.get("reset_in_seconds"):
        parts.append(f"retry in {data['reset_in_seconds']}s")
    elif data.get("reset_at"):
        parts.append(f"resets {time.strftime('%Y-%m-%d %H:%M', time.localtime(data['reset_at']))}")
    elif data.get("message"):
        parts.append(data["message"][:80])
    parts.append("still waiting" if reminder else "reply in that pane, not answered for you")
    body = " - ".join(part for part in parts if part)
    return body if len(body) <= 200 else body[:197] + "..."


def notice(action_kind, record, fingerprint_value, data=None, reminder=False):
    return {
        "action": action_kind,
        "kind": record.get("kind"),
        "fingerprint": fingerprint_value,
        "title": notice_title(record),
        "body": notice_body(record, data, reminder),
        "sound": "request",
        "reminder": reminder,
        "pane": record.get("pane"), "session": record.get("session"),
        "turn": record.get("turn"),
        "request_id": record.get("request_id"),
    }


def triage(record, status, detail):
    detail = dict(detail, session=record.get("session"), turn=record.get("turn"))
    return {
        "action": "triage",
        "pane": record.get("pane"),
        "task": record.get("task"),
        "project": record.get("project"),
        "status": status,
        "detail": detail,
    }


def is_resolved(store, pane, session, request_id):
    """Exact-id resolution evidence; the bounded cache is only an accelerator."""
    if not pane or not session or not request_id:
        return False
    return fingerprint(pane, session, request_id) in state(store)["resolved"] or any(
        event.get("status") == "attention-resolved" and event.get("pane") == pane
        and (event.get("session") or (event.get("attention") or {}).get("session")) == session
        and (event.get("attention") or {}).get("request_id") == request_id
        for event in store.get("events", []))


def latch(attention, store, worker, record, now):
    """Hold a native pending request until its own reply event clears it."""
    pane = record["pane"]
    request_id = text(record.get("request_id"), 64) or fingerprint(
        record.get("kind"), pane, record.get("session"), record.get("title")
    )
    if is_resolved(store, pane, worker["session"], request_id):
        bump(attention, "ignored_resolved_ask")
        return []
    slot = attention["pending"].setdefault(pane, {})
    if request_id in slot or request_id in attention["overflow"].get(pane, {}):
        bump(attention, "duplicate_pending")
        return []
    entry = {
        "kind": record.get("kind"),
        "pane": pane,
        "session": worker["session"],
        "turn": worker.get("turn"),
        "request_id": request_id,
        "task": worker["task"],
        "project": worker["project"],
        "title": text(record.get("title"), MAX_TEXT),
        "detail": text(record.get("permission"), 64) or text(record.get("header"), 64),
        "since": now,
        "reminders": 0,
    }
    slot[request_id] = entry
    # Bound inside the same transaction: the cap limits the visible latch set,
    # and anything past it keeps its exact id in the overflow map.
    prune(attention, now)
    seen = fingerprint(entry["kind"], pane, worker["session"], request_id)
    bump(attention, "latched")
    return [
        notice("notify", entry, seen),
        triage(entry, f"attention-{entry['kind']}", {
            "request_id": request_id, "session": worker["session"], "title": entry["title"],
            "answered_by_supervisor": False,
        }),
    ]


def alert(attention, store, worker, record, now):
    """Record bounded quota/provider-error evidence; notice once when actionable."""
    pane = record["pane"]
    data = evidence(record)
    # Retry messages often include changing countdowns/attempt numbers. One
    # provider/retry class is one episode; retain latest bounded evidence.
    message_key = data.get("message") if record.get("kind") != "quota" else (
        "provider-limit" if QUOTA_MARKERS.search(data.get("message", "")) else "retry")
    key = fingerprint(record.get("kind"), pane, worker["session"],
                      data.get("provider"), data.get("error_name"),
                      data.get("status_code"), message_key)
    existing = attention["quota"].get(pane)
    if existing and existing.get("fingerprint") == key:
        existing["last_seen"] = now
        existing["count"] = existing.get("count", 1) + 1
        existing["evidence"] = data
        existing["message_key"] = message_key
        bump(attention, "duplicate_alert")
        if not actionable(record, data):
            return []
        if existing.get("notified"):
            return []
        existing["notified"] = True
        entry = dict(existing)
    else:
        entry = {
            "kind": record.get("kind"), "pane": pane, "session": worker["session"],
            "turn": worker.get("turn"),
            "task": worker["task"], "project": worker["project"],
            "fingerprint": key, "evidence": data, "since": now, "last_seen": now,
            "message_key": message_key,
            "count": 1, "notified": False,
        }
        attention["quota"][pane] = entry
        bump(attention, "recorded_alert")
        if not actionable(record, data):
            return []
        entry["notified"] = True
    return [
        notice("notify", entry, key, data),
        triage(entry, f"attention-{entry['kind']}", {
            "evidence": data, "count": entry.get("count", 1),
            "message_key": entry["message_key"], "fingerprint": key,
        }),
    ]


def resolve(attention, store, worker, record, now):
    """Clear exactly the replied ID and remember resolution before later replays."""
    pane = record["pane"]
    request_id = text(record.get("request_id"), 64)
    if not request_id:
        bump(attention, "unmatched_reply")
        return []
    # Native request ids belong to a session, not a watch/task label. A rewatch
    # must not revive an already-resolved request in that same exact session.
    slot = attention["pending"].get(pane, {})
    entry = slot.pop(request_id, None)
    from_overflow = False
    if entry is None:
        # A reply may name a request whose latch moved to overflow; the id is
        # still the same fact, so it clears there too.
        held = attention["overflow"].get(pane, {})
        entry = held.pop(request_id, None)
        from_overflow = entry is not None
        if not held:
            attention["overflow"].pop(pane, None)
    if not slot:
        attention["pending"].pop(pane, None)
    detail = {
        "request_id": request_id, "session": worker["session"], "turn": worker.get("turn"),
        "kind": entry.get("kind") if entry else None,
        "reply": text(record.get("reply"), 24) or None,
        "unmatched": entry is None,
    }
    if entry:
        detail["waited_seconds"] = max(0, int(now - entry.get("since", now)))
    if from_overflow:
        detail["from_overflow"] = True
    # Persist before leaving the application transaction or confirming ingress.
    # This includes resolve-first/unmatched ids, even if delivery never runs.
    event_id = uuid.uuid4().hex[:12]
    store.setdefault("events", []).append({
        "id": event_id, "pane": pane, "session": worker["session"],
        "turn": worker.get("turn"), "project": worker["project"], "task": worker["task"],
        "status": "attention-resolved", "time": now, "attention": dict(detail),
        "delivery": "retired", "filter_reason": "audit-only",
    })
    attention["resolved"][fingerprint(pane, worker["session"], request_id)] = now
    prune(attention, now)
    bump(attention, "resolved" if entry else "unmatched_reply")
    return [triage(dict(entry, turn=worker.get("turn")), "attention-resolved",
                   dict(detail, audit_event_id=event_id))] if entry else []


def apply(store, record, now):
    """Apply one normalized ingress record. Returns notice and triage actions."""
    if not isinstance(record, dict):
        return []
    attention = state(store)
    pane = record.get("pane")
    session = record.get("session")
    kind = record.get("kind")
    worker = owned(store.get("workers", {}), pane, session, record.get("agent"),
                   allow_finished=kind == "resolve")
    if worker is None:
        bump(attention, "ignored_unowned")
        return []
    record = dict(record, pane=pane, task=worker["task"], project=worker["project"])
    if kind in PENDING_KINDS:
        return latch(attention, store, worker, record, now)
    if kind in ALERT_KINDS:
        if kind == "blocked" and attention["pending"].get(pane):
            bump(attention, "suppressed_by_pending")
            return []
        return alert(attention, store, worker, record, now)
    if kind == "resolve":
        return resolve(attention, store, worker, record, now)
    if kind == "retry-recovered":
        entry = attention["quota"].get(pane)
        # Retry -> idle may mean giving up, not recovery from a provider limit.
        # Only a classified plain retry is cleared; unknown/legacy classes stay.
        if entry and entry.get("kind") == "quota" and entry.get("session") == session \
                and entry.get("message_key") == "retry":
            del attention["quota"][pane]
            bump(attention, "recovered_retry")
            return [triage(entry, "attention-recovered", {
                "fingerprint": entry["fingerprint"], "message_key": "retry",
            })]
        return []
    bump(attention, "ignored_kind")
    return []


def reconcile(store, statuses, now):
    """Periodic model-free fallback: expire stale latches, remind, recover."""
    attention = state(store)
    actions = []
    workers = store.get("workers", {})
    reminded = 0
    def oldest_since(pane, default=0):
        """Age of the oldest latch on a pane, used to order reminder passes."""
        slot = attention["pending"].get(pane) or {}
        return min((entry.get("since", 0) for entry in slot.values()), default=default)

    # Oldest latch is nagged first so a burst cannot starve the request the user
    # has been waiting about longest.
    for pane in sorted(attention["pending"], key=oldest_since):
        slot = attention["pending"][pane]
        worker = workers.get(pane)
        status = statuses.get(pane, "unknown")
        # A `watch` re-registration or a replaced session retires every latch
        # from the previous turn. Otherwise a reminder would name the task the
        # pane used to run.
        superseded = []
        if worker:
            superseded = [request_id for request_id, entry in slot.items()
                          if entry.get("session") != worker.get("session")
                          or entry.get("task") != worker.get("task")]
        for request_id in superseded:
            entry = slot.pop(request_id)
            bump(attention, "dropped_superseded_pending")
            actions.append(triage(entry, "attention-superseded", {
                "request_id": request_id, "kind": entry.get("kind"),
                "previous_task": entry.get("task"),
                "waited_seconds": max(0, int(now - entry.get("since", now))),
            }))
        if not worker or worker.get("finished"):
            for entry in slot.values():
                bump(attention, "dropped_unowned_pending")
            attention["pending"].pop(pane, None)
            held = attention["overflow"].pop(pane, None) or {}
            for entry in held.values():
                bump(attention, "dropped_unowned_overflow")
                actions.append(triage(entry, "attention-unanswered", {
                    "request_id": entry.get("request_id"), "kind": entry.get("kind"),
                    "from_overflow": True,
                    "waited_seconds": max(0, int(now - entry.get("since", now))),
                }))
            continue
        for request_id, entry in list(slot.items()):
            if status in {"idle", "done", "missing-or-replaced"}:
                entry.setdefault("stale_since", now)
                if now - entry["stale_since"] >= PENDING_STALE_AFTER:
                    del slot[request_id]
                    bump(attention, "dropped_stale_pending")
                    actions.append(triage(entry, "attention-unanswered", {
                        "request_id": request_id, "kind": entry.get("kind"),
                        "waited_seconds": max(0, int(now - entry.get("since", now))),
                    }))
                continue
            entry.pop("stale_since", None)
            if status not in {"blocked", "working"}:
                continue
            last = entry.get("reminded_at", entry.get("since", now))
            if now - last < PENDING_REMINDER or entry.get("reminders", 0) >= PENDING_MAX_REMINDERS:
                continue
            if reminded >= PENDING_REMINDERS_PER_PASS:
                continue
            entry["reminders"] = entry.get("reminders", 0) + 1
            entry["reminded_at"] = now
            reminded += 1
            bump(attention, "reminded")
            actions.append(notice("notify", entry, fingerprint(
                entry.get("kind"), pane, entry.get("session"), request_id, "reminder"
            ), reminder=True))
        if not slot:
            del attention["pending"][pane]
    for pane, entry in list(attention["quota"].items()):
        status = statuses.get(pane, "unknown")
        if entry.get("kind") == "blocked":
            if status != "blocked":
                del attention["quota"][pane]
                bump(attention, "recovered_blocked")
        elif entry.get("message_key") != "provider-limit" \
                and now - entry.get("last_seen", now) >= QUOTA_STALE_AFTER \
                and status in {"idle", "done", "working"}:
            del attention["quota"][pane]
            bump(attention, "recovered_alert")
    prune(attention, now)
    return actions


def summary(store, now=None):
    """Bounded view for status output and the coordinator."""
    attention = state(store)
    pending = {}
    for pane, slot in attention["pending"].items():
        pending[pane] = [
            {"request_id": entry.get("request_id"), "kind": entry.get("kind"),
             "task": entry.get("task"), "since": entry.get("since"),
             "reminders": entry.get("reminders", 0), "title": entry.get("title", "")[:80]}
            for entry in sorted(slot.values(), key=lambda e: e.get("since", 0))
        ]
    alerts = [
        {"pane": entry.get("pane"), "kind": entry.get("kind"), "task": entry.get("task"),
         "count": entry.get("count", 1), "evidence": entry.get("evidence", {})}
        for entry in sorted(attention["quota"].values(), key=lambda e: e.get("since", 0))
    ]
    overflowed = {
        pane: [{"request_id": entry.get("request_id"), "kind": entry.get("kind"),
                "task": entry.get("task"), "since": entry.get("since")}
               for entry in sorted(slot.values(), key=lambda e: e.get("since", 0))]
        for pane, slot in sorted(attention["overflow"].items())
    }
    return {"pending": pending, "overflowed": overflowed, "alerts": alerts,
            "counts": dict(counts(attention))}


def backoff(attempts):
    index = min(max(int(attempts) - 1, 0), len(BACKOFF_STEPS) - 1)
    return BACKOFF_STEPS[index]
