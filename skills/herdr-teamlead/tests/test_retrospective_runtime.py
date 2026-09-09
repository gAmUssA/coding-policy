"""Live retrospective boundaries with deterministic worker and file evidence."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import retrospective as notes, retrospective_runtime as runtime
from teamlead.assign import apply
from teamlead.cli import main
from teamlead.config import load_config
from teamlead.errors import HerdrError, StateError, UsageError
from teamlead.state import add_assignment, empty_state, save_state
from tests import test_cli as cli_fixture
from tests.fakes import FakeRunner, agent_json

AT = "2026-09-09T10:00:00+00:00"
OLD = "2026-09-09T09:00:00+00:00"


class RetrospectiveRuntimeTest(unittest.TestCase):
    _client = cli_fixture.ApplyCommandTest._client

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(cli_fixture.CONFIG))
        self.agents = {agent.name: agent for agent in load_config(self.config)}
        self.runner = FakeRunner()
        self.client = self._client({"grok": "idle", "codex": "idle"}, sessions={"grok": "grok-old", "codex": "codex-old"})
        self.state = empty_state()
        self.steps = []
        self.reports = {}
        for role, name, pane in (("developer", "grok", "w4:p1"), ("tester", "codex", "w3:p1")):
            add_assignment(self.state, OLD, role, name, task="old-task")
            brief, report = self.root / (name + "-brief.md"), self.root / (name + "-report.md")
            brief.write_text("Read authorized findings.\n")
            report.write_text("REPORT: completed with saved evidence.\n")
            self.reports[name] = report
            self.steps.append({"role": role, "agent": name, "pane_id": pane, "tier": None,
                               "brief": str(brief), "common": str(self.root / "common.md")})
        (self.root / "common.md").write_text("Respect scope and report evidence.\n")
        save_state(self.path, self.state)
        self.note = self.root / "draft.md"
        self.note.write_text("# Retrospective\n" + "".join("\n## " + section + "\nRecorded observations and a concrete next action.\n" for section in notes.SECTIONS))

    def request(self, steps=None):
        guard = runtime.Guard(self.path, self.state, self.client, self.agents, AT)
        return {"transitions": [{**guard._item(step), "report": str(self.reports[step["agent"]])} for step in (steps or self.steps)]}

    def record(self, request=None, name="retro-1"):
        check = runtime.check(self.path, self.state, self.client, self.agents, request or self.request(), AT)
        self.check_path = self.root / (name + "-check.json")
        self.check_path.write_text(json.dumps(check))
        self.metadata = {"id": name, "note": str(self.note), "period_start": OLD, "period_end": AT,
                         "triggers": ["daily", "transition"], "tasks": ["old-task"],
                         "participants": [row["agent"] for row in check["coverage"]], "unavailable": {},
                         "sources": [], "completed": True, "check": str(self.check_path)}
        with notes.lock(self.path):
            return notes.record(self.path, self.metadata, check["coverage"], AT)

    def guard(self, **kwargs):
        return runtime.Guard(self.path, self.state, self.client, self.agents, AT, **kwargs)

    def invoke(self, command, *args, client=None):
        out, err = io.StringIO(), io.StringIO()
        rc = main([command, "--state", str(self.path), "--config", str(self.config), *args],
                  stdout=out, stderr=err, client=client or self.client)
        return rc, out.getvalue(), err.getvalue()

    def test_check_is_read_only_and_unknown_running_worker_is_due(self):
        before = self.path.read_bytes()
        check = runtime.check(self.path, self.state, self.client, self.agents, self.request(), AT)
        self.assertTrue(check["due"])
        self.assertEqual(check["missing_coverage"], ["grok", "codex"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(notes.directory(self.path).exists())
        self.assertEqual(self.runner.writes(), [])
        empty = runtime.check(self.path, empty_state(), self.client, self.agents, self.request(), AT)
        self.assertTrue(empty["due"])
        self.assertFalse(empty["coverage"][0]["first_start"])

    def test_batch_sibling_outcome_does_not_invalidate_remaining_worker(self):
        self.record()
        guard = self.guard()
        guard.preflight(self.steps)
        add_assignment(self.state, AT, "developer", "grok", task="new-task")
        guard.before(self.steps[1])
        self.runner.set("agent get codex", agent_json("codex", "idle", "w3:p1", "replacement"))
        with self.assertRaises(UsageError):
            guard.before(self.steps[1])

    def test_changed_report_and_target_invalidate_only_their_worker(self):
        self.record()
        guard = self.guard()
        guard.preflight(self.steps)
        self.reports["codex"].write_text("REPORT: changed conclusion.\n")
        guard.before(self.steps[0])
        with self.assertRaises(UsageError):
            guard.before(self.steps[1])
        self.reports["codex"].write_text("REPORT: completed with saved evidence.\n")
        Path(self.steps[1]["brief"]).write_text("Different target.\n")
        with self.assertRaises(UsageError):
            guard.before(self.steps[1])

    def test_lifecycle_done_and_proven_idle_stale_working_do_not_invalidate(self):
        self.record()
        self.runner.set("agent get grok", agent_json("grok", "done", "w4:p1", "grok-old"))
        self.guard().preflight(self.steps)
        self.runner.set("agent get grok", agent_json("grok", "working", "w4:p1", "grok-old"))
        self.runner.set("agent read grok --source visible --lines 40", "Shift+Tab:mode\n")
        self.guard().preflight(self.steps)

    def test_apply_due_refuses_before_reservation_or_any_input(self):
        calls = []
        with self.assertRaises(UsageError):
            apply(self.client, {row["role"]: row["agent"] for row in self.steps}, self.agents,
                  {"common": self.steps[0]["common"], **{row["role"]: row["brief"] for row in self.steps}}, AT,
                  history=self.state["assignments"], retrospective_guard=self.guard(),
                  on_prepare=lambda *_: calls.append("reserved"), sleep=lambda _: None)
        self.assertEqual(calls, [])
        self.assertEqual(self.runner.writes(), [])

    def dispatch(self, steps, guard, **kwargs):
        return apply(self.client, {row["role"]: row["agent"] for row in steps}, self.agents,
                     {"common": steps[0]["common"], **{row["role"]: row["brief"] for row in steps}}, AT,
                     history=self.state["assignments"], retrospective_guard=guard,
                     sleep=lambda _: None, settle_sec=0, **kwargs)

    def test_later_worker_changed_after_first_result_receives_no_clear(self):
        self.record()
        results = []
        def on_result(result):
            results.append(result)
            add_assignment(self.state, AT, result["role"], result["agent"])
            self.runner.set("agent get codex", agent_json("codex", "idle", "w3:p1", "changed"))
        with self.assertRaises(UsageError):
            self.dispatch(self.steps, self.guard(), on_result=on_result)
        self.assertEqual([result["agent"] for result in results], ["grok"])
        self.assertFalse(any("w3:p1" in command or "prompt codex" in command for command in self.runner.writes()))

    def test_own_clear_between_configured_enters_and_delayed_settle(self):
        step = self.steps[1]
        self.record(self.request([step]))
        original_keys, original_wait = self.client.pane_send_keys, self.client.agent_wait
        def keys(pane, value):
            result = original_keys(pane, value)
            self.runner.set("agent get codex", agent_json("codex", "idle", pane, "after-first-enter"))
            return result
        def wait(name, **kwargs):
            result = original_wait(name, **kwargs)
            self.runner.set("agent get codex", agent_json("codex", "done", "w3:p1", "after-settle"))
            return result
        with patch.object(self.client, "pane_send_keys", side_effect=keys), patch.object(self.client, "agent_wait", side_effect=wait):
            result = self.dispatch([step], self.guard())
        self.assertEqual(result["applied"][0]["status"], "applied")
        self.assertEqual(len([command for command in self.runner.writes() if "send-keys" in command]), 2)
        self.assertTrue(notes.load(self.path)["transitions"])

    def test_foreign_process_after_clear_submit_is_not_blessed(self):
        self.record(self.request([self.steps[0]]))
        original = self.client.pane_send_keys
        def keys(pane, value):
            result = original(pane, value)
            self.runner.set("pane process-info", json.dumps({"result": {"process_info": {"pane_id": pane, "foreground_processes": [
                {"name": "grok", "pid": 999, "argv": ["grok", "--always-approve"]}]}}}))
            return result
        with patch.object(self.client, "pane_send_keys", side_effect=keys), self.assertRaises(HerdrError):
            self.dispatch([self.steps[0]], self.guard())
        self.assertFalse(any("agent prompt" in command for command in self.runner.writes()))
        self.assertEqual(notes.load(self.path)["transitions"], [])

    def test_offline_retrieval_ignores_config_state_migrations_and_deleted_sources(self):
        self.record()
        self.path.write_text('{"schema_version":999,"unreadable_to_owner":true}')
        self.config.unlink()
        for report in self.reports.values():
            report.unlink()
        before = self.path.read_bytes()
        with patch.dict(os.environ, {}, clear=True), patch("teamlead.cli._client", side_effect=AssertionError("offline")), patch("teamlead.cli.load_config", side_effect=AssertionError("offline")):
            rc, out, err = self.invoke("retro-list", "--since", AT)
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(out)["records"]), 1)
            rc, out, err = self.invoke("retro-show")
            self.assertEqual(rc, 0, err)
            self.assertIn("Coordination", json.loads(out)["markdown"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_record_revalidates_and_resumes_interrupted_first_write(self):
        check = runtime.check(self.path, self.state, self.client, self.agents, self.request(), AT)
        receipt = self.root / "check.json"
        receipt.write_text(json.dumps(check))
        metadata = {"id": "interrupted", "note": str(self.note), "period_start": OLD, "period_end": AT,
                    "triggers": ["daily", "transition"], "tasks": ["old-task"], "participants": ["grok", "codex"],
                    "unavailable": {}, "sources": [], "completed": True, "check": str(receipt)}
        record = self.root / "record.json"
        record.write_text(json.dumps(metadata))
        with patch.object(notes, "_install_note", side_effect=StateError("interrupted", {})):
            rc, _, _ = self.invoke("retro-record", "--record", str(record), "--now", AT)
        self.assertEqual(rc, 1)
        self.assertTrue((notes.directory(self.path) / "pending.json").exists())
        rc, _, err = self.invoke("retro-record", "--record", str(record), "--now", AT)
        self.assertEqual(rc, 0, err)
        self.assertFalse((notes.directory(self.path) / "pending.json").exists())
        self.reports["codex"].write_text("Changed after check.\n")
        rc, _, err = self.invoke("retro-record", "--record", str(record), "--now", AT)
        self.assertEqual(rc, 1)
        self.assertIn("changed since", err)

    def test_same_transition_retry_reuses_bridge_but_new_target_does_not(self):
        self.record(self.request([self.steps[0]]))
        first = self.guard()
        first.preflight([self.steps[0]])
        self.runner.set("agent get grok", agent_json("grok", "done", "w4:p1", "own-clear"))
        first.after_transition(self.steps[0])
        retry = self.guard()
        retry.preflight([self.steps[0]])
        retry.before(self.steps[0])
        changed = {**self.steps[0], "role": "reviewer"}
        with self.assertRaises(UsageError):
            self.guard().preflight([changed])

    def test_report_changed_after_termination_refuses_before_start(self):
        self.record(self.request([self.steps[0]]))
        guard = self.guard()
        guard.preflight([self.steps[0]])
        self.reports["grok"].write_text("Late output.\n")
        with self.assertRaises(UsageError):
            guard.before_launch(self.steps[0])
        self.assertEqual(self.runner.writes(), [])

    def test_first_judge_start_bridges_only_same_task_and_tier(self):
        self.path = self.root / "new-team.json"
        self.state = empty_state()
        plan = self.root / "judge-plan.json"
        plan.write_text(json.dumps({"assignments": {"judge": "codex"},
                                   "task_context": {"task": "judge-task"},
                                   "judge": {"agent": "codex", "model": "gpt-5.6-sol", "effort": "high"}}))
        self.runner.set("pane process-info", json.dumps({"result": {"process_info": {
            "pane_id": "w3:p1", "shell_pid": 100, "foreground_processes": [{"name": "zsh", "pid": 100, "argv": ["zsh"]}]}}}))
        def start(name, kind, pane, flags):
            self.runner.set("agent get codex", agent_json("codex", "idle", pane, "judge-new"))
            self.runner.set("pane process-info", json.dumps({"result": {"process_info": {
                "pane_id": pane, "shell_pid": 100, "foreground_processes": [{"name": kind, "pid": 300, "argv": [kind] + flags}]}}}))
            return {"agent": {"name": name, "agent": kind, "pane_id": pane, "agent_status": "idle"}, "argv": [kind] + flags}
        with patch.object(self.client, "agent_start", side_effect=start):
            rc, _, err = self.invoke("start-judge", "--assignments", str(plan), "--pane", "w3:p1", "--kind", "codex", "--task", "judge-task", "--now", AT)
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.path.exists())
        step = {**self.steps[1], "role": "judge", "tier": {"model": "gpt-5.6-sol", "effort": "high"}}
        self.guard(task="judge-task", no_clear=True).preflight([step])
        with self.assertRaises(UsageError):
            self.guard(task="another-task", no_clear=True).preflight([step])
        self.assertEqual(notes.load(self.path)["baseline_at"], AT)

    def test_canonical_state_symlink_survives_owner_write(self):
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        original = self.path
        self.path = alias
        record = self.root / "task.json"
        record.write_text(json.dumps({"task": "symlink-task", "base_revision": "a" * 40,
            "scope": "Test canonical state", "allowed_paths": ["src/*"],
            "authorization": {"source": "fixture", "quote": "Approve fixture task."}}))
        rc, _, err = self.invoke("task", "--record", str(record), "--now", AT)
        self.assertEqual(rc, 0, err)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.read_bytes(), original.read_bytes())
        self.record()
        self.assertEqual(notes.directory(alias), notes.directory(original))

    def test_known_review_evidence_invalidates_coverage_and_own_bridge(self):
        review = self.root / "independent-review.md"
        review.write_text("Approved recorded implementation.\n")
        self.state["recovery"]["dispatches"].append({"id": "original", "agent": "grok", "assignment_index": 0,
                                                       "report": {"report": str(review)}})
        self.record(self.request([self.steps[0]]))
        guard = self.guard()
        guard.preflight([self.steps[0]])
        self.runner.set("agent get grok", agent_json("grok", "idle", "w4:p1", "own-clear"))
        guard.after_transition(self.steps[0])
        guard.before(self.steps[0])
        review.write_text("Blocking late independent finding.\n")
        with self.assertRaises(UsageError):
            guard.before(self.steps[0])
        with self.assertRaises(UsageError):
            guard.before_launch(self.steps[0])

    def test_shell_without_readable_argv_refuses_before_start(self):
        self.path = self.root / "empty-team.json"
        self.state = empty_state()
        self.runner.set("pane process-info", json.dumps({"result": {"process_info": {
            "pane_id": "w3:p1", "shell_pid": 100, "foreground_processes": [{"name": "zsh", "pid": 100}]}}}))
        item = runtime.request({"transitions": [{"agent": "codex", "role": "judge", "context": "start", "pane": "w3:p1"}]})["transitions"][0]
        with patch.object(self.client, "process_args", side_effect=HerdrError("unreadable process", {})):
            with self.assertRaises(HerdrError):
                self.guard().before_start(item)
        self.assertFalse(notes.directory(self.path).exists())
        self.assertEqual(self.runner.writes(), [])

    def test_pending_note_refuses_dispatch_before_reservation(self):
        self.record()
        pending = notes.directory(self.path) / "pending.json"
        pending.write_text("{}")
        before = pending.read_bytes()
        with self.assertRaises(StateError):
            self.dispatch(self.steps, self.guard())
        self.assertEqual(pending.read_bytes(), before)
        self.assertEqual(self.runner.writes(), [])

    def test_recording_first_shell_observation_does_not_invent_previous_work(self):
        self.path = self.root / "first-shell.json"
        self.state = empty_state()
        self.runner.set("pane process-info", json.dumps({"result": {"process_info": {
            "pane_id": "w3:p1", "shell_pid": 100, "foreground_processes": [{"name": "zsh", "pid": 100, "argv": ["zsh"]}]}}}))
        value = runtime.request({"transitions": [{"agent": "codex", "role": "judge", "context": "start", "pane": "w3:p1"}]})
        self.record(value)
        self.guard().before_start(value["transitions"][0])
        self.assertEqual(self.runner.writes(), [])

    def test_unavailable_known_review_can_be_recorded_and_restoration_invalidates(self):
        review = self.root / "removed-review.md"
        review.write_text("Original independent review.\n")
        self.state["recovery"]["dispatches"].append({"id": "original", "agent": "grok", "assignment_index": 0,
                                                       "report": {"report": str(review)}})
        first = self.record(self.request([self.steps[0]]))
        review.unlink()
        value = self.request([self.steps[0]])
        value["transitions"][0]["unavailable"] = "Independent review artifact was cleaned up; prior note preserves its receipt."
        second = self.record(value, "missing-review")
        evidence = second["coverage"][0]["source"]["dispatch_evidence"]
        self.assertEqual(evidence["sha256"], first["coverage"][0]["source"]["dispatch_evidence"]["sha256"])
        self.assertIsNone(evidence["report"])
        self.guard().preflight([self.steps[0]])
        review.write_text("Restored independent review.\n")
        with self.assertRaises(UsageError):
            self.guard().preflight([self.steps[0]])


if __name__ == "__main__":
    unittest.main()
