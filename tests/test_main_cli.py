"""
Phase 2 CLI tests: main.py as the smallest real user-text entry point.
All subprocesses run with cwd in a temp dir (workspace/ artifacts stay out of
the repo). No API key is ever required by these tests; the no-key path must
fail honestly with exit code 2 and create nothing.
Run with: python3 -m unittest tests.test_main_cli -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "main.py")


def run_cli(args, cwd, env_overrides=None, stdin_text=None, timeout=120):
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, MAIN] + args,
        cwd=cwd, env=env, input=stdin_text, capture_output=True, text=True, timeout=timeout,
    )


class MainCliTestBase(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp(prefix="omega_cli_test_")

    def tearDown(self):
        shutil.rmtree(self.cwd, ignore_errors=True)


class TestNoKeyHonesty(MainCliTestBase):
    def test_argv_request_without_key_exits_2_and_creates_no_workspace(self):
        r = run_cli(["Build me a calculator app"], self.cwd)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MissingAPIKeyError", r.stdout)
        self.assertIn("No fallback", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "workspace")),
                         "nothing may be created when intake cannot run")

    def test_stdin_request_without_key_exits_2(self):
        r = run_cli([], self.cwd, stdin_text="Build me a calculator app\n")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("MissingAPIKeyError", r.stdout)

    def test_empty_invocation_prints_usage_exit_2(self):
        r = run_cli([], self.cwd, stdin_text="\n")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)

    def test_oversized_request_rejected_before_any_provider_call(self):
        r = run_cli(["x" * 1200], self.cwd)
        self.assertEqual(r.returncode, 2)
        self.assertIn("REQUEST NOT UNDERSTOOD", r.stdout)


class TestBenchmarkPathIntact(MainCliTestBase):
    def test_benchmark_mode_still_runs_legacy_demo(self):
        r = run_cli(["benchmark"], self.cwd, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("BENCHMARK SUMMARY", r.stdout)
        self.assertIn("[COMPLETED]", r.stdout)


class TestIntakePathBoundaries(MainCliTestBase):
    def test_intake_error_path_reports_and_exits_2(self):
        """With a fake key but a provider that cannot produce intake JSON
        (offline: no network here -> NetworkError path is honest failure)."""
        r = run_cli(["Build me a calculator app"], self.cwd,
                    env_overrides={"GEMINI_API_KEY": "gkey-FAKE-ui-not-real-" + "0" * 16},
                    stdin_text="", timeout=90)
        # allowed outcomes: NetworkError/timeout-style honest failure -> exit 2
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "workspace")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
