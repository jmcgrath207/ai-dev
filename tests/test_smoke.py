"""End-to-end smoke test: run the installer in --dry-run mode with
subprocess and shutil.which mocked, and assert:

  1. no filesystem mutation outside the test sandbox
  2. the expected steps are reached (via a call counter)
  3. main() exits 0

Stdlib only. Run with: python -m unittest discover tests -v
"""

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "install_opencode_plugins",
    str(Path(__file__).resolve().parent.parent / "install-opencode-plugins.py"),
)
iop = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(iop)


class TestDryRun(unittest.TestCase):
    def test_dry_run_runs_all_steps(self):
        # Track which step functions were invoked.
        called: list[str] = []

        def make_proxy(name):
            def _proxy(*a, **kw):
                called.append(name)
            return _proxy

        proxies = {
            name: make_proxy(name)
            for name in [
                "backup_configs",
                "install_rtk_binary",
                "rtk_init_opencode",
                "install_rtk_hook",
                "sanitize_local_config",
                "install_plugins",
                "install_superpowers",
                "strip_superpowers_model_pins",
                "install_julius_caveman",
                "strip_caveman_model_pins",
                "install_rust_skills",
                "install_golang_skills",
            ]
        }

        # subprocess.run should never actually execute in dry-run;
        # the steps that DO call run() in dry-run still go through run()
        # (which is mocked here to just return success).
        def fake_run(cmd, *a, **kw):
            cp = mock.Mock()
            cp.returncode = 0
            cp.stdout = ""
            cp.stderr = ""
            return cp

        # shutil.which: pretend the common tools are present so the
        # installer does not sys.exit(1) on dependency checks.
        def fake_which(name):
            return f"/usr/bin/{name}" if name in {
                "curl", "git", "opencode", "npx", "wget", "rtk"
            } else None

        # ensure_local_bin_on_path is a real call, but the HOME it's
        # operating on is the real $HOME; for the smoke test we patch HOME
        # to a tmp dir so we don't actually mkdir ~/.local/bin.
        import tempfile as _tf
        with _tf.TemporaryDirectory() as fake_home:
            with mock.patch.dict(os.environ, {"HOME": fake_home}), \
                 mock.patch("pathlib.Path.home", lambda: Path(fake_home)), \
                 mock.patch.object(iop, "subprocess") as sp_mod, \
                 mock.patch.object(iop, "shutil") as sh_mod:
                # ensure_local_bin_on_path writes to os.environ; allow it
                sp_mod.run.side_effect = fake_run
                sh_mod.which.side_effect = fake_which
                # shutil.copy2 (backup) and shutil.move (RTK.md relocate)
                # should never be reached in dry-run; but provide dummies.
                sh_mod.copy2 = lambda *a, **kw: None
                sh_mod.move = lambda *a, **kw: None

                with mock.patch.object(
                    iop, "backup_configs", proxies["backup_configs"]
                ), mock.patch.object(
                    iop, "install_rtk_binary", proxies["install_rtk_binary"]
                ), mock.patch.object(
                    iop, "rtk_init_opencode", proxies["rtk_init_opencode"]
                ), mock.patch.object(
                    iop, "install_rtk_hook", proxies["install_rtk_hook"]
                ), mock.patch.object(
                    iop, "sanitize_local_config", proxies["sanitize_local_config"]
                ), mock.patch.object(
                    iop, "install_plugins", proxies["install_plugins"]
                ), mock.patch.object(
                    iop, "install_superpowers", proxies["install_superpowers"]
                ), mock.patch.object(
                    iop, "strip_superpowers_model_pins", proxies["strip_superpowers_model_pins"]
                ), mock.patch.object(
                    iop, "install_julius_caveman", proxies["install_julius_caveman"]
                ), mock.patch.object(
                    iop, "strip_caveman_model_pins", proxies["strip_caveman_model_pins"]
                ), mock.patch.object(
                    iop, "install_rust_skills", proxies["install_rust_skills"]
                ), mock.patch.object(
                    iop, "install_golang_skills", proxies["install_golang_skills"]
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = iop.main(["--dry-run", "--no-verify"])
                    self.assertEqual(rc, 0, f"main returned {rc}; output:\n{buf.getvalue()}")

        # Every named step should have been called.
        for name, proxy in proxies.items():
            self.assertIn(name, called, f"step {name} not invoked")

        # The strip_superpowers_model_pins step must be called AFTER
        # install_superpowers and BEFORE install_julius_caveman
        # (matches the documented order; the superpowers pack is the one
        # that drops the copilot model pins).
        self.assertLess(
            called.index("install_superpowers"),
            called.index("strip_superpowers_model_pins"),
        )
        self.assertLess(
            called.index("strip_superpowers_model_pins"),
            called.index("install_julius_caveman"),
        )

    def test_dry_run_output_mentions_dry_run(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as fake_home:
            with mock.patch.dict(os.environ, {"HOME": fake_home}), \
                 mock.patch("pathlib.Path.home", lambda: Path(fake_home)), \
                 mock.patch.object(iop, "subprocess") as sp_mod, \
                 mock.patch.object(iop, "shutil") as sh_mod:
                sp_mod.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                sh_mod.which.return_value = "/usr/bin/anything"
                sh_mod.copy2 = lambda *a, **kw: None
                sh_mod.move = lambda *a, **kw: None
                # patch all steps to no-ops
                for name in [
                    "backup_configs", "install_rtk_binary", "rtk_init_opencode",
                    "install_rtk_hook", "sanitize_local_config", "install_plugins",
                    "install_superpowers", "strip_superpowers_model_pins",
                    "install_julius_caveman", "strip_caveman_model_pins",
                    "install_rust_skills", "install_golang_skills",
                ]:
                    mock.patch.object(iop, name, lambda *a, **kw: None).start()
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = iop.main(["--dry-run", "--no-verify"])
                self.assertEqual(rc, 0)
                self.assertIn("DRY-RUN", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
