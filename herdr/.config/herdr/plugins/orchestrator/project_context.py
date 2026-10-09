#!/usr/bin/env python3
"""Bounded local reference manifest. No execution, discovery scans or model calls."""
import argparse
import hashlib
import json
import stat
from pathlib import Path

MAX_FILES = 32
MAX_BYTES = 64 * 1024
MAX_TOTAL = 256 * 1024
MAX_TARGETS = 32
MAX_DEPTH = 16


def manifest(root, current, ledger, owned=(), rules=()):
    root = Path(root).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("root must be an absolute existing project directory")
    root = root.resolve()
    if len(owned) > MAX_TARGETS or len(rules) > MAX_FILES:
        raise ValueError("too many explicit context inputs")
    entries, issues, seen = [], [], set()
    total = 0

    def local(value):
        value = str(value)
        if any(c in value for c in ("://", "*", "?", "[", "\n", "\r")):
            raise ValueError("unsupported URL, glob or control character")
        path = Path(value)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(root):
            raise ValueError("outside project root (including symlinks)")
        return path

    def add(path, role):
        nonlocal total
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        if len(entries) >= MAX_FILES:
            issues.append({"role": role, "problem": "file budget exceeded"})
            return
        entry = {"path": key, "role": role}
        entries.append(entry)
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("context reference must be a regular file")
            # Read at most the per-file bound, even if the file grows mid-read.
            with path.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES or total + len(data) > MAX_TOTAL:
                raise ValueError("context byte budget exceeded; select a narrower reference")
            data.decode("utf-8")
            total += len(data)
            entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        except (OSError, ValueError) as error:
            entry["problem"] = str(error)[:160]
            issues.append({"role": role, "path": key, "problem": entry["problem"]})

    # Ledger paths are explicit intake inputs; they may live in the coordinator's
    # authorized state directory, not in the repository. Never infer them from cwd.
    for value, role in ((current, "current"), (ledger, "ledger")):
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{role} must be an explicit absolute path")
        add(path.resolve(), role)

    def rule_at(directory, role):
        chosen = directory / "AGENTS.md"
        if not chosen.exists() and not chosen.is_symlink():
            chosen = directory / "CLAUDE.md"
        try:
            add(local(chosen), role)
        except ValueError as error:
            issues.append({"role": role, "problem": str(error)})

    rule_at(root, "root-rules")
    directories = set()
    for value in owned:
        try:
            path = local(value)
            directory = path if path.is_dir() else path.parent
            if len(directory.relative_to(root).parts) > MAX_DEPTH:
                raise ValueError("scoped rule depth exceeded")
            while directory != root:
                directories.add(directory)
                directory = directory.parent
        except ValueError as error:
            issues.append({"role": "owned", "problem": str(error)})
    for directory in sorted(directories, key=lambda p: (len(p.parts), str(p))):
        if any((directory / name).exists() or (directory / name).is_symlink()
               for name in ("AGENTS.md", "CLAUDE.md")):
            rule_at(directory, "scoped-rules")
    # References are selected explicitly after reading root/scoped rules. No
    # markdown crawling, remote fetches, config execution or wildcard expansion.
    for value in rules:
        try:
            add(local(value), "selected-reference")
        except ValueError as error:
            issues.append({"role": "selected-reference", "problem": str(error)})
    payload = {"root": str(root), "entries": entries, "issues": issues}
    payload["fingerprint"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    payload["ready"] = not issues
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--rule", action="append", default=[])
    args = parser.parse_args()
    try:
        result = manifest(args.root, args.current, args.ledger, args.owned, args.rule)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
