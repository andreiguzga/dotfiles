# Herdr Configuration

This directory contains a Herdr config intended to preserve the tmux muscle memory from `tmux/.tmux.conf`.

## Installation

Apply with GNU Stow:

```bash
cd ~/Projects/Websites/dotfiles
stow -t ~ herdr
```

If Herdr is already running, reload the server config:

```bash
herdr server reload-config
```

## Radar

Install the Radar plugin and apply its managed UI configuration:

```bash
herdr plugin install hhdebb/herdr-radar --ref 1ba8cc4a3900191c9f1f4f850398c7dd10212df2
herdr plugin action invoke hhdebb.herdr-radar.configure
```

The pinned revision above is Radar v1.4.2, the version inspected during the
November 17, 2026 input-latency investigation.

### Source and installation state

Radar is a third-party GitHub-managed plugin, not a local plugin implemented in
this repository. Herdr downloads its source into
`~/.config/herdr/plugins/github/` (newer installs can use
`github-installations/`). The inspected checkout was
`hhdebb.herdr-radar-afabda667cbe`, with its own `.git` directory.

Dotfiles tracks Radar's user configuration and the managed UI blocks, not that
external checkout. `~/.config/herdr/plugins.json` holds Herdr's local plugin
registration, source revision and enabled state; it is runtime installation
metadata, not part of this Stow package. Consequently, Stow alone neither
installs Radar nor reproduces its enabled/disabled state.

Use `herdr plugin list --plugin hhdebb.herdr-radar --json` to inspect the actual
installation. The upstream source is <https://github.com/hhdebb/herdr-radar>.
Do not edit the downloaded checkout as a durable dotfiles customization.

### Animator isolation test

Radar is currently disabled for the input-latency investigation. Its animator
was also stopped using the plugin's `stopAnimator({purge: false})` helper, which
waits for the daemon to exit and preserves titles and sort keys. Agents and
their terminal processes were left running.

For the equivalent supported CLI workflow:

```bash
herdr plugin action invoke hhdebb.herdr-radar.state-stop
herdr plugin disable hhdebb.herdr-radar
herdr plugin log list --plugin hhdebb.herdr-radar --limit 5
```

The stop action is asynchronous: check its command log for successful completion.
Disabling the plugin prevents its startup and agent-detection watchdog hooks
from restarting the animator. A plain animator stop without disabling the hooks
can be undone when another agent is detected. The existing managed UI blocks
remain installed; this does not switch to Herdr's native sidebar.

To end the test without restarting Herdr or its agents:

```bash
herdr plugin enable hhdebb.herdr-radar
herdr plugin action invoke hhdebb.herdr-radar.state-start
```

With the `Ctrl-s` prefix:

- `prefix + ,`: Radar settings.
- `prefix + Shift-v`: grouped/recent agent view.
- `prefix + Shift-1..9`: workspace jump (indices stay stable).

Radar owns the marked sidebar, tab-bar, and theme blocks in `config.toml`.
Its configure action rewrites those blocks; adjust Radar settings in
`.config/herdr/plugins/config/hhdebb.herdr-radar/config.toml` instead.

## Tmux Mappings

- Prefix: `Ctrl-s`
- Reload config: `prefix + r`
- Navigate panes: `prefix + h/j/k/l`
- Return to last focused pane across tabs and workspaces: `prefix + t`
- Split right: `prefix + %`
- Split down: `prefix + "`
- Copy mode: `prefix + [`
- New tab: `prefix + c`
- Previous/next tab: `prefix + p/n`
- Jump to tabs: `prefix + 1..9`
- Next/previous agent needing attention: `prefix + a` / `prefix + Shift-a`
- Detach: `prefix + q`

## Agent Navigation

`Ctrl-s`, then `a` cycles through blocked agents (waiting for input or approval)
and done agents (finished work needing review), across all workspaces. When none
need attention, it cycles through all agents. `Ctrl-s`, then `Shift-a` reverses
the direction. Ordering follows the configured priority sidebar: blocked, done,
working, idle, unknown, with workspace/tab/pane order within each state.

The shortcut uses `focus-agent.py` and Python 3, with no extra dependencies.
An idle agent does not count as needing attention. If the focused agent is the
only one needing attention, the shortcut stays there until its state changes.
Run `python3 ~/.config/herdr/focus-agent.py next --dry-run` to inspect the next
target without changing focus. After adding this script to an existing install,
run the Stow command above again and reload the server config.

## Notes

- Herdr uses tabs where tmux uses windows, so tmux window numbering maps to `switch_tab = "prefix+1..9"`.
- Herdr copy mode already uses vim-like movement, `v`/Space selection, and `y`/Enter copy; it does not expose separate tmux `copy-mode-vi` bindings in config.
- Herdr persistence and `session.resume_agents_on_restore` replace the tmux-resurrect/tmux-continuum workflow for Herdr-managed panes and supported agents.
- The Catppuccin theme is built in, so no TPM or tmux-powerkit equivalent is needed.
- If `prefix + %` or `prefix + "` does not register in your terminal, use Herdr's default split keys instead: `prefix + v` for right and `prefix + minus` for down, or adjust `split_vertical` / `split_horizontal` in `.config/herdr/config.toml`.
