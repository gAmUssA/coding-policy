"""Delivered specialist evidence survives replay and gates warm follow-ups."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import engagement, recovery, report_delivery, supervision
from teamlead.errors import UsageError
from teamlead.state import add_assignment, empty_state, load_state_checked, save_state
from tests import test_report_delivery as delivery_fixture

AT = "2026-02-03T10:00:00+00:00"
LATER = "2026-02-03T11:00:00+00:00"
REQUIREMENT = {"specialty": "ux", "required_capabilities": ["interaction-design"],
               "independent": False, "engagement": "onboarding-design"}


class EngagementTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        who = supervision.identity("lead", str(self.root), "fixture", pane_id="lead-pane")
        supervision.bind(self.path, who, AT)
        self.report = self.root / "report.md"
        self.report.write_text("Inspected onboarding. Recommend grouping account fields; implementation remains open.\n")
        self.delivery = self.root / "delivery.json"
        self.delivery.write_text(json.dumps({"found": True, "agent": "worker", "report_path": str(self.report)}))
        self.state = empty_state()
        self.dispatch = {"id": "consult-1", "fingerprint": "a" * 64, "task": "task-1", "role": "advisor",
                         "agent": "worker", "fix_round": None, "plan": None, "work": None,
                         "requirements": copy.deepcopy(REQUIREMENT)}
        self.seed(self.dispatch)
        self.data = {"id": "assessment-1", "dispatch": "consult-1", "report": str(self.report),
                     "delivery": str(self.delivery), "outcome": "consultation assessed",
                     "contribution": "design", "summary": "The proposal supplies interaction decisions; independent review remains required."}

    def seed(self, dispatch):
        recovery.reserve(self.state["recovery"], dispatch, AT)
        add_assignment(self.state, AT, dispatch["role"], dispatch["agent"], task=dispatch["task"],
                       requirements=dispatch.get("requirements"), reviewer_scope=dispatch.get("reviewer_scope"))
        result = {key: copy.deepcopy(dispatch[key]) for key in ("task", "role", "agent", "fix_round", "requirements", "reviewer_scope") if key in dispatch}
        recovery.finish_dispatch(self.state["recovery"], dispatch["id"], {**result, "status": "applied"}, len(self.state["assignments"]) - 1, AT)
        supervision.enroll(self.path, {"id": dispatch["id"], "agent": dispatch["agent"], "task": dispatch["task"],
                                      "report": str(self.report), "pane_id": "worker-pane", "native_session": None}, AT)

    def assess(self, data=None, at=LATER):
        return engagement.record_assessment(self.state, self.path, self.data if data is None else data, at)

    def retire(self):
        return supervision.resolve(self.path, {"id": self.dispatch["id"], "outcome": "Report assessed; consultation ended",
                                               "evidence": [str(self.report)]}, LATER)

    def test_assessment_binds_actual_delivery_and_persists_without_accepting_task(self):
        before = copy.deepcopy(self.state["assignments"])
        result = self.assess()
        self.assertEqual(result["assignment_index"], 0)
        self.assertEqual(result["task"], "task-1")
        self.assertEqual(result["contribution"], "design")
        self.assertEqual(result["report_evidence"], recovery.receipt(str(self.report))[0])
        self.assertEqual(result["delivery_evidence"], recovery.receipt(str(self.delivery))[0])
        self.assertEqual(self.state["assignments"], before)
        self.assertTrue(supervision.load(self.path)["members"][0]["active"])
        save_state(self.path, self.state)
        loaded, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(loaded, self.state)

    def test_exact_assessment_retry_preserves_original_time_and_unavailable_receipts(self):
        original = copy.deepcopy(self.assess())
        self.report.unlink()
        self.delivery.unlink()
        self.assertEqual(self.assess(at="2026-02-03T12:00:00Z"), original)
        self.assertEqual(self.state["specialist_assessments"], [original])
        engagement.validate_assessments(self.state)

    def test_changed_retry_refuses_without_rewriting_original_assessment(self):
        self.assess()
        before = copy.deepcopy(self.state)
        for change in ({"summary": "Different conclusion"}, {"contribution": "none"}, {"report": str(self.root / "other.md")},
                       {"delivery": str(self.root / "other.json")}, {"outcome": "whole task done"}):
            with self.subTest(change=change), self.assertRaises(UsageError):
                self.assess({**self.data, **change})
            self.assertEqual(self.state, before)

    def test_pending_wrong_worker_or_wrong_report_delivery_is_not_assessable(self):
        for proof in ({"found": False, "agent": "worker", "report_path": str(self.report)},
                      {"found": True, "agent": "other", "report_path": str(self.report)},
                      {"found": True, "agent": "worker", "report_path": str(self.root / "other.md")},
                      {"found": 1, "agent": "worker", "report_path": str(self.report)}, [], "done"):
            with self.subTest(proof=proof):
                self.delivery.write_text(json.dumps(proof))
                with self.assertRaises(UsageError):
                    self.assess()
                self.assertEqual(self.state["specialist_assessments"], [])
        self.delivery.write_text("not JSON")
        with self.assertRaises(UsageError):
            self.assess()

    def test_missing_or_unreadable_evidence_does_not_append_assessment(self):
        for source in (self.report, self.delivery):
            before = source.read_bytes()
            source.unlink()
            with self.assertRaises(UsageError):
                self.assess()
            self.assertEqual(self.state["specialist_assessments"], [])
            source.write_bytes(before)

    def test_unenrolled_report_and_unconfirmed_dispatch_are_refused(self):
        other = self.root / "unenrolled.md"
        other.write_text("Different report")
        with self.assertRaises(UsageError):
            self.assess({**self.data, "report": str(other)})
        for status in ("reserved", "sending", "sent_but_not_started", "not_sent"):
            self.state["recovery"]["dispatches"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(UsageError):
                self.assess()
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_assessment_time_and_contribution_are_explicit(self):
        for contribution in ("maybe", "", None):
            with self.subTest(contribution=contribution), self.assertRaises(UsageError):
                self.assess({**self.data, "contribution": contribution})
        for at in ("2026-02-03T09:00:00Z", "2026-02-03T11:00:00"):
            with self.subTest(at=at), self.assertRaises(UsageError):
                self.assess(at=at)
        self.assertEqual(self.state["specialist_assessments"], [])

    def test_warm_followup_requires_assessment_and_retired_enrollment(self):
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})
        self.assess()
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})
        self.retire()
        engagement.require_followup(self.state, self.path, {"advisor": "worker"})

    def test_changed_sources_refuse_followup_while_saved_assessment_remains_readable(self):
        self.assess()
        self.retire()
        for path in (self.report, self.delivery):
            before = path.read_bytes()
            path.write_text("Changed evidence")
            with self.assertRaises(UsageError):
                engagement.require_followup(self.state, self.path, {"advisor": "worker"})
            engagement.validate_assessments(self.state)
            path.write_bytes(before)

    def test_intervening_assignment_cannot_reuse_previous_consultation_assessment(self):
        self.assess()
        self.retire()
        add_assignment(self.state, LATER, "advisor", "worker", task="other-task", requirements=REQUIREMENT)
        with self.assertRaises(UsageError):
            engagement.require_followup(self.state, self.path, {"advisor": "worker"})

    def test_corrupt_assessment_preserves_owner_file_and_refuses_read(self):
        self.assess()
        variants = ({"schema_version": 2}, {"assignment_index": 9}, {"assignment_index": False}, {"agent": "other-worker"},
                    {"task": "another-task"}, {"at": "2026-02-03T09:00:00Z"},
                    {"report_evidence": {**self.state["specialist_assessments"][0]["report_evidence"], "path": "/other.md"}})
        for change in variants:
            with self.subTest(change=change):
                corrupted = copy.deepcopy(self.state)
                corrupted["specialist_assessments"][0].update(change)
                self.path.write_text(json.dumps(corrupted))
                before = self.path.read_bytes()
                warnings = []
                _loaded, usable = load_state_checked(self.path, warn=warnings.append)
                self.assertFalse(usable)
                self.assertTrue(warnings)
                self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_assessment_ids_are_invalid(self):
        self.assess()
        self.state["specialist_assessments"].append(copy.deepcopy(self.state["specialist_assessments"][0]))
        with self.assertRaises(UsageError):
            engagement.validate_assessments(self.state)

    def test_missing_or_invalid_reviewer_scope_preserves_history_without_crashing(self):
        for role in ("reviewer", "advisor", "developer"):
            for missing, value in ((True, None), (False, []), (False, {}), (False, False), (False, "invalid")):
                state = empty_state()
                add_assignment(state, AT, role, "worker", task="task-1")
                if missing:
                    state["assignments"][0].pop("reviewer_scope")
                else:
                    state["assignments"][0]["reviewer_scope"] = value
                self.path.write_text(json.dumps(state))
                before = self.path.read_bytes()
                warnings = []
                with self.subTest(role=role, missing=missing, value=value):
                    _loaded, usable = load_state_checked(self.path, warn=warnings.append)
                    self.assertFalse(usable)
                    self.assertTrue(any("reviewer" in item for item in warnings))
                    self.assertEqual(self.path.read_bytes(), before)

    def test_schema_five_migration_preserves_unknown_specialty_and_reviewer_provenance(self):
        old = empty_state()
        add_assignment(old, AT, "reviewer", "worker", task="old-task")
        old.pop("specialist_assessments")
        old["schema_version"] = 5
        old["recovery"]["schema_version"] = 4
        del old["recovery"]["refusal_authorizations"]
        del old["recovery"]["diagnoses"]
        del old["recovery"]["legacy_ruling_recoveries"]
        original = old["assignments"][0]
        original["schema_version"] = 5
        original.pop("requirements")
        original.pop("reviewer_scope")
        self.path.write_text(json.dumps(old))
        migrated, usable = load_state_checked(self.path)
        self.assertTrue(usable)
        self.assertEqual(migrated["specialist_assessments"], [])
        self.assertEqual(migrated["assignments"][0], {**original, "schema_version": 6,
                                                    "requirements": None, "reviewer_scope": "unknown"})
        self.assertEqual(migrated["recovery"]["schema_version"], 9)

    def test_older_schema_cannot_bless_future_composition_fields(self):
        for target in ("document", "assignment"):
            old = empty_state()
            add_assignment(old, AT, "reviewer", "worker", task="old-task")
            old["schema_version"] = 5
            if target == "assignment":
                old.pop("specialist_assessments")
                old["assignments"][0]["schema_version"] = 5
            self.path.write_text(json.dumps(old))
            before = self.path.read_bytes()
            _loaded, usable = load_state_checked(self.path, warn=lambda _: None)
            self.assertFalse(usable)
            self.assertEqual(self.path.read_bytes(), before)

    def recovered_delivery_fixture(self):
        case = delivery_fixture.NativeDeliveryTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        state, request = case.recovery_fixture()
        state["assignments"][0].update(role="reviewer", reviewer_scope="unknown")
        dispatch = state["recovery"]["dispatches"][0]
        dispatch["role"] = "reviewer"
        dispatch["result"]["role"] = "reviewer"
        source = Path(request["source"])
        source.write_text(source.read_text().replace("Your role for this task is JUDGE", "Your role for this task is REVIEWER"))
        recovered = report_delivery.recover(state["recovery"], state["assignments"], request, delivery_fixture.AT)
        path = case.tmp / "state.json"
        who = supervision.identity("delivery-lead", str(case.tmp), "fixture", pane_id="delivery-lead-pane")
        supervision.bind(path, who, delivery_fixture.AT)
        supervision.enroll(path, {"id": dispatch["id"], "agent": dispatch["agent"], "task": dispatch["task"],
            "report": str(case.report), "pane_id": delivery_fixture.PANE, "native_session": None}, delivery_fixture.AT)
        delivery = case.tmp / "recovered-delivery.json"
        delivery.write_text(json.dumps(recovered))
        data = {"id": "recovered-assessment", "dispatch": dispatch["id"], "report": str(case.report), "delivery": str(delivery),
                "outcome": "Recovered report assessed", "contribution": "none", "summary": "Independent report read after native delivery recovery."}
        return state, path, data, recovered

    def test_exact_owner_recovered_delivery_can_be_assessed(self):
        state, path, data, recovered = self.recovered_delivery_fixture()
        self.assertTrue(recovered["found"])
        result = engagement.record_assessment(state, path, data, delivery_fixture.AT)
        self.assertEqual(result["dispatch"], recovered["dispatch"])
        self.assertEqual(result["delivery_evidence"], recovery.receipt(data["delivery"])[0])
        save_state(path, state)
        _loaded, usable = load_state_checked(path)
        self.assertTrue(usable)

    def test_recovered_delivery_cannot_assess_changed_report_bytes(self):
        state, path, data, _recovered = self.recovered_delivery_fixture()
        Path(data["report"]).write_text("A later report never covered by that delivery")
        with self.assertRaises(UsageError):
            engagement.record_assessment(state, path, data, delivery_fixture.AT)
        self.assertEqual(state["specialist_assessments"], [])

    def test_altered_or_unowned_recovered_delivery_cannot_be_assessed(self):
        state, path, data, recovered = self.recovered_delivery_fixture()
        for change in ({"id": "forged"}, {"dispatch": "different-dispatch"}, {"found": False},
                       {"input": {**recovered["input"], "report": "/other-report.md"}}):
            with self.subTest(change=change):
                Path(data["delivery"]).write_text(json.dumps({**recovered, **change}))
                with self.assertRaises(UsageError):
                    engagement.record_assessment(state, path, data, delivery_fixture.AT)
                self.assertEqual(state["specialist_assessments"], [])
        Path(data["delivery"]).write_text(json.dumps(recovered))
        state["recovery"]["delivery_recoveries"] = []
        with self.assertRaises(UsageError):
            engagement.record_assessment(state, path, data, delivery_fixture.AT)


if __name__ == "__main__":
    unittest.main()
