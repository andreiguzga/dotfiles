#!/usr/bin/env python3
"""Generate a per-task OpenCode permission profile for an owned Herdr worker.

Emits the rules for exactly one worker process as shell lines for that pane. It never
launches a worker, never writes a config file, and never answers a native approval.
This is an interruption policy, not a sandbox: an allowed command still runs with the
host user's full authority.

The profile is injected as `{"agent":{"build":{"permission":...}}}` through
`OPENCODE_CONFIG_CONTENT`. Inherited config is deep-merged with it key by key, so an
inherited per-pattern rule the profile does not name survives, keeps its position and
can still win OpenCode's last-match lookup. The generator therefore never trusts its
own payload: before printing anything it asks the installed binary for the effective
build ruleset (`opencode --pure debug agent build`) with and without the profile, and
refuses unless that native ruleset proves the policy (see `check_effective`).

The shell allowlist ships empty. Only commands the coordinator names explicitly are
allowed, and each is an exact string: a pattern without `*` is anchored on both ends,
so `git status` matches only `git status`, not `git status --short` or `git statusx`.

Read WORKER-PERMISSIONS.md next to this file before enabling any profile.
"""

import argparse
import json
import os
from pathlib import PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse

# Shell metacharacters and substitutions. A pattern containing any of these is not a
# single command as far as a human reviewer is concerned, so it is refused outright
# rather than emitted and left for the native evaluator to reinterpret. Plain spaces
# are allowed; quoting and whitespace oddities are caught by the round-trip check.
UNSAFE_PATTERN = set("|&;<>()$`\\\"'*?[]{}!#~\n\t\r")

# Shells and wrappers whose whole purpose is to run something else. No form of these
# is ever allow-listed, however exact: the payload is not reviewable from the command.
SHELL_WRAPPERS = frozenset({
    "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "eval", "exec", "env",
    "xargs", "sudo", "doas", "su", "timeout", "nohup", "nice", "stdbuf", "watch",
    "osascript", "ncat", "telnet", "expect", "script", "caffeinate",
})

# Code interpreters. Refused in the shapes that execute caller-supplied text or an
# arbitrary script. The `python -m <module>` form survives because the module name is
# a fixed, reviewable token and the coordinator still has to approve the exact string.
CODE_INTERPRETERS = frozenset({
    "python", "python3", "pypy", "ipython", "node", "ruby", "perl", "php", "deno",
})
INLINE_FLAGS = ("-c", "-e", "-E", "--eval", "--exec", "--require", "-r")
SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".php", ".sh",
                   ".zsh", ".bash")

# Modules that are legitimately runnable through `python -m`. Anything else is refused.
RUNNABLE_MODULES = frozenset({
    "unittest", "pytest", "mypy", "ruff", "build", "twine", "pip", "venv", "compileall",
})

# `make` and friends execute whatever the repository declares, which is not reviewable
# from the command text. The coordinator can still approve one exact `make <target>`.
DECLARED_RUNNERS = frozenset({"make", "just", "task", "ninja", "cmake"})

# Package managers. A bare `npm run` names whatever script the repository declares,
# which is not reviewable from the command text, so the subcommand is checked.
PACKAGE_MANAGERS = frozenset({"npm", "npx", "pnpm", "pnpx", "yarn", "bun", "deno"})
REVIEWABLE_PM_SUBCOMMANDS = frozenset({"test", "t", "run-script", "lint", "typecheck"})

# Subcommand prefixes of an otherwise-restricted tool that only inspect. Checked
# before the restricted lists so `npm test` and `git status` stay allowable as exact
# strings. Longest prefix wins, so `git branch --list` is allowed while `git branch`
# on its own is not.
READONLY_SUB = {
    "npm": frozenset({"test", "t", "run-script", "lint", "typecheck"}),
    "pnpm": frozenset({"test", "lint", "run-script", "typecheck"}),
    "yarn": frozenset({"test", "lint", "typecheck"}),
    "bun": frozenset({"test"}),
    "pip": frozenset({"list", "show", "freeze", "check"}),
    "brew": frozenset({"list", "info", "outdated", "config"}),
    "cargo": frozenset({"test", "check", "fmt", "clippy", "tree", "metadata"}),
    "go": frozenset({"test", "vet", "fmt", "list", "version", "env", "doc"}),
    "docker": frozenset({"ps", "images", "logs", "inspect", "version", "info"}),
    "kubectl": frozenset({"get", "describe", "logs", "explain", "version", "api-resources"}),
    "helm": frozenset({"list", "status", "get", "version"}),
    "terraform": frozenset({"fmt", "validate", "version", "show", "output"}),
    "aws": frozenset({"--version", "sts", "s3api"}),
    "gcloud": frozenset({"version", "config", "components"}),
    "doctl": frozenset({"version", "account", "context"}),
    "gh": frozenset({"auth", "version", "status"}),
    "git": frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "blame",
                      "describe", "shortlog", "whatchanged",
                      "branch --list", "branch -a", "branch --all", "branch --show-current",
                      "remote -v", "remote show", "config --get", "stash list"}),
}

