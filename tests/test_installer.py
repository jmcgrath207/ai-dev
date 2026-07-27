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


class TestConfigureSmallModel(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf
        self._tmp = Path(_tf.mkdtemp())
        import shutil as _sh
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json_path = self._tmp / "opencode.json"
        self.fake_jsonc_path = self._tmp / "opencode.jsonc"
        self._patch_json = mock.patch.object(iop, "GLOBAL_CONFIG_JSON", self.fake_json_path)
        self._patch_jsonc = mock.patch.object(iop, "GLOBAL_CONFIG_JSONC", self.fake_jsonc_path)
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        # Use non-dry-run for tests
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_sets_on_empty_config(self):
        self.assertFalse(self.fake_json_path.exists())
        iop.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["small_model"], iop.SMALL_MODEL)

    def test_idempotent(self):
        self.fake_json_path.write_text(
            json.dumps({"small_model": iop.SMALL_MODEL}), encoding="utf-8"
        )
        mtime = self.fake_json_path.stat().st_mtime_ns
        iop.configure_small_model()
        self.assertEqual(self.fake_json_path.stat().st_mtime_ns, mtime,
                         "file rewritten despite identical value")

    def test_updates_existing(self):
        self.fake_json_path.write_text(
            json.dumps({"small_model": "old/model"}), encoding="utf-8"
        )
        iop.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["small_model"], iop.SMALL_MODEL)

    def test_prefers_json_over_jsonc(self):
        self.fake_json_path.write_text("{}", encoding="utf-8")
        self.fake_jsonc_path.write_text("{}", encoding="utf-8")
        iop.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertIn("small_model", cfg)
        cfg2 = json.loads(self.fake_jsonc_path.read_text(encoding="utf-8"))
        self.assertNotIn("small_model", cfg2,
                         "should write to .json when both exist")

    def test_handles_invalid_json_without_crash(self):
        self.fake_json_path.write_text("not json", encoding="utf-8")
        iop.configure_small_model()  # must not raise


class TestInstallCompactionPlugin(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf
        self._tmp = Path(_tf.mkdtemp())
        import shutil as _sh
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_ts = self._tmp / "compaction.ts"
        self.fake_json = self._tmp / "opencode.json"
        self._patch_ts = mock.patch.object(iop, "COMPACTION_PLUGIN", self.fake_ts)
        self._patch_json = mock.patch.object(iop, "GLOBAL_CONFIG_JSON", self.fake_json)
        self._patch_jsonc = mock.patch.object(iop, "GLOBAL_CONFIG_JSONC",
                                               self._tmp / "opencode.jsonc")
        self._patch_ts.start()
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_ts.stop)
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_writes_ts_file(self):
        iop.install_compaction_plugin()
        self.assertTrue(self.fake_ts.exists())
        content = self.fake_ts.read_text(encoding="utf-8")
        self.assertIn("experimental.session.compacting", content)

    def test_adds_plugin_entry(self):
        self.fake_json.write_text(
            json.dumps({"plugin": ["opencode-rtk@latest"]}), encoding="utf-8"
        )
        iop.install_compaction_plugin()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertIn(iop.COMPACTION_PLUGIN_ENTRY, cfg["plugin"])
        # should be at front
        self.assertEqual(cfg["plugin"][0], iop.COMPACTION_PLUGIN_ENTRY)

    def test_idempotent_no_duplicate_entry(self):
        self.fake_json.write_text(
            json.dumps({"plugin": [iop.COMPACTION_PLUGIN_ENTRY]}), encoding="utf-8"
        )
        iop.install_compaction_plugin()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertEqual(cfg["plugin"].count(iop.COMPACTION_PLUGIN_ENTRY), 1)

    def test_creates_config_if_missing(self):
        self.assertFalse(self.fake_json.exists())
        iop.install_compaction_plugin()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertIn(iop.COMPACTION_PLUGIN_ENTRY, cfg.get("plugin", []))


