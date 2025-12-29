# Tmux Configuration

This directory contains your tmux configuration using TPM (Tmux Plugin Manager).

## Installation

1. Apply dotfiles with stow:
   ```bash
   cd ~/Projects/dotfiles
   stow -t ~ tmux
   ```

2. Install TPM and plugins (if not already installed):
   ```bash
   ./install-plugins.sh
   ```

    Or manually:
    ```bash
    git clone https://github.com/tmux-plugins/tpm ~/.tmux/plugins/tpm
    git clone https://github.com/tmux-plugins/tmux-sensible ~/.tmux/plugins/tmux-sensible
    git clone https://github.com/fabioluciano/tmux-powerkit ~/.tmux/plugins/tmux-powerkit
    git clone https://github.com/tmux-plugins/tmux-yank ~/.tmux/plugins/tmux-yank
    ```

3. Reload tmux configuration:
   ```bash
   tmux source-file ~/.tmux.conf
   ```

## Plugins Used

- **TPM** - Tmux Plugin Manager
- **tmux-sensible** - Sensible defaults for tmux
- **tmux-powerkit** - Feature-rich tmux theme with Tokyo Night variant
- **tmux-yank** - Copy text to system clipboard

## Keybindings

- **Prefix**: `Ctrl-s` (instead of default `Ctrl-b`)
- **Reload config**: `prefix + r`
- **Split vertical**: `prefix + "``
- **Split horizontal**: `prefix + %`
- **Navigate panes**: `h`, `j`, `k`, `l` (vim-style)
- **Copy mode**: `prefix + [` then use vim bindings
- **Yank selection**: `y` (in copy mode, copies to clipboard)

## Theme

- **Theme**: Tokyo Night (via tmux-powerkit)
- **Status bar**: Top position
- **Window numbers**: Right side
- **Features**: System monitoring, session info, user display

## Updating Plugins

Press `prefix + I` (Ctrl-s, then Shift+i) to install/update plugins via TPM.
