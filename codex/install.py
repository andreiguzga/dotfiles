#!/usr/bin/env python3
"""Back up an equivalent local Codex config, then install with GNU Stow."""

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import tomllib


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
    except (OSError, subprocess.CalledProcessError):
        if backup is not None and not config.exists() and not config.is_symlink():
            backup.rename(config)
        raise
    if not config.is_symlink() or config.resolve() != source:
        raise SystemExit("Stow did not create the expected config symlink.")
    print(f"Codex config: {config} -> {source}")


if __name__ == "__main__":
    main()
