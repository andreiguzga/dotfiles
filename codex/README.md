# Codex

The Codex configuration lives under `.codex/`. GNU Stow links its files into
`~/.codex`, so config edits appear in this repository. It includes model and
agent preferences, global agent instructions, plugins, desktop/TUI settings,
project trust, and MCP servers. The absolute paths currently describe the macOS
`gzg` account; review those paths and project trust entries before installing on
another system.

From the repository root:

```sh
python3 codex/install.py
codex mcp list
```

Requires Python 3.11+ and GNU Stow. The installer backs up an existing local
config when its settings match the repository, then stows the config,
instructions, and launcher. It links `~/.codex/agents` to the repository's
agent directory separately. If the config settings differ, merge them into the
repository file first; the installer refuses to discard them. Rerunning is
safe. Use the installer for the complete setup: plain Stow excludes the agents
directory. `--target-home /path/to/home` supports checking installation in a
separate directory.

Codex CLI 0.154.0 discovers individually symlinked role files but fails to spawn
them with "agent type is currently not available" (the underlying error is
"Too many levels of symbolic links"). A directory symlink works because each
role file itself is regular. The installer migrates an empty directory or
existing links pointing to the corresponding repository role files. It refuses
other files or links before modifying the installation, so local custom agents
must be reconciled first. The rest of the package retains Stow's `--no-folding`
layout.

Only `config.toml`, named `*.config.toml` profiles, `AGENTS.md`, the three named
role files in `agents/`, and `bin/` under `.codex` belong in Git. Authentication,
OAuth tokens, sessions,
caches, generated agent state, and backups remain local and are ignored by the
repository. Credentials stay in the existing OpenCode secret files or
1Password. Review config changes before committing: Codex can update this file
as preferences, plugins, and trusted projects change. If an application update
replaces a symlink with a regular file, merge it back and rerun the installer.

## Multi-agent workflow

This setup was validated with Codex CLI 0.154.0. The primary agent remains on
`gpt-6-astra` with high reasoning and owns planning, decisions, integration, and
final review. Multi-agent support allows up to three concurrent subagent threads
per session and defaults unspecified subagents to `gpt-5.6-luna` with low
reasoning.

The standalone roles in `.codex/agents/` are:

- `scout`: read-only, coherent multi-file exploration on `gpt-5.6-luna`/low.
- `developer`: focused substantial implementation on `gpt-5.6-sol`/high.
- `verifier`: independent diff review and focused checks on
  `gpt-5.6-luna`/low.

`.codex/AGENTS.md` tells the primary agent when to use each role, what a useful
brief and report contain, and when parallel work is appropriate. Role files set
the role identity, model, reasoning effort, and instructions. Conversation
inheritance is selected when a subagent is spawned; it is not a role-file
setting. The primary agent should send a self-contained task without inherited
turns when possible, or use the smallest supported recent-turn subset.

Parse every tracked TOML file after editing it:

```sh
python3 -c 'import pathlib, tomllib; [tomllib.loads(p.read_text()) for p in pathlib.Path("codex/.codex").glob("**/*.toml")]'
```

For an installation check that does not touch the live home directory, create a
temporary directory and pass it to `--target-home`. Strict app-server startup
and a `config/read` request verify the global agent settings. A `thread/start`
request loads standalone role definitions, but discovery alone does not prove
spawning works. Actual spawn-handler tests using a loopback mock Responses API
reproduced the per-file symlink failure and verified directory links for all
three roles plus the Luna/low fallback. Captured child requests confirmed the
configured model, reasoning, and role instructions; `fork_turns="none"` excluded
a parent-only test marker. This tests routing without paid model inference.
The three named roles also passed a live `READY` smoke test without model or
reasoning overrides.

Run the installer regression checks with:

```sh
python3 -B -m unittest discover -s codex -p test_install.py
```

After changing the setup, start a fresh Codex session and ask it to spawn each
named role with no model/reasoning overrides and `fork_turns="none"`, giving each
only the task "Reply READY; do not use tools." Keep the threads available for
inspection. This makes real model calls; use it to verify the installed layout.

## MCP servers

The Codex and OpenCode base configs define the same five user-configured
servers: Atlassian, Asana, Home Assistant, Fusion 360, and NetBird. All five are
disabled by default. Codex's built-in `node_repl` and computer-use settings are
unchanged.

MCP clients start every enabled server when their process starts; these configs
do not provide true tool-demand loading. Consequently, a broken credential can
produce one failed startup authentication per enabled client process. Enabling
or disabling a server in a config does not change an already running process:
restart Codex or OpenCode to apply it.

### Codex profiles

Start a session with one logical subset enabled:

```sh
codex --profile home     # Home Assistant only
codex --profile work     # Atlassian and Asana
codex --profile cad      # Fusion 360 only
codex --profile network  # NetBird only
```

Plain `codex` leaves all five disabled. Each `<name>.config.toml` profile only
overrides the relevant `enabled` field and is layered on the base
`~/.codex/config.toml`; server definitions and credentials remain in their
existing single locations.

### OpenCode profiles

OpenCode merges the file selected by `OPENCODE_CONFIG` on top of its global
config. The checked-in overrides are inert unless selected for that process:

```sh
OPENCODE_CONFIG="$HOME/.config/opencode/profiles/home.json" opencode
OPENCODE_CONFIG="$HOME/.config/opencode/profiles/work.json" opencode
OPENCODE_CONFIG="$HOME/.config/opencode/profiles/cad.json" opencode
OPENCODE_CONFIG="$HOME/.config/opencode/profiles/network.json" opencode
```

The same commands can be run from a project directory for a project-scoped
session. Plain `opencode` leaves all five disabled. There is intentionally no
wrapper executable: invoking `opencode` from an `opencode` launcher can recurse.

### Credentials and server prerequisites

Home Assistant and NetBird reuse `~/.config/opencode/bin` launchers. The Home
Assistant launcher reads `~/.config/opencode/secrets/home-assistant.env` when it
exists, but obtains its token from 1Password by default even if that file
contains an old `HOMEASSISTANT_TOKEN`. Configure the URL, item reference, and
account with `HOMEASSISTANT_URL`, `HOMEASSISTANT_TOKEN_REF`, and `OP_ACCOUNT`.
An `op read` error or empty result stops the launcher before `uvx` can contact
Home Assistant, and the token is never printed.

Direct token use is an explicit fallback only. Set
`HOMEASSISTANT_TOKEN_SOURCE=environment` and provide a non-empty
`HOMEASSISTANT_TOKEN` in the process environment or secret file. Any other
source value is rejected. See
`opencode/.config/opencode/secrets/home-assistant.env.example` for both modes.

Fusion 360 uses Homebrew `uvx` in socket mode; its Fusion add-in must be running
for CAD operations.

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

After authentication or config changes, restart the selected client. In Codex,
use `/mcp` to inspect connections. See the
[official MCP documentation](https://developers.openai.com/codex/mcp).
