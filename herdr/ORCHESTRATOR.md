# Herdr delivery coordinator

Radar provides project grouping, activity order and persistent completion/blocked marks.
The `gzg.orchestrator` plugin provides a persistent Python supervisor; the OpenCode
`orchestrator` primary agent handles task assignments, decisions and verification.

## Start

Quit and restart OpenCode to load the new agent. In a Herdr pane, launch:

```sh
opencode --agent orchestrator
```

Use one coordinator session for the Herdr server. Tell it, for example:

> Coordinate Tapify at /Users/gzg/Projects/Websites/tapify. Use Claude for UI,
> Codex for API work and OpenCode for review. Goal: [outcome]. Acceptance criteria:
> [criteria]. You may create isolated worktrees. Up to three workers. Prepare
> reviewed changes; ask me before committing, opening a PR or deploying.
> Decide small reversible implementation choices and batch non-blocking decisions.

The coordinator registers its exact session with the helper, records project tool
choices, creates named task tabs/workspaces and registers only its owned workers.
An agent definition configures behavior; it cannot force compliance by other tools.
Native CLI permissions remain in effect. Ordinary worker task decisions should be
returned as a final `DECISION` report, so the coordinator can handle them.

Workers using OpenCode must launch with `--agent build`, not `orchestrator`.
Existing sessions are left outside supervision until intentionally assigned a task.

## Controls

Run these from a Herdr pane in the same session:

```sh
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py status
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py pause
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py resume
python3 ~/.config/herdr/plugins/orchestrator/supervisor.py start
```

The plugin starts its daemon on Herdr startup; `start` is safe to repeat.
It samples registered worker states every five seconds, with a five-second settling
window, and submits one event batch only when the coordinator is idle/done. It uses
no model tokens to poll. Completed/blocked/missing sessions and long-running turns
(20 minutes by default) wake the coordinator. The coordinator must acknowledge each
batch with `ack ID ...`; an unacknowledged batch prevents further submissions.
The supervisor never sends keys to permission dialogs.

The coordinator must be open in a live Herdr pane for AI triage. If it is busy or
closed, events remain queued. This is persistent supervision of a live coordinator,
not a headless replacement for its model session. Delivery failures are marked
`uncertain`; inspect the coordinator history and `status` before acknowledging them.
No blind retries. After an intentional new coordinator session, run `bind` there.
The supervisor refuses binding a different pane while another one is registered.
To intentionally replace the coordinator pane, run the helper with `unbind`, then
`bind` in the new coordinator. This preserves project/worker/event records; review
and acknowledge any earlier sent or uncertain batch before resuming delivery.

Runtime state and logs live in `~/.local/state/herdr-orchestrator/<socket-hash>/`;
project ledgers live in `~/.local/state/herdr-orchestrator/projects/<project>/`.
State is tied to the Herdr socket; exact agent session IDs guard against reused panes.
The monitor stays local to that server. Remote-server workers need a separately
scoped supervisor; this initial integration does not forward remote monitoring.

Owned workers' attention sounds are suppressed while the supervisor is healthy and
active. Other agents retain their existing sounds. Coordinator decisions/completions
use its normal attention behavior. Sidebar blocked markers remain visible for audit.

## Radar keys

The prefix is **Ctrl+S** in these dotfiles:

- `prefix+,`: settings.
- `prefix+Shift+V`: grouped/recent agent view.
- `prefix+Shift+1..9`: workspace jump (indices stay stable).
- `prefix+A`: existing attention-aware next-agent navigation.

Radar owns its managed sidebar block. Changes made directly inside that block are
rewritten by its configure action. Adjust settings in its plugin config instead.

## Installation on another machine

Stow the `herdr` and `opencode` packages, then in Herdr:

```sh
herdr plugin install hhdebb/herdr-radar
herdr plugin link ~/.config/herdr/plugins/orchestrator
herdr plugin action invoke gzg.orchestrator.start
herdr plugin action invoke hhdebb.herdr-radar.configure
```

The agent inherits your configured model. No extra provider credentials are needed
for the supervisor; each worker CLI uses its own existing login.

Tests: `python3 -m unittest discover -s herdr -p 'test_orchestrator.py' -v`.
