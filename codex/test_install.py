"""Regression checks for agent directory links and non-destructive migration."""

from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(shutil.which("stow"), "GNU Stow is required")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "repo/codex"
        self.source = self.package / ".codex"
        self.source.mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.agents = self.home / ".codex/agents"
        for name in ("install.py", ".stow-local-ignore", "test_install.py"):
            shutil.copy2(Path(__file__).parent / name, self.package / name)
        (self.source / "config.toml").write_text('model = "gpt-6-astra"\n')
        (self.source / "AGENTS.md").write_text("Orchestrate focused workers.\n")
        (self.source / "agents").mkdir()
        for name in ("scout", "developer", "verifier"):
            (self.source / "agents" / f"{name}.toml").write_text(f'name = "{name}"\n')

    def install(self, succeeds=True):
        result = subprocess.run(
            [sys.executable, "-B", str(self.package / "install.py"),
             "--target-home", str(self.home)], capture_output=True, text=True
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def assert_installed(self):
        self.assertTrue(self.agents.is_symlink())
        self.assertEqual(self.agents.resolve(), self.source / "agents")
        for name in ("scout", "developer", "verifier"):
            role = self.agents / f"{name}.toml"
            self.assertTrue(role.is_file())
            self.assertFalse(role.is_symlink(), "Codex rejects symlinked role files")
        for name in ("config.toml", "AGENTS.md"):
            self.assertTrue((self.home / ".codex" / name).is_symlink())
        self.assertFalse((self.home / "test_install.py").exists())

    def test_fresh_install_and_repeat(self):
        self.install()
        self.assert_installed()
        self.install()
        self.assert_installed()

    def test_legacy_file_links_migrate(self):
        self.agents.mkdir(parents=True)
        for role in (self.source / "agents").iterdir():
            (self.agents / role.name).symlink_to(role)
        self.install()
        self.assert_installed()

    def test_empty_directory_migrates(self):
        self.agents.mkdir(parents=True)
        self.install()
        self.assert_installed()

    def test_custom_role_preserved_before_config_backup(self):
        self.agents.mkdir(parents=True)
        custom = self.agents / "custom.toml"
        custom.write_text("local role\n")
        config = self.home / ".codex/config.toml"
        shutil.copy2(self.source / "config.toml", config)
        self.install(succeeds=False)
        self.assertEqual(custom.read_text(), "local role\n")
        self.assertFalse(config.is_symlink())
        self.assertEqual(list(config.parent.glob("config.toml.before-stow-*")), [])

    def test_foreign_directory_link_preserved(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        self.agents.parent.mkdir()
        self.agents.symlink_to(foreign)
        self.install(succeeds=False)
        self.assertEqual(self.agents.resolve(), foreign)
        self.assertFalse((self.home / ".codex/config.toml").exists())

    def test_foreign_role_link_preserved(self):
        self.agents.mkdir(parents=True)
        foreign = self.root / "foreign.toml"
        foreign.write_text("local role\n")
        link = self.agents / "scout.toml"
        link.symlink_to(foreign)
        self.install(succeeds=False)
        self.assertEqual(link.resolve(), foreign)
        self.assertEqual(foreign.read_text(), "local role\n")

    def test_regular_named_role_preserved(self):
        self.agents.mkdir(parents=True)
        role = self.agents / "scout.toml"
        role.write_text("local scout\n")
        self.install(succeeds=False)
        self.assertEqual(role.read_text(), "local scout\n")

    def test_failed_replacement_restores_legacy_links(self):
        self.agents.mkdir(parents=True)
        source = self.source / "agents"
        for role in source.iterdir():
            (self.agents / role.name).symlink_to(role)
        install_agents = runpy.run_path(str(self.package / "install.py"))["install_agents"]
        original = Path.symlink_to

        def fail_directory_link(path, target, target_is_directory=False):
            if path == self.agents:
                raise OSError("simulated link failure")
            return original(path, target, target_is_directory)

        with patch.object(Path, "symlink_to", fail_directory_link):
            with self.assertRaisesRegex(OSError, "simulated link failure"):
                install_agents(self.agents, source)
        self.assertFalse(self.agents.is_symlink())
        for role in source.iterdir():
            self.assertTrue((self.agents / role.name).is_symlink())
            self.assertEqual((self.agents / role.name).resolve(), role)

    def test_agent_failure_after_stow_restores_original_config(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        original = (self.source / "config.toml").read_bytes()
        config.write_bytes(original)
        main = runpy.run_path(str(self.package / "install.py"))["main"]
        failure = Mock(side_effect=OSError("simulated agent migration failure"))
        with patch.dict(main.__globals__, {"install_agents": failure}):
            with patch.object(sys, "argv", ["install.py", "--target-home", str(self.home)]):
                with self.assertRaisesRegex(OSError, "simulated agent migration failure"):
                    main()
        self.assertFalse(config.is_symlink())
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(list(config.parent.glob("config.toml.before-stow-*")), [])


if __name__ == "__main__":
    unittest.main()
