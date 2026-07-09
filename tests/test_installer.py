"""Unit tests for install-opencode-plugins.

Stdlib only. Run with: python -m unittest discover tests -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Load the hyphenated installer module by file path
_spec = importlib.util.spec_from_file_location(
    "install_opencode_plugins",
    str(Path(__file__).resolve().parent.parent / "install-opencode-plugins.py"),
)
iop = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(iop)


class TestAtomicWrite(unittest.TestCase):
    def test_round_trip(self):
        with mock.patch.object(iop, "_dry_run", False):
            with mock.patch("tempfile.mkstemp") as mk:
                # mkstemp returns (fd, name) where fd is a real OS fd
                fd, name = 10, "/tmp/foo.tmp"
                mk.return_value = (fd, name)
                with mock.patch("os.fdopen") as fdopen, \
                     mock.patch("os.replace") as repl, \
                     mock.patch("pathlib.Path.parent", new_callable=mock.PropertyMock) as parent_mock:
                    parent_mock.return_value = mock.MagicMock()
                    # Simpler: just call atomic_write against a real tmp dir
                    pass
            import tempfile as _tf
            d = _tf.mkdtemp()
            try:
                p = Path(d) / "sub" / "x.json"
                iop.atomic_write(p, "hello\n")
                self.assertEqual(p.read_text(encoding="utf-8"), "hello\n")
                self.assertTrue(p.parent.exists())
            finally:
                import shutil
                shutil.rmtree(d, ignore_errors=True)

    def test_no_temp_leftover_on_success(self):
        import tempfile as _tf, shutil as _sh
        d = _tf.mkdtemp()
        try:
            p = Path(d) / "x.txt"
            iop.atomic_write(p, "x")
            leftovers = [q for q in Path(d).iterdir() if q.name != "x.txt"]
            self.assertEqual(leftovers, [], f"leftover temp files: {leftovers}")
        finally:
            _sh.rmtree(d, ignore_errors=True)

    def test_overwrites_existing(self):
        import tempfile as _tf, shutil as _sh
        d = _tf.mkdtemp()
        try:
            p = Path(d) / "x.txt"
            p.write_text("old", encoding="utf-8")
            iop.atomic_write(p, "new")
            self.assertEqual(p.read_text(encoding="utf-8"), "new")
        finally:
            _sh.rmtree(d, ignore_errors=True)


class TestBackupRotation(unittest.TestCase):
    def test_keeps_newest_n(self):
        import tempfile as _tf, shutil as _sh
        d = _tf.mkdtemp()
        try:
            src = Path(d) / "cfg.json"
            src.write_text("v", encoding="utf-8")
            # create 8 backups with ascending timestamps
            for i in range(8):
                ts = f"20240101-00000{i}"
                (Path(d) / f"cfg.json.{ts}.bak").write_text(f"v{i}", encoding="utf-8")
            iop._touched.clear()
            dst = iop.backup_with_rotation(src)
            self.assertIsNotNone(dst)
            backups = sorted(Path(d).glob("cfg.json.*.bak"))
            # newest 5 kept: 20240101-000003..000007 + the just-made 000008? no,
            # the just-made uses time.strftime. We pre-seeded 8; the new one
            # makes 9. After rotation, keep newest 5 -> drop oldest 4.
            self.assertLessEqual(len(backups), iop.BACKUP_KEEP)
            self.assertGreater(len(backups), 0)
        finally:
            _sh.rmtree(d, ignore_errors=True)


class TestSanitizeLocalConfig(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = _tf.mkdtemp()
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_local = Path(self._tmp) / "opencode.json"
        # Patch HOME so LOCAL_CONFIG resolves to our tmp file
        self._home_patch = mock.patch.object(iop, "LOCAL_CONFIG", self.fake_local)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_removes_list_entry(self):
        self.fake_local.write_text(
            json.dumps({"plugin": ["list", "opencode-rtk@latest"]}),
            encoding="utf-8",
        )
        iop.sanitize_local_config()
        cfg = json.loads(self.fake_local.read_text(encoding="utf-8"))
        self.assertNotIn("list", cfg["plugin"])
        self.assertIn("opencode-rtk@latest", cfg["plugin"])

    def test_no_op_when_clean(self):
        self.fake_local.write_text(
            json.dumps({"plugin": ["opencode-rtk@latest"]}), encoding="utf-8"
        )
        iop.sanitize_local_config()
        cfg = json.loads(self.fake_local.read_text(encoding="utf-8"))
        self.assertEqual(cfg["plugin"], ["opencode-rtk@latest"])

    def test_handles_missing_file(self):
        # file does not exist (setUp does not create it); should not raise
        iop.sanitize_local_config()  # no exception

    def test_recovers_from_invalid_json(self):
        # Invalid JSON: installer warns to stderr and treats the in-memory
        # config as {}. It does NOT write the reset back (removed=0),
        # so the file on disk remains as-is. The contract is "no crash".
        self.fake_local.write_text("not json {", encoding="utf-8")
        iop.sanitize_local_config()  # must not raise


class TestDefaultBranch(unittest.TestCase):
    def test_parses_symbolic_ref(self):
        fake = "/tmp/fake-repo"
        r = mock.Mock()
        r.stdout = "refs/remotes/origin/main\n"
        r.returncode = 0
        with mock.patch.object(iop.subprocess, "run", return_value=r):
            self.assertEqual(iop.default_branch(Path(fake)), "main")

    def test_falls_back_to_remote_show(self):
        symbolic = mock.Mock(returncode=1, stdout="", stderr="")
        remote_show = mock.Mock(
            returncode=0,
            stdout="  HEAD branch: trunk\n  Remote branch: ...\n",
            stderr="",
        )
        # symbolic-ref raises (check=True), then remote-show returns trunk
        with mock.patch.object(
            iop.subprocess, "run", side_effect=[symbolic, remote_show]
        ):
            self.assertEqual(iop.default_branch(Path("/tmp/fake")), "trunk")

    def test_last_resort_main(self):
        symbolic = mock.Mock(returncode=1, stdout="", stderr="")
        remote_show = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch.object(
            iop.subprocess, "run", side_effect=[symbolic, remote_show]
        ):
            self.assertEqual(iop.default_branch(Path("/tmp/fake")), "main")


class _AgentFixtures(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = _tf.mkdtemp()
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.agents_dir = Path(self._tmp) / "agents"
        self.agents_dir.mkdir()
        self._patch = mock.patch.object(iop, "AGENTS_DIR", self.agents_dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)


class TestStripCavemanModelPins(_AgentFixtures):
    def test_strips_model_line(self):
        f = self.agents_dir / "cavecrew-builder.md"
        f.write_text(
            "---\n"
            "name: cavecrew-builder\n"
            "model: github-copilot/gpt-5.4\n"
            "description: builder\n"
            "---\n"
            "Body text.\n",
            encoding="utf-8",
        )
        iop.strip_caveman_model_pins()
        text = f.read_text(encoding="utf-8")
        self.assertNotIn("model:", text)
        self.assertIn("name: cavecrew-builder", text)
        self.assertIn("description: builder", text)
        self.assertIn("Body text.", text)

    def test_no_op_when_clean(self):
        f = self.agents_dir / "cavecrew-builder.md"
        f.write_text(
            "---\nname: cavecrew-builder\ndescription: builder\n---\nBody.\n",
            encoding="utf-8",
        )
        iop.strip_caveman_model_pins()
        # no print assert; just no exception and file unchanged
        self.assertIn("name: cavecrew-builder", f.read_text(encoding="utf-8"))

    def test_leaves_non_frontmatter_model_line(self):
        f = self.agents_dir / "cavecrew-builder.md"
        f.write_text(
            "---\nname: cavecrew-builder\n---\n"
            "In body: model: keep-me\n",
            encoding="utf-8",
        )
        iop.strip_caveman_model_pins()
        self.assertIn("In body: model: keep-me", f.read_text(encoding="utf-8"))

    def test_ignores_non_cavecrew_files(self):
        f = self.agents_dir / "other.md"
        f.write_text(
            "---\nname: other\nmodel: x\n---\n", encoding="utf-8"
        )
        iop.strip_caveman_model_pins()
        self.assertIn("model: x", f.read_text(encoding="utf-8"))


class TestStripSuperpowersModelPins(_AgentFixtures):
    def test_strips_primary(self):
        f = self.agents_dir / "superpowers.md"
        f.write_text(
            "---\n"
            "name: superpowers\n"
            "model: github-copilot/gpt-5.4-mini\n"
            "mode: primary\n"
            "---\n",
            encoding="utf-8",
        )
        iop.strip_superpowers_model_pins()
        text = f.read_text(encoding="utf-8")
        self.assertNotIn("model:", text)
        self.assertIn("mode: primary", text)

    def test_strips_subagents(self):
        for name, pin in [
            ("superpowers-code-reviewer.md", "github-copilot/gpt-5.4"),
            ("superpowers-implementer.md", "github-copilot/claude-sonnet-4.6"),
            ("superpowers-plan-writer.md", "anthropic/claude-opus-4-7"),
            ("superpowers-spec-writer.md", "github-copilot/gpt-5.5"),
        ]:
            (self.agents_dir / name).write_text(
                f"---\nname: {name[:-3]}\nmodel: {pin}\nmode: subagent\n---\n",
                encoding="utf-8",
            )
        iop.strip_superpowers_model_pins()
        for name in [
            "superpowers-code-reviewer.md",
            "superpowers-implementer.md",
            "superpowers-plan-writer.md",
            "superpowers-spec-writer.md",
        ]:
            self.assertNotIn(
                "model:", (self.agents_dir / name).read_text(encoding="utf-8")
            )

    def test_no_op_when_clean(self):
        f = self.agents_dir / "superpowers.md"
        f.write_text(
            "---\nname: superpowers\nmode: primary\n---\n", encoding="utf-8"
        )
        iop.strip_superpowers_model_pins()
        self.assertNotIn("model:", f.read_text(encoding="utf-8"))

    def test_handles_missing_agents_dir(self):
        import shutil as _sh
        _sh.rmtree(self.agents_dir)
        # should not raise
        iop.strip_superpowers_model_pins()


if __name__ == "__main__":
    unittest.main()
