"""Outcome tests for bounded recovery and truthful, reusable attempt history."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from teamlead.errors import UsageError
from teamlead.recovery import (
    DIAGNOSIS_BOUND_CEILING,
    abort_pre_send, active_plans, authorize_context, authorize_plan, authorize_refused_dispatch, brief_identity, checkpoint, confirmed_fix,
    diagnose, require_investigation_before_judge, require_judge_mode,
    dispatch_identity, finish_dispatch, fresh_transition, mark_sending,
    migrate_store, prior_dispatch, record_refusal, record_report, refusal_move, register_task, reserve,
    task_statuses, validate_store, validate_work,
)
from teamlead.state import add_assignment, empty_state, load_state_checked, save_state

AT = "2026-02-03T10:00:00+00:00"
TASK = "fixture-task"
BASE = "a" * 40
HEAD = "b" * 40
AUTH = {"source": "fixture operator message", "quote": "Approve this task and its stated bounds."}
#: The operator's request a cited checkpoint ruling answers (#400).
REQUEST = {"source": "fixture operator message", "quote": "Ask the judge to rule on this exhaustion."}
WORK = {"base_revision": BASE, "scope": "Correct parser findings", "paths": ["src/parser.py"], "findings": ["F1"]}
PROGRESS = "PROGRESS: two of the three findings closed under the prior remedy.\n"


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
        investigation = self.root / "investigation.md"
        investigation.write_text("Reproduction: the quoted case. Cause: two parsers. Experiment: unify them.\n")
        self.investigation = str(investigation)
        self.investigation_sha = hashlib.sha256(investigation.read_bytes()).hexdigest()
        self.review = self.root / "review.md"
        self.review.write_text("Reviewed head: " + HEAD + "\nBlocking finding F1: quoted input is still accepted as a completion signal.\n")

    def exhaust(self):
        for fix in (None, 1, 2, 3, 4, 5):
            add_assignment(self.state, "2026-02-03T09:00:0{}+00:00".format(fix or 0), "developer", "worker", task=TASK, fix_round=fix)

    def seed_checkpoint(self):
        self.exhaust()
        self.consult_investigator("2026-02-03T09:30:00+00:00", "2026-02-03T09:45:00+00:00")
        add_assignment(self.state, AT, "judge", "judge", task=TASK)
        return checkpoint(self.store, self.history, {
            "id": "checkpoint-5", "task": TASK, "defect": "F1 remains open", "previous_attempts": "Five attempts changed parsing and quoting",
            "progress": "Some counterexamples now pass; the quoted case remains red", "change_in_approach": "Use one canonical parser",
            "judge_report": str(self.judge_report), "requested_by": REQUEST,
        }, AT, "judge")

    def approve(self):
        # The operator overrides a remedy; a diagnosis is recorded first.
        self.seed_checkpoint()
        remedy = self.diagnosis("diag-approve", "continue", 1)
        diag = self.run_diagnosis(remedy, "judge")
        return authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "checkpoint-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH,
            "supersedes": diag["plan"]}, AT)

    def reservation(self, number, plan="plan-1"):
        return {"id": f"fix-{number}", "task": TASK, "role": "developer", "agent": "worker", "fix_round": number,
                "fingerprint": "c" * 64, "plan": plan, "work": copy.deepcopy(WORK)}

    def finish(self, number, plan="plan-1"):
        record = self.reservation(number, plan)
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        add_assignment(self.state, "2026-02-03T11:00:0{}+00:00".format(number), "developer", "worker", task=TASK, fix_round=number)
        finish_dispatch(self.store, record["id"], {"status": "applied", **{
            key: record[key] for key in ("task", "role", "agent", "fix_round")}}, len(self.history) - 1, AT)

    def diagnosis_report(self, remedy, bound, name="diagnosis.md", extra="", assessment=None):
        path = self.root / name
        path.write_text(
            "DIAGNOSIS: the find-rate held flat while every round closed its finding.\n"
            "REMEDY: {} — {}\n"
            "BOUND: {} — one attempt per open finding.\n"
            "ASSESSMENT: {}\n"
            "EVIDENCE: rounds 10-20 and their review bodies.\n"
            "UNVERIFIED: none\n{}".format(remedy, "the named change", bound,
                                           self.investigation if assessment is None else assessment, extra))
        return str(path)

    def judge_after_developer(self, number):
        # The diagnosis gate needs the pinned judge's assignment after the
        # latest developer attempt, and the consultation it rules on before
        # that dispatch.
        self.consult_investigator("2026-02-03T11:30:0{}+00:00".format(number), "2026-02-03T11:45:0{}+00:00".format(number))
        add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(number), "judge", "judge", task=TASK)

    def next_checkpoint(self, name):
        # Each exhaustion records its own checkpoint; a plan's first fix is
        # always its checkpoint's round plus one.
        return checkpoint(self.store, self.history, {
            "id": name, "task": TASK, "defect": "F1 remains open", "previous_attempts": "The bounded remedy was spent",
            "progress": "The named change landed; the finding did not close",
            "change_in_approach": "Take the next remedy on the ladder"}, AT, "judge")["id"]

    def consult_investigator(self, at, assessed_at):
        """Record the consultation the judge rules on, assessed before it."""
        add_assignment(self.state, at, "investigator", "worker", task=TASK)
        self.investigator_index = len(self.history) - 1
        self.investigator_assessed_at = assessed_at
        return self.investigator_index

    def investigated(self, index=None, at=None, report=None):
        """The assessed investigator consultation #408 requires."""
        if index is None:
            index = getattr(self, "investigator_index", 0)
        if at is None:
            at = getattr(self, "investigator_assessed_at", "2026-02-03T09:45:00+00:00")
        report = self.investigation if report is None else report
        sha = (self.investigation_sha if report == self.investigation
               else hashlib.sha256(Path(report).read_bytes()).hexdigest() if Path(report).exists()
               else "d" * 64)
        return [{"task": TASK, "role": "investigator", "assignment_index": index, "at": at,
                 "report": report, "report_evidence": {"path": report, "sha256": sha}}]

    def run_diagnosis(self, data, judge="judge", enrolled=None, investigations=None):
        # The fixture's judge dispatch enrolls the report the request cites,
        # unless a case overrides it to prove the binding.
        if enrolled is None:
            enrolled = data.get("judge_report")
        if investigations is None:
            investigations = self.investigated()
        return diagnose(self.store, self.history, data, AT, judge, enrolled, enrolled is not None, investigations)

    def diagnosis(self, name, remedy, bound, checkpoint_id="checkpoint-5", extra="", assessment=None):
        return {"id": name, "task": TASK, "checkpoint": checkpoint_id,
                "judge_report": self.diagnosis_report(remedy, bound, name + ".md", extra, assessment),
                "scope": WORK["scope"], "allowed_paths": ["src/*"]}

    def test_a_bounded_remedy_records_the_plan_its_bound_authorizes(self):
        # coding-policy#407: the judge supplies the budget the operator used to.
        self.seed_checkpoint()
        record = self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        self.assertEqual((record["remedy"], record["bound"], record["fix_round"]), ("continue", 2, 5))
        plan = _item_plan = next(row for row in self.store["plans"] if row["id"] == record["plan"])
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 7))
        self.assertEqual(plan["authorization"]["source"], record["judge_evidence"]["path"])
        self.assertIn("REMEDY: continue", plan["authorization"]["quote"])
        # The ordinary allowance machinery enforces it from here.
        validate_work(self.store, self.history, TASK, 6, plan["id"], WORK)
        with self.assertRaisesRegex(UsageError, "outside the approved task or budget"):
            validate_work(self.store, self.history, TASK, 8, plan["id"], WORK)
        self.assertEqual(self.run_diagnosis(self.diagnosis("diag-1", "continue", 2)), record)
        validate_store(self.store, self.history)

    def test_the_remedy_ladder_descends_and_stop_is_terminal(self):
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-1", "continue", 1), "judge")
        # The bound must be spent before the next diagnosis.
        with self.assertRaisesRegex(UsageError, "unspent attempts under plan"):
            self.run_diagnosis(self.diagnosis("diag-2", "restructure", 2), "judge")
        self.finish(6, "diag-1:plan")
        self.judge_after_developer(6)
        second = self.next_checkpoint("checkpoint-6")
        # coding-policy#415: a rung is reissuable once, and only against the
        # progress the re-entry records. A remedy that produced none is not.
        with self.assertRaisesRegex(UsageError, "needs its PROGRESS line"):
            self.run_diagnosis(self.diagnosis("diag-2", "continue", 2, second), "judge")
        again = self.run_diagnosis(self.diagnosis("diag-2", "continue", 2, second, extra=PROGRESS), "judge")
        self.assertTrue(again["reissue"])
        self.finish(7, "diag-2:plan")
        self.finish(8, "diag-2:plan")
        self.judge_after_developer(8)
        third = self.next_checkpoint("checkpoint-7")
        # That rung is now spent; the reissue does not repeat.
        with self.assertRaisesRegex(UsageError, "may not sit above restructure"):
            self.run_diagnosis(self.diagnosis("diag-3", "continue", 1, third, extra=PROGRESS), "judge")
        self.assertFalse(self.run_diagnosis(self.diagnosis("diag-3", "restructure", 1, third), "judge")["reissue"])
        self.finish(9, "diag-3:plan")
        self.judge_after_developer(9)
        fourth = self.next_checkpoint("checkpoint-8")
        self.assertTrue(self.run_diagnosis(self.diagnosis("diag-4", "restructure", 1, fourth, extra=PROGRESS), "judge")["reissue"])
        self.finish(10, "diag-4:plan")
        self.judge_after_developer(10)
        fifth = self.next_checkpoint("checkpoint-9")
        with self.assertRaisesRegex(UsageError, "may not sit above stop"):
            self.run_diagnosis(self.diagnosis("diag-5", "restructure", 1, fifth, extra=PROGRESS), "judge")
        stop = self.run_diagnosis(self.diagnosis("diag-5", "stop", "none", fifth), "judge")
        self.assertEqual((stop["bound"], stop["plan"]), (None, None))
        with self.assertRaisesRegex(UsageError, "terminal"):
            self.run_diagnosis(self.diagnosis("diag-6", "stop", "none", fifth), "judge")
        self.assertEqual(task_statuses(self.store, self.history)[TASK]["status"], "diagnosed_stop")
        validate_store(self.store, self.history)

    def test_a_changed_scope_supersedes_an_unspent_remedy_and_still_descends(self):
        # coding-policy#407: Fix Loops re-enters on a changed scope or an
        # operator override, not only on an exhausted bound.
        self.seed_checkpoint()
        first = self.run_diagnosis(self.diagnosis("diag-1", "continue", 3), "judge")
        with self.assertRaisesRegex(UsageError, "unspent attempts under plan"):
            self.run_diagnosis(self.diagnosis("diag-2", "restructure", 2), "judge")
        with self.assertRaisesRegex(UsageError, "Supersedes must name"):
            self.run_diagnosis({**self.diagnosis("diag-2", "restructure", 2), "supersedes": "plan-nope"}, "judge")
        # Naming the plan is not the change: an early re-entry proves one.
        with self.assertRaisesRegex(UsageError, "needs the change it claims"):
            self.run_diagnosis({**self.diagnosis("diag-2", "restructure", 2), "supersedes": first["plan"]}, "judge")
        narrowed = {**self.diagnosis("diag-2", "restructure", 2), "supersedes": first["plan"], "allowed_paths": ["src/parser.py"]}
        # The ladder still descends: a supersession is a diagnosis like any other.
        with self.assertRaisesRegex(UsageError, "needs its PROGRESS line"):
            self.run_diagnosis({**narrowed, **self.diagnosis("diag-2", "continue", 2), "supersedes": first["plan"], "allowed_paths": ["src/parser.py"]}, "judge")
        # An operator override stands in for a changed scope.
        override = self.run_diagnosis({**self.diagnosis("diag-override", "restructure", 2),
                                       "supersedes": first["plan"], "authorization": AUTH})
        self.assertEqual(override["supersedes"], first["plan"])
        second = override
        self.assertEqual(second["supersedes"], first["plan"])
        plans = {row["id"]: row for row in self.store["plans"]}
        self.assertIn(first["plan"], plans)
        self.assertEqual(plans[second["plan"]]["supersedes"], first["plan"])
        with self.assertRaisesRegex(UsageError, "needs the change it claims"):
            self.run_diagnosis({**self.diagnosis("diag-late", "stop", "none"), "supersedes": second["plan"]})
        self.assertNotIn(first["plan"], [row["id"] for row in active_plans(self.store)])
        with self.assertRaisesRegex(UsageError, "superseded"):
            validate_work(self.store, self.history, TASK, 6, first["plan"], WORK)
        validate_work(self.store, self.history, TASK, 6, second["plan"], WORK)
        validate_store(self.store, self.history)

    def test_a_diagnosis_needs_an_exhausted_budget_a_checkpoint_and_the_pinned_judge(self):
        with self.assertRaisesRegex(UsageError, "requires id, task, checkpoint"):
            self.run_diagnosis({"id": "d", "task": TASK}, "judge")
        self.seed_checkpoint()
        for remedy, bound, message in (("continue", "0", "positive BOUND"), ("continue", "none", "positive BOUND"),
                                       ("sideways", "1", "must carry DIAGNOSIS")):
            with self.assertRaisesRegex(UsageError, message):
                self.run_diagnosis(self.diagnosis("diag-bad", remedy, bound), "judge")
        with self.assertRaisesRegex(UsageError, "pinned judge's completed assignment"):
            self.run_diagnosis(self.diagnosis("diag-1", "continue", 1), "someone-else")
        incomplete = self.root / "incomplete.md"
        incomplete.write_text("DIAGNOSIS: x\nREMEDY: continue — more\nBOUND: 2\n")
        with self.assertRaisesRegex(UsageError, "must carry DIAGNOSIS"):
            self.run_diagnosis({**self.diagnosis("diag-2", "continue", 1), "judge_report": str(incomplete)}, "judge")

    def test_a_stop_remedy_ends_implementation_on_the_task(self):
        # coding-policy#407: `stop` is terminal, so no allowance survives it.
        self.seed_checkpoint()
        first = self.run_diagnosis(self.diagnosis("diag-1", "continue", 3), "judge")
        validate_work(self.store, self.history, TASK, 6, first["plan"], WORK)
        self.run_diagnosis({**self.diagnosis("diag-2", "stop", "none"), "supersedes": first["plan"], "authorization": AUTH})
        for plan in (first["plan"], None):
            with self.assertRaisesRegex(UsageError, "terminal"):
                validate_work(self.store, self.history, TASK, 6, plan, WORK)
        with self.assertRaisesRegex(UsageError, "terminal"):
            validate_work(self.store, self.history, TASK, 3, None, None)
        # What `stop` ships still gets reviewed, tested and released.
        validate_work(self.store, self.history, TASK, None, None, None, implementation=False)
        # Only the operator overrides a ruling, and a plan over the stop is how.
        self.judge_after_developer(6)
        override = authorize_plan(self.store, self.history, {"id": "override", "task": TASK, "checkpoint": "checkpoint-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 1, "authorization": AUTH}, AT)
        self.assertEqual(override["first_fix"], 6)
        validate_work(self.store, self.history, TASK, 6, "override", WORK)
        with self.assertRaisesRegex(UsageError, "overridden by plan override"):
            validate_work(self.store, self.history, TASK, 6, None, WORK)
        validate_work(self.store, self.history, TASK, 6, "override", WORK, implementation=False)
        # The status follows the override, as validate_work does.
        self.assertEqual(task_statuses(self.store, self.history)[TASK]["status"], "within_authorized_budget")
        validate_store(self.store, self.history)

    def test_a_report_must_be_the_one_supervision_enrolled(self):
        self.seed_checkpoint()
        request = self.diagnosis("diag-1", "continue", 2)
        with self.assertRaisesRegex(UsageError, "not the one supervision enrolled"):
            self.run_diagnosis(request, enrolled=str(self.root / "elsewhere.md"))
        record = self.run_diagnosis(request)
        self.assertEqual(record["remedy"], "continue")

    def test_a_remedy_names_what_it_means(self):
        self.seed_checkpoint()
        bare = self.root / "bare.md"
        bare.write_text("DIAGNOSIS: flat find-rate\nREMEDY: restructure\nBOUND: 2 — one per finding\n"
                        "ASSESSMENT: " + self.investigation + "\nEVIDENCE: rounds 10-20\nUNVERIFIED: none\n")
        with self.assertRaisesRegex(UsageError, "names its remedy and what it means"):
            self.run_diagnosis({**self.diagnosis("diag-1", "continue", 2), "judge_report": str(bare)}, "judge")

    def test_a_bound_states_developer_attempts_within_a_ceiling(self):
        # coding-policy#415: the operator's budget was a human pricing the
        # spend; the replacement states its units and justifies the number.
        self.seed_checkpoint()
        with self.assertRaisesRegex(UsageError, "exceeds the 5-attempt ceiling"):
            self.run_diagnosis(self.diagnosis("diag-1", "continue", DIAGNOSIS_BOUND_CEILING + 1), "judge")
        bare = self.root / "unjustified.md"
        bare.write_text("DIAGNOSIS: flat find-rate\nREMEDY: continue — two more rounds\nBOUND: 2\n"
                        "ASSESSMENT: " + self.investigation + "\nEVIDENCE: rounds 10-20\nUNVERIFIED: none\n")
        with self.assertRaisesRegex(UsageError, "justifies the number"):
            self.run_diagnosis({**self.diagnosis("diag-1", "continue", 2), "judge_report": str(bare)}, "judge")
        record = self.run_diagnosis(self.diagnosis("diag-1", "continue", DIAGNOSIS_BOUND_CEILING), "judge")
        self.assertEqual(record["bound"], DIAGNOSIS_BOUND_CEILING)
        validate_store(self.store, self.history)

    def _report_without_assessment(self):
        path = self.root / "noassess.md"
        path.write_text("DIAGNOSIS: flat find-rate\nREMEDY: continue — two more rounds\n"
                        "BOUND: 2 — one per finding\nEVIDENCE: rounds 10-20\nUNVERIFIED: none\n")
        return str(path)

    def test_a_changed_investigator_report_refuses_the_diagnosis(self):
        # rules/stateful-artifacts.md Hints, Not Authority: the saved receipt
        # is a last-seen snapshot, so a rewritten report authorizes nothing.
        self.seed_checkpoint()
        Path(self.investigation).write_text("Rewritten after the assessment.\n")
        with self.assertRaisesRegex(UsageError, "changed since its assessment"):
            self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        Path(self.investigation).unlink()
        with self.assertRaisesRegex(UsageError, "Cannot read evidence"):
            self.run_diagnosis(self.diagnosis("diag-2", "continue", 2), "judge")

    def test_a_diagnosis_cites_the_assessment_it_ruled_on(self):
        # coding-policy#415: the ordering gate proves an assessment exists; the
        # citation proves this diagnosis consumed it.
        self.seed_checkpoint()
        with self.assertRaisesRegex(UsageError, "must carry DIAGNOSIS"):
            self.run_diagnosis({**self.diagnosis("diag-1", "continue", 2),
                                "judge_report": self._report_without_assessment()}, "judge")
        with self.assertRaisesRegex(UsageError, "not an assessed investigator report"):
            self.run_diagnosis(self.diagnosis("diag-1", "continue", 2, assessment=str(self.root / "other.md")), "judge")
        record = self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        self.assertEqual(record["investigator_report"]["report"], self.investigation)
        self.assertEqual(record["investigator_report"]["evidence"],
                         {"path": self.investigation, "sha256": self.investigation_sha})
        validate_store(self.store, self.history)

    def test_an_older_diagnosis_row_migrates_to_the_recorded_shape(self):
        # rules/stateful-artifacts.md: the owner upgrades, and a version-1 row
        # carried neither field, so its defaults are the facts it already held.
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        older = copy.deepcopy(self.store)
        row = older["diagnoses"][0]
        row.update(schema_version=1)
        del row["reissue"], row["investigator_report"]
        # Both record kinds migrate in one pass; neither short-circuits the
        # other (rules/stateful-artifacts.md Migration Policy).
        older["checkpoints"][0]["schema_version"] = 1
        older["checkpoints"][0].pop("requested_by", None)
        self.assertTrue(migrate_store(older))
        self.assertEqual(older["checkpoints"][0]["schema_version"], 2)
        self.assertEqual(older["diagnoses"][0]["schema_version"], 2)
        self.assertIs(older["diagnoses"][0]["reissue"], False)
        self.assertIsNone(older["diagnoses"][0]["investigator_report"])
        validate_store(older, self.history)

    def test_an_older_diagnosis_carrying_newer_fields_is_refused(self):
        # A version-1 row predates both fields, so one already carrying either
        # is unowned newer data; stamping it would let the value through.
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        for field, value in (("reissue", True), ("investigator_report", None)):
            corrupt = copy.deepcopy(self.store)
            row = corrupt["diagnoses"][0]
            row.update(schema_version=1)
            del row["reissue"], row["investigator_report"]
            row[field] = value
            with self.assertRaisesRegex(UsageError, "newer recorded fields"):
                migrate_store(corrupt)

    def test_a_judge_seat_is_not_spent_before_the_assessment_exists(self):
        # coding-policy#408: the gate guards the dispatch, not only the record,
        # so the most expensive seat is never spent on an uninvestigated loop.
        # Inside the allowance an ordinary dispute reaches the judge freely.
        add_assignment(self.state, "2026-02-03T09:00:00+00:00", "developer", "worker", task=TASK)
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK, []))
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, "unknown-task", []))
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "consult the investigator"):
            require_investigation_before_judge(self.store, self.history, TASK, [])
        index = self.consult_investigator("2026-02-03T09:30:00+00:00", "2026-02-03T09:45:00+00:00")
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK, self.investigated(index=index)))

    def test_a_declared_diagnosis_on_a_stopped_task_refuses_before_dispatch(self):
        # coding-policy#425: with the mode declared, the bound #400 could not
        # enforce is enforceable — before the expensive round, not after its
        # report exists.
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-stop", "stop", None), "judge")
        with self.assertRaisesRegex(UsageError, "diagnosed `stop`"):
            require_investigation_before_judge(self.store, self.history, TASK, self.investigated(),
                                               mode="diagnosis")
        # An adjudication on the same task is untouched: `stop` ends
        # implementation and the ladder, never a contested verdict's ruling.
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK,
                                                            self.investigated(), mode="adjudication"))
        # The operator's plan over the stop lifts the refusal.
        authorize_plan(self.store, self.history, {"id": "over-stop", "task": TASK, "checkpoint": "checkpoint-5",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH}, AT)
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK,
                                                            self.investigated(), mode="diagnosis"))

    def test_an_adjudication_carries_no_assessment_requirement(self):
        # The assessment gate belongs to diagnosis; an adjudication rules on a
        # contested verdict and needs none.
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "consult the investigator"):
            require_investigation_before_judge(self.store, self.history, TASK, [], mode="diagnosis")
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK, [],
                                                             mode="adjudication"))

    def test_an_unknown_mode_is_refused_rather_than_defaulted(self):
        self.seed_checkpoint()
        with self.assertRaisesRegex(UsageError, "declares its mode"):
            require_investigation_before_judge(self.store, self.history, TASK, self.investigated(),
                                               mode="whatever")
        with self.assertRaisesRegex(UsageError, "declares what it is for"):
            require_judge_mode(None)
        self.assertEqual(require_judge_mode("diagnosis"), "diagnosis")

    def test_a_stopped_task_still_reaches_the_judge_for_an_adjudication(self):
        # coding-policy#400: a pre-dispatch refusal was considered for a
        # `stop`ped task and dropped. `stop` ends implementation and the
        # diagnosis ladder, never adjudication — this gate sees no judge mode,
        # so refusing here would refuse a contested verdict's ruling too.
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-stop", "stop", None), "judge")
        self.assertIsNone(require_investigation_before_judge(self.store, self.history, TASK, self.investigated()))

    def test_a_diagnosis_rules_on_a_prepared_causal_assessment(self):
        # coding-policy#408: the investigator's profile is written for repeated
        # unsuccessful fixes, and the judge is the more expensive seat.
        self.seed_checkpoint()
        seeded, seeded_at = self.investigator_index, self.investigator_assessed_at
        request = self.diagnosis("diag-1", "continue", 2)
        with self.assertRaisesRegex(UsageError, "prepared causal assessment"):
            self.run_diagnosis(request, investigations=[])
        # Another task's assessment, and one predating the latest attempt, are
        # not this loop's evidence.
        with self.assertRaisesRegex(UsageError, "prepared causal assessment"):
            self.run_diagnosis(request, investigations=[{"task": "another", "role": "investigator", "assignment_index": 0}])
        with self.assertRaisesRegex(UsageError, "prepared causal assessment"):
            self.run_diagnosis(request, investigations=[{"task": TASK, "role": "architect", "assignment_index": 0}])
        with self.assertRaisesRegex(UsageError, "prepared causal assessment"):
            self.run_diagnosis(request, investigations=self.investigated(index=0))
        # A consultation delivered after the judge dispatch is not what it read.
        late = self.consult_investigator("2026-02-03T20:00:00+00:00", "2026-02-03T20:30:00+00:00")
        with self.assertRaisesRegex(UsageError, "assessed before the judge dispatch"):
            self.run_diagnosis(request, investigations=self.investigated(index=late))
        # Dispatched early, assessed late: the judge still did not read it.
        with self.assertRaisesRegex(UsageError, "assessed before the judge dispatch"):
            self.run_diagnosis(request, investigations=self.investigated(index=seeded, at="2026-02-03T23:00:00+00:00"))
        self.assertEqual(self.run_diagnosis(request, investigations=self.investigated(index=seeded, at=seeded_at))["remedy"], "continue")

    def test_an_adjudication_report_is_not_a_diagnosis(self):
        # coding-policy#407: RULING and ACTION belong to adjudication; a mixed
        # report could carry a blocked ruling and still grant attempts.
        self.seed_checkpoint()
        mixed = self.root / "mixed.md"
        mixed.write_text("RULING: blocked — which boundary ships?\nACTION: ask the operator\n"
                         "DIAGNOSIS: flat find-rate\nREMEDY: continue — two more rounds\n"
                         "BOUND: 2 — one per finding\nASSESSMENT: " + self.investigation
                         + "\nEVIDENCE: rounds 1-5\nUNVERIFIED: none\n")
        with self.assertRaisesRegex(UsageError, "adjudication's RULING or ACTION"):
            self.run_diagnosis({**self.diagnosis("diag-1", "continue", 2), "judge_report": str(mixed)})
        self.assertEqual(self.store["diagnoses"], [])
        self.assertEqual([row for row in self.store["plans"]], [])

    def test_corrupt_diagnoses_refuse_the_ledger(self):
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        for mutate in (
            lambda row: row.update(remedy="sideways"),
            lambda row: row.update(bound=None),
            lambda row: row.update(plan=None),
            lambda row: row.update(judge_evidence="not-a-receipt"),
            lambda row: row.update(reissue=True),
            lambda row: row.update(investigator_report={"schema_version": 2, "report": "relative.md",
                                                        "evidence": {"path": "relative.md", "sha256": "d" * 64}}),
            lambda row: row.update(investigator_report={"schema_version": 2, "report": "/tmp/other.md",
                                                        "evidence": row["investigator_report"]["evidence"]}),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(corrupt["diagnoses"][0])
            with self.assertRaises(UsageError):
                validate_store(corrupt, self.history)
        corrupt = copy.deepcopy(self.store)
        corrupt["diagnoses"].append({**corrupt["diagnoses"][0], "id": "diag-back", "remedy": "continue"})
        with self.assertRaisesRegex(UsageError, "must move down the remedy ladder"):
            validate_store(corrupt, self.history)
        for mutate in (lambda row: row.update(fix_round=4),
                       lambda row: row.update(base_revision="f" * 40)):
            corrupt = copy.deepcopy(self.store)
            mutate(corrupt["diagnoses"][0])
            with self.assertRaisesRegex(UsageError, "another task, base or fix round"):
                validate_store(corrupt, self.history)

    def test_a_diagnosis_plan_is_matched_on_every_field_the_remedy_derives(self):
        # coding-policy#412: the check compared task, bound and authorization
        # source alone, so a same-task plan carrying another scope or path set
        # could be attached and `validate_work` would then enforce ITS budget
        # and scope against this diagnosis.
        self.seed_checkpoint()
        self.run_diagnosis(self.diagnosis("diag-1", "continue", 2), "judge")
        for field, value in (("scope", "Something else entirely"),
                             ("allowed_paths", ["docs/*"])):
            with self.subTest(field=field):
                corrupt = copy.deepcopy(self.store)
                plan = next(row for row in corrupt["plans"] if row["id"] == corrupt["diagnoses"][0]["plan"])
                plan[field] = value
                with self.assertRaisesRegex(UsageError, "does not match the remedy that authorized it"):
                    validate_store(corrupt, self.history)
        # The rest are caught by the plan's own guards; the ledger is refused
        # either way, and no substitution reaches `validate_work`.
        for field, value in (("checkpoint", "another-checkpoint"),
                             ("base_revision", "f" * 40),
                             ("first_fix", 9),
                             ("last_fix", 99),
                             ("supersedes", "some-other-plan")):
            with self.subTest(field=field):
                corrupt = copy.deepcopy(self.store)
                plan = next(row for row in corrupt["plans"] if row["id"] == corrupt["diagnoses"][0]["plan"])
                plan[field] = value
                with self.assertRaises(UsageError):
                    validate_store(corrupt, self.history)

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
        self.assertEqual([row["id"] for row in active_plans(self.store)], ["plan-1"])
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
        self.assertEqual((status["status"], status["paused_work"]), ("awaiting_diagnosis", "implementation"))

    def test_a_cited_judge_report_still_needs_the_pinned_judge_after_latest_development(self):
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "pinned judge"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1", "previous_attempts": "Five fixes",
                "progress": "Still blocked", "change_in_approach": "Reassess", "judge_report": str(self.judge_report), "requested_by": REQUEST}, AT, "judge")

    def test_an_exhausted_allowance_awaits_the_diagnosis_without_a_judge_citation(self):
        # The allowance boundary is a budget decision only the operator makes.
        # Requiring a ruling first spent 16 of one fleet's 30 lifetime judge
        # dispatches on one task (gamussa/coding-policy#396).
        self.exhaust()
        record = checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
            "previous_attempts": "Five fixes", "progress": "Still blocked",
            "change_in_approach": "Reassess"}, AT, "judge")
        self.assertNotIn("judge_evidence", record)
        self.assertNotIn("judge_agent", record)
        self.assertEqual(record["fix_round"], 5)
        status = task_statuses(self.store, self.history)[TASK]
        self.assertEqual((status["status"], status["paused_work"]), ("awaiting_diagnosis", "implementation"))

    def test_that_checkpoint_carries_the_operator_bounded_approval_through(self):
        self.exhaust()
        self.consult_investigator("2026-02-03T09:30:00+00:00", "2026-02-03T09:45:00+00:00")
        add_assignment(self.state, AT, "judge", "judge", task=TASK)
        checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
            "previous_attempts": "Five fixes", "progress": "Still blocked",
            "change_in_approach": "Reassess"}, AT, "judge")
        # The operator overrides a remedy; without one there is nothing to override.
        with self.assertRaisesRegex(UsageError, "no diagnosis at fix round 5"):
            authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "cp",
                "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH}, AT)
        diag = self.run_diagnosis(self.diagnosis("diag-cp", "continue", 1, "cp"))
        self.assertEqual(diag["fix_round"], 5)
        plan = authorize_plan(self.store, self.history, {"id": "plan-1", "task": TASK, "checkpoint": "cp",
            "scope": WORK["scope"], "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH,
            "supersedes": diag["plan"]}, AT)
        self.assertEqual((plan["first_fix"], plan["last_fix"]), (6, 7))
        validate_work(self.store, self.history, TASK, 6, "plan-1", WORK)

    def test_an_unexhausted_budget_still_refuses_the_checkpoint(self):
        with self.assertRaisesRegex(UsageError, "not exhausted"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
                "previous_attempts": "None", "progress": "None",
                "change_in_approach": "Reassess"}, AT, "judge")

    def test_an_exhausted_budget_names_the_diagnosis_not_an_operator_plan(self):
        with self.assertRaisesRegex(UsageError, "teamlead diagnose"):
            validate_work(self.store, self.history, TASK, 6, None, WORK)

    def replay_data(self):
        return {"id": "checkpoint-5", "task": TASK, "defect": "F1 remains open",
                "previous_attempts": "Five attempts changed parsing and quoting",
                "progress": "Some counterexamples now pass; the quoted case remains red",
                "change_in_approach": "Use one canonical parser", "judge_report": str(self.judge_report), "requested_by": REQUEST}

    def test_a_checkpoint_written_before_the_bump_migrates_with_its_evidence(self):
        prior = self.seed_checkpoint()
        evidence = copy.deepcopy(prior["judge_evidence"])
        prior["schema_version"] = 1
        prior.pop("requested_by", None)
        self.assertTrue(migrate_store(self.store))
        self.assertEqual(prior["schema_version"], 2)
        self.assertNotIn("requested_by", prior)
        self.assertEqual((prior["judge_agent"], prior["judge_evidence"]), ("judge", evidence))
        validate_store(self.store, self.history)
        self.assertFalse(migrate_store(self.store))

    def test_a_rejected_document_is_left_exactly_as_it_was_found(self):
        # coding-policy#400: the nested row upgrades ran before the enclosing
        # document's own preflight, so a store the migration then refused had
        # already been stamped in memory.
        prior = self.seed_checkpoint()
        prior["schema_version"] = 1
        prior.pop("requested_by", None)
        rejected = copy.deepcopy(self.store)
        rejected["schema_version"] = 5
        # Unowned newer data for a version-5 document: the preflight refuses it.
        before = copy.deepcopy(rejected)
        with self.assertRaisesRegex(UsageError, "unowned newer"):
            migrate_store(rejected)
        self.assertEqual(rejected, before)
        self.assertEqual(rejected["checkpoints"][0]["schema_version"], 1)

    def test_an_older_checkpoint_without_its_required_ruling_refuses_to_migrate(self):
        prior = self.seed_checkpoint()
        prior["schema_version"] = 1
        prior.pop("requested_by", None)
        del prior["judge_evidence"]
        with self.assertRaisesRegex(UsageError, "missing the ruling evidence"):
            migrate_store(self.store)

    def test_replaying_a_legacy_checkpoint_with_its_original_payload_returns_it(self):
        # coding-policy#400: the receipt is required of NEW records. An older
        # row was written before the field existed, so re-running its original
        # request is an already-processed one, not a missing receipt
        # (rules/file-hygiene.md Idempotency).
        prior = self.seed_checkpoint()
        prior["schema_version"] = 2
        prior.pop("requested_by", None)
        legacy = {key: value for key, value in self.replay_data().items() if key != "requested_by"}
        replay = checkpoint(self.store, self.history, legacy, AT, "judge")
        self.assertIs(replay, prior)
        self.assertNotIn("requested_by", replay)
        self.assertEqual(len(self.store["checkpoints"]), 1)
        validate_store(self.store, self.history)

    def test_replaying_a_migrated_checkpoint_returns_it(self):
        # The row outlives the writer's version: re-running an already-recorded
        # checkpoint must return it, not read the version as changed evidence.
        prior = self.seed_checkpoint()
        prior["schema_version"] = 1
        prior.pop("requested_by", None)
        migrate_store(self.store)
        replay = checkpoint(self.store, self.history, self.replay_data(), AT, "judge")
        self.assertIs(replay, prior)
        self.assertEqual(len(self.store["checkpoints"]), 1)
        validate_store(self.store, self.history)

    def test_reusing_a_checkpoint_identity_for_other_evidence_still_refuses(self):
        self.seed_checkpoint()
        with self.assertRaisesRegex(UsageError, "already describes different evidence"):
            checkpoint(self.store, self.history, {"id": "checkpoint-5", "task": TASK, "defect": "Another defect",
                "previous_attempts": "Five attempts changed parsing and quoting",
                "progress": "Some counterexamples now pass; the quoted case remains red",
                "change_in_approach": "Use one canonical parser", "judge_report": str(self.judge_report), "requested_by": REQUEST}, AT, "judge")

    def test_a_second_cited_ruling_for_the_task_is_refused(self):
        # The bound is per task: a re-granted budget exhausting again must not
        # buy another ruling (gamussa/coding-policy#396).
        self.seed_checkpoint()
        for fix in (6, 7):
            add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(fix), "developer", "worker", task=TASK, fix_round=fix)
        with self.assertRaisesRegex(UsageError, "at most one per task"):
            checkpoint(self.store, self.history, {"id": "checkpoint-7", "task": TASK, "defect": "F1 still open",
                "previous_attempts": "Seven attempts", "progress": "Unchanged",
                "change_in_approach": "Rewrite the parser", "judge_report": str(self.judge_report), "requested_by": REQUEST}, AT, "judge")

    def test_a_later_checkpoint_without_a_ruling_still_records(self):
        self.seed_checkpoint()
        for fix in (6, 7):
            add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(fix), "developer", "worker", task=TASK, fix_round=fix)
        record = checkpoint(self.store, self.history, {"id": "checkpoint-7", "task": TASK, "defect": "F1 still open",
            "previous_attempts": "Seven attempts", "progress": "Unchanged",
            "change_in_approach": "Rewrite the parser"}, AT, "judge")
        self.assertEqual(record["fix_round"], 7)
        self.assertNotIn("judge_evidence", record)

    def test_a_partial_ruling_trio_on_a_checkpoint_is_refused(self):
        prior = self.seed_checkpoint()
        del prior["judge_evidence"]
        with self.assertRaises(UsageError):
            validate_store(self.store, self.history)

    def test_a_cited_ruling_records_the_operator_request_that_asked_for_it(self):
        # coding-policy#400: the ledger called the ruling "operator-requested"
        # while recording nothing of the request.
        self.exhaust()
        self.consult_investigator("2026-02-03T09:30:00+00:00", "2026-02-03T09:45:00+00:00")
        add_assignment(self.state, AT, "judge", "judge", task=TASK)
        base = {"id": "cp", "task": TASK, "defect": "F1 remains open",
                "previous_attempts": "Five attempts", "progress": "Partly",
                "change_in_approach": "One parser"}
        with self.assertRaisesRegex(UsageError, "the operator's to request"):
            checkpoint(self.store, self.history, {**base, "judge_report": str(self.judge_report)}, AT, "judge")
        for bad in ({"source": "operator"}, {"source": "operator", "quote": ""}, "operator said so"):
            with self.subTest(receipt=bad):
                with self.assertRaises(UsageError):
                    checkpoint(self.store, self.history,
                               {**base, "judge_report": str(self.judge_report), "requested_by": bad}, AT, "judge")
        # The receipt without a ruling has nothing to authorize.
        with self.assertRaisesRegex(UsageError, "record the checkpoint without it"):
            checkpoint(self.store, self.history, {**base, "requested_by": REQUEST}, AT, "judge")
        record = checkpoint(self.store, self.history,
                            {**base, "judge_report": str(self.judge_report), "requested_by": REQUEST}, AT, "judge")
        self.assertEqual(record["requested_by"], REQUEST)
        validate_store(self.store, self.history)

    def test_a_ledger_citing_two_rulings_for_one_task_is_refused(self):
        # coding-policy#400: `checkpoint` refuses the second as it writes it;
        # the read boundary checked each row alone and accepted both.
        self.seed_checkpoint()
        corrupt = copy.deepcopy(self.store)
        first = corrupt["checkpoints"][0]
        corrupt["checkpoints"].append({**first, "id": "checkpoint-5b"})
        with self.assertRaisesRegex(UsageError, "more than one operator-requested ruling"):
            validate_store(corrupt, self.history)

    def test_legacy_citations_predating_the_bound_still_read(self):
        # coding-policy#436: the bound arrived with version 3. Reading it over
        # version-2 rows made ledgers written before it exist unreadable, which
        # blocked new work on four tasks and 20 rows of real history.
        self.seed_checkpoint()
        legacy = copy.deepcopy(self.store)
        for index, row in enumerate(list(legacy["checkpoints"])):
            row["schema_version"] = 2
            row.pop("requested_by", None)
            legacy["checkpoints"].append({**copy.deepcopy(row), "id": "legacy-{}".format(index)})
        self.assertEqual(len(legacy["checkpoints"]), 2)
        validate_store(legacy, self.history)
        # A version-3 row still carries the bound: one per task, no more.
        mixed = copy.deepcopy(legacy)
        current = {**copy.deepcopy(self.store["checkpoints"][0]), "id": "current-a"}
        mixed["checkpoints"].append(current)
        validate_store(mixed, self.history)
        mixed["checkpoints"].append({**copy.deepcopy(current), "id": "current-b"})
        with self.assertRaisesRegex(UsageError, "more than one operator-requested ruling"):
            validate_store(mixed, self.history)

    def test_an_unknown_checkpoint_field_is_refused(self):
        self.exhaust()
        with self.assertRaisesRegex(UsageError, "optional judge_report"):
            checkpoint(self.store, self.history, {"id": "cp", "task": TASK, "defect": "F1",
                "previous_attempts": "Five fixes", "progress": "Still blocked",
                "change_in_approach": "Reassess", "operator_note": "approve"}, AT, "judge")

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
        self.assertEqual(next(row for row in self.store["plans"] if row["id"] == "plan-1"), original)
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

    REPORT = "/reports/tester.md"
    BRIEF = "brief-identity-tester"

    def dispatch_tester(self, number, agent, identity: "str | None" = BRIEF):
        record = {"id": "tester-{}-{}".format(number, agent), "task": TASK, "role": "tester", "agent": agent, "fix_round": None,
                  "fingerprint": ("%02d" % number) * 32, "plan": None, "work": None, "brief_identity": identity,
                  "provider": agent.rsplit("-", 1)[0]}
        reserve(self.store, record, AT)
        mark_sending(self.store, record["id"], AT, {"cleared": True})
        add_assignment(self.state, "2026-02-03T12:00:0{}+00:00".format(number), "tester", agent, task=TASK)
        finish_dispatch(self.store, record["id"], {"status": "applied", **{
            key: record[key] for key in ("task", "role", "agent", "fix_round")}}, len(self.history) - 1, AT)
        return record["id"]

    def refusal_receipt(self, agent, name="refusal.json", **overrides):
        path = self.root / name
        path.write_text(json.dumps({"agent": agent, "state": "idle", "report_path": self.REPORT,
                                    "found": False, "elapsed_seconds": 12, "reason": "terminal_provider_refusal", **overrides}))
        return str(path)

    def test_refusal_is_recorded_once_against_its_applied_dispatch(self):
        # coding-policy#399: wait-report's exit 5 went to stdout and nowhere
        # else, so nothing could tell a first refusal from a second.
        first = self.dispatch_tester(1, "codex-a")
        receipt = self.refusal_receipt("codex-a")
        with self.assertRaisesRegex(UsageError, "requires dispatch and receipt"):
            record_refusal(self.store, {"dispatch": first}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "wrong.json", reason="marker_unconfirmed")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("someone-else", "other.json")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "not wait-report JSON"):
            record_refusal(self.store, {"dispatch": first, "receipt": str(self.review)}, AT, "codex", self.REPORT)
        result = record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex", self.REPORT)
        self.assertEqual((result["provider"], result["reason"]), ("codex", "terminal_provider_refusal"))
        self.assertEqual(result["evidence"]["sha256"], hashlib.sha256(Path(receipt).read_bytes()).hexdigest())
        self.assertEqual(record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex", self.REPORT), result)
        with self.assertRaisesRegex(UsageError, "different refusal receipt"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "later.json", elapsed_seconds=99)}, AT, "codex", self.REPORT)
        self.assertEqual([row["kind"] for row in self.store["events"] if row["kind"] == "provider_refusal_recorded"], ["provider_refusal_recorded"])
        pending = {"id": "tester-pending", "task": TASK, "role": "tester", "agent": "claude-a", "fix_round": None,
                   "fingerprint": "ab" * 32, "plan": None, "work": None}
        reserve(self.store, pending, AT)
        with self.assertRaisesRegex(UsageError, "confirmed applied dispatch"):
            record_refusal(self.store, {"dispatch": "tester-pending", "receipt": self.refusal_receipt("claude-a", "pending.json")}, AT, "claude", self.REPORT)
        abort_pre_send(self.store, "tester-pending", AT, "fixture")
        validate_store(self.store, self.history)

    def test_refusal_receipt_must_bind_to_the_dispatch_report_and_full_exit_five_shape(self):
        first = self.dispatch_tester(1, "codex-a")
        with self.assertRaisesRegex(UsageError, "no supervision enrollment"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", None)
        with self.assertRaisesRegex(UsageError, "names report /reports/other.md"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "other.json", report_path="/reports/other.md")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "complete exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "working.json", state="working")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "complete exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a", "extra.json", extra=True)}, AT, "codex", self.REPORT)
        result = record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        self.assertEqual(result["report_path"], self.REPORT)
        validate_store(self.store, self.history)

    def test_a_malformed_receipt_is_a_usage_error_not_a_crash(self):
        # coding-policy#403: `agent` and `state` reached set membership
        # unvalidated, so a list or object raised TypeError out of the CLI.
        first = self.dispatch_tester(1, "codex-a")
        for name, override in (("list-agent", {"agent": ["codex-a"]}), ("dict-state", {"state": {"idle": True}}),
                               ("int-reason", {"reason": 5}), ("list-report", {"report_path": [self.REPORT]}),
                               ("negative", {"elapsed_seconds": -1}), ("float", {"elapsed_seconds": 1.5})):
            path = self.root / (name + ".json")
            path.write_text(json.dumps({"agent": "codex-a", "state": "idle", "report_path": self.REPORT, "found": False,
                                        "elapsed_seconds": 12, "reason": "terminal_provider_refusal", **override}))
            with self.assertRaises(UsageError):
                record_refusal(self.store, {"dispatch": first, "receipt": str(path)}, AT, "codex", self.REPORT)
        self.assertIsNone(self.store["dispatches"][0].get("refusal"))

    def test_a_not_sent_retry_refreshes_the_provider_the_fingerprint_misses(self):
        # coding-policy#403: reserve reuses a not_sent row, so a config change
        # before the retry would otherwise leave the original provider on it.
        record = {"id": "tester-retry", "task": TASK, "role": "tester", "agent": "codex-a", "fix_round": None,
                  "fingerprint": "cd" * 32, "plan": None, "work": None, "brief_identity": self.BRIEF, "provider": "codex"}
        reserve(self.store, record, AT)
        abort_pre_send(self.store, record["id"], AT, "fixture")
        reserve(self.store, {**record, "provider": "claude", "brief_identity": "brief-identity-refreshed"}, AT)
        saved = next(row for row in self.store["dispatches"] if row["id"] == record["id"])
        self.assertEqual((saved["provider"], saved["brief_identity"]), ("claude", "brief-identity-refreshed"))
        # A stale move naming the old provider would fail the ledger on the
        # next refusal, so the retry drops one the new record does not carry.
        abort_pre_send(self.store, record["id"], AT, "fixture")
        self.store["dispatches"][-1]["refusal_move"] = {"schema_version": 1, "from": "gone", "from_provider": "codex", "provider": "claude"}
        reserve(self.store, {**record, "provider": "grok"}, AT)
        saved = next(row for row in self.store["dispatches"] if row["id"] == record["id"])
        self.assertNotIn("refusal_move", saved)
        self.assertEqual(saved["provider"], "grok")
        validate_store(self.store, self.history)

    def test_the_dispatchs_recorded_provider_outranks_the_current_config(self):
        # coding-policy#403: a config edit between the send and the record
        # must not re-attribute the refusal to the new kind.
        first = self.dispatch_tester(1, "codex-a")
        result = record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "claude", self.REPORT)
        self.assertEqual(result["provider"], "codex")
        legacy = self.dispatch_tester(2, "grok-a")
        del self.store["dispatches"][-1]["provider"]
        self.assertEqual(record_refusal(self.store, {"dispatch": legacy, "receipt": self.refusal_receipt("grok-a", "legacy.json")}, AT, "grok", self.REPORT)["provider"], "grok")
        validate_store(self.store, self.history)

    def test_a_move_never_reuses_a_report_path_the_chain_burned(self):
        # coding-policy#403: two attempts enrolled against one file would let
        # their evidence collide.
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "already carries the refusal"):
            refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF, self.REPORT)
        self.assertIsNotNone(refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF, "/reports/tester-2.md"))
        # An alias of the burned path is the same file (#403).
        with self.assertRaisesRegex(UsageError, "already carries the refusal"):
            refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF, "/reports/sub/../tester.md")
        self.assertIsNotNone(refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF))

    def test_a_reworded_brief_is_not_a_move(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "reworded brief is not a move"):
            refusal_move(self.store, TASK, "tester", None, "claude", "brief-identity-reworded")
        self.assertIsNotNone(refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF))
        validate_store(self.store, self.history)

    def test_a_refused_dispatch_without_brief_identity_cannot_be_moved(self):
        legacy = self.dispatch_tester(1, "grok-a", identity=None)
        del self.store["dispatches"][-1]["brief_identity"]
        record_refusal(self.store, {"dispatch": legacy, "receipt": self.refusal_receipt("grok-a", "legacy.json")}, AT, "grok", self.REPORT)
        with self.assertRaisesRegex(UsageError, "predates brief identity"):
            refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF)
        validate_store(self.store, self.history)

    def test_brief_identity_masks_only_the_report_path(self):
        common = self.root / "COMMON.md"
        common.write_text("# common\n")
        brief = self.root / "tester.md"
        paths = {"common": str(common), "tester": str(brief)}
        brief.write_text("Write `/reports/a.md` covering the plan.\nREPORT: /reports/a.md\n")
        first = brief_identity(paths, "tester", "/reports/a.md")
        brief.write_text("Write `/reports/b.md` covering the plan.\nREPORT: /reports/b.md\n")
        self.assertEqual(brief_identity(paths, "tester", "/reports/b.md"), first)
        self.assertNotEqual(brief_identity(paths, "tester", None), first)
        brief.write_text("Write `/reports/b.md` covering the plan.\nSkip the security checks.\nREPORT: /reports/b.md\n")
        self.assertNotEqual(brief_identity(paths, "tester", "/reports/b.md"), first)
        with self.assertRaisesRegex(UsageError, "Cannot read brief"):
            brief_identity({**paths, "tester": str(self.root / "missing.md")}, "tester", None)

    def test_one_refusal_permits_one_move_and_the_second_stops_the_line(self):
        self.assertIsNone(refusal_move(self.store, TASK, "tester", None, "codex", self.BRIEF))
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "same provider"):
            refusal_move(self.store, TASK, "tester", None, "codex", self.BRIEF)
        self.assertIsNone(refusal_move(self.store, TASK, "reviewer", None, "codex", self.BRIEF))
        self.assertIsNone(refusal_move(self.store, TASK, "tester", 3, "codex", self.BRIEF))
        self.assertIsNone(refusal_move(self.store, "another-task", "tester", None, "codex", self.BRIEF))
        move = refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF)
        self.assertEqual(move, {"schema_version": 1, "from": first, "from_provider": "codex", "provider": "claude"})
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = move
        validate_store(self.store, self.history)
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude", self.REPORT)
        with self.assertRaisesRegex(UsageError, "refused by 2 providers") as caught:
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        self.assertEqual(caught.exception.details["refusals"], [first, second])
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "codex", self.BRIEF)
        validate_store(self.store, self.history)

    def test_a_second_move_waits_for_the_first_moves_outcome(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        move = refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF)
        pending = {"id": "tester-move", "task": TASK, "role": "tester", "agent": "claude-a", "fix_round": None,
                   "fingerprint": "ab" * 32, "plan": None, "work": None, "refusal_move": move, "brief_identity": self.BRIEF}
        reserve(self.store, pending, AT)
        with self.assertRaisesRegex(UsageError, "already moved to provider claude"):
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        abort_pre_send(self.store, "tester-move", AT, "fixture")
        retry = refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        assert retry is not None
        self.assertEqual(retry["from"], first)
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = move
        with self.assertRaisesRegex(UsageError, "already moved to provider claude") as caught:
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        self.assertEqual(caught.exception.details["moves"], [second])
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude", self.REPORT)
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        validate_store(self.store, self.history)

    def test_store_migration_stamps_a_clean_older_store_and_refuses_unowned_fields(self):
        first = self.dispatch_tester(1, "codex-a")
        identified = copy.deepcopy(self.store)
        identified["schema_version"] = 5
        del identified["refusal_authorizations"]
        with self.assertRaisesRegex(UsageError, "unowned newer refusal"):
            migrate_store(identified)
        old = copy.deepcopy(self.store)
        old["schema_version"] = 5
        del old["refusal_authorizations"]
        del old["diagnoses"]
        del old["legacy_ruling_recoveries"]
        for row in old["dispatches"]:
            del row["brief_identity"]
            del row["provider"]
        before = copy.deepcopy(old)
        self.assertTrue(migrate_store(old))
        self.assertEqual(old["schema_version"], 9)
        self.assertEqual(old.pop("refusal_authorizations"), [])
        self.assertEqual(old.pop("diagnoses"), [])
        self.assertEqual(old.pop("legacy_ruling_recoveries"), [])
        self.assertEqual({key: value for key, value in old.items() if key != "schema_version"},
                         {key: value for key, value in before.items() if key != "schema_version"})
        old["refusal_authorizations"] = []
        old["diagnoses"] = []
        old["legacy_ruling_recoveries"] = []
        validate_store(old, self.history)
        self.assertFalse(migrate_store(old))
        stale = copy.deepcopy(self.store)
        stale["schema_version"] = 5
        for row in stale["dispatches"]:
            del row["brief_identity"]
            del row["provider"]
        with self.assertRaisesRegex(UsageError, "unowned newer records"):
            migrate_store(stale)
        # Version 6 owned brief_identity but not provider: a v6 store carrying
        # one is unowned newer data, a clean one stamps to 7 (#403).
        six = copy.deepcopy(self.store)
        six["schema_version"] = 6
        del six["diagnoses"]
        del six["legacy_ruling_recoveries"]
        with self.assertRaisesRegex(UsageError, "unowned newer refusal"):
            migrate_store(six)
        six = copy.deepcopy(self.store)
        six["schema_version"] = 6
        del six["diagnoses"]
        del six["legacy_ruling_recoveries"]
        for row in six["dispatches"]:
            del row["provider"]
        self.assertTrue(migrate_store(six))
        self.assertEqual(six["schema_version"], 9)
        self.assertEqual(six["diagnoses"], [])
        validate_store(six, self.history)
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        for version in (4, 5):
            stale = copy.deepcopy(self.store)
            stale["schema_version"] = version
            del stale["refusal_authorizations"]
            del stale["diagnoses"]
            for row in stale["dispatches"]:
                del row["brief_identity"]
                del row["provider"]
            with self.assertRaisesRegex(UsageError, "unowned newer refusal"):
                migrate_store(stale)
        with self.assertRaisesRegex(UsageError, "Unsupported recovery schema"):
            validate_store({**copy.deepcopy(self.store), "schema_version": 5}, self.history)

    def test_operator_authorization_permits_one_dispatch_after_the_stop(self):
        grant = {"id": "auth-1", "task": TASK, "role": "tester", "fix_round": None, "provider": "codex", "brief": "revised",
                 "decision": "Run the revised tester brief on codex.", "authorization": AUTH}
        with self.assertRaisesRegex(UsageError, "refusals from 0 provider"):
            authorize_refused_dispatch(self.store, grant, AT)
        with self.assertRaisesRegex(UsageError, "requires id, task, role"):
            authorize_refused_dispatch(self.store, {**grant, "extra": 1}, AT)
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        # One refusal is a move, not an operator decision (Copilot on #402).
        with self.assertRaisesRegex(UsageError, "refusals from 1 provider"):
            authorize_refused_dispatch(self.store, grant, AT)
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = {"schema_version": 1, "from": first, "from_provider": "codex", "provider": "claude"}
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude", self.REPORT)
        with self.assertRaisesRegex(UsageError, "authorize-refused-dispatch"):
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        saved = authorize_refused_dispatch(self.store, grant, AT)
        self.assertEqual(authorize_refused_dispatch(self.store, grant, AT), saved)
        with self.assertRaisesRegex(UsageError, "different decision"):
            authorize_refused_dispatch(self.store, {**grant, "decision": "Something else."}, AT)
        with self.assertRaisesRegex(UsageError, "unchanged or revised"):
            authorize_refused_dispatch(self.store, {**grant, "id": "auth-bad", "brief": "anything"}, AT)
        # The grant replaces the stop with its own scope: the approved provider and, here, a revised brief.
        with self.assertRaisesRegex(UsageError, "approves provider codex"):
            refusal_move(self.store, TASK, "tester", None, "grok", "brief-identity-reworded")
        move = refusal_move(self.store, TASK, "tester", None, "codex", "brief-identity-reworded")
        self.assertEqual(move, {"schema_version": 1, "from": second, "from_provider": "claude", "provider": "codex", "authorization": "auth-1"})
        third = self.dispatch_tester(3, "codex-a", identity="brief-identity-reworded")
        self.store["dispatches"][-1]["refusal_move"] = move
        validate_store(self.store, self.history)
        # The grant is consumed; the stop holds again until another decision.
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        record_refusal(self.store, {"dispatch": third, "receipt": self.refusal_receipt("codex-a", "third.json")}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "refused by 2 providers"):
            refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"][1]["refusal_move"]["authorization"] = "auth-1"
        with self.assertRaisesRegex(UsageError, "reuses a consumed authorization"):
            validate_store(corrupt, self.history)
        for mutate in (
            lambda grant: grant.update(role="reviewer"),
            lambda grant: grant.update(provider="grok"),
            lambda grant: grant.update(brief="unchanged"),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(corrupt["refusal_authorizations"][0])
            with self.assertRaisesRegex(UsageError, "exceeds its authorization"):
                validate_store(corrupt, self.history)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"][-1]["refusal"]["provider"] = "grok"
        with self.assertRaisesRegex(UsageError, "other than the one it moved to"):
            validate_store(corrupt, self.history)
        corrupt = copy.deepcopy(self.store)
        corrupt["refusal_authorizations"].append({**saved, "id": "auth-early", "role": "reviewer"})
        with self.assertRaisesRegex(UsageError, "precedes the independent refusals"):
            validate_store(corrupt, self.history)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"][-1]["refusal_move"]["authorization"] = "auth-missing"
        with self.assertRaises(UsageError):
            validate_store(corrupt, self.history)
        self.assertEqual(third, "tester-3-codex-a")

    def test_an_unchanged_brief_authorization_holds_the_brief_fixed(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = {"schema_version": 1, "from": first, "from_provider": "codex", "provider": "claude"}
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude", self.REPORT)
        authorize_refused_dispatch(self.store, {"id": "auth-grok", "task": TASK, "role": "tester", "fix_round": None, "provider": "grok",
                                                "brief": "unchanged", "decision": "Send the same brief to grok.", "authorization": AUTH}, AT)
        with self.assertRaisesRegex(UsageError, "approves the refused brief unchanged"):
            refusal_move(self.store, TASK, "tester", None, "grok", "brief-identity-reworded")
        move = refusal_move(self.store, TASK, "tester", None, "grok", self.BRIEF)
        assert move is not None
        self.assertEqual((move["provider"], move["authorization"]), ("grok", "auth-grok"))
        self.dispatch_tester(3, "grok-a")
        self.store["dispatches"][-1]["refusal_move"] = move
        validate_store(self.store, self.history)

    def test_refusal_receipt_accepts_the_enrolled_pane_id_as_the_worker_name(self):
        first = self.dispatch_tester(1, "codex-a")
        receipt = self.refusal_receipt("w3:p1", "pane.json")
        with self.assertRaisesRegex(UsageError, "complete exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex", self.REPORT)
        with self.assertRaisesRegex(UsageError, "complete exit-5 output"):
            record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex", self.REPORT, aliases=(None,))
        self.assertEqual(record_refusal(self.store, {"dispatch": first, "receipt": receipt}, AT, "codex", self.REPORT, aliases=("w3:p1",))["provider"], "codex")

    def test_corrupt_refusal_records_refuse_the_ledger(self):
        first = self.dispatch_tester(1, "codex-a")
        record_refusal(self.store, {"dispatch": first, "receipt": self.refusal_receipt("codex-a")}, AT, "codex", self.REPORT)
        second = self.dispatch_tester(2, "claude-a")
        self.store["dispatches"][-1]["refusal_move"] = refusal_move(self.store, TASK, "tester", None, "claude", self.BRIEF)
        validate_store(self.store, self.history)
        for mutate in (
            lambda row: row["refusal"].update(reason="something_else"),
            lambda row: row["refusal"].update(evidence="not-a-receipt"),
            lambda row: row["refusal"].update(receipt="/elsewhere/refusal.json"),
            lambda row: row["refusal"].pop("report_path"),
            lambda row: row["refusal"].pop("provider"),
            lambda row: row.update(status="not_sent", result=None),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(next(row for row in corrupt["dispatches"] if row["id"] == first))
            with self.assertRaises(UsageError):
                validate_store(corrupt, self.history)
        for mutate in (
            lambda row: row["refusal_move"].update({"from": second}),
            lambda row: row["refusal_move"].update(provider="codex"),
            lambda row: row["refusal_move"].update(from_provider="claude"),
            lambda row: row["refusal_move"].update(schema_version=2),
            lambda row: row["refusal_move"].pop("from"),
            lambda row: row.update(brief_identity="brief-identity-reworded"),
        ):
            corrupt = copy.deepcopy(self.store)
            mutate(next(row for row in corrupt["dispatches"] if row["id"] == second))
            with self.assertRaises(UsageError):
                validate_store(corrupt, self.history)
        # A refused source must precede its move: a self-reference and a later row both refuse.
        record_refusal(self.store, {"dispatch": second, "receipt": self.refusal_receipt("claude-a", "second.json")}, AT, "claude", self.REPORT)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"][-1]["refusal_move"] = {"schema_version": 1, "from": second, "from_provider": "claude", "provider": "grok"}
        with self.assertRaises(UsageError):
            validate_store(corrupt, self.history)
        corrupt = copy.deepcopy(self.store)
        corrupt["dispatches"][0]["refusal_move"] = {"schema_version": 1, "from": second, "from_provider": "claude", "provider": "codex"}
        with self.assertRaises(UsageError):
            validate_store(corrupt, self.history)


if __name__ == "__main__":
    unittest.main()
