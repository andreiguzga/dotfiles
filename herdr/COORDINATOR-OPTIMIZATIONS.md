# Bounded coordinator context and wakeups

One OpenCode coordinator in Control serves all registered projects on a Herdr
server. The worker limit is **three total**, scheduled by explicit project/task
priorities. OpenCode workers still start with `--agent build`.

## Project context gate

At intake, project switch, new assignment and review, explicitly read the target
project's CURRENT and ledger, root AGENTS.md (CLAUDE.md fallback), and only relevant
referenced/scoped rules. Control's cwd does not load another repository's rules.
Refresh changed files before affected work. Reuse per-project tool, exact model
and authority choices; only the user's updated brief changes those choices.
Rules constrain work and never grant commit/deploy/paid/irreversible authority.

[`templates/AGENTS.md`](templates/AGENTS.md) is the canonical concise template;
[`templates/CLAUDE.md`](templates/CLAUDE.md) is a thin explicit-read adapter. Adopt
them only within a project's authorized scope, replacing placeholders with
verified commands. No duplicate rulebooks are required.

Optional local helper, after selecting task-relevant references:

```sh
python3 ~/.config/herdr/plugins/orchestrator/project_context.py \
  --root /absolute/project \
  --current /absolute/authorized-ledger-directory/CURRENT.md \
  --ledger /absolute/authorized-ledger-directory/tasks.md \
  --owned src/example.py --rule docs/testing.md
```

Read the emitted references with your read tool; hashes are not instructions.
The helper is deterministic and cwd-independent: root/scoped AGENTS.md with
CLAUDE.md fallback, explicit selected local references, SHA-256 fingerprints,
and visible missing/unreadable/unsupported inputs. It never executes commands,
scans secrets, crawls Markdown, parses configs or calls network/models. Inspect
the target's config-declared `instructions` yourself and select relevant rules.
URLs/globs and symlink escapes are reported unsupported; do not silently ignore
required rules. Handle them separately within the brief/native tool policy.

Bounds: 32 files, 64 KiB/file, 256 KiB total, 32 owned paths, 16 scoped levels.
CURRENT/ledger must be explicit absolute authorized paths; selected rules stay
inside the project root. Nonregular files are rejected. Exit 1 / `ready: false`
means a context gap, not permission to skip it. Missing root rules may be recorded
as absent and work may use the explicit brief and existing verified commands;
unreadable required rules block the affected portion. Narrow oversized context.

## Compact delivery brief and ledger

Every assignment contains these fields (omit full transcripts):

```text
TASK / PRIORITY: project, task ID, dependency order
ROOT / BRANCH: absolute target/worktree, authorized branch
CONTEXT: CURRENT, ledger, root/scoped/selected rule paths, fingerprint; skills needed
TOOL / MODEL: recorded harness and exact model ID, task-specific override if authorized
OBJECTIVE / AC: bounded outcome and observable acceptance criteria
OWNED FILES / DEPENDENCIES: edit ownership, upstream evidence, overlapping writers
ALLOWED ACTIONS: explicit authority, native approvals remain in force
CHECKS: focused actual commands, cwd and expected evidence
SOFT BUDGET: first evidence/checkpoint within 15 minutes or 8 exploratory calls
RESULT CONTRACT: RESULT / CHANGES / CHECKS(actual) / RISKS / DECISIONS;
  implemented / verified / committed / activated separately; no further delegation
```

Workers explicitly read applicable rules and use only this narrower project/task
context. For simple configuration changes, first observe evidence and use the
simplest reproduction/reload/check (reload only if authorized). At a soft-budget
checkpoint report progress, evidence, remaining hypothesis and next bounded step.
A healthy long turn is not killed. Ordinary reversible decisions stay local;
true scope/product blockers return DECISION with options, recommendation, impact
and blocking status in the normal final message, not interactive product loops.

CURRENT summarizes project goal/AC, priority, chosen tools/models/authority,
context paths/fingerprint and active tasks. The task ledger adds ownership,
dependencies, branch/worktree, pane/exact session/watch turn, brief/checks,
checkpoint evidence, results, distinct delivery states and outstanding decisions.
These fields are coordinator conventions, not a new ledger service/schema.

