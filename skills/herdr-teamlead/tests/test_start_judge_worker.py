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

    def write_config(self, model, effort, kind, judge=None, agent_kind=None):
        """The current operator config start-judge reads: one judge agent and its block."""
        config = {"schema_version": 2, "agents": [{
            "name": "judge", "kind": agent_kind or kind, "usage_prompt": "/usage", "usage_marker": "Current week",
            "usage_read_source": "visible", "clear_prompt": "/clear"}]}
        block = {"agent": "judge", "model": model}
        if effort is not None:
            block["effort"] = effort
        config["judge"] = block if judge is None else judge
        self.config.write_text(json.dumps(config), encoding="utf-8")

    def run_launcher(self, model="claude-fable-5-1", effort: str | None = "max", kind="claude", launch_args=None,
                     config_judge=None, config_kind=None, write_config=True, **env):
        self.log.unlink(missing_ok=True)
        self.config = self.root / "config.json"
        if write_config:
            self.write_config(model, effort, kind, config_judge, config_kind)
        launch_state = self.root / ("state-" + str(len(list(self.root.glob("state-*.retrospectives")))) + ".json")
        # The plan carries the seat's declared mode (#425); these fixtures
        # exercise adjudication, which carries no assessment requirement.
        self.plan.write_text(json.dumps({"schema_version": 3, "assignments": {"judge": "judge"},
            "judge": {"agent": "judge", "model": model, "effort": effort, "mode": "adjudication",
                      "launch_args": launch_args or []}}), encoding="utf-8")
        return subprocess.run(["bash", str(SUT), str(self.plan), "w1:p2", kind, "--config", str(self.config),
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

    def test_each_supported_cli_on_a_pinned_judge(self):
        for model, effort, kind in (("claude-opus-5-5", "high", "claude"),
                                    ("gpt-6.1-sol", "ultra", "codex"), ("grok-4.6", "high", "grok")):
            with self.subTest(kind=kind):
                result = self.run_launcher(model, effort, kind)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["effort"], effort)
                expected = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox", "grok": "--always-approve"}[kind]
                self.assertIn(expected, json.loads(result.stdout)["verified"]["argv"])

    def test_a_plan_that_differs_from_the_current_judge_config_never_starts(self):
        current = {"agent": "judge", "model": "claude-opus-5", "effort": "max"}
        cases: tuple[tuple[str, dict[str, str] | None, str | None], ...] = (
            ("model", current, None),
            ("effort", dict(current, model="claude-fable-5-1", effort="high"), None),
            ("agent", dict(current, agent="other", model="claude-fable-5-1"), None),
            ("kind", None, "codex"))
        for label, config_judge, config_kind in cases:
            with self.subTest(differs=label):
                result = self.run_launcher(config_judge=config_judge, config_kind=config_kind)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("config_error", result.stderr)
                self.assertFalse(self.log.exists(), "no worker may start on a stale judge plan")

    def test_a_judge_below_the_judgment_floor_never_starts(self):
        for model, effort in (("claude-haiku-4-5", None), ("sonnet-5", "max"), ("claude-opus-5", "medium")):
            with self.subTest(model=model, effort=effort):
                result = self.run_launcher(model, effort)
                self.assertEqual(result.returncode, 1)
                self.assertIn("top_models.claude", result.stderr)
                self.assertFalse(self.log.exists())

    def test_a_missing_config_or_judge_block_never_starts(self):
        result = self.run_launcher(write_config=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("config", result.stderr)
        self.assertFalse(self.log.exists())
        self.write_config("claude-fable-5-1", "max", "claude")
        config = json.loads(self.config.read_text())
        del config["judge"]
        self.config.write_text(json.dumps(config), encoding="utf-8")
        result = self.run_launcher(write_config=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("judge", result.stderr)
        self.assertFalse(self.log.exists())

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

    def test_a_plan_with_no_declared_mode_never_starts(self):
        # coding-policy#425: an undeclared mode is refused, never defaulted —
        # defaulting would pick which of the two gates the seat is held to.
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.plan.read_text())
        del plan["judge"]["mode"]
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        self.log.unlink(missing_ok=True)
        launch_state = self.root / "state-no-mode.json"
        result = subprocess.run(["bash", str(SUT), str(self.plan), "w1:p2", "claude",
                                 "--config", str(self.root / "config.json"),
                                 "--state", str(launch_state), "--now", "2026-09-09T10:00:00+00:00",
                                 "--task", "judge-fixture"],
            env={**os.environ, "HERDR_BIN": str(self.fake), "FAKE_LOG": str(self.log)},
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("re-plan with --judge-mode", result.stdout + result.stderr)
        self.assertFalse(self.log.exists(), "no worker may be started without a declared mode")

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
