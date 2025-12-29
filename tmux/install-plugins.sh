#!/bin/bash
# Install TPM (Tmux Plugin Manager) and required plugins

set -e

TMUX_PLUGINS_DIR="$HOME/.tmux/plugins"

echo "Installing TPM (Tmux Plugin Manager)..."
if [ ! -d "$TMUX_PLUGINS_DIR/tpm" ]; then
  git clone https://github.com/tmux-plugins/tpm "$TMUX_PLUGINS_DIR/tpm"
else
  echo "TPM already installed"
fi

echo ""
echo "Installing tmux plugins..."

plugins=(
  "tmux-plugins/tmux-sensible"
  "catppuccin/tmux"
  "tmux-plugins/tmux-yank"
)

for plugin in "${plugins[@]}"; do
  plugin_name=$(basename "$plugin")
  if [ ! -d "$TMUX_PLUGINS_DIR/$plugin_name" ]; then
    echo "  Installing $plugin_name..."
    git clone "https://github.com/$plugin" "$TMUX_PLUGINS_DIR/$plugin_name"
  else
    echo "  $plugin_name already installed"
  fi
done

echo ""
echo "Reloading tmux configuration..."
tmux source-file ~/.tmux.conf 2>/dev/null || echo "Warning: Could not reload tmux configuration (tmux not running?)"

echo ""
echo "✓ TPM and plugins installed successfully!"
echo "  Press prefix + I to install/update plugins manually"