# Subcommands that install, publish, or otherwise change machine or account state.
# These are never generated as allows; they keep the native ask.
RESTRICTED_HEADS = (
    "rm", "rmdir", "mv", "chmod", "chown", "kill", "killall", "pkill", "sudo", "doas",
    "curl", "wget", "ssh", "scp", "rsync", "nc", "ncat", "telnet", "brew", "pip", "pip3",
    "pipx", "uv", "uvx", "npm", "pnpm", "yarn", "bun", "go", "cargo", "docker", "kubectl",
    "helm", "terraform", "tofu", "aws", "gcloud", "az", "doctl", "flyctl", "vercel",
    "netlify", "heroku", "sst", "wrangler", "gh",
)

# Subcommands of otherwise readable tools that write state or publish. Matched as
# "<tool> <subcommand>" so `git log --output=` and friends never inherit an allow.
RESTRICTED_SUB = {
    "git": {"push", "commit", "reset", "checkout", "switch", "restore", "clean", "merge",
            "rebase", "cherry-pick", "revert", "tag", "branch", "config", "remote", "stash",
            "fetch", "pull", "submodule", "apply", "am", "gc", "worktree", "init", "clone",
            "mv", "rm"},
    "gh": {"pr", "issue", "release", "workflow", "run", "repo", "gist", "secret", "api"},
    "npm": {"publish", "install", "i", "add", "remove", "uninstall", "update", "upgrade",
            "ci", "exec", "audit", "login", "logout", "link", "unlink"},
    "pnpm": {"publish", "install", "add", "remove", "update", "dlx", "exec", "prune"},
    "yarn": {"publish", "install", "add", "remove", "upgrade", "dlx"},
    "bun": {"publish", "install", "add", "remove", "x"},
    "pip": {"install", "uninstall", "download", "wheel", "cache"},
    "brew": {"install", "uninstall", "upgrade", "tap", "untap", "link", "unlink", "services"},
    "doctl": {"compute", "apps", "databases", "kubernetes", "registry", "vpcs", "volumes"},
    "docker": {"build", "push", "pull", "run", "exec", "cp", "rm", "system", "volume",
               "network", "compose", "image", "container"},
    "kubectl": {"apply", "create", "delete", "patch", "edit", "replace", "scale", "rollout",
                "exec", "cp", "drain", "cordon", "uncordon", "taint"},
    "helm": {"install", "upgrade", "uninstall", "rollback", "delete", "push", "pull"},
    "terraform": {"apply", "destroy", "import", "init", "plan", "refresh", "taint", "untaint"},
    "aws": {"s3", "ec2", "iam", "lambda", "deploy", "cloudformation", "rds", "kms"},
    "gcloud": {"compute", "storage", "sql", "deploy", "functions", "run", "gke", "redis"},
    "az": {"vm", "webapp", "function", "sql", "deployment", "group", "keyvault", "acr"},
    "sst": {"deploy", "remove", "dev", "diff", "shell", "secret", "unlock"},
}

# Bash subcommands that are inherently arguments or mutations rather than reads.
BASH_READONLY = frozenset({"export", "unset", "readonly", "set", "shopt", "source", "."})
BASH_WRITERS = frozenset({"printf", "tee", "dd", "install", "truncate", "mktemp", "split"})

