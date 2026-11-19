import concurrent.futures
import io
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


PLUGINS = Path(__file__).resolve().parents[1] / ".config/sketchybar/plugins"
sys.path.insert(0, str(PLUGINS))
import aerospace_sync as sync
import run_once as runner


WORKSPACES = "1|true|1|External\n1|false|2|External\n2|true|3|Built-in Retina Display\n"
WINDOWS = (
    "1|true|1|com.brave.Browser|Brave\n"
    "1|true|1|com.mitchellh.ghostty|Ghostty\n"
    "2|true|3|com.spotify.client|Spotify\n"
)


class SyncTests(unittest.TestCase):
    def items(self, focused="1|com.mitchellh.ghostty|Ghostty\n", windows=WINDOWS):
        return sync.desired_items(2, ["1", "2", "3"], WORKSPACES, windows, focused)

    def test_workspace_visibility_and_window_count(self):
        items = self.items()
        self.assertEqual(items["space.1.1"]["label"], "+1")
        self.assertEqual(items["space.1.1"]["icon.color"], sync.ACTIVE)
        self.assertEqual(items["space.1.2"]["drawing"], "off")
        self.assertEqual(items["space.2.3"]["drawing"], "on")

    def test_empty_active_workspace_stays_visible(self):
        items = self.items(focused="", windows="")
        self.assertEqual(items["space.1.1"]["drawing"], "on")
        self.assertEqual(items["space.1.1"]["label.drawing"], "off")
        self.assertEqual(items["front_app.1"]["label"], "Desktop")
        self.assertEqual(items["front_app.1"]["icon.background.drawing"], "off")
        self.assertNotIn("icon.background.image", items["front_app.1"])

    def test_focused_app_and_nonfocused_monitor_fallback(self):
        items = self.items()
        self.assertEqual(items["front_app.1"]["label"], "Ghostty")
        self.assertEqual(items["front_app.2"]["label"], "Spotify")
        self.assertEqual(items["front_app.2"]["icon.background.image.scale"], "0.58")

    def test_inactive_populated_workspace_is_visible(self):
        items = self.items(windows=WINDOWS + "1|false|2|com.apple.mail|Mail\n")
        self.assertEqual(items["space.1.2"]["drawing"], "on")
        self.assertEqual(items["space.1.2"]["background.drawing"], "off")

    def test_identical_snapshot_makes_no_updates(self):
        items = self.items()
        self.assertEqual(sync.changed_arguments(items, items), [])

    def test_focus_change_does_not_repaint_workspace_icons(self):
        arguments = sync.changed_arguments(self.items(), self.items("1|com.brave.Browser|Brave\n"))
        self.assertEqual(arguments, [
            "--set", "front_app.1", "label=Brave", "icon.background.image=app.com.brave.Browser"
        ])

    def test_count_change_does_not_reload_identical_app_icon(self):
        one_window = WINDOWS.replace("1|true|1|com.mitchellh.ghostty|Ghostty\n", "")
        arguments = sync.changed_arguments(self.items(), self.items(windows=one_window))
        self.assertEqual(arguments, ["--set", "space.1.1", "label="])

    def test_reload_generation_repaints_and_unchanged_poll_skips_ipc(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sync, "state_directory", return_value=Path(directory)), \
                 patch.object(sync, "query", side_effect=lambda *args, **kwargs: {
                     "list-workspaces": WORKSPACES,
                     "list-windows": "1|com.mitchellh.ghostty|Ghostty\n" if "--focused" in args else WINDOWS,
                 }[args[0]]), patch.object(sync.subprocess, "run") as command:
                sync.refresh(2, ["1", "2", "3"], "first")
                self.assertEqual(command.call_count, 1)
                sync.refresh(2, ["1", "2", "3"], "first")
                self.assertEqual(command.call_count, 1)
                sync.refresh(2, ["1", "2", "3"], "second")
                self.assertEqual(command.call_count, 2)

    def test_failed_bar_write_does_not_cache_unsent_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sync, "state_directory", return_value=Path(directory)), \
                 patch.object(sync, "query", side_effect=[WORKSPACES, WINDOWS, ""]), \
                 patch.object(sync.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "sketchybar")):
                with self.assertRaises(subprocess.CalledProcessError):
                    sync.refresh(2, ["1", "2", "3"], "first")
                self.assertFalse((Path(directory) / "aerospace-sync.json").exists())

    def test_queries_have_deadlines(self):
        with patch.object(sync.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok")) as command:
            self.assertEqual(sync.query("list-workspaces"), "ok")
            self.assertEqual(command.call_args.kwargs["timeout"], 2)

    def test_invalid_cache_shape_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "aerospace-sync.json"
            cache.write_text(json.dumps(["not an object"]))
            with patch.object(sync, "state_directory", return_value=Path(directory)), \
                 patch.object(sync, "query", side_effect=[WORKSPACES, WINDOWS, ""]), \
                 patch.object(sync.subprocess, "run") as command:
                sync.refresh(2, ["1", "2", "3"], "first")
            self.assertEqual(command.call_count, 1)
            self.assertEqual(json.loads(cache.read_text())["generation"], "first")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = {**os.environ, "TMPDIR": str(self.root)}

    def command(self, code, seconds=3, key="test"):
        return [sys.executable, str(PLUGINS / "run_once.py"), key, str(seconds), sys.executable, "-c", code]

    def wait_for(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), str(path))

    def test_overlapping_event_burst_runs_only_one_worker(self):
        marker = self.root / "runs"
        code = f"from pathlib import Path; import time; p=Path({str(marker)!r}); p.open('a').write('run\\n'); time.sleep(1)"
        owner = subprocess.Popen(self.command(code), env=self.environment)
        self.addCleanup(lambda: owner.poll() is None and owner.kill())
        self.wait_for(marker)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: subprocess.run(self.command(code), env=self.environment).returncode, range(12)))
        self.assertEqual(results, [0] * 12)
        self.assertEqual(owner.wait(timeout=5), 0)
        self.assertEqual(marker.read_text(), "run\n")

    def test_timeout_kills_descendants_and_releases_lock(self):
        marker = self.root / "escaped"
        child = f"import time; from pathlib import Path; time.sleep(0.6); Path({str(marker)!r}).touch()"
        code = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
        result = subprocess.run(self.command(code, seconds=0.2), env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 124)
        time.sleep(0.7)
        self.assertFalse(marker.exists())
        self.assertEqual(subprocess.run(self.command("pass"), env=self.environment).returncode, 0)

    def test_termination_stops_worker_before_releasing_lock(self):
        marker = self.root / "started"
        escaped = self.root / "escaped"
        code = f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(0.7); Path({str(escaped)!r}).touch()"
        owner = subprocess.Popen(self.command(code), env=self.environment)
        self.wait_for(marker)
        owner.terminate()
        self.assertEqual(owner.wait(timeout=5), 128 + signal.SIGTERM)
        time.sleep(0.8)
        self.assertFalse(escaped.exists())
        self.assertEqual(subprocess.run(self.command("pass"), env=self.environment).returncode, 0)

    def test_killed_supervisor_keeps_worker_lock_until_worker_exits(self):
        marker = self.root / "started"
        escaped = self.root / "overlap"
        code = f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(0.5)"
        owner = subprocess.Popen(self.command(code), env=self.environment)
        self.wait_for(marker)
        owner.kill()
        owner.wait(timeout=5)
        other = f"from pathlib import Path; Path({str(escaped)!r}).touch()"
        subprocess.run(self.command(other), env=self.environment, check=True)
        self.assertFalse(escaped.exists())
        time.sleep(0.6)
        subprocess.run(self.command(other), env=self.environment, check=True)
        self.assertTrue(escaped.exists())

    def test_independent_jobs_do_not_block_each_other(self):
        marker = self.root / "started"
        owner = subprocess.Popen(self.command(f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(0.3)"), env=self.environment)
        self.wait_for(marker)
        result = subprocess.run(self.command("raise SystemExit(7)", key="other"), env=self.environment)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(owner.wait(timeout=5), 0)

    def test_cooldown_collapses_sequential_events(self):
        marker = self.root / "runs"
        code = f"from pathlib import Path; Path({str(marker)!r}).open('a').write('run\\n')"
        command = self.command(code)
        command[2:2] = ["--min-interval", "30"]
        for _ in range(3):
            subprocess.run(command, env=self.environment, check=True)
        self.assertEqual(marker.read_text(), "run\n")
        lock = self.root / f"sketchybar-jobs-{os.getuid()}" / "test.lock"
        lock.write_text("0")
        subprocess.run(command, env=self.environment, check=True)
        self.assertEqual(marker.read_text(), "run\nrun\n")

    def test_completed_job_does_not_signal_its_former_group(self):
        with patch.object(runner, "state_directory", return_value=self.root), \
             patch.object(runner.os, "killpg") as kill_group:
            self.assertEqual(runner.run_once("completed", 3, [sys.executable, "-c", "pass"]), 0)
        kill_group.assert_not_called()

    def test_cancellation_survives_os_error_fallback_in_subprocess_wait(self):
        handlers = {}
        process = Mock()

        def install_handler(signum, handler):
            previous = handlers.get(signum, signal.SIG_DFL)
            handlers[signum] = handler
            return previous

        def wait_with_os_error_fallback(**kwargs):
            # Python 3.15's macOS kqueue wait treats any OSError as a reason
            # to fall back to another wait, swallowing InterruptedError.
            try:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            except OSError:
                return 0

        process.wait.side_effect = wait_with_os_error_fallback
        with patch.object(runner, "state_directory", return_value=self.root), \
             patch.object(runner.subprocess, "Popen", return_value=process), \
             patch.object(runner.signal, "signal", side_effect=install_handler), \
             patch.object(runner, "stop_process_group", return_value=True) as stop:
            self.assertEqual(runner.run_once("interrupted", 3, ["worker"]), 128 + signal.SIGTERM)
        stop.assert_called_once_with(process)

    def test_cancellation_keeps_leader_unreaped_until_both_signals(self):
        events = []
        process = Mock(pid=1234, returncode=None)
        process.wait.side_effect = lambda **kwargs: events.append("wait")
        with patch.object(runner.os, "killpg", side_effect=lambda pid, sig: events.append(sig)), \
             patch.object(runner.time, "sleep"):
            self.assertTrue(runner.stop_process_group(process))
        self.assertEqual(events, [signal.SIGTERM, signal.SIGKILL, "wait"])

    def test_denied_cancellation_is_reported_without_crashing_or_blocking(self):
        process = Mock(pid=1234, returncode=None)
        process.poll.return_value = None
        errors = io.StringIO()
        with patch.object(runner.os, "killpg", side_effect=PermissionError()), \
             patch.object(runner.sys, "stderr", errors):
            self.assertFalse(runner.stop_process_group(process))
        process.wait.assert_not_called()
        self.assertIn("permission denied stopping worker group 1234", errors.getvalue())
        self.assertRegex(errors.getvalue(), r"^\d{4}-\d{2}-\d{2}T")


class ConfigTests(unittest.TestCase):
    def test_config_builds_quoted_bounded_jobs_without_native_window_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            log = root / "calls"
            config = root / "config with spaces"
            config.symlink_to(PLUGINS.parent, target_is_directory=True)
            scripts = {
                "sketchybar": '''#!/bin/bash
printf '%s\\0' "$@" >> "$CALL_LOG"
printf '\\n' >> "$CALL_LOG"
if [ "$1" = --query ] && [ "$2" = displays ]; then
  printf '[\\n{"arrangement-id":1},\\n{"arrangement-id":2}\\n]\\n'
fi
''',
                "aerospace": "#!/bin/bash\nprintf '1|External\\n2|Built-in Retina Display\\n'\n",
                "networksetup": "#!/bin/bash\nprintf 'Hardware Port: Wi-Fi\\nDevice: en0\\n'\n",
            }
            for name, script in scripts.items():
                path = binaries / name
                path.write_text(script)
                path.chmod(0o755)
            environment = {
                **os.environ, "PATH": f"{binaries}:/usr/bin:/bin",
                "CONFIG_DIR": str(config), "TMPDIR": str(root), "CALL_LOG": str(log),
            }
            subprocess.run(["/bin/bash", str(config / "sketchybarrc")], env=environment, check=True, timeout=5)
            calls = [line.decode().rstrip("\0").split("\0") for line in log.read_bytes().splitlines()]
            script_arguments = [argument for call in calls for argument in call if argument.startswith("script=")]
            self.assertEqual(len(script_arguments), 17)
            for argument in script_arguments:
                command = shlex.split(argument.removeprefix("script="))
                if command[0].endswith("volume.sh"):
                    continue
                self.assertEqual(command[:3], ["python3", "-B", str(config / "plugins/run_once.py")])
            sync_call = next(call for call in calls if call[:4] == ["--add", "item", "aerospace_sync", "left"])
            self.assertIn("update_freq=2", sync_call)
            sync_command = shlex.split(next(arg[7:] for arg in sync_call if arg.startswith("script=")))
            self.assertIn("--min-interval", sync_command)
            self.assertIn(str(config / "plugins/aerospace_sync.sh"), sync_command)
            self.assertIn("--generation", sync_command)
            subscriptions = sync_call[sync_call.index("--subscribe") + 2:]
            self.assertNotIn("space_windows_change", subscriptions)
            self.assertIn("aerospace_workspace_change", subscriptions)
            self.assertIn("aerospace_focus_change", subscriptions)


class MediaTests(unittest.TestCase):
    def run_media(self, payload, hung_player=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "bar-call.json"
            scripts = {
                "pgrep": "raise SystemExit(0)",
                "osascript": "import time; time.sleep(10)" if hung_player else "print('playing\\nArtist\\nTrack')",
                "sketchybar": "import os,json,sys; from pathlib import Path; Path(os.environ['CALL_LOG']).write_text(json.dumps(sys.argv[1:]))",
            }
            for name, source in scripts.items():
                path = root / name
                path.write_text(f"#!{sys.executable}\n{source}\n")
                path.chmod(0o755)
            environment = {
                **os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                "NAME": "media.1", "INFO": payload, "CALL_LOG": str(marker),
            }
            result = subprocess.run(["/bin/sh", str(PLUGINS / "media.sh")], env=environment, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(marker.read_text()) if marker.exists() else None

    def test_unresponsive_player_preserves_last_bar_state(self):
        self.assertIsNone(self.run_media("", hung_player=True))

    def test_playback_event_still_updates_track(self):
        arguments = self.run_media(json.dumps({"state": "playing", "title": "Track", "artist": "Artist", "app": "Spotify"}))
        self.assertIn("drawing=on", arguments)
        self.assertIn("label=Artist - Track", arguments)

    def test_paused_event_hides_track(self):
        self.assertIn("drawing=off", self.run_media(json.dumps({"state": "paused"})))


if __name__ == "__main__":
    unittest.main()
