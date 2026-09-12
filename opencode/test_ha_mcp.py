import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parent
LAUNCHER = PACKAGE / ".config/opencode/bin/ha-mcp"


class HomeAssistantLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.secrets = self.home / ".config/opencode/secrets"
        self.secrets.mkdir(parents=True)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.op_log = self.root / "op.log"
        self.uvx_log = self.root / "uvx.log"
        self._write_executable(
            "op",
            """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_OP_LOG"
if [ "${FAKE_OP_FAIL:-0}" = 1 ]; then
  exit 23
fi
printf '%s' "${FAKE_OP_TOKEN:-}"
""",
        )
        self._write_executable(
            "uvx",
            """#!/bin/sh
{
  printf 'token=%s\\n' "${HOMEASSISTANT_TOKEN-}"
  printf 'url=%s\\n' "${HOMEASSISTANT_URL-}"
  printf 'args=%s\\n' "$*"
} > "$TEST_UVX_LOG"
""",
        )

    def _write_executable(self, name, contents):
        path = self.fake_bin / name
        path.write_text(contents)
        path.chmod(0o755)

    def _run(self, extra_env=None):
        env = os.environ.copy()
        for name in (
            "HOMEASSISTANT_TOKEN",
            "HOMEASSISTANT_TOKEN_SOURCE",
            "HOMEASSISTANT_TOKEN_REF",
            "HOMEASSISTANT_URL",
            "OP_ACCOUNT",
        ):
            env.pop(name, None)
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "TEST_OP_LOG": str(self.op_log),
                "TEST_UVX_LOG": str(self.uvx_log),
            }
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["sh", str(LAUNCHER), "--test-argument"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_default_1password_mode_overrides_stale_file_token(self):
        (self.secrets / "home-assistant.env").write_text(
            'export HOMEASSISTANT_TOKEN="stale-token"\n'
            'export HOMEASSISTANT_TOKEN_REF="op://Test/Home Assistant/token"\n'
            'export OP_ACCOUNT="test.1password.com"\n'
        )

        result = self._run({"FAKE_OP_TOKEN": "fresh-token"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("token=fresh-token", self.uvx_log.read_text())
        self.assertNotIn("stale-token", self.uvx_log.read_text())
        self.assertEqual(
            self.op_log.read_text().strip(),
            "read op://Test/Home Assistant/token --account test.1password.com",
        )

    def test_explicit_environment_mode_skips_1password(self):
        result = self._run(
            {
                "HOMEASSISTANT_TOKEN_SOURCE": "environment",
                "HOMEASSISTANT_TOKEN": "environment-token",
                "FAKE_OP_FAIL": "1",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("token=environment-token", self.uvx_log.read_text())
        self.assertFalse(self.op_log.exists())

    def test_1password_failure_never_launches_uvx(self):
        (self.secrets / "home-assistant.env").write_text(
            'export HOMEASSISTANT_TOKEN="stale-token"\n'
        )

        result = self._run({"FAKE_OP_FAIL": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.uvx_log.exists())
        self.assertNotIn("stale-token", result.stdout + result.stderr)

    def test_empty_1password_result_never_launches_uvx(self):
        result = self._run({"FAKE_OP_TOKEN": ""})

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.uvx_log.exists())

    def test_invalid_token_source_never_launches_credentials_or_uvx(self):
        result = self._run({"HOMEASSISTANT_TOKEN_SOURCE": "automatic"})

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.op_log.exists())
        self.assertFalse(self.uvx_log.exists())


if __name__ == "__main__":
    unittest.main()