# Read-only commands, for the `--list-safe` reference only. NEVER auto-allowed: the
# shell allowlist stays empty until the coordinator names an exact command. Kept so a
# reviewer has a shortlist to approve from, not so anything is pre-approved.
SAFE_COMMANDS = (
    "ls", "pwd", "cat", "head", "tail", "wc", "rg", "grep", "git status", "git diff",
    "git log", "git show", "git branch --list", "git remote -v", "git rev-parse",
    "git ls-files", "git blame", "git describe", "git shortlog",
)

# Options that write a file or mutate state for a nominally read-only tool.
WRITE_OPTIONS = ("--output", "-o", "--out", "--outfile", "--output-file", "--patch",
                 "--format", "--to", "--backup", "--sort", "--file", "--files-from",
                 "--exec", "--prune", "--delete", "--remove", "--save")


def fail(message):
    raise SystemExit(f"worker-permissions: {message}")


def split_command(command):
    return shlex.split(command)


def head_of(command):
    """The bare executable name, as the shell would resolve the first token."""
    tokens = split_command(command)
    if not tokens:
        return ""
    head = tokens[0]
    return PurePosixPath(head).name


def pair_of(command):
    """`<head>` and `<head> <subcommand>` for restriction matching."""
    tokens = split_command(command)
    head = head_of(command)
    if len(tokens) >= 2 and not tokens[1].startswith("-"):
        return head, f"{head} {tokens[1]}"
    return head, head


def reject_interpreter(command):
    tokens = split_command(command)
    head = head_of(command)

    if head in SHELL_WRAPPERS:
        fail(f"refusing shell wrapper {command!r}: the payload is not reviewable "
             "from the command text. Run the target command directly")

    if head in CODE_INTERPRETERS:
        flags = [token for token in tokens if token in INLINE_FLAGS]
        if flags:
            fail(f"refusing inline-code invocation {command!r}: {flags[0]} runs "
                 "caller-supplied text. Use a module or script the coordinator reviews")
        scripts = [token for token in tokens[1:] if token.endswith(SCRIPT_SUFFIXES)]
        if scripts:
            fail(f"refusing interpreter script {command!r}: {scripts[0]} is "
                 "executed as-is. Approve the specific tool invocation instead")
        if "-m" in tokens:
            index = tokens.index("-m")
            module = tokens[index + 1] if index + 1 < len(tokens) else ""
            root = module.split(".")[0]
            if root not in RUNNABLE_MODULES:
                fail(f"refusing module {module!r} in {command!r}: not a known "
                     f"runnable module. Allowed: {' '.join(sorted(RUNNABLE_MODULES))}")
        elif len(tokens) > 1:
            fail(f"refusing interpreter invocation {command!r}: only the "
                 "`-m <module>` form is reviewable")

    if head in PACKAGE_MANAGERS:
        _, pair = pair_of(command)
        subcommand = pair.split(" ", 1)[1] if " " in pair else ""
        if subcommand not in REVIEWABLE_PM_SUBCOMMANDS:
            fail(f"refusing package manager command {command!r}: {subcommand or 'no subcommand'} "
                 f"is not reviewable from the command text. "
                 f"Allowed: {' '.join(sorted(REVIEWABLE_PM_SUBCOMMANDS))}")

    if head in DECLARED_RUNNERS:
        # Not refused, but the caller must have named it deliberately.
        return


def reject_pattern(command):
    for character in UNSAFE_PATTERN:
        if character in command:
            fail(f"refusing command {command!r}: contains {character!r}. "
                 "Pass a single command with plain arguments; no redirection, "
                 "substitution, glob, pipe, chain or quote")


def reject_write_option(command):
    tokens = split_command(command)
    for token in tokens[1:]:
        name = token.split("=", 1)[0]
        if name in WRITE_OPTIONS:
            fail(f"refusing command {command!r}: {name} writes files or mutates state")


def reject_redirect(command):
    if ">" in command or "<" in command:
        fail(f"refusing command {command!r}: redirects are not verifiable")


