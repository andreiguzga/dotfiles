# Attention events

Native approval/question requests and provider quota or error states on
registered workers reach you in about a second, whether or not the coordinator
is idle, and whether or not Herdr's lifecycle says the worker is working.

## What you get

A direct Herdr toast, deduplicated per request, for an owned worker when:

- the worker raises a native approval request (`permission.asked`);
- the worker asks you a question (`question.asked`);
- the worker's provider retries long enough or reports a limit
  (`session.status` retry, `session.error`);
- Herdr reports the pane as `blocked` and no native request id was captured.

Each toast names the pane, the task, and where to act. It never contains
transcript text, never answers anything, and never replays. A request stays
latched until its own reply event clears that exact request id, even while
lifecycle status keeps saying `working`. Re-registering a pane with `watch`, or
the pane's session being replaced, retires that turn's latches and queued
notices, so a reminder can never name a superseded task.
Resolution is remembered by exact pane/session/request id: a late duplicate
ASKED cannot relatch or re-notify that resolved request. A bounded resolution
cache is backed by retained resolution audit events, separate from toast dedup.
Valid owned resolve-first/unmatched replies are also audited inside the ingress
application transaction. Exact resolution retires pending unanswered facts, and
notice delivery revalidates the unresolved request immediately before dispatch;
sent/uncertain delivery receipts are preserved.

Not every completion notifies you. Ordinary `idle`/`done` transitions still go
only to the coordinator queue, as before.

## Coverage: OpenCode is exact, other harnesses are status-only

| Worker | What you get | Request id |
| --- | --- | --- |
| OpenCode | native event, sub-second | exact, per request |
| Claude, Codex, anything else | Herdr `blocked` status only | none |

For OpenCode workers the companion plugin reads the harness event directly, so a
`permission.asked` is caught even while Herdr reports `working` — which is
exactly the failure this feature exists for.

For every other harness there is **no full screen fallback**. Nothing here reads
a pane's screen, scrollback or transcript, and no historic scrape is performed.
Those workers are covered only by Herdr's own `blocked` status, and that value
is derived rather than observed: the managed integration maps
`permission.asked → blocked` and `permission.replied → working`, so a worker
mid-approval can still read `working`. When Herdr skips screen detection for a
pane (`screen_detection_skipped`) the fallback is degraded for that pane; both
`supervisor.py status` and `supervisor.py pending` list those panes under
`detection_skipped` so the reduced coverage is visible rather than inferred.

## Two ingress paths

Event push, immediate:

- `opencode/.config/opencode/plugins/herch-orchestrator-attention.js` watches
  OpenCode's native events in the worker process and spools one JSON line per
  attention record to `supervisor.py event`. It reports no agent lifecycle
  state: the managed `herdr-agent-state.js` owns `pane.report_agent`, and a
  second writer on one pane would overwrite the first.
- The `gzg.orchestrator` manifest hooks `pane.agent_status_changed` and
  `pane.agent_detected` to `attention-event.py`. Herdr's hook payload is
  `{"event": "<name>", "data": {...}}` and carries no session id, so the hook
  reads the event name from `event`, the fields from `data`, and resolves the
  pane's live agent session before recording anything. It delivers the actions
  it computes — notify and triage — in the same invocation, because no later
  pass would retry records it already drained.

Bounded fallback, periodic and model-free:

- The supervisor's five-second tick drains the spool in bounded batches, expires
  stale latches, emits capped reminders, and recovers alerts for panes that have
  moved on.
- If a harness never exposes events, the tick still sees `blocked` through
  `agent list` and reconciles from there.

## Durability of the ingress queue

Writers only ever append to `events-in.jsonl` under `spool.lock`. A drainer
claims up to 100 records at a time, parks them in `events-inflight.jsonl`, and
deletes that file only after the state transaction has committed. Unconsumed
lines stay on disk in a claim file. Consequences:

- A burst far larger than one batch is applied across successive batches; no
  record is truncated away.
- A crash between claim and apply replays the batch. Latches and dedup make that
  replay idempotent.
- A partially written trailing line is treated as unconsumed, so an interrupted
  append is completed by the writer's retry instead of being dropped.

## Ownership

A notice is only possible when all of these hold: the pane is registered with
`watch`, the record's session equals that worker session, the worker is not
`finished`, and the agent kind matches. Unregistered panes, reused panes with a
replaced session, finished workers and mismatched agents are counted and
dropped. The coordinator's own pane is not a worker.

Event text is data. Titles, questions and provider messages are stripped of
escape and control bytes, whitespace-collapsed, length-capped, and only ever
reported. Nothing an event says is treated as an instruction, and a native
permission or question is never answered, approved, or cancelled by this
plugin.

## Notice and triage are separate

`notification show` goes out immediately. It is not gated on coordinator
`idle`/`done`, not gated on `pause`, not gated on the previous batch's
acknowledgement, and not queued behind the coordinator. Herdr's own toast rate
limiting is respected with bounded backoff (5s, 15s, 45s, 120s, 300s) and a
90-second success cooldown per request, so a failing or busy toast path cannot
produce a hot loop.

The same facts are also audited as `attention-permission`, `attention-question`,
`attention-quota`, `attention-error`, `attention-resolved`,
`attention-unanswered`, `attention-superseded`, `attention-recovered` and `attention-blocked`
events. A code-only filter retires resolved/superseded/recovered pending wakeups,
coalesces equivalent retries, and allows one terminal review event per submitted
watch turn. Native request model escalation waits a reversible 120-second grace;
direct notices do not wait. A resolved request before delivery causes no prompt.
Sent/uncertain batches are never discarded by the filter. See
[COORDINATOR-OPTIMIZATIONS.md](COORDINATOR-OPTIMIZATIONS.md).
Actionable events keep the existing delivery safety: one batch only when
the coordinator is idle/done, never while a batch is `sent` or `uncertain`, and
`ack ID ...` still required.

