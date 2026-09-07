# Codex

The full Codex configuration lives in `.codex/config.toml`. GNU Stow links
`~/.codex/config.toml` to this file, so config edits appear in this repository.
It includes model preferences, plugins, desktop/TUI settings, project trust,
and MCP servers. The absolute paths currently describe the macOS `gzg` account;
review those paths and project trust entries before installing on another system.

From the repository root:

```sh
python3 codex/install.py
codex mcp list
```

Requires Python 3.11+ and GNU Stow. The installer backs up an existing local
config when its settings match the repository, then stows the config and
launcher. If the settings differ, merge them into the repository file first;
the installer refuses to discard them. Rerunning is safe. For a fresh home,
`stow --no-folding -t ~ codex` also works. `--target-home /path/to/home` on the
installer supports checking installation in a separate directory.

Only `config.toml` and `bin/` under `.codex` belong in Git. Authentication,
OAuth tokens, sessions, caches, and backups remain local and are ignored by
the repository. Credentials stay in the existing OpenCode secret files or
1Password. Review config changes before committing: Codex can update this file
as preferences, plugins, and trusted projects change. If an application update
replaces the symlink with a regular file, merge it back and rerun the installer.

## MCP servers

The config includes the same five servers as
`opencode/.config/opencode/opencode.json`: Atlassian, Asana, Home Assistant,
Fusion 360, and NetBird.

Home Assistant and NetBird reuse `~/.config/opencode/bin` launchers and their
secret-file/1Password handling. Fusion 360 uses Homebrew `uvx` in socket mode;
its Fusion add-in must be running for CAD operations.

Asana uses Node.js/npm and `mcp-remote`, following the
[Asana Codex guide](https://developers.asana.com/docs/connecting-mcp-clients-to-asanas-v2-server).
The launcher reads `~/.config/opencode/secrets/asana.env`, passes credentials
through a private temporary file, and deletes it on exit. It uses the existing
`http://localhost:3334/oauth/callback` redirect. Complete browser authorization
on first launch; tokens are stored locally in `~/.mcp-auth/`. Avoid authenticating
OpenCode simultaneously on port 3334. OpenCode's Asana comment-schema plugin is
client-specific and is not loaded by Codex.

Authenticate Atlassian on each machine:

```sh
codex mcp login atlassian
```

Restart Codex after installation, then use `/mcp` to inspect connections. See
the [official MCP documentation](https://developers.openai.com/codex/mcp).