def check_verification_command(command):
    """Accept only a plain, single-spaced, unquoted command with plain arguments.

    The round-trip check matters: OpenCode compares its own tokenised, arity-reduced
    form of the command against these patterns, so a pattern carrying quotes,
    redundant whitespace or a tilde is not something a reviewer can reason about.
    """
    command = command.strip()
    if not command:
        fail("empty verification command")
    reject_pattern(command)
    # Restriction first: a git-write, install, publish or deploy command is refused
    # for what it does, regardless of how well its text parses.
    if is_restricted(command):
        fail(f"verification command {command!r} is restricted (it installs, "
             "publishes, deploys or writes git state). Leave it out and run it "
             "manually when the user approves it")
    reject_interpreter(command)
    reject_redirect(command)
    reject_write_option(command)
    try:
        tokens = split_command(command)
    except ValueError as error:
        fail(f"cannot parse verification command {command!r}: {error}")
    if not tokens:
        fail(f"cannot parse verification command {command!r}")
    if " ".join(tokens) != command:
        fail(f"refusing command {command!r}: quotes or unusual spacing. "
             "Pass plain tokens separated by single spaces")
    first = tokens[0]
    if first.startswith("-") or first in ("", ".", ".."):
        fail(f"refusing command {command!r}: {first!r} is not an executable")
    if "/" in first:
        fail(f"refusing command {command!r}: {first!r} must be a bare executable "
             "found on PATH, not a path the repository controls")
    if not head_of(command):
        fail(f"cannot parse verification command {command!r}")
    return command


# Characters OpenCode's wildcard matcher or a reviewer would read as more than one path.
UNSAFE_PATH = set("*?\\\n\r\t")


