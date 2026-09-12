#!/usr/bin/env python3
"""Back up an equivalent local Codex config, then install with GNU Stow."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import subprocess
import tomllib


def inspect_agents(target, source):
    """Allow only our directory link or an empty/legacy Stow-owned directory."""
    if target.is_symlink():
        if target.resolve() != source:
            raise SystemExit(f"{target} links elsewhere; reconcile it before installing.")
        return {}
    if not target.exists():
        return {}
    if not target.is_dir():
        raise SystemExit(f"{target} is not a directory; reconcile it before installing.")
    links = {}
    for entry in target.iterdir():
        expected = source / entry.name
        if (not entry.is_symlink() or not expected.is_file()
                or entry.resolve() != expected):
            raise SystemExit(f"{entry} is not a managed agent link; preserve and reconcile "
                             "local agents before installing.")
        links[entry.name] = os.readlink(entry)
    return links


def install_agents(target, source):
    # Codex 0.154.0 refuses symlinked role files. A directory link keeps their
    # final path components regular files while retaining the repository source.
    links = inspect_agents(target, source)
    if target.is_symlink():
        return
    existed = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        for name in links:
            (target / name).unlink()
        if existed:
            target.rmdir()
        target.symlink_to(os.path.relpath(source, target.parent), target_is_directory=True)
    except OSError:
        # Restore removed managed links if replacement fails; never overwrite
        # a file another process may have created in the meantime.
        if existed and not target.is_symlink():
            target.mkdir(exist_ok=True)
            for name, destination in links.items():
                entry = target / name
                if not entry.exists() and not entry.is_symlink():
                    entry.symlink_to(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-home", type=Path, default=Path.home())
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    target = args.target_home.expanduser().resolve()
    source = package / ".codex/config.toml"
    config = target / ".codex/config.toml"
    expected = tomllib.loads(source.read_text())
    backup = None
    agents_source = package / ".codex/agents"
    agents_target = target / ".codex/agents"
    # Refuse custom files before Stow or config backup changes anything.
    inspect_agents(agents_target, agents_source)

    if config.is_symlink():
        if config.resolve() != source:
            raise SystemExit(f"{config} links elsewhere; reconcile it before installing.")
    elif config.exists():
        if tomllib.loads(config.read_text()) != expected:
            raise SystemExit(f"{config} differs from the repository. Merge the settings "
                             "into codex/.codex/config.toml before installing.")
        backup = config.with_name("config.toml.before-stow-"
                                  + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        config.rename(backup)
        print(f"Backup: {backup}")

    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["stow", "--restow", "--no-folding", "--target", str(target),
                        "--dir", str(package.parent), package.name], check=True)
        install_agents(agents_target, agents_source)
    except (OSError, subprocess.CalledProcessError, SystemExit):
        if backup is not None:
            # Stow may already have installed our link before agent migration
            # failed. Restore the original file without touching a foreign link.
            if config.is_symlink() and config.resolve() == source:
                config.unlink()
            if not config.exists() and not config.is_symlink():
                backup.rename(config)
        raise
    if not config.is_symlink() or config.resolve() != source:
        raise SystemExit("Stow did not create the expected config symlink.")
    print(f"Codex config: {config} -> {source}")
    print(f"Codex agents: {agents_target} -> {agents_source}")


if __name__ == "__main__":
    main()
