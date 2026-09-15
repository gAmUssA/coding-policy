"""A warm consultation preserves its engagement without authorizing other work."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import retrospective as notes, retrospective_runtime
from teamlead.assign import apply, dry_run, native_context_session
from teamlead.errors import AgentBusyError, HerdrError, UsageError
from teamlead.herdr import HerdrClient
from teamlead.state import empty_state
from tests.fakes import ScriptedReads, agent_json
from tests.test_assign import BY_NAME, PANES, runner_with
from tests.test_qualification import AT, qualified_tier

OLD = "2026-01-08T11:00:00+00:00"


class SpecialistRetentionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.paths = {"common": str(self.root / "COMMON.md"), "advisor": str(self.root / "brief.md")}
        for path in self.paths.values():
            Path(path).write_text("Inspect the authorized interaction and report evidence.\n")
        self.requirement = {"specialty": "ux", "required_capabilities": ["interaction-design"],
                            "independent": False, "engagement": "onboarding-design"}
        self.requirements = {"advisor": self.requirement}
        self.tier = {"kind": "claude", **qualified_tier()}
        self.tier["qualification"][0]["role"] = "advisor"
        self.argv = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "high"]
        self.proof = {"source": "process_argv", "pane_id": PANES["claude"], "pid": 200,
                      "model": "sonnet-5", "effort": "high", "argv": self.argv}
        self.native = native_context_session(json.loads(agent_json("claude", "idle", PANES["claude"], "consultation"))["result"]["agent"], "claude")
        self.previous = {"schema_version": 6, "at": OLD, "role": "advisor", "agent": "claude", "task": "task-1",
                         "status": "applied", "cleared": True, "clear_reason": "automatic", "fix_round": None,
                         "context_session": self.native, "tier": {**self.tier, "verified": self.proof},
                         "requirements": copy.deepcopy(self.requirement), "reviewer_scope": None}
        self.history = [self.previous]
        self.reset_client()

    def reset_client(self, *, native: str | None = "consultation", status="idle", pane=None):
        self.runner = runner_with({"claude": status})
        self.runner.set("agent get claude", agent_json("claude", status, pane or PANES["claude"], native))
        self.runner.set("pane process-info", json.dumps({"result": {"process_info": {
            "pane_id": PANES["claude"], "foreground_processes": [{"name": "claude", "pid": 200, "argv": self.argv}]}}}))
        self.client = HerdrClient("herdr", self.runner)

    def dispatch(self, **kwargs):
        options = {"task": "task-1", "history": self.history, "retain_specialist": True,
                   "requirements": self.requirements, "tiers": {"advisor": self.tier},
                   "qualifications": {"advisor": self.tier["qualification"]}, "sleep": lambda _: None,
                   "settle_sec": 0, "warn": lambda _: None}
        options.update(kwargs)
        return apply(self.client, {"advisor": "claude"}, BY_NAME, self.paths, AT, **options)

    def assert_no_input(self):
        self.assertEqual(self.runner.writes(), [])
        self.assertFalse(any(command.startswith(("agent start", "-TERM")) for command in self.runner.commands()))

    def test_same_engagement_retains_session_and_preserves_evidence(self):
        before = copy.deepcopy(self.history)
        result = self.dispatch()["applied"][0]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["requirements"], self.requirement)
        self.assertEqual(result["context_session"], self.native)
        self.assertEqual(result["clear_reason"], "retained")
        self.assertFalse(result["cleared"])
        self.assertIsNone(result["fix_round"])
        self.assertEqual(self.history, before)
        self.assertEqual(len(self.runner.writes()), 1)
        self.assertTrue(self.runner.writes()[0].startswith("agent prompt claude"))
        self.assertFalse(any(command.startswith(("agent start", "-TERM")) for command in self.runner.commands()))

    def test_direct_dispatch_refuses_noncanonical_requirements_before_input(self):
        for change in ({"required_capabilities": []}, {"specialty": "UX"},
                       {"engagement": "onboarding\nreset"}, {"required_capabilities": ["ux", "ux"]}):
            requirement = {**self.requirement, **change}
            self.previous["requirements"] = requirement
            with self.subTest(change=change), self.assertRaises(UsageError):
                self.dispatch(requirements={"advisor": requirement})
            self.assert_no_input()

    def test_each_consultation_responsibility_can_retain(self):
        for role in ("investigator", "architect"):
            with self.subTest(role=role):
                self.reset_client()
                previous = {**self.previous, "role": role}
                tier = copy.deepcopy(self.tier)
                tier["qualification"][0]["role"] = role
                result = apply(self.client, {role: "claude"}, BY_NAME,
                    {"common": self.paths["common"], role: self.paths["advisor"]}, AT,
                    task="task-1", history=[previous], retain_specialist=True,
                    requirements={role: self.requirement}, tiers={role: tier},
                    qualifications={role: tier["qualification"]}, sleep=lambda _: None, settle_sec=0)
                self.assertEqual(result["applied"][0]["clear_reason"], "retained")

    def test_other_responsibilities_and_multi_worker_batches_are_refused(self):
        for assignments in ({role: "claude"} for role in ("developer", "reviewer", "tester", "release", "judge")):
            with self.subTest(assignments=assignments), self.assertRaises(UsageError):
                apply(self.client, assignments, BY_NAME, self.paths, AT, task="task-1", retain_specialist=True)
            self.assert_no_input()
        with self.assertRaises(UsageError):
            apply(self.client, {"advisor": "claude", "investigator": "codex"}, BY_NAME, self.paths, AT,
                  task="task-1", retain_specialist=True)
        self.assert_no_input()

    def test_context_modes_and_correction_parameters_cannot_be_mixed(self):
        for option in ({"no_clear": True}, {"retain_context": True}, {"fix_round": 1},
                       {"fix_round": 6}, {"plan_id": "extra-fixes"}, {"work": {}}, {"task": None}):
            with self.subTest(option=option), self.assertRaises(UsageError):
                self.dispatch(**option)
            self.assert_no_input()

    def test_retention_requires_complete_engagement_requirements(self):
        for requirement in (None, {}, {**self.requirement, "engagement": ""},
                            {**self.requirement, "independent": "false"},
                            {**self.requirement, "required_capabilities": "ux"}):
            with self.subTest(requirement=requirement), self.assertRaises(UsageError):
                self.dispatch(requirements={"advisor": requirement})
            self.assert_no_input()

    def test_changed_task_role_status_or_engagement_refuses_before_worker_calls(self):
        for mutation in ({"task": "another-task"}, {"role": "investigator"},
                         {"status": "sent_but_not_started"}, {"fix_round": 1},
                         {"requirements": None}, {"requirements": {**self.requirement, "specialty": "security"}},
                         {"requirements": {**self.requirement, "engagement": "another-consultation"}},
                         {"requirements": {**self.requirement, "independent": True}},
                         {"requirements": {**self.requirement, "required_capabilities": ["accessibility"]}}):
            with self.subTest(mutation=mutation), self.assertRaises(UsageError):
                self.dispatch(history=[{**self.previous, **mutation}])
            self.assertEqual(self.runner.calls, [])

    def test_missing_history_intervening_work_and_ambiguous_chronology_refuse(self):
        later = {**self.previous, "at": "2026-01-08T11:30:00Z", "task": "other-task"}
        for history in ([], [later, self.previous], [self.previous, copy.deepcopy(self.previous)],
                        [{**self.previous, "at": None}]):
            with self.subTest(history=history), self.assertRaises(UsageError):
                self.dispatch(history=history)
            self.assertEqual(self.runner.calls, [])

    def test_later_unrelated_worker_does_not_break_retention(self):
        other = {**self.previous, "at": AT, "agent": "codex", "task": "other-task"}
        result = self.dispatch(history=[self.previous, other])
        self.assertEqual(result["applied"][0]["context_session"], self.native)

    def test_unknown_or_changed_tier_requires_fresh_assignment(self):
        for tier in (None, {**self.previous["tier"], "verified": None},
                     {**self.previous["tier"], "model": "opus-5"},
                     {**self.previous["tier"], "effort": "xhigh"},
                     {**self.previous["tier"], "kind": "grok"}):
            with self.subTest(tier=tier), self.assertRaises(UsageError):
                self.dispatch(history=[{**self.previous, "tier": tier}])
            self.assertEqual(self.runner.calls, [])
        with self.assertRaises(UsageError):
            self.dispatch(tiers={})

    def test_missing_or_changed_native_identity_and_pane_refuse(self):
        for native, pane in ((None, None), ("replacement", None), ("consultation", "another-pane")):
            with self.subTest(native=native, pane=pane):
                self.reset_client(native=native, pane=pane)
                with self.assertRaises(HerdrError):
                    self.dispatch()
                self.assert_no_input()
        self.reset_client()
        with self.assertRaises(HerdrError):
            self.dispatch(history=[{**self.previous, "context_session": None}])
        self.assert_no_input()

    def test_busy_or_blocked_worker_receives_no_input(self):
        for status in ("working", "blocked"):
            with self.subTest(status=status):
                self.reset_client(status=status)
                with self.assertRaises(AgentBusyError):
                    self.dispatch()
                self.assert_no_input()

    def test_session_or_readiness_change_during_composer_read_refuses_prompt(self):
        for native, status in (("replacement", "idle"), ("consultation", "blocked")):
            with self.subTest(native=native, status=status):
                self.reset_client()
                original_read = self.client.agent_read
                def read(*args, **kwargs):
                    result = original_read(*args, **kwargs)
                    self.runner.set("agent get claude", agent_json("claude", status, PANES["claude"], native))
                    return result
                with patch.object(self.client, "agent_read", side_effect=read), self.assertRaises((HerdrError, AgentBusyError)):
                    self.dispatch()
                self.assert_no_input()

    def test_fresh_consultation_records_post_clear_native_session(self):
        self.runner.responses["agent get claude"] = ScriptedReads([
            agent_json("claude", "idle", PANES["claude"], "outgoing"),
            agent_json("claude", "idle", PANES["claude"], "incoming"),
        ])
        result = self.dispatch(retain_specialist=False, tiers={}, history=[])["applied"][0]
        self.assertTrue(result["cleared"])
        self.assertEqual(result["context_session"]["value"], "incoming")
        self.assertEqual(result["requirements"], self.requirement)

    def test_stale_post_clear_native_identity_cannot_support_retention(self):
        result = self.dispatch(retain_specialist=False, tiers={}, history=[])["applied"][0]
        self.assertEqual(result["status"], "applied")
        self.assertIsNone(result["context_session"])

    def test_new_architect_dispatch_cannot_omit_requirements_outside_cli(self):
        with self.assertRaisesRegex(UsageError, "require explicit specialist requirements"):
            apply(self.client, {"architect": "claude"}, BY_NAME,
                {"common": self.paths["common"], "architect": self.paths["advisor"]}, AT,
                task="task-1", sleep=lambda _: None, settle_sec=0)
        self.assert_no_input()

    def test_native_identity_published_after_initial_prompt_is_captured(self):
        self.runner.responses["agent get claude"] = ScriptedReads([
            agent_json("claude", "idle", PANES["claude"], "outgoing"),
            agent_json("claude", "idle", PANES["claude"]),
            agent_json("claude", "working", PANES["claude"], "after-prompt"),
        ])
        result = self.dispatch(retain_specialist=False, tiers={}, history=[])["applied"][0]
        self.assertEqual(result["context_session"]["value"], "after-prompt")

    def test_dry_run_checks_saved_identity_and_prints_retained_plan_without_calls(self):
        result = dry_run(self.client, {"advisor": "claude"}, BY_NAME, self.paths,
            task="task-1", retain_specialist=True, history=self.history,
            requirements=self.requirements, tiers={"advisor": self.tier})
        self.assertEqual(result["clear_reason"], "retained")
        self.assertEqual(result["steps"][0]["requirements"], self.requirement)
        self.assertFalse(any(command["argv"][1:3] == ["agent", "start"]
                             or command["argv"][0] == "kill" for command in result["steps"][0]["commands"]))
        self.assertEqual(self.runner.calls, [])
        with self.assertRaises(UsageError):
            dry_run(self.client, {"advisor": "claude"}, BY_NAME, self.paths,
                task="task-1", retain_specialist=True, history=[], requirements=self.requirements,
                tiers={"advisor": self.tier})

    def guard(self, at=AT):
        state = empty_state()
        state["assignments"] = self.history
        return retrospective_runtime.Guard(self.path, state, self.client, BY_NAME, at, task="task-1", retain=True)

    def test_warm_followup_preserves_daily_retrospective_gate(self):
        with notes.lock(self.path):
            notes.establish_baseline(self.path, notes.empty(self.path), OLD)
        result = self.dispatch(retrospective_guard=self.guard())
        self.assertEqual(result["applied"][0]["clear_reason"], "retained")
        self.assertEqual(notes.load(self.path)["transitions"], [])
        self.reset_client()
        prepared = []
        with self.assertRaises(UsageError):
            self.dispatch(retrospective_guard=self.guard("2026-01-09T11:00:00Z"),
                          on_prepare=lambda *_: prepared.append(True))
        self.assertEqual(prepared, [])
        self.assert_no_input()

    def test_fresh_transition_still_requires_retrospective_coverage(self):
        with notes.lock(self.path):
            notes.establish_baseline(self.path, notes.empty(self.path), OLD)
        guard = self.guard()
        guard.retain = False
        with self.assertRaises(UsageError):
            self.dispatch(retain_specialist=False, retrospective_guard=guard)
        self.assert_no_input()


if __name__ == "__main__":
    unittest.main()