## Inspecting

```sh
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py status
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py pending
```

`pending` prints the latched request ids, the `overflowed` ids retained beyond
the visible cap, per-pane captured provider evidence, `detection_skipped`, and
counters (`latched`, `resolved`, `duplicate_pending`, `overflowed_pending`,
`dropped_superseded_pending`, `ignored_unowned`, `notice_shown`,
`notice_failed`, `notice_backoff`). No transcript or scrollback is ever read.

`status` additionally reports which daemon is running. See the activation
section for why a heartbeat alone does not prove the new code loaded.

## Bounded data

Per pane: at most 32 visible latched requests plus 256 retained overflow ids
(oldest stay visible), and one captured provider alert. Provider messages cap at
240 characters, header maps at 16 entries of 48 characters, reset hints come
from `retry-after` and `x-ratelimit-*`-style headers or the retry `next`
timestamp, and the dedup key set is bounded and expires after a day. Reminders
are capped at three per request and two per reconcile pass, oldest latch first.

## Activation

Not live from this worktree. Both halves must be installed; installing only the
Herdr half makes the fallback look live while the primary path is missing.

```sh
# 1. Companion OpenCode plugin -> ~/.config/opencode/plugins/
stow -t ~ opencode                       # or: ln -s <repo>/opencode/.config/opencode/plugins/herdr-orchestrator-attention.js \
                                         #        ~/.config/opencode/plugins/herdr-orchestrator-attention.js

# 2. Herdr plugin -> the linked orchestrator directory
herdr plugin link ~/.config/herdr/plugins/orchestrator
herdr server reload-config

# 3. Restart the supervisor daemon so it runs the new code
herdr plugin action invoke gzg.orchestrator.start
```

Step 3 matters: `start` spawns a daemon that takes an exclusive `daemon.lock`. If
the previous daemon still holds it, the new one exits immediately and the **old
code keeps polling**. A fresh heartbeat is not proof of that: the old daemon
keeps updating it. Prove the replacement by pid, fingerprint and start time.

Each daemon writes its own identity into the lock file and into state, but only
after it has acquired the lock:

| Field | Meaning |
| --- | --- |
| `pid` | the lock-holding supervisor process |
| `started_at` | when that process started its loop |
| `fingerprint` | digest of the sources **that process** loaded |
| `sources` | byte size of each loaded helper |
| `python` | interpreter version of that process |

`supervisor.py status` reports `daemon` exactly as the running daemon wrote it,
never a fingerprint recomputed from the CLI's own sources. Liveness is a
separate `lock` block: `lock_held` asks the kernel whether any process holds
`daemon.lock`, and `pid`/`alive` corroborate the recorded owner. `running` is
true only when the lock is held, that pid is alive, and it matches `daemon.pid`.
Stale metadata therefore never reads as running.

Replace the old supervisor safely:

```sh
# 1. Identify the holder. Read pid from the lock file, then confirm the lock
#    is actually held and that the pid is a supervisor, not the Herdr server.
STATE=$(python3 -c 'import hashlib,os;from pathlib import Path;print(Path(os.environ["XDG_STATE_HOME"])/"herdr-orchestrator"/hashlib.sha256(os.environ["HERDR_SOCKET_PATH"].encode()).hexdigest()[:16])')
OLD_PID=$(awk '{print $1}' "$STATE/daemon.lock")
ps -o pid=,command= -p "$OLD_PID"          # must be: <python> .../supervisor.py daemon
test -f "$STATE/daemon.lock"                # and status must show lock_held: true

# 2. TERM only that identified helper. Never the Herdr server.
kill -TERM "$OLD_PID"

# 3. Start the replacement and verify the NEW identity.
herdr plugin action invoke gzg.orchestrator.start
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py status
```

Confirm in that final `status`: `lock.pid` differs from `$OLD_PID`,
`daemon.pid` equals `lock.pid`, `lock.lock_held` is `true`, `running` is `true`,
`daemon.started_at` is later than the old value, and `daemon.fingerprint` is
present. If `lock.lock_held` is `false` after starting, the replacement did not
take the lock and the old code is still running.

`kill -TERM` targets only the pid shown as a `supervisor.py daemon` command.
Never signal the Herdr server, and never use `--auto`.

The companion OpenCode plugin loads on the next OpenCode start in worker panes,
so restart a worker pane's OpenCode after installing it. Confirm with one real
approval prompt before relying on it. Do not use `--auto`, and do not stop the
Herdr server; nothing here needs either.

The coordinator binding is unchanged: run `bind` in the coordinator pane after
an intentional new coordinator session, and `unbind` before rebinding elsewhere.
Native CLI permissions are untouched.

## Limits

- Only OpenCode workers emit native request ids. Claude, Codex and other
  harnesses reach you through Herdr `blocked` status, which has no request id
  and, as noted above, is a derived value with degraded coverage when Herdr
  skips screen detection.
- A quota signal is treated as actionable at retry attempt 2 or when the message
  carries a limit marker, so a single transient retry stays silent.
- There is no webhook or external listener. The ingress spool and the Herdr
  socket are the entire transport.
- Child-session attribution depends on the parent session having been observed;
  a request raised in a child session before its `session.created` event fails
  the session guard and is dropped. That direction is safe but silent.
- Restarting the supervisor mid-batch can leave a coordinator batch
  `uncertain`. The attention notices are independent: they are already
  delivered or already latched, so a restart loses no pending request.
- `daemon.pid` in state persists after the process exits. Only `lock.lock_held`
  plus a matching live pid establishes that a daemon is running now.
