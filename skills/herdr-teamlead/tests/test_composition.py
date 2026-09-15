"""Specialist selection respects eligibility and actual contribution history."""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.composition import normalize_requirement, parse_requirements, selection_constraints
from teamlead.errors import PlanError, UsageError
from teamlead.planner import plan
from tests.test_planner import snapshot


def requirement(independent=False, engagement="onboarding-flow"):
    return {"specialty": "ux", "required_capabilities": ["browser", "ux"],
            "independent": independent, "engagement": engagement}


def worker(name, capabilities=("browser", "ux")):
    return SimpleNamespace(name=name, capabilities=capabilities)


def assignment(name="prior", role="advisor", status="applied", **extra):
    return {"task": "onboarding", "role": role, "agent": name, "status": status,
            "requirements": requirement(), **extra}


class RequirementTest(unittest.TestCase):
    def test_legacy_roles_need_no_requirements(self):
        self.assertEqual(parse_requirements(None, ["developer", "reviewer", "tester"], None), {})
        self.assertEqual(parse_requirements(None, ["architect"], None, allow_historical_architect=True), {})

    def test_normalized_shape_preserves_responsibility_and_engagement(self):
        data = {"schema_version": 1, "assignments": {"advisor": requirement()}}
        data["assignments"]["advisor"]["required_capabilities"].reverse()
        saved = copy.deepcopy(data)
        result = parse_requirements(data, ["advisor", "developer"], "onboarding")
        self.assertEqual(result, {"advisor": requirement()})
        self.assertEqual(data, saved)

    def test_consultations_require_explicit_task_and_requirements(self):
        for role in ("advisor", "investigator", "architect"):
            with self.subTest(role=role), self.assertRaisesRegex(UsageError, "require explicit"):
                parse_requirements(None, [role], "onboarding")
        with self.assertRaisesRegex(UsageError, "--task"):
            parse_requirements({"schema_version": 1, "assignments": {"advisor": requirement()}}, ["advisor"], None)

    def test_malformed_or_unknown_envelopes_refuse(self):
        cases = [[], {}, {"schema_version": True, "assignments": {}},
                 {"schema_version": 2, "assignments": {"advisor": requirement()}},
                 {"schema_version": 1, "assignments": []},
                 {"schema_version": 1, "assignments": {}},
                 {"schema_version": 1, "assignments": {"typo": requirement()}},
                 {"schema_version": 1, "assignments": {"advisor": requirement()}, "task": "other"}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(UsageError):
                parse_requirements(data, ["advisor"], "onboarding")

    def test_invalid_requirements_never_silently_weaken_the_gate(self):
        cases = [None, {}, {**requirement(), "unknown": True},
                 {**requirement(), "specialty": "UX"}, {**requirement(), "specialty": " ux"},
                 {**requirement(), "required_capabilities": []},
                 {**requirement(), "required_capabilities": ["ux", "ux"]},
                 {**requirement(), "required_capabilities": [[]]},
                 {**requirement(), "independent": "false"},
                 {**requirement(), "engagement": ""}, {**requirement(), "engagement": " new"},
                 {**requirement(), "engagement": "new\nflow"}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(UsageError):
                normalize_requirement(data, "advisor")
        with self.assertRaisesRegex(UsageError, "pinned judge"):
            normalize_requirement(requirement(), "judge")
        for role in ("reviewer", "tester"):
            with self.subTest(role=role), self.assertRaisesRegex(UsageError, "independent:true"):
                normalize_requirement(requirement(), role)


class EligibilityTest(unittest.TestCase):
    def test_empty_specialist_bench_reports_the_actual_eligibility_gap(self):
        requests = {"advisor": requirement()}
        bench = [worker("spare", ()), worker("other", ())]
        constraints = selection_constraints(["advisor"], bench, requests, [], "onboarding")
        with self.assertRaises(PlanError) as caught:
            plan(["advisor"], snapshot(spare=99, other=80), exclude=constraints["exclude"], requirements=requests,
                 selection_rationale=constraints["rationale"])
        self.assertIn("missing declared capabilities browser, ux", str(caught.exception))
        self.assertIn("preserve independence", str(caught.exception))
        self.assertEqual(caught.exception.details["eligibility"], constraints["rationale"])

    def test_lower_headroom_capable_worker_beats_unqualified_worker(self):
        agents = [worker("qualified"), worker("spare", ("ux",))]
        requests = {"advisor": requirement()}
        constraints = selection_constraints(["advisor"], agents, requests, [], "onboarding")
        result = plan(["advisor"], snapshot(qualified=30, spare=99), exclude=constraints["exclude"],
                      requirements=requests, familiarity=constraints["familiarity"])
        self.assertEqual(result["assignments"], {"advisor": "qualified"})
        self.assertIn("missing declared capabilities browser", " ".join(constraints["rationale"]))

    def test_unconfigured_snapshot_worker_cannot_claim_specialty(self):
        constraints = selection_constraints(["advisor"], [worker("configured")], {"advisor": requirement()},
                                            [], "onboarding", candidate_names=["configured", "ghost"])
        self.assertEqual(constraints["exclude"]["advisor"], ["ghost"])
        self.assertIn("absent from current config", " ".join(constraints["rationale"]))

    def test_old_config_capabilities_do_not_come_from_model_name(self):
        constraints = selection_constraints(["advisor"], [worker("best-ux-model", ())],
                                            {"advisor": requirement()}, [], "onboarding")
        self.assertEqual(constraints["exclude"]["advisor"], ["best-ux-model"])

    def test_independence_follows_possible_contributions_across_context_and_model_changes(self):
        for role in ("developer", "architect", "advisor", "investigator"):
            for status in ("applied", "unknown", "sending", "sent_but_not_started"):
                with self.subTest(role=role, status=status):
                    history = [assignment(role=role, status=status),
                               assignment(role="release", requirements=None, context_session={"value": "different"})]
                    constraints = selection_constraints(["reviewer", "tester"], [worker("prior"), worker("fresh")],
                                                        {}, history, "onboarding")
                    self.assertEqual(constraints["exclude"], {"reviewer": ["prior"], "tester": ["prior"]})

    def test_non_independent_consultation_can_continue_author_work(self):
        constraints = selection_constraints(["advisor"], [worker("prior")], {"advisor": requirement()},
                                            [assignment(role="developer")], "onboarding")
        self.assertEqual(constraints["exclude"]["advisor"], [])

    def test_pending_send_is_excluded_even_before_assignment_row_exists(self):
        constraints = selection_constraints(["reviewer"], [worker("prior")], {}, [], "onboarding",
                                            dispatches=[assignment(role="developer", status="sending")])
        self.assertEqual(constraints["exclude"]["reviewer"], ["prior"])
        for status in ("reserved", "not_sent"):
            constraints = selection_constraints(["reviewer"], [worker("prior")], {}, [], "onboarding",
                                                dispatches=[assignment(role="developer", status=status)])
            self.assertEqual(constraints["exclude"]["reviewer"], [])

    def test_other_tasks_and_unbound_legacy_runs_keep_candidates(self):
        for task in (None, "another-task"):
            constraints = selection_constraints(["reviewer"], [worker("prior")], {}, [assignment()], task)
            self.assertEqual(constraints["exclude"]["reviewer"], [])

    def test_architecture_under_reviewer_role_is_still_a_contribution(self):
        for tier in (None, {"round": "architect"}, {"round": "reconciliation"}):
            constraints = selection_constraints(["tester"], [worker("prior")], {},
                                                [assignment(role="reviewer", requirements=None, tier=tier)], "onboarding")
            self.assertEqual(constraints["exclude"]["tester"], ["prior"])
        constraints = selection_constraints(["tester"], [worker("prior")], {},
                                            [assignment(role="reviewer", requirements=None, tier={"round": "review"},
                                                        reviewer_scope="verification")], "onboarding")
        self.assertEqual(constraints["exclude"]["tester"], [])

    def test_new_untiered_verification_remains_eligible_for_followup(self):
        history = [assignment(role="reviewer", requirements=None, tier=None, reviewer_scope="verification")]
        constraints = selection_constraints(["reviewer", "tester"], [worker("prior")], {}, history, "onboarding")
        self.assertEqual(constraints["exclude"], {"reviewer": [], "tester": []})

    def test_predevelopment_tester_requires_contribution_assessment(self):
        history = [assignment(role="tester", tier={"round": "test_plan"})]
        constraints = selection_constraints(["reviewer", "tester"], [worker("prior")], {}, history, "onboarding")
        self.assertEqual(constraints["exclude"], {"reviewer": ["prior"], "tester": ["prior"]})
        assessment = {"assignment_index": 0, "task": "onboarding", "agent": "prior", "contribution": "none"}
        constraints = selection_constraints(["reviewer", "tester"], [worker("prior")], {}, history, "onboarding", assessments=[assessment])
        self.assertEqual(constraints["exclude"], {"reviewer": [], "tester": []})

    def test_migration_or_requirements_never_invent_verification_provenance(self):
        for scope in (None, "unknown", "design"):
            for tier in (None, {"round": "review"}):
                with self.subTest(scope=scope, tier=tier):
                    history = [assignment(role="reviewer", schema_version=6, reviewer_scope=scope,
                                          requirements=requirement(independent=True), tier=tier)]
                    constraints = selection_constraints(["reviewer"], [worker("prior")], {}, history, "onboarding")
                    self.assertEqual(constraints["exclude"]["reviewer"], ["prior"])

    def test_authored_tier_overrides_a_verification_label_until_assessed(self):
        for round_type in ("architect", "reconciliation"):
            with self.subTest(round_type=round_type):
                history = [assignment(role="reviewer", reviewer_scope="verification", tier={"round": round_type})]
                constraints = selection_constraints(["reviewer"], [worker("prior")], {}, history, "onboarding")
                self.assertEqual(constraints["exclude"]["reviewer"], ["prior"])
                assessment = {"assignment_index": 0, "task": "onboarding", "agent": "prior", "contribution": "none"}
                constraints = selection_constraints(["reviewer"], [worker("prior")], {}, history, "onboarding", assessments=[assessment])
                self.assertEqual(constraints["exclude"]["reviewer"], [])

    def test_assessed_no_contribution_overrides_only_its_non_developer_dispatch(self):
        assessment = {"assignment_index": 0, "task": "onboarding", "agent": "prior", "contribution": "none"}
        constraints = selection_constraints(["reviewer"], [worker("prior")], {}, [assignment()], "onboarding",
                                            dispatches=[assignment(assignment_index=0)], assessments=[assessment])
        self.assertEqual(constraints["exclude"]["reviewer"], [])
        for history in ([assignment(role="developer")], [assignment(), assignment(role="architect")]):
            constraints = selection_constraints(["reviewer"], [worker("prior")], {}, history, "onboarding", assessments=[assessment])
            self.assertEqual(constraints["exclude"]["reviewer"], ["prior"])

    def test_any_assessed_design_or_implementation_remains_a_contribution(self):
        for contribution in ("design", "implementation"):
            assessments = [
                {"assignment_index": 0, "task": "onboarding", "agent": "prior", "contribution": contribution},
                {"assignment_index": 1, "task": "onboarding", "agent": "prior", "contribution": "none"},
            ]
            constraints = selection_constraints(["reviewer"], [worker("prior")], {}, [], "onboarding", assessments=assessments)
            self.assertEqual(constraints["exclude"]["reviewer"], ["prior"])

    def test_familiarity_requires_confirmed_same_task_role_and_requirements(self):
        requests = {"advisor": requirement()}
        variants = [assignment(), assignment(name="unsent", status="sent_but_not_started"),
                    assignment(name="other-task", task="other"), assignment(name="other-role", role="architect"),
                    assignment(name="other-engagement", requirements=requirement(engagement="other")),
                    assignment(name="old", requirements=None)]
        agents = [worker(row["agent"]) for row in variants]
        constraints = selection_constraints(["advisor"], agents, requests, variants, "onboarding")
        self.assertEqual(constraints["familiarity"]["advisor"], {
            "prior": 1, "unsent": 0, "other-task": 0, "other-role": 0, "other-engagement": 0, "old": 0,
        })
        self.assertIn("never expertise or completion", " ".join(constraints["rationale"]))

    def test_independent_specialist_rejects_a_familiar_author(self):
        request = requirement(independent=True)
        constraints = selection_constraints(["advisor"], [worker("prior")], {"advisor": request},
                                            [assignment(requirements=request)], "onboarding")
        self.assertEqual(constraints["exclude"]["advisor"], ["prior"])
        self.assertEqual(constraints["familiarity"]["advisor"], {})


if __name__ == "__main__":
    unittest.main()