## Deterministic wakeup filter

- Direct native notices remain independent of coordinator availability and pause.
- Pending native model escalation waits **120 seconds** (reversible constant
  `NATIVE_ESCALATION_GRACE`). Resolved-before-delivery produces no model prompt.
  Unresolved requests remain visible in status/pending and escalate once even
  after direct reminder caps; nothing approves, answers or cancels the dialog.
  Exact pane/session/request-ID resolution tombstones suppress late duplicate
  ASKED records before latching, notifying or queuing a wakeup. The hot cache is
  capped at 256 entries/one day and backed by retained resolution event audit,
  independent of notice cooldown, watch turn and sent/uncertain delivery status.
  Every valid owned resolution (including resolve-first/unmatched IDs) is audited
  in the application transaction before ingress confirmation or notice delivery.
  Pending unanswered facts retire on exact-ID resolution; sent/uncertain receipts
  remain untouched. Native notice actions carry request identity and revalidate
  unresolved state immediately before serialized dispatch, preventing stale
  same-batch or interleaved-resolution notices while preserving durable retry intent.
- Native retry → busy/idle emits one recovery fact, not every status frame.
  Only the explicit `retry` message class may clear/recover a quota entry or
  retire its pending event. `provider-limit` remains visible and escalates even
  if the worker gives up and becomes idle; ordinary lifecycle/alert aging does
  not prove that usage/credits recovered. Provider errors remain actionable.
  Equivalent quota/retry countdowns coalesce within their class with bounded evidence.
  Alert notices revalidate the current fingerprint, so recovered plain retries
  do not emit stale same-batch toasts, while active limits/errors still notify.
- Stable idle/done queues one **review-needed** fact per watch turn, including
  tasks already idle at registration. Working/idle churn does not reset it.
  If the worker resumes working before delivery, the review fact waits for
  settling. A follow-up accepted prompt must always be watched as a new turn.
- Rewatch retires all prior **pending** turn events/latches/alerts, including
  same-label/same-session tasks. Exact session/project/task/turn guards protect
  reused panes. Genuine blocked, missing-session, error and decision facts remain
  actionable. Blocked/progress facts covered by native requests or already
  recovered are audit-only.
  This intentionally includes stale undelivered lifecycle review facts: a
  previous turn's idle event cannot stand in for a new submitted turn's review.
  Other panes and every sent/uncertain receipt are preserved. Finish is a
  coordinator-reviewed end to a task, not a detector claiming success.
  A matching-session resolution arriving after finish still persists durable
  exact-ID audit/cache evidence; finished workers cannot raise new notices/work.
- Pending resolved/superseded/recovered facts become `retired`; equivalent
  undelivered facts become `coalesced` with an event-ID reference. No audit event
  is deleted. Unknown facts are retained, not guessed equivalent.
- **Sent/uncertain evidence is never filtered or auto-acknowledged**, even after
  recovery or rewatch. It blocks new batches until explicitly inspected and acked.
  Prompts contain bounded task/event references; evidence stays in status.

This filter is code-only. Terminal state requires coordinator inspection and
verification and cannot establish success. No summarizer, transcript collector,
new framework, provider change or additional coordinator is introduced.

## Verification and integration

Tests mock Herdr and model transport, using temporary state only:

```sh
python3 -m unittest discover -s herdr -p 'test*.py' -q
node --check opencode/.config/opencode/plugins/herdr-orchestrator-attention.js
node herdr/test_attention_plugin.mjs
git diff --check
```

Changes in this worktree are **not installed or activated**. Independent review
is required before integration. Merge the orchestrator agent instructions with
T07's overlapping MAIN changes; do not replace that file wholesale. After approved
integration, quit/restart OpenCode coordinator and affected workers to load agent/
companion definitions. Replace only the identified supervisor daemon and verify its
new pid/start time/source fingerprint as documented in ATTENTION-EVENTS.md. A call
to `start` alone does not replace an existing lock holder. No restart was performed
for this implementation. Context gates/soft budgets/worker count remain agent
instructions, not enforcement by another service; existing non-OpenCode native
event coverage limits remain.
