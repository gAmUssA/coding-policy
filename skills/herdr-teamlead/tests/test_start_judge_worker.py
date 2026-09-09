"""Exercise the shell entrypoint against Herdr's structured launch response."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SUT = Path(__file__).resolve().parents[1] / "start-judge-worker.sh"


class JudgeLauncherTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = self.root / "plan.json"
        self.log = self.root / "calls.json"
        self.fake = self.root / "herdr"
        self.fake.write_text("#!" + sys.executable + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
launch_file = Path(os.environ["FAKE_LOG"])
if os.environ.get("FAKE_FAIL"):
    sys.exit(7)
if args[:2] == ["pane", "process-info"]:
    pane = args[args.index("--pane") + 1]
    if launch_file.exists():
        started = json.loads(launch_file.read_text())
        kind = started[started.index("--kind") + 1]
        process = {"name": kind, "pid": 200, "argv": [kind] + started[started.index("--") + 1:]}
    else:
        process = {"name": "zsh", "pid": 100, "argv": ["zsh"]}
    print(json.dumps({"result": {"process_info": {"pane_id": pane, "shell_pid": 100, "foreground_processes": [process]}}}))
    sys.exit(0)
if args[:2] == ["agent", "get"]:
    started = json.loads(launch_file.read_text())
    print(json.dumps({"result": {"agent": {"name": started[2], "agent": started[started.index("--kind") + 1],
        "pane_id": started[started.index("--pane") + 1], "agent_status": "idle"}}}))
    sys.exit(0)
launch_file.write_text(json.dumps(args))
kind = args[args.index("--kind") + 1]
pane = args[args.index("--pane") + 1]
argv = [kind] + args[args.index("--") + 1:]
if os.environ.get("FAKE_ARGV"):
    argv = json.loads(os.environ["FAKE_ARGV"])
print(json.dumps({"result": {"agent": {"name": args[2], "agent": kind,
    "pane_id": pane, "agent_status": "idle"}, "argv": argv}}))
''', encoding="utf-8")
        self.fake.chmod(0o755)

    def run_launcher(self, model="claude-fable-5-1", effort: str | None = "max", kind="claude", launch_args=None, **env):
        self.log.unlink(missing_ok=True)
        launch_state = self.root / ("state-" + str(len(list(self.root.glob("state-*.retrospectives")))) + ".json")
        self.plan.write_text(json.dumps({"schema_version": 3, "assignments": {"judge": "judge"},
            "judge": {"agent": "judge", "model": model, "effort": effort, "launch_args": launch_args or []}}), encoding="utf-8")
        return subprocess.run(["bash", str(SUT), str(self.plan), "w1:p2", kind,
                               "--state", str(launch_state), "--now", "2026-09-09T10:00:00+00:00", "--task", "judge-fixture"],
            env={**os.environ, "HERDR_BIN": str(self.fake), "FAKE_LOG": str(self.log), **env},
            capture_output=True, text=True, check=False)

    def test_launch_proves_model_and_effort_without_a_banner(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertTrue(proof["argv_verified"])
        self.assertEqual(proof["verified"]["argv"], ["claude", "--dangerously-skip-permissions", "--model", "claude-fable-5-1", "--effort", "max"])
        self.assertEqual(json.loads(self.log.read_text())[7:], ["--", "--dangerously-skip-permissions", "--model", "claude-fable-5-1", "--effort", "max"])

    def test_no_effort_model_and_each_supported_cli(self):
        for model, effort, kind in (("claude-haiku-4-5", None, "claude"),
                                    ("gpt-5.6-sol", "xhigh", "codex"), ("grok-4.6", "high", "grok")):
            with self.subTest(kind=kind):
                result = self.run_launcher(model, effort, kind)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["effort"], effort)
                expected = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox", "grok": "--always-approve"}[kind]
                self.assertIn(expected, json.loads(result.stdout)["verified"]["argv"])

    def test_judge_restrictive_launch_options_refuse_before_transport(self):
        result = self.run_launcher(launch_args=["--permission-mode", "plan"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("required YOLO mode", result.stderr)
        self.assertFalse(self.log.exists())

    def test_judge_losing_only_yolo_flag_is_unproven(self):
        result = self.run_launcher(FAKE_ARGV=json.dumps(["claude", "--model", "claude-fable-5-1", "--effort", "max"]))
        self.assertEqual(result.returncode, 1)
        self.assertIn("launch options", result.stderr)

    def test_missing_different_duplicate_and_transcript_arguments_refuse(self):
        for argv in (["claude", "--model", "claude-fable-5-1"],
                     ["claude", "--model", "claude-fable-5-1", "--effort", "xhigh"],
                     ["claude", "--model", "claude-fable-5-10", "--effort", "max"],
                     ["claude", "--model", "claude-fable-5-1", "--effort", "max", "--effort", "low"],
                     "Claude Code claude-fable-5-1 max"):
            with self.subTest(argv=argv):
                result = self.run_launcher(FAKE_ARGV=json.dumps(argv))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_invalid_config_never_starts(self):
        for model, effort, kind in (("", "max", "claude"), ("opus-5", "invalid", "claude"),
                                   ("model", "high", "unsupported")):
            with self.subTest(kind=kind):
                result = self.run_launcher(model, effort, kind)
                self.assertEqual(result.returncode, 2 if kind == "unsupported" else 1)
                self.assertFalse(self.log.exists())

    def test_transport_error_and_usage_fail_loudly(self):
        result = self.run_launcher(FAKE_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("herdr", result.stderr)
        usage = subprocess.run(["bash", str(SUT)], capture_output=True, text=True, check=False)
        self.assertEqual(usage.returncode, 2)
        self.assertIn("usage", usage.stderr)


if __name__ == "__main__":
    unittest.main()