def task_root(cwd):
    """Canonical task cwd and the git worktree root OpenCode reports edit paths against.

    The edit, write and apply_patch tools of 1.18.35 all ask with
    `path.relative(instance.worktree, file)`, and the worktree is the checkout's
    `git rev-parse --show-toplevel`; a non-git directory gets `/`. Patterns relative
    to anything else would match a different tree, so a non-git cwd is refused.
    """
    if not cwd or not os.path.isabs(cwd):
        fail(f"task cwd must be an absolute path: {cwd!r}")
    normal = os.path.realpath(cwd)
    if normal != cwd:
        fail(f"task cwd {cwd!r} is not canonical (it is {normal!r}); "
             "pass the resolved path so it cannot name a different tree")
    if not os.path.isdir(normal):
        fail(f"task cwd is not a directory: {cwd!r}")
    result = subprocess.run(["git", "-C", normal, "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0 or not result.stdout.strip():
        fail(f"task cwd {cwd!r} is not inside a git worktree; OpenCode would match "
             "edit paths against '/', which this generator does not support")
    worktree = os.path.realpath(result.stdout.strip())
    if os.path.commonpath([worktree, normal]) != worktree:
        fail(f"task cwd {cwd!r} is outside its git worktree {worktree!r}")
    return normal, worktree


def owned_pattern(cwd, worktree, entry):
    """Edit pattern for one owned entry, relative to the worktree root.

    Refuses anything that could reach further than the entry names: globs, traversal,
    absolute paths, `.git`, the whole cwd, and any symlink on the way to or inside the
    target (a symlink lets a permitted path write somewhere else). A missing file is
    allowed as an exact future file when its parent directory already exists.
    """
    if not entry or any(character in UNSAFE_PATH for character in entry):
        fail(f"refusing owned path {entry!r}: empty or contains a glob or control character")
    if os.path.isabs(entry) or entry.startswith("~"):
        fail(f"refusing owned path {entry!r}: give it relative to the task cwd")
    parts = [part for part in entry.split("/") if part not in ("", ".")]
    if not parts:
        fail(f"refusing owned path {entry!r}: owning the whole cwd is not a scope")
    if ".." in parts or ".git" in parts:
        fail(f"refusing owned path {entry!r}: no traversal and no .git")
    current = cwd
    for part in parts:
        current = os.path.join(current, part)
        if os.path.islink(current):
            fail(f"refusing owned path {entry!r}: {current!r} is a symlink")
    target = current
    relative = os.path.relpath(target, worktree)
    if os.path.isdir(target):
        for directory, subdirs, files in os.walk(target, followlinks=False):
            for name in subdirs + files:
                if os.path.islink(os.path.join(directory, name)):
                    fail(f"refusing owned directory {entry!r}: it contains symlink "
                         f"{os.path.join(directory, name)!r}")
        return f"{relative}/**"
    if os.path.exists(target):
        if not os.path.isfile(target):
            fail(f"refusing owned path {entry!r}: not a regular file or directory")
        return relative
    if entry.endswith("/"):
        fail(f"owned directory does not exist: {entry!r}; create it first")
    if not os.path.isdir(os.path.dirname(target)):
        fail(f"owned path {entry!r} does not exist and neither does its parent directory")
    return relative


def owned_patterns(cwd, owned):
    """Sorted edit patterns for every owned entry, plus the canonical cwd and worktree."""
    root, worktree = task_root(cwd)
    patterns = sorted({owned_pattern(root, worktree, entry) for entry in owned})
    if not patterns:
        fail("at least one owned path is required")
    return patterns, root, worktree


def restricted_pattern(command):
    """The restricted head or `<head> <subcommand>` of a command, or None.

    None means the command looks inspect-only and may be approved as an exact allow.
    """
    tokens = split_command(command)
    head = head_of(command)
    subcommand = tokens[1] if len(tokens) >= 2 and not tokens[1].startswith("-") else ""
    if head in BASH_WRITERS:
        return head
    if head in BASH_READONLY:
        return None
    # An inspect-only subcommand prefix of a restricted tool stays allowable.
    readonly = READONLY_SUB.get(head, frozenset())
    for length in (2, 1):
        prefix = " ".join(tokens[1:1 + length])
        if prefix and prefix in readonly:
            return None
    if subcommand in RESTRICTED_SUB.get(head, ()):
        return f"{head} {subcommand}"
    if head in RESTRICTED_HEADS:
        return head
    return None


def is_restricted(command):
    return restricted_pattern(command) is not None



# Permissions the profile governs, and what each one's catch-all must resolve to.
# Everything else (read, external_directory, question, doom_loop, ...) keeps the
# native and inherited policy untouched.
GUARDED = {"bash": "ask", "edit": "ask", "task": "deny"}

# Variables the profile owns in the worker's environment.
INJECTED = ("OPENCODE_PERMISSION", "OPENCODE_CONFIG_CONTENT")

MODEL = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._:@/-]+")

# Plugin hooks that can add permission rules (`config`) or answer asks
# (`permission.ask`). Matched in plugin source text; see `reject_permission_plugins`.
PLUGIN_HOOK = re.compile(r"""permission\.ask\b|\[\s*["']config["']\s*\]"""
                         r"""|(?<![\w.$])["']?config["']?\s*:|(?<![\w.$])config\(""")
SOURCE_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")


def build_profile(cwd, owned, verification=(), edits=True):
    """Permission payload for one task: catch-all first, then the exact allows.

    `edits=False` keeps `edit` at a bare `"*": "ask"`; the owned paths are still
    validated so a refused scope is reported either way.
    """
    # Commands are checked first: a rejected command is a stronger signal than a
    # rejected path, and the coordinator should see it before scope complaints.
    checked = []
    for command in verification or []:
        value = check_verification_command(command)
        if value not in checked:
            checked.append(value)

    patterns, root, worktree = owned_patterns(cwd, owned)
    profile = {
        "bash": dict([("*", "ask")] + [(command, "allow") for command in checked]),
        "edit": dict([("*", "ask")] + [(pattern, "allow") for pattern in patterns
                                       if edits]),
        # A worker reports through its own final message and never recruits anyone.
        "task": "deny",
    }
    return profile, root, worktree, patterns, checked


def without_edit_allows(profile):
    edit = {pattern: action for pattern, action in profile["edit"].items()
            if action != "allow"}
    return dict(profile, edit=edit)


def with_inherited_denies(profile, baseline):
    """Re-assert every inherited bash and edit deny after the profile's own rules.

    The profile's `"*": "ask"` would otherwise shadow a deny that sits earlier in the
    ruleset, and an owned-path allow would override an overlapping one. A deny whose
    pattern is a key the profile itself sets (`*`, or an approved allow) cannot be
    re-asserted without dropping that rule, so it is refused instead.
    """
    carried = {name: dict(rules) if isinstance(rules, dict) else rules
               for name, rules in profile.items()}
    for permission in ("bash", "edit"):
        for rule in baseline:
            if rule["action"] != "deny" or not wildcard_match(permission, rule["permission"]):
                continue
            current = carried[permission].get(rule["pattern"])
            if current not in (None, "deny"):
                fail(f"inherited {permission} deny {rule['pattern']!r} collides with the "
                     f"profile's own {current!r} rule; refusing rather than weaken it")
            carried[permission][rule["pattern"]] = "deny"
    return carried


def wildcard_match(text, pattern):
    """Port of OpenCode 1.18.35 `Wildcard.match` (non-Windows)."""
    escaped = re.sub(r"[.+^${}()|[\]\\]", lambda found: "\\" + found.group(0),
                     pattern.replace("\\", "/"))
    escaped = escaped.replace("*", ".*").replace("?", ".")
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    return re.fullmatch(escaped, text.replace("\\", "/"), re.S) is not None


def _globs(pattern):
    """A wildcard pattern as plain `*`/`?` globs; a trailing ` *` is optional."""
    pattern = pattern.replace("\\", "/")
    return [pattern[:-2], pattern] if pattern.endswith(" *") else [pattern]


def _intersects(left, right):
    """Whether two `*`/`?` globs accept a common string (product of the two NFAs)."""
    seen, stack = set(), [(0, 0)]
    while stack:
        i, j = stack.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(left) and j == len(right):
            return True
        if i < len(left) and left[i] == "*":
            stack.append((i + 1, j))
        if j < len(right) and right[j] == "*":
            stack.append((i, j + 1))
        if i < len(left) and j < len(right):
            a, b = left[i], right[j]
            if a in "*?" or b in "*?" or a == b:
                stack.append((i if a == "*" else i + 1, j if b == "*" else j + 1))
    return False


def patterns_overlap(left, right):
    """True when some text matches both OpenCode wildcard patterns."""
    return any(_intersects(a, b) for a in _globs(left) for b in _globs(right))


def evaluate(rules, permission, pattern):
    """`Permission.evaluate`: the LAST rule matching both fields wins; default ask."""
    for rule in reversed(rules):
        if wildcard_match(permission, rule["permission"]) and \
                wildcard_match(pattern, rule["pattern"]):
            return rule["action"]
    return "ask"


def check_effective(baseline, candidate, profile, tools=()):
    """Problems that stop the native candidate ruleset from proving the profile.

    For each guarded permission, in the ruleset OpenCode will actually evaluate:
    - the last catch-all (`*`) rule has the profile's default action;
    - no `allow` comes after it unless the profile emitted that exact allow, so an
      inherited per-pattern allow such as `curl *` cannot survive;
    - every profile allow comes after it and evaluates to allow for its own text;
    - every pattern the baseline denies is still denied, and no non-deny rule after
      its last deny can match any text it matches (N1: an inherited deny can never
      be overridden by a narrower or broader later rule).
    And when the worker's model edits through `apply_patch`, no edit allow exists.
    """
    problems = []
    for permission, default in GUARDED.items():
        own = profile[permission]
        own = own if isinstance(own, dict) else {"*": own}
        indexed = [(index, rule) for index, rule in enumerate(candidate)
                   if wildcard_match(permission, rule["permission"])]
        catch_all = [index for index, rule in indexed if rule["pattern"] == "*"]
        if not catch_all or candidate[catch_all[-1]]["action"] != default:
            problems.append(f"{permission}: the effective catch-all is not {default!r}")
            continue
        last = catch_all[-1]
        later = [(index, rule) for index, rule in indexed if index > last]
        for index, rule in later:
            if rule["action"] == "allow" and own.get(rule["pattern"]) != "allow":
                problems.append(f"{permission}: inherited allow {rule['pattern']!r} "
                                f"(rule {index}) survives the profile")
        for pattern, action in own.items():
            if action != "allow":
                continue
            if not any(rule["pattern"] == pattern and rule["action"] == "allow"
                       for _, rule in later):
                problems.append(f"{permission}: profile allow {pattern!r} is shadowed "
                                "by the catch-all")
            elif evaluate(candidate, permission, pattern) != "allow":
                problems.append(f"{permission}: profile allow {pattern!r} is overridden "
                                "by an inherited rule")
        denied = [rule["pattern"] for rule in baseline if rule["action"] == "deny"
                  and wildcard_match(permission, rule["permission"])]
        for pattern in dict.fromkeys(denied):
            positions = [index for index, rule in indexed
                         if rule["action"] == "deny" and rule["pattern"] == pattern]
            if not positions:
                problems.append(f"{permission}: inherited deny {pattern!r} is gone")
                continue
            for index, rule in indexed:
                if index > positions[-1] and rule["action"] != "deny" and \
                        patterns_overlap(rule["pattern"], pattern):
                    problems.append(f"{permission}: rule {index} {rule['action']} "
                                    f"{rule['pattern']!r} overrides inherited deny "
                                    f"{pattern!r}")
    if "apply_patch" in tools and any(action == "allow"
                                      for action in profile["edit"].values()):
        problems.append("edit: the model edits through apply_patch, whose moves ask "
                        "exactly like updates; an edit allow would pass moves silently")
    return problems


def native_debug(cwd, env, *command):
    """JSON from `opencode --pure debug <command>` run with the installed binary."""
    binary = shutil.which("opencode", path=env.get("PATH"))
    if not binary:
        fail("opencode is not on PATH; cannot verify the effective ruleset")
    try:
        result = subprocess.run([binary, "--pure", "debug", *command], cwd=cwd,
                                env=env, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        fail(f"opencode debug {' '.join(command)} timed out; refusing")
    if result.returncode != 0:
        fail(f"opencode debug {' '.join(command)} exited {result.returncode}: "
             f"{result.stderr.strip()[-400:]}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        fail(f"cannot parse opencode debug {' '.join(command)} output ({error}); refusing")


def native_agent(cwd, env):
    """The effective build agent: ordered permission rules, tool set and model."""
    agent = native_debug(cwd, env, "agent", "build")
    try:
        rules, tools = agent["permission"], agent["tools"]
        if not all(isinstance(rule[key], str) for rule in rules
                   for key in ("permission", "pattern", "action")):
            raise TypeError("rule fields")
        if not isinstance(tools, dict):
            raise TypeError("tools")
    except (KeyError, TypeError) as error:
        fail(f"unexpected opencode debug agent build output ({error}); refusing")
    return agent


def plugin_files(origin):
    """Source files of one configured plugin, or a refusal when they cannot be found."""
    spec = origin.get("spec")
    if not isinstance(spec, str) or not spec:
        fail(f"unreadable plugin entry {origin!r}; refusing")
    if spec.startswith("file://"):
        return [urllib.parse.unquote(spec[len("file://"):])]
    name = spec if not spec[1:].count("@") else spec[:spec.rindex("@")]
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    package = os.path.join(cache, "opencode", "packages", spec, "node_modules", name)
    if not os.path.isdir(package):
        fail(f"cannot locate plugin {spec!r} (looked in {package!r}); refusing")
    files = []
    for directory, subdirs, names in os.walk(package):
        subdirs[:] = [entry for entry in subdirs if entry != "node_modules"]
        files.extend(os.path.join(directory, entry) for entry in names
                     if entry.endswith(SOURCE_SUFFIXES) and not entry.endswith(".d.ts"))
    return files


def reject_permission_plugins(config):
    """Refuse when any configured plugin could add permission rules or answer asks.

    `--pure` keeps plugins out of the native check, so their effect cannot be proven
    there. Every plugin OpenCode would load (global and project, as listed in
    `plugin_origins`) is read instead, and any source mentioning a `config` or
    `permission.ask` hook is refused. This is a static text check, not an execution.
    """
    origins = config.get("plugin_origins") or []
    if len(origins) != len(config.get("plugin") or []):
        fail("configured plugins and their origins disagree; refusing")
    for origin in origins:
        for path in plugin_files(origin):
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError as error:
                fail(f"cannot read plugin source {path!r} ({error}); refusing")
            if PLUGIN_HOOK.search(text):
                fail(f"plugin {origin['spec']!r} may set permissions or answer asks "
                     f"({path}); its rules cannot be verified, refusing")


def preflight(profile, cwd, model=None, env=None):
    """Fail closed unless the native ruleset, with the profile applied, proves it.

    Returns the profile actually emitted (inherited denies re-asserted, and edit
    allows dropped when the model edits through `apply_patch`) and the native agent.
    """
    baseline_env = {key: value for key, value in (env or os.environ).items()
                    if key not in INJECTED}
    reject_permission_plugins(native_debug(cwd, baseline_env, "config"))
    baseline = native_agent(cwd, baseline_env)["permission"]
    profile = with_inherited_denies(profile, baseline)

    def candidate_for(value):
        env = dict(baseline_env, OPENCODE_CONFIG_CONTENT=config_content(value, model))
        return native_agent(cwd, env)

    candidate = candidate_for(profile)
    if "apply_patch" in candidate["tools"] and profile != without_edit_allows(profile):
        profile = without_edit_allows(profile)
        candidate = candidate_for(profile)
    problems = check_effective(baseline, candidate["permission"], profile,
                               candidate["tools"])
    if model and candidate.get("model") != dict(zip(("providerID", "modelID"),
                                                    model.split("/", 1))):
        problems.append(f"model: the build agent did not resolve to {model!r}")
    if problems:
        fail("the effective native ruleset does not prove this profile; refusing:\n  "
             + "\n  ".join(problems))
    return profile, candidate


def config_content(profile, model=None):
    build = {"permission": profile}
    if model:
        build["model"] = model
    return json.dumps({"agent": {"build": build}}, separators=(",", ":"))


def shell_lines(profile, model=None):
    """Lines for the worker pane: drop a stale top-level copy, set the scoped one."""
    payload = config_content(profile, model).replace("'", "'\\''")
    return ["unset OPENCODE_PERMISSION",
            f"export OPENCODE_CONFIG_CONTENT='{payload}'"]


def describe(root, worktree, profile, candidate, model):
    edits = [pattern for pattern, action in profile["edit"].items() if action == "allow"]
    tools = sorted(name for name in ("edit", "write", "apply_patch")
                   if candidate["tools"].get(name))
    lines = [f"task cwd            {root}",
             f"git worktree        {worktree} (edit patterns are relative to this)",
             f"model               {model or 'not pinned'}; edit tools {', '.join(tools)}"]
    if not edits:
        lines.append("  edit allow        none: every edit, write and patch asks "
                     "(no pinned model, or the model uses apply_patch)")
    lines.extend(f"  edit allow {pattern}" for pattern in edits)
    lines.extend(f"  bash allow {pattern}" for pattern, action in profile["bash"].items()
                 if action == "allow")
    lines.extend(f"  {name} deny {pattern} (inherited, re-asserted last)"
                 for name in ("bash", "edit")
                 for pattern, action in profile[name].items() if action == "deny")
    lines.append("everything else     native/inherited policy; bash and edit default ask")
    lines.append("task                deny (no delegation)")
    lines.append("verified            opencode --pure debug agent build, with and without")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="worker-permissions",
        description="Emit a natively verified per-task OpenCode permission profile.")
    parser.add_argument("--cwd",
                        help="Absolute, canonical task working directory inside a git "
                             "worktree. Required unless --list-safe")
    parser.add_argument("--owned", action="append", default=[], metavar="PATH",
                        help="Owned file or directory, relative to --cwd. Repeatable.")
    parser.add_argument("--verify", action="append", default=[], metavar="COMMAND",
                        help="Exact coordinator-approved verification command. Repeatable.")
    parser.add_argument("--model", metavar="PROVIDER/MODEL",
                        help="Worker model, pinned into the profile. Without it, or for a "
                             "model that edits through apply_patch, no edit is allowed.")
    parser.add_argument("--list-safe", action="store_true",
                        help="Print the read-only command reference and exit")
    parser.add_argument("--show", action="store_true",
                        help="Print the resolved profile summary to stderr")
    args = parser.parse_args(argv)

    if args.list_safe:
        print("Reference only; these are not auto-allowed by default:")
        for command in SAFE_COMMANDS:
            print(f"  {command}")
        print("\nApprove one explicitly with --verify when the coordinator accepts")
        print("that it runs project code. 'git branch' and 'git remote' without a")
        print("read-only flag are treated as state-changing.")
        return 0

    if not args.cwd:
        fail("--cwd is required")
    if not args.owned:
        fail("--owned is required; refusing to emit a profile with no scope")
    if args.model is not None and not MODEL.fullmatch(args.model):
        fail(f"--model must be provider/model: {args.model!r}")

    # Without a pinned model the runtime default is unknown under --pure (plugins can
    # supply it), so it may edit through apply_patch: no edit allows at all.
    profile, root, worktree, _, _ = build_profile(
        args.cwd, args.owned, args.verify, edits=bool(args.model))
    profile, candidate = preflight(profile, root, args.model)

    for line in shell_lines(profile, args.model):
        print(line)
    if args.show:
        print(describe(root, worktree, profile, candidate, args.model), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
