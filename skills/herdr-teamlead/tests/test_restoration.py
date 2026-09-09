"""The restoration helper waits for a released pane and name, then starts once.

Every scenario drives the real transport (`HerdrClient`) through a scripted
runner, so the `agent_not_found` / `agent_name_taken` branches are proven on
Herdr's actual stderr JSON shape rather than on a hand-built exception. Sleep
is injected and recorded; nothing here waits on a clock.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import contextlib
import io
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from teamlead import cli, restoration
from teamlead.errors import HerdrError, UsageError
from teamlead.herdr import HerdrClient, error_code
from teamlead.restoration import NAME_TAKEN_RETRIES, RELEASE_POLL_ATTEMPTS, RELEASE_POLL_INTERVAL_SEC, restore
from tests.fakes import FakeCompleted

#: Synthetic identifiers; no real session, pane, or process.
SESSION = "0f5c8e1a-3d4b-4c2e-9a7f-1b2c3d4e5f60"
OTHER_SESSION = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"
NAME, KIND, PANE = "codex-review", "codex", "w7:p1"
SHELL, STOPPED, NEW_PID, FOREIGN = 20782, 22241, 81605, 9999
TOKENS = ["resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-m", "gpt-6-astra", "-c", "model_reasoning_effort=ultra"]
CLAUDE_TOKENS = ["--resume", SESSION, "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]


def process_info(*pids, shell=SHELL, pane=PANE):
    """A `pane process-info` body whose foreground list holds exactly `pids`."""
    names = {SHELL: "zsh", STOPPED: KIND, NEW_PID: KIND}
    processes = [{"pid": pid, "name": names.get(pid, "other"), "argv": [names.get(pid, "other")]} for pid in pids]
    return json.dumps({"id": "cli:pane:process_info", "result": {"process_info": {
        "pane_id": pane, "shell_pid": shell, "foreground_process_group_id": shell, "foreground_processes": processes}}})


def agent_record(pane=PANE, status="idle", name=NAME, kind=KIND):
    return {"agent": kind, "name": name, "agent_status": status, "pane_id": pane, "terminal_id": "term-7", "workspace_id": "w7"}


def herdr_error(code, message="refused"):
    return json.dumps({"error": {"code": code, "message": message}, "id": "cli:test"})


def ok(stdout):
    return FakeCompleted(0, stdout, "")


def failed(code, message="refused"):
    return FakeCompleted(1, "", herdr_error(code, message))


def reserved(pane=PANE):
    return ok(json.dumps({"id": "cli:agent:get", "result": {"type": "agent_info", "agent": agent_record(pane=pane)}}))


def started(argv=None, **record):
    argv = [KIND] + TOKENS if argv is None else argv
    return ok(json.dumps({"id": "cli:agent:start", "result": {"type": "agent_started", "agent": agent_record(**record), "argv": argv}}))


SHELL_ONLY = ok(process_info(SHELL))
STOPPING = ok(process_info(STOPPED))
RELEASED = failed("agent_not_found", "agent target codex-review not found")


class ScriptedRunner:
    """Responses keyed by argv prefix, each key yielding in order, the last repeating."""

    def __init__(self):
        self.scripts = {}
        self.calls = []

    def script(self, prefix, *responses):
        self.scripts[prefix] = list(responses)
        return self

    def __call__(self, argv):
        self.calls.append(list(argv))
        joined = shlex.join(argv[1:])
        matches = [prefix for prefix in self.scripts if joined.startswith(prefix)]
        if not matches:
            raise AssertionError("unscripted herdr call: " + joined)
        responses = self.scripts[max(matches, key=len)]
        return responses.pop(0) if len(responses) > 1 else responses[0]

    def commands(self):
        return [shlex.join(argv[1:]) for argv in self.calls]

    def starts(self):
        return [argv for argv in self.calls if argv[1:3] == ["agent", "start"]]

    def count(self, prefix):
        return sum(1 for command in self.commands() if command.startswith(prefix))


class RestorationCase(unittest.TestCase):
    def setUp(self):
        self.runner = ScriptedRunner()
        self.client = HerdrClient(binary="herdr", runner=self.runner)
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def ready(self):
        self.runner.script("pane process-info", SHELL_ONLY).script("agent get", RELEASED).script("agent start", started())
        return self

    def restore(self, tokens=TOKENS, **overrides):
        kwargs = dict(name=NAME, kind=KIND, pane=PANE, tokens=tokens, shell_pid=SHELL, stopped_pid=STOPPED, sleep=self.sleep)
        kwargs.update(overrides)
        return restore(self.client, **kwargs)

    def assertNoStart(self):
        self.assertEqual(self.runner.starts(), [])


class ReleaseGateTest(RestorationCase):
    def test_delayed_shell_release_waits_then_starts_once(self):
        self.runner.script("pane process-info", STOPPING, ok(process_info()), SHELL_ONLY)
        self.runner.script("agent get", RELEASED).script("agent start", started())
        result = self.restore()
        self.assertEqual(result["release"], {"attempts": 3, "shell_pid": SHELL})
        self.assertEqual(result["start"], {"attempts": 1, "name_taken_retries": 0})
        self.assertEqual(self.sleeps, [RELEASE_POLL_INTERVAL_SEC] * 2)
        self.assertEqual(len(self.runner.starts()), 1)
        self.assertEqual(result["argv"], [KIND] + TOKENS)
        self.assertEqual(result["started"]["pane_id"], PANE)

    def test_delayed_name_release_waits_then_starts_once(self):
        self.runner.script("pane process-info", SHELL_ONLY)
        self.runner.script("agent get", reserved(), reserved(), RELEASED).script("agent start", started())
        result = self.restore()
        self.assertEqual(result["release"]["attempts"], 3)
        self.assertEqual(self.sleeps, [RELEASE_POLL_INTERVAL_SEC] * 2)
        self.assertEqual(len(self.runner.starts()), 1)

    def test_both_predicates_must_hold_on_the_same_read(self):
        # The shell appears on read 1 and regresses on read 2 while the name
        # releases on read 2; only read 3 satisfies both at once.
        self.runner.script("pane process-info", SHELL_ONLY, STOPPING, SHELL_ONLY)
        self.runner.script("agent get", reserved(), RELEASED, RELEASED).script("agent start", started())
        result = self.restore()
        self.assertEqual(result["release"]["attempts"], 3)
        self.assertEqual(len(self.runner.starts()), 1)

    def test_release_timeout_names_the_pending_pane_and_never_starts(self):
        self.runner.script("pane process-info", STOPPING).script("agent get", RELEASED)
        with self.assertRaisesRegex(HerdrError, r"exhausted after {} reads: pane w7:p1 still runs the stopped process 22241\. No start".format(RELEASE_POLL_ATTEMPTS)):
            self.restore()
        self.assertNoStart()
        self.assertEqual(self.runner.count("pane process-info"), RELEASE_POLL_ATTEMPTS)
        self.assertEqual(self.sleeps, [RELEASE_POLL_INTERVAL_SEC] * (RELEASE_POLL_ATTEMPTS - 1))

    def test_release_timeout_names_the_reserved_name_and_never_starts(self):
        self.runner.script("pane process-info", SHELL_ONLY).script("agent get", reserved())
        with self.assertRaisesRegex(HerdrError, r"Herdr still reserves the agent name 'codex-review'"):
            self.restore()
        self.assertNoStart()
        self.assertEqual(self.runner.count("agent get"), RELEASE_POLL_ATTEMPTS)

    def test_foreign_foreground_process_refuses_without_start(self):
        for pids in ((SHELL, FOREIGN), (FOREIGN,), (STOPPED, FOREIGN)):
            with self.subTest(pids=pids):
                self.setUp()
                self.runner.script("pane process-info", ok(process_info(*pids))).script("agent get", RELEASED)
                with self.assertRaisesRegex(HerdrError, r"foreground process 9999 that is neither its shell 20782 nor the stopped process 22241"):
                    self.restore()
                self.assertNoStart()
                self.assertEqual(self.sleeps, [])

    def test_changed_shell_pid_refuses_without_start(self):
        self.runner.script("pane process-info", ok(process_info(30000, shell=30000))).script("agent get", RELEASED)
        with self.assertRaisesRegex(HerdrError, r"shell PID 30000 instead of the archived 20782; the pane was replaced"):
            self.restore()
        self.assertNoStart()

    def test_name_bound_to_another_pane_refuses_without_start(self):
        self.runner.script("pane process-info", SHELL_ONLY).script("agent get", reserved(pane="w9:p1"))
        with self.assertRaisesRegex(HerdrError, r"bound to pane 'w9:p1', not the restoration pane 'w7:p1'"):
            self.restore()
        self.assertNoStart()

    def test_malformed_herdr_data_refuses_before_any_start(self):
        cases = (
            ("non-JSON process info", "pane process-info", ok("not json"), "non-JSON"),
            ("process info for another pane", "pane process-info", ok(process_info(SHELL, pane="w9:p1")), "no process information"),
            ("missing shell pid", "pane process-info", ok(json.dumps({"result": {"process_info": {"pane_id": PANE, "foreground_processes": []}}})), "no shell PID"),
            ("foreground not a list", "pane process-info", ok(json.dumps({"result": {"process_info": {"pane_id": PANE, "shell_pid": SHELL, "foreground_processes": {}}}})), "no foreground process list"),
            ("foreground entry without pid", "pane process-info", ok(json.dumps({"result": {"process_info": {"pane_id": PANE, "shell_pid": SHELL, "foreground_processes": [{"name": "zsh"}]}}})), "malformed foreground process record"),
            ("boolean pid", "pane process-info", ok(json.dumps({"result": {"process_info": {"pane_id": PANE, "shell_pid": SHELL, "foreground_processes": [{"pid": True}]}}})), "malformed foreground process record"),
            ("agent get without a record", "agent get", ok(json.dumps({"result": {"type": "agent_info"}})), "no agent record"),
            ("agent get without result", "agent get", ok(json.dumps({"id": "x"})), "without a `result` field"),
        )
        for label, prefix, response, pattern in cases:
            with self.subTest(case=label):
                self.setUp()
                self.ready().runner.script(prefix, response)
                with self.assertRaisesRegex(HerdrError, pattern):
                    self.restore()
                self.assertNoStart()

    def test_unknown_name_lookup_failures_propagate_without_start(self):
        for stderr in (herdr_error("herdr_unavailable", "socket closed"), "plain text failure"):
            with self.subTest(stderr=stderr):
                self.setUp()
                self.runner.script("pane process-info", SHELL_ONLY).script("agent get", FakeCompleted(1, "", stderr))
                with self.assertRaisesRegex(HerdrError, r"herdr failed running `herdr agent get codex-review`"):
                    self.restore()
                self.assertNoStart()
                self.assertEqual(self.sleeps, [])


class StartTest(RestorationCase):
    def test_transient_name_taken_rechecks_release_then_starts_once_more(self):
        self.ready().runner.script("agent start", failed("agent_name_taken", "name in use"), started())
        result = self.restore()
        self.assertEqual(result["start"], {"attempts": 2, "name_taken_retries": 1})
        self.assertEqual(len(self.runner.starts()), 2)
        commands = self.runner.commands()
        first, second = [index for index, command in enumerate(commands) if command.startswith("agent start")]
        between = commands[first + 1:second]
        self.assertTrue(any(command.startswith("pane process-info") for command in between))
        self.assertTrue(any(command.startswith("agent get") for command in between))

    def test_name_taken_retries_are_bounded(self):
        self.ready().runner.script("agent start", failed("agent_name_taken"))
        with self.assertRaisesRegex(HerdrError, r"refused the name 'codex-review' {} times".format(NAME_TAKEN_RETRIES + 1)):
            self.restore()
        self.assertEqual(len(self.runner.starts()), NAME_TAKEN_RETRIES + 1)

    def test_name_taken_followed_by_an_occupied_pane_refuses_a_second_start(self):
        # The refusal lied, or a start landed anyway: the recheck sees a new
        # foreground process and the helper must not start a duplicate.
        self.ready().runner.script("agent start", failed("agent_name_taken"))
        self.runner.script("pane process-info", SHELL_ONLY, ok(process_info(NEW_PID)))
        with self.assertRaisesRegex(HerdrError, r"foreground process 81605 that is neither"):
            self.restore()
        self.assertEqual(len(self.runner.starts()), 1)

    def test_other_start_failures_are_never_retried(self):
        for label, response, pattern in (
            ("blocked during startup", failed("agent_not_ready", "agent blocked"), r"failed with agent_not_ready; a process may already be running"),
            ("no error code", FakeCompleted(1, "", "boom"), r"failed with no Herdr error code; a process may already be running"),
            ("syntax error", FakeCompleted(2, "", "unknown flag"), r"failed with no Herdr error code"),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.ready().runner.script("agent start", response, started())
                with self.assertRaisesRegex(HerdrError, pattern):
                    self.restore()
                self.assertEqual(len(self.runner.starts()), 1)

    def test_a_started_identity_or_argv_mismatch_is_never_retried(self):
        for label, response, pattern in (
            ("other pane", started(pane="w9:p1"), r"identity or readiness differs"),
            ("other name", started(name="codex"), r"identity or readiness differs"),
            ("other kind", started(kind="claude"), r"identity or readiness differs"),
            ("not ready", started(status="working"), r"identity or readiness differs"),
            ("no agent record", ok(json.dumps({"result": {"argv": [KIND] + TOKENS}})), r"identity or readiness differs"),
            ("dropped effort", started(argv=[KIND] + TOKENS[:-2]), r"instead of the requested resume argv"),
            ("other session", started(argv=[KIND] + [TOKENS[0], OTHER_SESSION] + TOKENS[2:]), r"instead of the requested resume argv"),
            ("reordered flags", started(argv=[KIND, TOKENS[0], TOKENS[2], TOKENS[1]] + TOKENS[3:]), r"instead of the requested resume argv"),
            ("other executable", started(argv=["claude"] + TOKENS), r"instead of the requested resume argv"),
            ("no argv", ok(json.dumps({"result": {"agent": agent_record()}})), r"instead of the requested resume argv"),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.ready().runner.script("agent start", response, started())
                with self.assertRaisesRegex(HerdrError, pattern):
                    self.restore()
                self.assertEqual(len(self.runner.starts()), 1)

    def test_exact_argv_preservation(self):
        for kind, tokens in ((KIND, TOKENS), ("claude", CLAUDE_TOKENS), ("grok", ["--resume", SESSION, "--always-approve", "--no-subagents"])):
            with self.subTest(kind=kind):
                self.setUp()
                self.ready().runner.script("agent start", started(argv=["/opt/homebrew/bin/" + kind] + tokens, kind=kind))
                result = self.restore(tokens=tokens, kind=kind)
                self.assertEqual(self.runner.starts(), [["herdr", "agent", "start", NAME, "--kind", kind, "--pane", PANE, "--", *tokens]])
                self.assertEqual(result["argv"], ["/opt/homebrew/bin/" + kind] + tokens)
                self.assertEqual(result["kind"], kind)


class InputTest(RestorationCase):
    def test_resume_argv_is_validated_before_any_herdr_call(self):
        for kind, tokens in (
            (KIND, ["resume", SESSION]),
            (KIND, ["resume", "--last", "--dangerously-bypass-approvals-and-sandbox"]),
            (KIND, ["resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-s", "read-only"]),
            ("claude", ["--continue", "--dangerously-skip-permissions"]),
            ("claude", ["--resume", SESSION]),
            ("grok", ["--resume", SESSION, "--fork-session", "--always-approve"]),
            (KIND, ["--", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox"]),
        ):
            with self.subTest(kind=kind, tokens=tokens):
                self.setUp()
                with self.assertRaisesRegex(HerdrError, r"Resume argv refused before any Herdr call"):
                    self.restore(tokens=tokens, kind=kind)
                self.assertEqual(self.runner.calls, [])

    def test_missing_or_conflicting_inputs_refuse_without_herdr_calls(self):
        for label, overrides in (
            ("no tokens", {"tokens": []}),
            ("empty token", {"tokens": ["resume", "", SESSION]}),
            ("shell is the stopped pid", {"stopped_pid": SHELL}),
            ("zero shell pid", {"shell_pid": 0}),
            ("boolean stopped pid", {"stopped_pid": True}),
            ("empty name", {"name": " "}),
            ("empty pane", {"pane": ""}),
        ):
            with self.subTest(case=label):
                self.setUp()
                with self.assertRaises(UsageError):
                    self.restore(**overrides)
                self.assertEqual(self.runner.calls, [])

    def test_error_code_reads_herdr_stderr_json(self):
        self.assertEqual(error_code(HerdrError("m", {"stderr": herdr_error("agent_not_found")})), "agent_not_found")
        for details in ({"stderr": "plain text"}, {"stderr": '{"error": "x"}'}, {"stderr": '{"error": {"code": ""}}'}, {"stderr": "{not json"}, {}, None):
            with self.subTest(details=details):
                self.assertIsNone(error_code(HerdrError("m", details)))
        self.assertIsNone(error_code(ValueError("no details")))


class CliTest(RestorationCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="teamlead-restore-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "xdg"), "XDG_CONFIG_HOME": str(self.tmp / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)

    def argv(self, *tokens):
        return ["restore-session", "--agent", NAME, "--kind", KIND, "--pane", PANE, "--shell-pid", str(SHELL), "--stopped-pid", str(STOPPED), "--", *tokens]

    def test_success_prints_json_and_touches_no_owner_state(self):
        self.ready()
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(self.argv(*TOKENS), stdout=out, stderr=err, client=self.client)
        self.assertEqual((code, err.getvalue()), (0, ""))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["argv"], [KIND] + TOKENS)
        self.assertEqual(payload["agent"], NAME)
        self.assertEqual(payload["release"], {"attempts": 1, "shell_pid": SHELL})
        self.assertEqual(self.runner.starts(), [["herdr", "agent", "start", NAME, "--kind", KIND, "--pane", PANE, "--", *TOKENS]])
        self.assertFalse((self.tmp / "xdg").exists())

    def test_failure_is_a_json_error_with_exit_one(self):
        self.runner.script("pane process-info", SHELL_ONLY).script("agent get", failed("herdr_unavailable"))
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(self.argv(*TOKENS), stdout=out, stderr=err, client=self.client)
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertEqual(json.loads(err.getvalue())["error"], "herdr_error")
        self.assertNoStart()

    def test_non_object_lookup_results_return_actionable_json_without_side_effects(self):
        owner = self.tmp / "owner.json"
        owner_bytes = b'{"preserved": "synthetic owner history"}\n'
        owner.write_bytes(owner_bytes)
        args = ["restore-session", "--state", str(owner), *self.argv(*TOKENS)[1:]]
        for operation in ("pane process-info", "agent get"):
            for value in (None, [], "unexpected", 7, False):
                with self.subTest(operation=operation, value=value):
                    self.runner = ScriptedRunner()
                    self.client = HerdrClient(binary="herdr", runner=self.runner)
                    self.ready().runner.script(operation, ok(json.dumps({"result": value})))
                    out, err = io.StringIO(), io.StringIO()
                    with patch("teamlead.cli.time.sleep", side_effect=self.sleep):
                        code = cli.main(args, stdout=out, stderr=err, client=self.client)
                    self.assertEqual((code, out.getvalue()), (1, ""))
                    failure = json.loads(err.getvalue())
                    self.assertEqual(failure["error"], "herdr_error")
                    self.assertIn(operation, failure["message"])
                    self.assertIn("result", failure["message"])
                    self.assertIn("object", failure["message"])
                    self.assertIn("inspect", failure["message"].lower())
                    self.assertIn("before retrying", failure["message"])
                    self.assertIn(operation, failure["details"]["command"])
                    self.assertNoStart()
                    self.assertEqual(self.runner.count(operation), 1)
                    self.assertEqual(self.sleeps, [])
                    self.assertEqual(owner.read_bytes(), owner_bytes)
                    self.assertEqual(list(self.tmp.iterdir()), [owner])

    def test_valid_lookup_objects_wait_for_name_release_then_start_once(self):
        self.ready().runner.script("agent get", reserved(), RELEASED)
        out, err = io.StringIO(), io.StringIO()
        with patch("teamlead.cli.time.sleep", side_effect=self.sleep):
            code = cli.main(self.argv(*TOKENS), stdout=out, stderr=err, client=self.client)
        self.assertEqual((code, err.getvalue()), (0, ""))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["release"], {"attempts": 2, "shell_pid": SHELL})
        self.assertEqual(payload["start"], {"attempts": 1, "name_taken_retries": 0})
        self.assertEqual(payload["argv"], [KIND] + TOKENS)
        self.assertEqual(len(self.runner.starts()), 1)
        self.assertEqual(self.sleeps, [RELEASE_POLL_INTERVAL_SEC])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_unknown_start_results_keep_uncertainty_guidance_and_never_retry(self):
        for value in (None, [], "unexpected", 7, False, {}):
            with self.subTest(value=value):
                self.runner = ScriptedRunner()
                self.client = HerdrClient(binary="herdr", runner=self.runner)
                self.ready().runner.script("agent start", ok(json.dumps({"result": value})), started())
                out, err = io.StringIO(), io.StringIO()
                code = cli.main(self.argv(*TOKENS), stdout=out, stderr=err, client=self.client)
                self.assertEqual((code, out.getvalue()), (1, ""))
                failure = json.loads(err.getvalue())
                self.assertEqual(failure["error"], "herdr_error")
                self.assertIn("a process may now be running", failure["message"])
                self.assertIn("start was not retried", failure["message"])
                self.assertEqual(len(self.runner.starts()), 1)
                self.assertEqual(list(self.tmp.iterdir()), [])

    def test_refused_argv_never_reaches_herdr(self):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(self.argv("resume", SESSION), stdout=out, stderr=err, client=self.client)
        self.assertEqual(code, 1)
        self.assertIn("Resume argv refused before any Herdr call", json.loads(err.getvalue())["message"])
        self.assertEqual(self.runner.calls, [])

    def test_missing_required_options_exit_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(["restore-session", "--agent", NAME, "--kind", KIND, "--pane", PANE, "--", *TOKENS])
        self.assertEqual(caught.exception.code, 2)

    def test_run_command_strips_a_leading_separator(self):
        self.ready()
        args = SimpleNamespace(agent=NAME, kind=KIND, pane=PANE, shell_pid=SHELL, stopped_pid=STOPPED, resume_argv=["--", *TOKENS])
        result = restoration.run_command(args, self.client, sleep=self.sleep)
        self.assertEqual(result["argv"], [KIND] + TOKENS)
        self.assertEqual(self.runner.starts()[0][-len(TOKENS):], TOKENS)


if __name__ == "__main__":
    unittest.main()
