#!/usr/bin/env python3
"""Run one bounded process tree per bar job; discard overlapping events."""

import argparse
from datetime import datetime
import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


def state_directory():
    directory = Path(tempfile.gettempdir()) / f"sketchybar-jobs-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def report(message):
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"{timestamp} SketchyBar: {message}", file=sys.stderr)


def stop_process_group(process):
    if process.returncode is not None:
        return True

    # Only cancel unfinished jobs. Do not reap the group leader between signals:
    # retaining its PID prevents us from signaling an unrelated, reused group.
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            break
        except PermissionError:
            if process.poll() is not None:
                return True
            report(f"permission denied stopping worker group {process.pid}")
            return False
        if signum == signal.SIGTERM:
            time.sleep(0.1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        report(f"worker {process.pid} did not exit after cancellation")
        return False
    return True


def run_once(key, seconds, command, min_interval=0):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key) or seconds <= 0 or min_interval < 0:
        raise ValueError("invalid job name or timeout")

    # Never unlink lock files: flock follows the inode, not the filename. The OS
    # releases this lock if the owner exits/crashes; no stale-lock timer needed.
    with (state_directory() / f"{key}.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        if min_interval:
            lock.seek(0)
            try:
                elapsed = time.monotonic() - float(lock.read())
            except ValueError:
                elapsed = min_interval
            if 0 <= elapsed < min_interval:
                return 0
            lock.seek(0)
            lock.truncate()
            lock.write(str(time.monotonic()))
            lock.flush()

        # Keep the lock in the worker too: even SIGKILL of this supervisor must
        # not permit a second copy while its original worker is still alive.
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, start_new_session=True,
            pass_fds=(lock.fileno(),),
        )
        previous_handlers = {}

        def interrupted(signum, frame):
            raise InterruptedError(signum)

        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        cancel = False
        try:
            return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            cancel = True
            report(f"{key} exceeded {seconds:g}s; requesting process-tree cancellation")
            return 124
        except InterruptedError as error:
            cancel = True
            return 128 + error.args[0]
        finally:
            # Do not interrupt cleanup and release the lock before children die.
            for signum in previous_handlers:
                signal.signal(signum, signal.SIG_IGN)
            try:
                if cancel:
                    stop_process_group(process)
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-interval", type=float, default=0)
    parser.add_argument("key")
    parser.add_argument("seconds", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("a command is required")
    sys.exit(run_once(args.key, args.seconds, args.command, args.min_interval))
