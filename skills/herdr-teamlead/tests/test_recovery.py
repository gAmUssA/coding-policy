"""Outcome tests for bounded recovery and truthful, reusable attempt history."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from teamlead.errors import UsageError
from teamlead.recovery import (
    abort_pre_send, authorize_context, authorize_plan, checkpoint, confirmed_fix,
    dispatch_identity, finish_dispatch, fresh_transition, mark_sending,
    prior_dispatch, record_report, register_task, reserve, task_statuses,
    validate_store, validate_work,
)
from teamlead.state import add_assignment, empty_state, load_state_checked, save_state

AT = "2026-02-03T10:00:00+00:00"
TASK = "fixture-task"
BASE = "a" * 40
HEAD = "b" * 40
AUTH = {"source": "fixture operator message", "quote": "Approve this task and its stated bounds."}
WORK = {"base_revision": BASE, "scope": "Correct parser findings", "paths": ["src/parser.py"], "findings": ["F1"]}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = empty_state()
        self.store = self.state["recovery"]
        self.history = self.state["assignments"]
        register_task(self.store, {"task": TASK, "base_revision": BASE, "scope": WORK["scope"],
                                  "allowed_paths": ["src/*"], "authorization": AUTH}, AT)
        self.judge_report = self.root / "judge.md"
        self.judge_report.write_text("RULING: amend — correct the remaining parser defect\nACTION: Use one canonical parser\n")
        self.review = self.root / "review.md"
        self.review.write_text("Reviewed head: " + HEAD + "\nBlocking finding F1: quoted input is still accepted as a completion signal.\n")

    def seed_checkpoint(self):
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "worker", task=TASK, fix_round=fix)
        add_assignment(self.state, AT, "judge", "judge", task=TASK)
        return checkpoint(self.store, self.history, {
            "id": "checkpoint-5", "task": TASK, "defect": "F1 remains open", "previous_attempts": "Five attempts changed parsing and quoting",
            "progress": "Some counterexamples now pass; the quoted case remains red", "change_in_approach": "Use one canonical parser",
            "judge_report": str(self.judge_report),
        }, AT, "judge")

    def approve(self):
        self.seed_checkpoint()
        return authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "checkpoint-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH}, AT)

    def reservation(self, number):
        return {"id": f"fix-{number}", "task": TASK, "role": "developer", "agent": "worker", "fix_round": number,
                "fingerprint": "c" * 64, "plan": "plan-1", "work": copy.deepcopy(WORK)}

    def finish(self, number):
        record = self.reservation(number)
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        add_assignment(self.state, "2026-02-03T11:00:0{}+00:00".format(number), "developer", "worker", task=TASK, fix_round=number)
        finish_dispatch(self.store, record["id"], {"status": "applied", **{
            key: record[key] for key in ("task", "role", "agent", "fix_round")}}, len(self.history) - 1, AT)

    def test_two_corrections_share_one_approval_and_the_first_outside_budget_refuses(self):
        plan = self.approve()
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 7))
        validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)
        self.finish(6)
        with self.assertRaisesRegex(UsageError, "actual blocking review"):
            validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)
        result = record_report(self.store, {"dispatch": "fix-6", "head_revision": HEAD, "verdict": "blocking",
            "review_mode": "full", "reviewer": "independent-reviewer", "report": str(self.review), "changed_paths": ["src/parser.py"]}, AT)
        self.assertEqual(result["evidence"]["sha256"], hashlib.sha256(self.review.read_bytes()).hexdigest())
        validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)
        self.finish(7)
        self.assertEqual(len(self.store["plans"]), 1)
        self.assertEqual(confirmed_fix(self.history, TASK), 7)
        with self.assertRaisesRegex(UsageError, "outside the approved task or budget"):
            validate_work(self.store, self.history, TASK, 8, "plan-1", WORK)
        path = self.root / "state.json"
        save_state(path, self.state)
        restored, usable = load_state_checked(path)
        self.assertTrue(usable)
        self.assertEqual(restored, self.state)

    def test_ordinary_sixth_wrong_task_scope_base_and_paths_are_refused(self):
        self.approve()
        for task, plan, work in (
            (TASK, None, WORK), ("other-task", "plan-1", WORK),
            (TASK, "plan-1", {**WORK, "scope": "Other work"}),
            (TASK, "plan-1", {**WORK, "base_revision": HEAD}),
            (TASK, "plan-1", {**WORK, "paths": ["secrets/config.py"]}),
        ):
            with self.subTest(task=task, work=work), self.assertRaises(UsageError):
                validate_work(self.store, self.history, task, 6, plan, work)

    def test_checkpoint_pauses_implementation_even_when_an_audit_runs(self):
        self.seed_checkpoint()
        add_assignment(self.state, AT, "reviewer", "auditor", task=TASK)
        status = task_statuses(self.store, self.history)[TASK]
        self.assertEqual((status["status"], status["paused_work"]), ("waiting_for_operator", "implementation"))

    def test_checkpoint_needs_the_pinned_judge_after_latest_development(self):
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "worker", task=TASK, fix_round=fix)
        with self.assertRaisesRegex(UsageError, "pinned judge"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1", "previous_attempts": "Five fixes",
                "progress": "Still blocked", "change_in_approach": "Reassess", "judge_report": str(self.judge_report)}, AT, "judge")

    def test_retry_returns_the_same_dispatch_and_never_consumes_twice(self):
        self.approve()
        self.finish(6)
        previous = copy.deepcopy(self.state)
        replay = prior_dispatch(self.store, "fix-6", "c" * 64)
        assert replay is not None
        self.assertEqual(replay["status"], "applied")
        self.assertEqual(self.state, previous)
        self.assertEqual(confirmed_fix(self.history, TASK), 6)
        with self.assertRaisesRegex(UsageError, "different inputs"):
            prior_dispatch(self.store, "fix-6", "d" * 64)

    def test_interrupted_send_holds_its_slot_and_a_proven_pre_send_failure_retries(self):
        self.approve()
        record = self.reservation(6)
        reserve(self.store, record, AT)
        abort_pre_send(self.store, record["id"], AT, "clear refused before a brief was sent")
        replay = prior_dispatch(self.store, record["id"], record["fingerprint"])
        assert replay is not None
        self.assertEqual(replay["status"], "not_sent")
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        abort_pre_send(self.store, record["id"], AT, "connection lost after send began")
        with self.assertRaisesRegex(UsageError, "uncertain outcome"):
            prior_dispatch(self.store, record["id"], record["fingerprint"])
        with self.assertRaisesRegex(UsageError, "unresolved dispatch"):
            reserve(self.store, {**record, "id": "another-id"}, AT)
        self.assertEqual(len(self.store["dispatches"]), 1)
        self.assertEqual(confirmed_fix(self.history, TASK), 5)

    def test_release_clear_and_explicit_null_recovery_preserve_original_rows(self):
        add_assignment(self.state, AT, "developer", "worker", task=TASK)
        original = copy.deepcopy(self.history)
        permission = authorize_context(self.store, self.history, {"task": TASK, "assignment_index": 0,
            "reason": "Old dispatch missed its native session ID", "authorization": AUTH, "evidence": str(self.review)}, AT, None)
        self.assertEqual(permission["basis"], "operator_authorized_fresh_handoff")
        self.assertEqual(self.history, original)
        transition = fresh_transition(self.store, self.history, TASK, 1)
        assert transition is not None
        self.assertEqual(transition["reason"], "authorized_context_recovery")
        add_assignment(self.state, "2026-02-03T10:00:01+00:00", "release", "worker", task=TASK, cleared=True, clear_reason="automatic")
        transition = fresh_transition(self.store, self.history, TASK, 1)
        assert transition is not None
        self.assertEqual(transition["release_assignment"], 1)
        self.assertEqual(self.history[0], original[0])

    def test_corrupt_bounds_or_unrecorded_sixth_fix_leave_state_unusable(self):
        self.approve()
        self.finish(6)
        corrupt = copy.deepcopy(self.store)
        corrupt["plans"][0]["last_fix"] = 999
        with self.assertRaises(UsageError):
            validate_store(corrupt, self.history)

    def test_changed_operator_decision_supersedes_bounds_without_erasing_approval(self):
        original = copy.deepcopy(self.approve())
        data = {"id": "plan-2", "task": TASK, "checkpoint": "checkpoint-5", "scope": "A changed correction approach",
                "allowed_paths": ["src/parser.py"], "additional_fixes": 1, "authorization": AUTH}
        with self.assertRaisesRegex(UsageError, "supersedes"):
            authorize_plan(self.store, self.history, data, AT)
        changed = authorize_plan(self.store, self.history, {**data, "supersedes": "plan-1"}, AT)
        self.assertEqual(self.store["plans"][0], original)
        self.assertEqual((changed["first_fix"], changed["last_fix"]), (6, 6))
        with self.assertRaisesRegex(UsageError, "superseded"):
            validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)
        validate_work(self.store, self.history, TASK, 6, "plan-2", {**WORK, "scope": data["scope"]})
        validate_store(self.store, self.history)

    def test_review_receipts_preserve_independence_full_approval_and_current_artifact(self):
        self.approve()
        self.finish(6)
        data = {"dispatch": "fix-6", "head_revision": HEAD, "verdict": "blocking", "review_mode": "full",
                "reviewer": "independent-reviewer", "report": str(self.review), "changed_paths": ["src/parser.py"]}
        for change in ({"reviewer": "worker"}, {"verdict": "approved", "review_mode": "scoped"},
                       {"head_revision": "c" * 40}, {"changed_paths": ["outside/scope.py"]}):
            with self.subTest(change=change), self.assertRaises(UsageError):
                record_report(self.store, {**data, **change}, AT)
        record_report(self.store, data, AT)
        self.review.write_text("This report was replaced after it was recorded.\n")
        with self.assertRaisesRegex(UsageError, "artifact changed"):
            validate_work(self.store, self.history, TASK, 7, "plan-1", WORK)

    def test_future_or_corrupt_recovery_records_preserve_disk_and_refuse_history(self):
        self.approve()
        self.finish(6)
        variants = []
        future = copy.deepcopy(self.state)
        future["recovery"]["plans"][0]["schema_version"] = 2
        variants.append(future)
        invalid_result = copy.deepcopy(self.state)
        invalid_result["recovery"]["dispatches"][0]["result"] = None
        variants.append(invalid_result)
        invalid_version = copy.deepcopy(self.state)
        invalid_version["recovery"]["schema_version"] = True
        variants.append(invalid_version)
        path = self.root / "invalid.json"
        for variant in variants:
            with self.subTest(variant=variant):
                save_state(path, variant)
                before = path.read_bytes()
                warnings = []
                restored, usable = load_state_checked(path, warn=warnings.append)
                self.assertFalse(usable)
                self.assertEqual(restored["assignments"], [])
                self.assertEqual(path.read_bytes(), before)
                self.assertTrue(warnings)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"] = []
        with self.assertRaisesRegex(UsageError, "lacks its owner-managed"):
            validate_store(corrupt, self.history)

    def test_brief_identity_changes_with_content_and_explicit_reuse_is_detectable(self):
        common, brief = self.root / "COMMON.md", self.root / "developer.md"
        common.write_text("Common\n")
        brief.write_text("First attempt\n")
        paths_by_role = {"common": str(common), "developer": str(brief)}
        first = dispatch_identity(TASK, "developer", "worker", 1, paths_by_role, "try-1")
        brief.write_text("Changed attempt\n")
        second = dispatch_identity(TASK, "developer", "worker", 1, paths_by_role, "try-1")
        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])


if __name__ == "__main__":
    unittest.main()
