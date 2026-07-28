"""Tests for configure-openrouter.py — a separate provider config script.

Run with: python -m pytest tests/test_openrouter.py -v
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "configure_openrouter",
    str(Path(__file__).resolve().parent.parent / "configure-openrouter.py"),
)
cr = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(cr)


class TestConfigureSmallModel(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json_path = self._tmp / "opencode.json"
        self.fake_jsonc_path = self._tmp / "opencode.jsonc"
        self._patch_json = mock.patch.object(cr, "GLOBAL_CONFIG_JSON", self.fake_json_path)
        self._patch_jsonc = mock.patch.object(cr, "GLOBAL_CONFIG_JSONC", self.fake_jsonc_path)
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(cr, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_sets_on_empty_config(self):
        self.assertFalse(self.fake_json_path.exists())
        cr.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["small_model"], cr.SMALL_MODEL)

    def test_idempotent(self):
        self.fake_json_path.write_text(
            json.dumps({"small_model": cr.SMALL_MODEL}), encoding="utf-8"
        )
        mtime = self.fake_json_path.stat().st_mtime_ns
        cr.configure_small_model()
        self.assertEqual(self.fake_json_path.stat().st_mtime_ns, mtime,
                         "file rewritten despite identical value")

    def test_updates_existing(self):
        self.fake_json_path.write_text(
            json.dumps({"small_model": "old/model"}), encoding="utf-8"
        )
        cr.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["small_model"], cr.SMALL_MODEL)

    def test_prefers_json_over_jsonc(self):
        self.fake_json_path.write_text("{}", encoding="utf-8")
        self.fake_jsonc_path.write_text("{}", encoding="utf-8")
        cr.configure_small_model()
        cfg = json.loads(self.fake_json_path.read_text(encoding="utf-8"))
        self.assertIn("small_model", cfg)
        cfg2 = json.loads(self.fake_jsonc_path.read_text(encoding="utf-8"))
        self.assertNotIn("small_model", cfg2,
                         "should write to .json when both exist")

    def test_handles_invalid_json_without_crash(self):
        self.fake_json_path.write_text("not json", encoding="utf-8")
        cr.configure_small_model()  # must not raise


class TestConfigureAgentModels(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json = self._tmp / "opencode.json"
        self._patch_json = mock.patch.object(cr, "GLOBAL_CONFIG_JSON", self.fake_json)
        self._patch_jsonc = mock.patch.object(
            cr, "GLOBAL_CONFIG_JSONC", self._tmp / "opencode.jsonc"
        )
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(cr, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def _assert_models(self, cfg):
        for agent, expected in cr.AGENT_MODELS.items():
            self.assertEqual(cfg["agent"][agent]["model"], expected)

    def test_sets_agent_models(self):
        cr.configure_agent_models()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_models(cfg)

    def test_idempotent(self):
        cr.configure_agent_models()
        mtime = self.fake_json.stat().st_mtime_ns
        cr.configure_agent_models()
        self.assertEqual(self.fake_json.stat().st_mtime_ns, mtime)

    def test_creates_config_if_missing(self):
        self.assertFalse(self.fake_json.exists())
        cr.configure_agent_models()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_models(cfg)

    def test_preserves_existing_agent_settings(self):
        self.fake_json.write_text(
            json.dumps({"agent": {"plan": {"temperature": 0.5, "steps": 50}}}),
            encoding="utf-8",
        )
        cr.configure_agent_models()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertEqual(cfg["agent"]["plan"]["temperature"], 0.5)
        self.assertEqual(cfg["agent"]["plan"]["steps"], 50)
        self.assertEqual(cfg["agent"]["plan"]["model"], cr.AGENT_MODELS["plan"])

    def test_handles_invalid_json_without_crash(self):
        self.fake_json.write_text("not json", encoding="utf-8")
        cr.configure_agent_models()

    def test_handles_non_dict_agent(self):
        self.fake_json.write_text(json.dumps({"agent": "string"}), encoding="utf-8")
        cr.configure_agent_models()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_models(cfg)


class TestConfigureOpenRouterRouting(unittest.TestCase):
    def setUp(self):
        import tempfile as _tf, shutil as _sh
        self._tmp = Path(_tf.mkdtemp())
        self.addCleanup(_sh.rmtree, self._tmp, True)
        self.fake_json = self._tmp / "opencode.json"
        self._patch_json = mock.patch.object(cr, "GLOBAL_CONFIG_JSON", self.fake_json)
        self._patch_jsonc = mock.patch.object(
            cr, "GLOBAL_CONFIG_JSONC", self._tmp / "opencode.jsonc"
        )
        self._patch_json.start()
        self._patch_jsonc.start()
        self.addCleanup(self._patch_json.stop)
        self.addCleanup(self._patch_jsonc.stop)
        self._dry = mock.patch.object(cr, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def _assert_config(self, cfg):
        opts = cfg["provider"]["openrouter"]["options"]
        self.assertEqual(opts["timeout"], 120_000)
        self.assertEqual(opts["headerTimeout"], 15_000)
        self.assertEqual(opts["chunkTimeout"], 45_000)
        self.assertNotIn("provider", opts)
        models = cfg["provider"]["openrouter"]["models"]
        for mid in cr.OPENROUTER_ROUTING_MODELS:
            self.assertEqual(
                models[mid]["options"]["provider"],
                cr.OPENROUTER_ROUTING,
            )

    def test_writes_timeouts_and_sort_by_price(self):
        cr.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_config(cfg)

    def test_idempotent(self):
        cr.configure_openrouter_routing()
        mtime = self.fake_json.stat().st_mtime_ns
        cr.configure_openrouter_routing()
        self.assertEqual(self.fake_json.stat().st_mtime_ns, mtime)
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_config(cfg)

    def test_creates_config_if_missing(self):
        self.assertFalse(self.fake_json.exists())
        cr.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_config(cfg)

    def test_handles_invalid_json_without_crash(self):
        self.fake_json.write_text("not json", encoding="utf-8")
        cr.configure_openrouter_routing()

    def test_preserves_other_providers(self):
        self.fake_json.write_text(
            json.dumps({"provider": {"anthropic": {"options": {"baseURL": "x"}}}}),
            encoding="utf-8",
        )
        cr.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self.assertIn("anthropic", cfg["provider"])
        self._assert_config(cfg)

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
        cr.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        opts = cfg["provider"]["openrouter"]["options"]
        self.assertNotIn("provider", opts)
        self._assert_config(cfg)

    def test_replaces_dynamic_order_with_sort(self):
        self.fake_json.write_text(
            json.dumps({
                "provider": {
                    "openrouter": {
                        "options": {"timeout": 120_000},
                        "models": {
                            "z-ai/glm-5.2": {
                                "options": {
                                    "provider": {
                                        "order": ["novita", "akashml"],
                                        "allow_fallbacks": False,
                                        "zdr": True,
                                    },
                                },
                            },
                        },
                    },
                },
            }),
            encoding="utf-8",
        )
        cr.configure_openrouter_routing()
        cfg = json.loads(self.fake_json.read_text(encoding="utf-8"))
        self._assert_config(cfg)
        prov = cfg["provider"]["openrouter"]["models"]["z-ai/glm-5.2"]["options"]["provider"]
        self.assertNotIn("order", prov)
        self.assertNotIn("zdr", prov)


class TestRemoveOpenrouterRoutingCron(unittest.TestCase):
    def setUp(self):
        self._dry = mock.patch.object(cr, "_dry_run", False)
        self._dry.start()
        self.addCleanup(self._dry.stop)

    def test_removes_marker_lines(self):
        existing = (
            f"0 * * * * /usr/bin/python3 /tmp/x.py # {cr.OPENROUTER_CRON_MARKER}\n"
            "30 * * * * /usr/bin/true\n"
        )
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw))
            if cmd[:2] == ["crontab", "-l"]:
                return mock.Mock(returncode=0, stdout=existing, stderr="")
            if cmd == ["crontab", "-"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(cr, "check_cmd", return_value=True), \
             mock.patch.object(cr.subprocess, "run", side_effect=fake_run):
            cr.remove_openrouter_routing_cron()
        install = [c for c in calls if c[0] == ["crontab", "-"]]
        self.assertEqual(len(install), 1)
        table = install[0][1]["input"]
        self.assertNotIn(cr.OPENROUTER_CRON_MARKER, table)
        self.assertIn("/usr/bin/true", table)

    def test_noop_when_absent(self):
        def fake_run(cmd, **kw):
            if cmd[:2] == ["crontab", "-l"]:
                return mock.Mock(
                    returncode=0, stdout="30 * * * * /usr/bin/true\n", stderr=""
                )
            raise AssertionError(f"unexpected {cmd}")

        with mock.patch.object(cr, "check_cmd", return_value=True), \
             mock.patch.object(cr.subprocess, "run", side_effect=fake_run):
            cr.remove_openrouter_routing_cron()

    def test_skips_when_no_crontab(self):
        with mock.patch.object(cr, "check_cmd", return_value=False):
            cr.remove_openrouter_routing_cron()


if __name__ == "__main__":
    unittest.main()
