"""Local manifest tests; temporary projects only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "project_context", Path(__file__).parent / ".config/herdr/plugins/orchestrator/project_context.py")
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


class ContextTests(unittest.TestCase):
    def test_two_projects_cwd_independence_and_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            projects = []
            for name, command in (("a", "npm test; OpenCode"), ("b", "pytest; Claude")):
                root = base / name
                root.mkdir()
                (root / "AGENTS.md").write_text(command)
                (root / "CURRENT.md").write_text("active T01")
                (root / "ledger.md").write_text(command)
                projects.append(root)
            def read(root):
                return context.manifest(root, root / "CURRENT.md", root / "ledger.md")
            first = read(projects[0])
            with patch("pathlib.Path.cwd", return_value=projects[1]):
                self.assertEqual(first, read(projects[0]))
            second = read(projects[1])
            self.assertTrue(first["ready"] and second["ready"])
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])
            self.assertTrue(all(str(projects[0]) in e["path"] for e in first["entries"]))
            (projects[0] / "AGENTS.md").write_text("npm run check; Codex")
            self.assertNotEqual(first["fingerprint"], read(projects[0])["fingerprint"])

    def test_missing_fallback_scoped_selected_and_unsafe_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            current, ledger = root / "CURRENT.md", root / "ledger.md"
            current.write_text("current")
            ledger.write_text("ledger")
            self.assertFalse(context.manifest(root, current, ledger)["ready"])
            (root / "CLAUDE.md").write_text("fallback")
            (root / "src").mkdir()
            (root / "other").mkdir()
            (root / "src/AGENTS.md").write_text("scoped")
            (root / "other/AGENTS.md").write_text("unrelated")
            (root / "checks.md").write_text("checks")
            result = context.manifest(root, current, ledger, ["src/file.py"], ["checks.md"])
            self.assertTrue(result["ready"])
            paths = [e["path"] for e in result["entries"]]
            self.assertIn(str(root / "CLAUDE.md"), paths)
            self.assertNotIn(str(root / "other/AGENTS.md"), paths)
            (root / "escape.md").symlink_to(root.parent / "outside.md")
            for ref in ("../outside.md", "https://example.com/rules", "*.md", "escape.md"):
                result = context.manifest(root, current, ledger, rules=[ref])
                self.assertFalse(result["ready"], ref)
                self.assertEqual(len(result["entries"]), 3)

    def test_bounds_and_absolute_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("x" * (context.MAX_BYTES + 1))
            result = context.manifest(root, root / "CURRENT", root / "ledger")
            self.assertFalse(result["ready"])
            self.assertNotIn("sha256", result["entries"][-1])
            with self.assertRaises(ValueError):
                context.manifest(root, "CURRENT", root / "ledger")
            with self.assertRaises(ValueError):
                context.manifest(root, root / "CURRENT", root / "ledger", owned=["x"] * 33)