class TestInstallConciseAgentsMd(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf
        self._tmp = Path(_tf.mkdtemp())
        import shutil as _sh
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_agents_md = self._tmp / "AGENTS.md"
        self._patch = mock.patch.object(iop, "AGENTS_MD", self.fake_agents_md)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_writes_content(self):
        iop.install_concise_agents_md()
        self.assertTrue(self.fake_agents_md.exists())
        content = self.fake_agents_md.read_text(encoding="utf-8")
        self.assertIn("MUST keep replies short", content)
        self.assertEqual(content, iop.read_text(iop.AGENTS_MD_SRC))

    def test_backups_existing_before_overwrite(self):
        self.fake_agents_md.write_text("old content", encoding="utf-8")
        iop.install_concise_agents_md()
        backups = sorted(self._tmp.glob("AGENTS.md.*.bak"))
        self.assertEqual(len(backups), 1, "should create one backup")
        self.assertIn("old content", backups[0].read_text(encoding="utf-8"))

    def test_idempotent_no_backup_when_unchanged(self):
        iop.install_concise_agents_md()
        first_mtime = self.fake_agents_md.stat().st_mtime_ns
        iop.install_concise_agents_md()
        self.assertEqual(self.fake_agents_md.stat().st_mtime_ns, first_mtime,
                         "file rewritten despite identical content")


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


class TestConfigureAgentOptimizations(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json = self._tmp / "opencode.json"
        self._patch_json = mock.patch.object(iop, "GLOBAL_CONFIG_JSON", self.fake_json)
        self._patch_jsonc = mock.patch.object(iop, "GLOBAL_CONFIG_JSONC",
                                               self._tmp / "opencode.jsonc")
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_writes_agent_block(self):
        iop.configure_agent_optimizations()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertIn("agent", cfg)
        self.assertEqual(cfg["agent"]["plan"]["model"], "openrouter/z-ai/glm-5.2")
        self.assertEqual(cfg["agent"]["explore"]["steps"], 15)

    def test_idempotent(self):
        iop.configure_agent_optimizations()
        mtime = self.fake_json.stat().st_mtime_ns
        iop.configure_agent_optimizations()
        self.assertEqual(self.fake_json.stat().st_mtime_ns, mtime)

    def test_creates_config_if_missing(self):
        self.assertFalse(self.fake_json.exists())
        iop.configure_agent_optimizations()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertEqual(cfg["agent"]["plan"]["model"], "openrouter/z-ai/glm-5.2")

    def test_handles_invalid_json_without_crash(self):
        self.fake_json.write_text("not json", encoding="utf-8")
        iop.configure_agent_optimizations()


class TestRemoveSuperpowersAgents(_AgentFixtures):
    def test_removes_all_superpowers(self):
        for name in [
            "superpowers.md", "superpowers-code-reviewer.md",
            "superpowers-implementer.md", "superpowers-plan-writer.md",
            "superpowers-spec-writer.md",
        ]:
            (self.agents_dir / name).write_text("---\nname: test\n---\n", encoding="utf-8")
        iop.remove_superpowers_agents()
        remaining = list(self.agents_dir.glob("superpowers*.md"))
        self.assertEqual(remaining, [])

    def test_preserves_non_superpowers(self):
        (self.agents_dir / "superpowers.md").write_text("---\nname: test\n---\n", encoding="utf-8")
        (self.agents_dir / "my-agent.md").write_text("---\nname: mine\n---\n", encoding="utf-8")
        iop.remove_superpowers_agents()
        remaining = sorted(f.name for f in self.agents_dir.glob("*.md"))
        self.assertEqual(remaining, ["my-agent.md"])

    def test_no_op_when_clean(self):
        iop.remove_superpowers_agents()  # no crash

    def test_handles_missing_dir(self):
        import shutil as _sh
        _sh.rmtree(self.agents_dir)
        iop.remove_superpowers_agents()  # no crash


class TestInstallAstGrepBinary(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_bin = self._tmp / "sg"
        self._patch_bin = mock.patch.object(iop, "AST_GREP_BIN", self.fake_bin)
        self._patch_bin.start()
        self.addCleanup(self._patch_bin.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_downloads_and_installs_binary(self):
        self.fake_bin.parent.mkdir(parents=True, exist_ok=True)
        self.fake_bin.write_text("", encoding="utf-8")
        fake_sg = Path("/tmp/fake-sg")
        fake_ast_grep = Path("/tmp/fake-ast-grep")
        with mock.patch.object(iop.platform, "system", return_value="Linux"), \
             mock.patch.object(iop.platform, "machine", return_value="x86_64"), \
             mock.patch.object(iop, "check_cmd", return_value=True), \
             mock.patch.object(iop, "run") as mock_run, \
             mock.patch("zipfile.ZipFile") as mock_zf, \
             mock.patch("shutil.copy2") as mock_copy, \
             mock.patch.object(Path, "chmod"), \
             mock.patch.object(Path, "iterdir",
                               return_value=[fake_sg, fake_ast_grep]), \
             mock.patch.object(Path, "is_file", return_value=True):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            mock_zf.return_value.__enter__.return_value.extractall = mock.Mock()
            iop.install_ast_grep_binary()
            self.assertEqual(mock_copy.call_count, 2)

    def test_force_reinstall_when_present(self):
        self.fake_bin.parent.mkdir(parents=True, exist_ok=True)
        self.fake_bin.write_text("old", encoding="utf-8")
        fake_sg = Path("/tmp/fake-sg")
        fake_ast_grep = Path("/tmp/fake-ast-grep")
        with mock.patch.object(iop.platform, "system", return_value="Linux"), \
             mock.patch.object(iop.platform, "machine", return_value="x86_64"), \
             mock.patch.object(iop, "check_cmd", return_value=True), \
             mock.patch.object(iop, "run") as mock_run, \
             mock.patch("zipfile.ZipFile") as mock_zf, \
             mock.patch("shutil.copy2") as mock_copy, \
             mock.patch.object(Path, "chmod"), \
             mock.patch.object(Path, "iterdir",
                               return_value=[fake_sg, fake_ast_grep]), \
             mock.patch.object(Path, "is_file", return_value=True):
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            mock_zf.return_value.__enter__.return_value.extractall = mock.Mock()
            iop.install_ast_grep_binary()
            self.assertEqual(mock_copy.call_count, 2)

    def test_skips_unsupported_platform(self):
        with mock.patch.object(iop.platform, "system", return_value="Windows"), \
             mock.patch.object(iop.platform, "machine", return_value="x86_64"):
            iop.install_ast_grep_binary()  # no crash, no download

    def test_target_triple_linux_x86_64(self):
        self.assertEqual(iop._ast_grep_target_triple(), "x86_64-unknown-linux-gnu")

    def test_target_triple_macos_arm(self):
        with mock.patch.object(iop.platform, "system", return_value="Darwin"), \
             mock.patch.object(iop.platform, "machine", return_value="arm64"):
            self.assertEqual(iop._ast_grep_target_triple(), "aarch64-apple-darwin")

    def test_target_triple_unsupported(self):
        with mock.patch.object(iop.platform, "system", return_value="Unknown"), \
             mock.patch.object(iop.platform, "machine", return_value="x86_64"):
            self.assertIsNone(iop._ast_grep_target_triple())


class TestInstallAstGrepSkill(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_cache = self._tmp / "cache"
        self.fake_target = self._tmp / "target"
        self._patch_cache = mock.patch.object(iop, "AST_GREP_SKILL_CACHE", self.fake_cache)
        self._patch_target = mock.patch.object(iop, "AST_GREP_SKILL_DIR", self.fake_target)
        self._patch_cache.start()
        self._patch_target.start()
        self.addCleanup(self._patch_cache.stop)
        self.addCleanup(self._patch_target.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def _create_fake_repo(self):
        """Create a fake cloned repo with the expected skill subtree."""
        skill_src = self.fake_cache / "ast-grep/skills/ast-grep"
        skill_src.mkdir(parents=True)
        (skill_src / "SKILL.md").write_text("---\nname: ast-grep\n---\nskill content\n", encoding="utf-8")
        ref_dir = skill_src / "references"
        ref_dir.mkdir()
        (ref_dir / "rule_reference.md").write_text("# rule reference\n", encoding="utf-8")

    def test_installs_skill_files(self):
        self._create_fake_repo()
        with mock.patch.object(iop, "_fetch_or_clone") as mock_fetch:
            mock_fetch.side_effect = lambda *a, **kw: None
            iop.install_ast_grep_skill()
        self.assertTrue((self.fake_target / "SKILL.md").exists())
        self.assertTrue((self.fake_target / "references/rule_reference.md").exists())

    def test_idempotent(self):
        self._create_fake_repo()
        with mock.patch.object(iop, "_fetch_or_clone") as mock_fetch:
            mock_fetch.side_effect = lambda *a, **kw: None
            iop.install_ast_grep_skill()
            iop.install_ast_grep_skill()  # second call
        self.assertTrue((self.fake_target / "SKILL.md").exists())

    def test_creates_target_dir_if_missing(self):
        self.assertFalse(self.fake_target.exists())
        self._create_fake_repo()
        with mock.patch.object(iop, "_fetch_or_clone") as mock_fetch:
            mock_fetch.side_effect = lambda *a, **kw: None
            iop.install_ast_grep_skill()
        self.assertTrue(self.fake_target.exists())


class TestConfigureOpenRouterRouting(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json = self._tmp / "opencode.json"
        self._patch_json = mock.patch.object(iop, "GLOBAL_CONFIG_JSON", self.fake_json)
        self._patch_jsonc = mock.patch.object(iop, "GLOBAL_CONFIG_JSONC",
                                               self._tmp / "opencode.jsonc")
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(iop, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def _assert_routing(self, cfg):
        """Assert timeouts at provider level, routing per-model."""
        opts = cfg["provider"]["openrouter"]["options"]
        self.assertEqual(opts["timeout"], 120_000)
        self.assertEqual(opts["headerTimeout"], 15_000)
        self.assertEqual(opts["chunkTimeout"], 45_000)
        self.assertNotIn("provider", opts)  # routing NOT at provider level
        models = cfg["provider"]["openrouter"]["models"]
        for mid in iop.OPENROUTER_ROUTING_MODELS:
            self.assertIn(mid, models)
            self.assertEqual(models[mid]["options"]["provider"], {"sort": {"by": "price"}})

    def test_writes_routing_block(self):
        iop.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_routing(cfg)

    def test_idempotent(self):
        iop.configure_openrouter_routing()
        mtime = self.fake_json.stat().st_mtime_ns
        iop.configure_openrouter_routing()
        self.assertEqual(self.fake_json.stat().st_mtime_ns, mtime)

    def test_creates_config_if_missing(self):
        self.assertFalse(self.fake_json.exists())
        iop.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_routing(cfg)

    def test_handles_invalid_json_without_crash(self):
        self.fake_json.write_text("not json", encoding="utf-8")
        iop.configure_openrouter_routing()

    def test_preserves_other_providers(self):
        self.fake_json.write_text(
            json.dumps({"provider": {"anthropic": {"options": {"baseURL": "x"}}}}),
            encoding="utf-8",
        )
        iop.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertIn("anthropic", cfg["provider"])
        self._assert_routing(cfg)

    def test_removes_legacy_provider_level_routing(self):
        self.fake_json.write_text(
            json.dumps({
                "provider": {
                    "openrouter": {
                        "options": {
                            "provider": {"allow_fallbacks": False},
                        },
                    },
                },
            }),
            encoding="utf-8",
        )
        iop.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        opts = cfg["provider"]["openrouter"]["options"]
        self.assertNotIn("provider", opts)  # stripped from provider level
        self._assert_routing(cfg)


if __name__ == "__main__":
    unittest.main()
