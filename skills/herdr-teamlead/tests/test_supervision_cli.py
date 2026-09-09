"""Public CLI dispatch enrollment and offline supervision with fake workers."""

import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import io
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from teamlead import cli, supervision
from teamlead.errors import StateError
from teamlead.state import state_lock
from tests import test_cli as fixture
from tests.fakes import FakeRunner

AT = fixture.AT
TASK = "fleet-task"


class SupervisionCliTest(fixture.CliCase):
    _client = fixture.ApplyCommandTest._client

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner()
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        self.bindings = supervision.default_state_path().parent / "supervision-bindings"
        self.identity = supervision.identity("lead-native", str(self.tmp), "fixture", pane_id="lead-pane")
        supervision.bind(self.state, self.identity, AT, root=self.bindings)
        self.reports = {role: str(self.tmp / (role + "-report.md")) for role in self.briefs}

    def invoke(self, arguments, client=None):
        self.out, self.err = io.StringIO(), io.StringIO()
        return self.run_cli(self.base() + arguments, client=client)

    def apply_arguments(self, assignments=None, reports=None, task: str | None = TASK, **options):
        assignments = assignments or {"developer": "grok"}
        reports = {role: self.reports[role] for role in assignments} if reports is None else reports
        result = ["apply", "--assignments", json.dumps(assignments), "--common", str(self.common),
                  "--composer-settle", "0", "--now", AT, "--dispatch-id", "dispatch-fixture"]
        if task is not None:
            result += ["--task", task]
        result += self.brief_args(*assignments)
        for role, report in reports.items():
            result += ["--report", role + "=" + report]
        for option, value in options.items():
            result.append("--" + option.replace("_", "-"))
            if value is not True:
                result.append(str(value))
        return result

    def saved(self):
        return supervision.load(self.state)

    def test_all_roles_are_enrolled_before_clear_and_assignment(self):
        assignments = {"developer": "grok", "tester": "claude", "reviewer": "codex"}
        client = self._client({name: "idle" for name in assignments.values()})
        original_runner = client._runner
        writes_checked = []
        def checking_runner(argv):
            if "send-text" in argv or "send-keys" in argv or "prompt" in argv:
                enrolled = {row["assignment"]["agent"] for row in self.saved()["members"] if row["active"]}
                if "grok" in argv or "w4:p1" in argv:
                    self.assertIn("grok", enrolled)
                if "claude" in argv or "w2:p1" in argv:
                    self.assertIn("claude", enrolled)
                if "codex" in argv or "w3:p1" in argv:
                    self.assertIn("codex", enrolled)
                writes_checked.append(argv)
            return original_runner(argv)
        client._runner = checking_runner
        code, out, err = self.invoke(self.apply_arguments(assignments), client)
        self.assertEqual(code, 0, err)
        self.assertTrue(writes_checked)
        self.assertEqual(len(json.loads(out)["applied"]), 3)
        members = self.saved()["members"]
        self.assertEqual({row["id"] for row in members}, {"dispatch-fixture:" + role for role in assignments})
        self.assertEqual({row["assignment"]["report"] for row in members}, set(self.reports.values()))
        self.assertTrue(all(row["active"] for row in members))
        self.assertTrue(all(row["assignment"]["native_session"] is None for row in members), "Outgoing context must not become incoming proof")

    def test_release_assignment_uses_the_same_pre_send_enrollment_contract(self):
        self.briefs["release"] = self.tmp / "release.md"
        self.briefs["release"].write_text("Release the accepted task; make no source changes.")
        self.reports["release"] = str(self.tmp / "release-report.md")
        code, _, err = self.invoke(self.apply_arguments({"release": "grok"}), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        member = self.saved()["members"][0]
        self.assertEqual(member["id"], "dispatch-fixture:release")
        self.assertEqual(member["assignment"]["report"], self.reports["release"])
        self.assertTrue(member["active"])

    def test_pinned_judge_is_enrolled_with_verified_launch_arguments(self):
        self.briefs["judge"] = self.tmp / "judge.md"
        self.briefs["judge"].write_text("Resolve the recorded disputed finding read-only.")
        self.reports["judge"] = str(self.tmp / "judge-report.md")
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        client = self._client({"claude": "idle"}, sessions={"claude": "judge-native"})
        self.runner.set("pane process-info --pane w2:p1", json.dumps({"result": {"process_info": {
            "pane_id": "w2:p1", "foreground_processes": [{"name": "claude", "pid": 200,
            "argv": ["claude", "--dangerously-skip-permissions", "--model", "claude-opus-4-6", "--effort", "high"]}]}}}))
        code, _, err = self.invoke(self.apply_arguments({"judge": "claude"}, no_clear=True), client)
        self.assertEqual(code, 0, err)
        member = self.saved()["members"][0]
        self.assertEqual(member["id"], "dispatch-fixture:judge")
        self.assertEqual(supervision.expected_assignment(member)["native_session"]["value"], "judge-native")

    def test_bound_dry_run_remains_read_only(self):
        before = supervision.store_path(self.state).read_bytes()
        client = self._client({})
        code, out, err = self.invoke(self.apply_arguments(dry_run=True), client)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["dry_run"])
        self.assertEqual(supervision.store_path(self.state).read_bytes(), before)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.runner.calls, [])

    def test_missing_task_or_any_report_refuses_before_all_live_calls(self):
        for task, reports in ((None, {}), (TASK, {}), (TASK, {"developer": self.reports["developer"]})):
            with self.subTest(task=task, reports=reports):
                client = self._client({"grok": "idle", "claude": "idle"})
                code, _, err = self.invoke(self.apply_arguments({"developer": "grok", "tester": "claude"}, reports=reports, task=task), client)
                self.assertEqual(code, 1)
                self.assertIn("--report", err)
                self.assertEqual(self.runner.calls, [])
                self.assertEqual(self.saved()["members"], [])

    def test_bad_report_map_is_rejected_before_input(self):
        bad_maps = ({"developer": "relative.md"}, {"tester": self.reports["tester"]}, {"developer": str(self.tmp)})
        for reports in bad_maps:
            with self.subTest(reports=reports):
                client = self._client({"grok": "idle"})
                code, _, err = self.invoke(self.apply_arguments(reports=reports), client)
                self.assertEqual(code, 1)
                self.assertIn("report", err)
                self.assertEqual(self.runner.calls, [])
        client = self._client({"grok": "idle"})
        code, _, _ = self.invoke(self.apply_arguments() + ["--report", "developer=" + self.reports["developer"]], client)
        self.assertEqual(code, 1)
        self.assertEqual(self.runner.calls, [])

    def test_shared_report_destination_refuses_the_entire_parallel_assignment(self):
        client = self._client({"grok": "idle", "claude": "idle"})
        same = {"developer": self.reports["developer"], "tester": self.reports["developer"]}
        code, _, err = self.invoke(self.apply_arguments({"developer": "grok", "tester": "claude"}, reports=same), client)
        self.assertEqual(code, 1)
        self.assertIn("distinct report", err)
        self.assertEqual(self.runner.calls, [])

    def test_enrollment_write_failure_prevents_even_clear_input(self):
        client = self._client({"grok": "idle"})
        with patch.object(supervision, "enroll", side_effect=StateError("Cannot persist enrollment; restore the owner directory.", {})):
            code, _, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 1)
        self.assertIn("persist enrollment", err)
        self.assertEqual(self.runner.writes(), [])
        self.assertFalse(self.state.exists())

    def test_interrupted_send_remains_active_and_retry_never_resends(self):
        client = self._client({"grok": "idle"})
        with patch("teamlead.assign.send_message", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.invoke(self.apply_arguments(), client)
        member = self.saved()["members"][0]
        self.assertTrue(member["active"])
        dispatch = json.loads(self.state.read_text())["recovery"]["dispatches"][0]
        self.assertEqual(dispatch["status"], "sending")
        client = self._client({"grok": "idle"})
        code, _, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 1)
        self.assertIn("uncertain outcome", err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.saved()["members"][0], member)

    def test_confirmed_replay_is_idempotent_and_repairs_missing_enrollment_without_send(self):
        code, out, err = self.invoke(self.apply_arguments(), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        original_id = json.loads(out)["applied"][0]["dispatch_id"]
        original_state = self.state.read_bytes()
        original_member = self.saved()["members"][0]
        # Simulate a previous installation with a persisted dispatch receipt but
        # no enrollment row; this is explicit owner recovery, not normal cleanup.
        supervision.transaction(self.state, lambda data: data["members"].clear())
        client = self._client({})
        code, out, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.saved()["members"][0]["id"], original_id)
        self.assertEqual(self.saved()["members"][0]["assignment"], original_member["assignment"])
        self.assertEqual(self.state.read_bytes(), original_state)
        self.assertEqual(self.runner.calls, [])
        code, _, err = self.invoke(self.apply_arguments(), self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.saved()["members"]), 1)

    def test_sidecar_failure_after_confirmed_send_does_not_make_it_replayable(self):
        original_enroll = supervision.enroll
        calls = []
        def fail_after_send(*arguments, **options):
            calls.append(arguments)
            if len(calls) == 3:
                raise StateError("Post-send sidecar failure; restore its storage.", {})
            return original_enroll(*arguments, **options)
        with patch.object(supervision, "enroll", side_effect=fail_after_send):
            code, _, err = self.invoke(self.apply_arguments(), self._client({"grok": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("Post-send sidecar", err)
        self.assertEqual(json.loads(self.state.read_text())["recovery"]["dispatches"][0]["status"], "applied")
        client = self._client({})
        code, out, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(json.loads(self.state.read_text())["assignments"]), 1)

    def test_report_change_under_same_dispatch_id_refuses_even_if_enrollment_missing(self):
        code, _, err = self.invoke(self.apply_arguments(), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        supervision.transaction(self.state, lambda data: data["members"].clear())
        client = self._client({})
        code, _, err = self.invoke(self.apply_arguments(reports={"developer": str(self.tmp / "wrong-report.md")}), client)
        self.assertEqual(code, 1)
        self.assertIn("different inputs", err)
        self.assertEqual(self.runner.calls, [])

    def test_legacy_applied_retry_after_binding_repairs_without_resend(self):
        # Model a real old installation: this distinct state has never had a
        # supervision owner or discovery record. Never clear a live binding.
        self.state = self.tmp / "never-bound-legacy-state.json"
        arguments = self.apply_arguments(reports={})
        code, _, err = self.invoke(arguments, self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        binding = self.tmp / "legacy-native-binding.json"
        binding.write_text(json.dumps(supervision.identity("legacy-native", str(self.tmp), "fixture", pane_id="lead-pane")))
        code, _, err = self.invoke(["supervision-bind", "--record", str(binding), "--now", AT])
        self.assertEqual(code, 0, err)
        client = self._client({})
        code, out, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(len(self.saved()["members"]), 1)

    def test_missing_bound_owner_refuses_legacy_fallback_before_any_worker_read(self):
        discovery = supervision.binding_path(self.identity)
        original = discovery.read_bytes()
        supervision.store_path(self.state).unlink()
        client = self._client({"grok": "idle"})
        code, _, err = self.invoke(self.apply_arguments(reports={}), client)
        self.assertEqual(code, 1)
        self.assertIn("missing supervision owner", err)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(supervision.store_path(self.state).exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(discovery.read_bytes(), original)

    def test_new_native_lead_cannot_bypass_missing_earlier_owner(self):
        discovery = supervision.binding_path(self.identity)
        original = discovery.read_bytes()
        supervision.store_path(self.state).unlink()
        client = self._client({"grok": "idle"})
        with patch.dict(os.environ, {"HERDR_ENV": "new-session", "HERDR_PANE_ID": "new-lead-pane", "CODEX_THREAD_ID": "replacement-native"}):
            code, _, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 1)
        self.assertIn("missing supervision owner", err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(discovery.read_bytes(), original)

    def test_interrupted_first_bind_requires_completion_before_dispatch(self):
        self.state = self.tmp / "interrupted-first-bind.json"
        who = supervision.identity("initializing-native", str(self.tmp), "fixture", pane_id="lead-pane")
        record = self.tmp / "initializing-binding.json"
        record.write_text(json.dumps(who))
        original_save = supervision.save_state
        def fail_final_owner(path, value):
            if Path(path) == supervision.store_path(self.state) and value["binding"] is not None:
                raise StateError("Simulated interrupted binding commit; resume initialization.", {})
            return original_save(path, value)
        with patch.object(supervision, "save_state", side_effect=fail_final_owner):
            code, _, err = self.invoke(["supervision-bind", "--record", str(record), "--now", AT])
        self.assertEqual(code, 1, err)
        self.assertIsNone(self.saved()["binding"])
        owner_before = supervision.store_path(self.state).read_bytes()
        discovery = supervision.binding_path(who)
        discovery_before = discovery.read_bytes()
        client = self._client({"grok": "idle"})
        code, _, err = self.invoke(self.apply_arguments(), client)
        self.assertEqual(code, 1)
        self.assertIn("initialization is incomplete", err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(supervision.store_path(self.state).read_bytes(), owner_before)
        self.assertEqual(discovery.read_bytes(), discovery_before)
        code, _, err = self.invoke(["supervision-bind", "--record", str(record), "--now", AT])
        self.assertEqual(code, 0, err)
        code, _, err = self.invoke(self.apply_arguments(), self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.saved()["members"][0]["active"])

    def test_no_clear_retains_verified_native_proof_for_all_roles(self):
        native_sessions = {"grok": "grok-native", "claude": "claude-native"}
        assignments = {"developer": "grok", "tester": "claude"}
        code, _, err = self.invoke(self.apply_arguments(assignments, no_clear=True), self._client({name: "idle" for name in native_sessions}, sessions=native_sessions))
        self.assertEqual(code, 0, err)
        for member in self.saved()["members"]:
            self.assertEqual(supervision.expected_assignment(member)["native_session"]["value"], native_sessions[member["assignment"]["agent"]])

    def test_offline_commands_do_not_lock_or_read_main_state_or_config(self):
        self.state.write_text("corrupt main state must remain untouched")
        self.config.unlink()
        evidence = self.tmp / "outcome.md"
        evidence.write_text("Saved verified outcome")
        assignment = {"id": "offline-dispatch", "agent": "grok", "task": TASK, "report": self.reports["developer"], "pane_id": None, "native_session": None}
        record = self.tmp / "enroll.json"
        record.write_text(json.dumps(assignment))
        with state_lock(self.state):
            code, _, err = self.invoke(["supervision-enroll", "--record", str(record), "--now", AT])
            self.assertEqual(code, 0, err)
            code, out, err = self.invoke(["supervision-status", "--now", AT])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["active"][0]["id"], "offline-dispatch")
            code, out, err = self.invoke(["supervision-drain", "--now", AT])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["events"], [])
        self.assertEqual(self.state.read_text(), "corrupt main state must remain untouched")

    def test_watch_uses_injected_cli_clock_and_returns_durable_observation(self):
        assignment = {"id": "blocked-dispatch", "agent": "grok", "task": TASK, "report": self.reports["developer"], "pane_id": "w4:p1", "native_session": None}
        supervision.enroll(self.state, assignment, AT)
        client = self._client({"grok": "blocked"})
        self.runner.set("agent read grok --source visible --lines 200", "Candidate approval dialog")
        # Isolate the fleet sleeper from Python's shared time module: ps's
        # subprocess timeout machinery may sleep while waiting for its child.
        fleet_sleep = Mock(name="fleet_sleep")
        with patch.object(cli, "now_iso", return_value=AT), patch.object(cli, "time", SimpleNamespace(sleep=fleet_sleep)):
            code, out, err = self.invoke(["supervision-watch", "--now", AT], client)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["reason"], "events")
        fleet_sleep.assert_not_called()
        self.assertEqual(self.runner.writes(), [])
        self.assertTrue(self.saved()["events"])


if __name__ == "__main__":
    unittest.main()
