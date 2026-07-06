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

## Tmux Mappings

- Prefix: `Ctrl-s`
- Reload config: `prefix + r`
- Navigate panes: `prefix + h/j/k/l`
- Split right: `prefix + %`
- Split down: `prefix + "`
- Copy mode: `prefix + [`
- New tab: `prefix + c`
- Previous/next tab: `prefix + p/n`
- Jump to tabs: `prefix + 1..9`
- Detach: `prefix + q`

## Notes

- Herdr uses tabs where tmux uses windows, so tmux window numbering maps to `switch_tab = "prefix+1..9"`.
- Herdr copy mode already uses vim-like movement, `v`/Space selection, and `y`/Enter copy; it does not expose separate tmux `copy-mode-vi` bindings in config.
- Herdr persistence and `session.resume_agents_on_restore` replace the tmux-resurrect/tmux-continuum workflow for Herdr-managed panes and supported agents.
- The Tokyo Night theme is built in, so no TPM or tmux-powerkit equivalent is needed.
- If `prefix + %` or `prefix + "` does not register in your terminal, use Herdr's default split keys instead: `prefix + v` for right and `prefix + minus` for down, or adjust `split_vertical` / `split_horizontal` in `.config/herdr/config.toml`.
