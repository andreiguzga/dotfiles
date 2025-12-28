# Cross-Platform Dotfiles Setup

This dotfiles repo is now configured to work on both MacOS and Arch Linux.

## What Was Changed

### zsh/.zshrc
Added OS detection using `$OSTYPE`:
- **MacOS** (`darwin`): Loads Homebrew paths, MacOS-specific locations, and apps
- **Linux** (`linux-gnu`): Loads Linux-specific paths

All OS-specific paths are now conditionally loaded based on the detected OS.

## Setup Instructions

### On Arch Linux

1. Install required tools:
```bash
# Core tools
sudo pacman -S stow neovim tmux zsh bat yazi

# Additional tools used in config
sudo pacman -S fzf fd zoxide

# Oh My Zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# Oh My Posh (for custom prompt)
wget https://github.com/JanDeDobbeleer/oh-my-posh/releases/latest/download/posh-linux-amd64 -O ~/.local/bin/oh-my-posh
chmod +x ~/.local/bin/oh-my-posh

# Node Version Manager (for Node.js)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
```

2. Apply dotfiles:
```bash
cd ~/Projects/dotfiles
stow -t ~ nvim tmux zsh bat yazi
```

3. Change default shell:
```bash
chsh -s $(which zsh)
```

### On MacOS

1. Install required tools:
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Core tools
brew install stow neovim tmux zsh bat fzf fd zoxide

# Oh My Zsh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# Oh My Posh
brew install jandedobbeleer/oh-my-posh/tap/oh-my-posh

# Node.js (via Homebrew)
brew install node@18
```

2. Apply dotfiles:
```bash
cd ~/Projects/dotfiles
stow -t ~ nvim tmux zsh bat yazi
```

3. Change default shell:
```bash
chsh -s $(which zsh)
```

## OS-Specific Paths

### Linux
- NVM: `~/.nvm`
- Android SDK: `~/Android/Sdk`
- Lua language server: `~/.config/nvim/tools/lua-language-server/bin/linux`
- Node.js: via nvm

### MacOS
- NVM: `/opt/homebrew/opt/nvm`
- Android SDK: `~/Library/Android/sdk`
- Lua language server: `~/.config/nvim/tools/lua-language-server/bin/macOS`
- Node.js: `/opt/homebrew/opt/node@18/bin`
- Java: `/opt/homebrew/opt/openjdk@17`

## Notes

- The nvim, tmux, bat, and yazi configs are cross-platform and work on both systems
- Ghostty and Wezterm configs are terminal emulator-specific and should only be stowed on their respective platforms
- If you need different nvim settings per OS, you can add similar OS detection in `init.lua`
