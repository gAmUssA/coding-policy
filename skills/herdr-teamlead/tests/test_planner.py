"""Tests for teamlead.planner. Pure function in, pure dict out."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import unittest

from teamlead.errors import PlanError
from teamlead.planner import plan

ROLES = ["developer", "tester", "reviewer"]


def snapshot(**headrooms):
    """Build a minimal snapshot carrying only what the planner reads."""
    return {
        "schema_version": 1,
        "measured_at": "2026-02-03T10:00:00+00:00",
        "agents": {
            name: {"kind": name, "state": "idle", "headroom_pct": headroom}
            for name, headroom in headrooms.items()
        },
    }


class OrderingTest(unittest.TestCase):
    def test_heaviest_role_goes_to_the_most_headroom(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(
            result["assignments"],
            {"developer": "grok", "tester": "claude", "reviewer": "codex"},
        )

    def test_assignment_order_follows_the_roles_given(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(list(result["assignments"]), ROLES)

    def test_reordering_the_roles_no_longer_reorders_the_agents(self):
        # --roles used to decide which seat was heaviest. The cost weights do
        # now, so the lightest-first caller gets the same plan as before.
        result = plan(
            ["reviewer", "tester", "developer"], snapshot(claude=92.0, codex=87.0, grok=100.0)
        )
        self.assertEqual(result["assignments"]["developer"], "grok")
        self.assertEqual(result["assignments"]["tester"], "claude")
        self.assertEqual(result["assignments"]["reviewer"], "codex")

    def test_reordering_the_roles_still_keys_the_document_that_way(self):
        result = plan(
            ["reviewer", "tester", "developer"], snapshot(claude=92.0, codex=87.0, grok=100.0)
        )
        self.assertEqual(
            list(result["assignments"]), ["reviewer", "tester", "developer"]
        )

    def test_is_deterministic_across_repeated_calls(self):
        data = snapshot(claude=92.0, codex=87.0, grok=100.0)
        self.assertEqual(plan(ROLES, data), plan(ROLES, data))

    def test_integer_headrooms_work_the_same_as_floats(self):
        result = plan(ROLES, snapshot(claude=92, codex=87, grok=100))
        self.assertEqual(result["assignments"]["developer"], "grok")

    def test_fewer_roles_than_agents_leaves_the_rest_unassigned(self):
        result = plan(["developer"], snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(result["assignments"], {"developer": "grok"})


class TieBreakTest(unittest.TestCase):
    def test_headroom_tie_falls_back_to_name(self):
        result = plan(["developer", "tester"], snapshot(zeta=50.0, alpha=50.0))
        self.assertEqual(result["assignments"]["developer"], "alpha")
        self.assertEqual(result["assignments"]["tester"], "zeta")

    def test_previous_role_count_beats_name(self):
        counts = {"developer": {"alpha": 3, "zeta": 1}}
        result = plan(["developer"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["developer"], "zeta")

    def test_role_counts_are_per_role_not_global(self):
        # alpha has been developer a lot but tester never; the tester pick must
        # not be penalised by the developer history.
        counts = {"developer": {"alpha": 9}}
        result = plan(["tester"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["tester"], "alpha")

    def test_headroom_still_outranks_role_history(self):
        counts = {"developer": {"grok": 10}}
        result = plan(["developer"], snapshot(claude=92.0, grok=100.0), counts)
        self.assertEqual(result["assignments"]["developer"], "grok")

    def test_equal_role_counts_fall_through_to_name(self):
        counts = {"developer": {"alpha": 2, "zeta": 2}}
        result = plan(["developer"], snapshot(zeta=50.0, alpha=50.0), counts)
        self.assertEqual(result["assignments"]["developer"], "alpha")


class NullHeadroomTest(unittest.TestCase):
    def test_null_headroom_agents_sort_last(self):
        result = plan(ROLES, snapshot(claude=None, codex=87.0, grok=100.0))
        self.assertEqual(
            result["assignments"],
            {"developer": "grok", "tester": "codex", "reviewer": "claude"},
        )

    def test_null_headroom_agents_are_ordered_by_name_alone(self):
        result = plan(["developer", "tester"], snapshot(zeta=None, alpha=None))
        self.assertEqual(result["assignments"], {"developer": "alpha", "tester": "zeta"})

    def test_role_history_does_not_reorder_null_headroom_agents(self):
        counts = {"developer": {"alpha": 5, "zeta": 0}}
        result = plan(["developer"], snapshot(zeta=None, alpha=None), counts)
        self.assertEqual(result["assignments"]["developer"], "alpha")

    def test_zero_headroom_still_outranks_null(self):
        result = plan(["developer", "tester"], snapshot(alpha=None, zeta=0.0))
        self.assertEqual(result["assignments"]["developer"], "zeta")

    def test_missing_headroom_key_is_treated_as_null(self):
        data = {"agents": {"alpha": {"state": "working"}, "zeta": {"headroom_pct": 10.0}}}
        result = plan(["developer"], data)
        self.assertEqual(result["assignments"]["developer"], "zeta")


class OutputShapeTest(unittest.TestCase):
    def test_carries_a_schema_version(self):
        self.assertEqual(plan(["developer"], snapshot(grok=100.0))["schema_version"], 4)

    def test_rationale_has_one_line_per_role_naming_the_field(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(len(result["rationale"]), 3)
        self.assertIn("developer -> grok", result["rationale"][0])
        self.assertIn("100% headroom", result["rationale"][0])
        self.assertIn("grok=100%", result["rationale"][0])

    def test_rationale_names_the_previous_role_count(self):
        counts = {"developer": {"grok": 4}}
        result = plan(["developer"], snapshot(grok=100.0), counts)
        self.assertIn("held this role 4x before", result["rationale"][0])

    def test_rationale_explains_a_null_headroom_pick(self):
        result = plan(["developer"], snapshot(grok=None))
        self.assertIn("no headroom reading", result["rationale"][0])

    def test_snapshot_ref_defaults_to_the_measurement_timestamp(self):
        result = plan(["developer"], snapshot(grok=100.0))
        self.assertEqual(
            result["snapshot_ref"],
            {"source": None, "measured_at": "2026-02-03T10:00:00+00:00"},
        )

    def test_snapshot_ref_is_echoed_when_supplied(self):
        ref = {"source": "/tmp/snap.json", "measured_at": "2026-02-03T10:00:00+00:00"}
        result = plan(["developer"], snapshot(grok=100.0), snapshot_ref=ref)
        self.assertEqual(result["snapshot_ref"], ref)


class CostWeightTest(unittest.TestCase):
    """The seat's weight, not the caller's --roles order, decides who fills it."""

    def test_the_heaviest_seat_is_filled_first_whatever_roles_says(self):
        # reviewer arrives first and still gets the smallest agent, because
        # developer outweighs it.
        result = plan(["reviewer", "developer"], snapshot(alpha=90.0, zeta=60.0))
        self.assertEqual(result["assignments"], {"reviewer": "zeta", "developer": "alpha"})

    def test_an_override_can_make_the_lightest_seat_the_heaviest(self):
        result = plan(
            ["developer", "reviewer"],
            snapshot(alpha=60.0, zeta=55.0),
            role_costs={"reviewer": 30.0},
        )
        self.assertEqual(result["assignments"], {"developer": "zeta", "reviewer": "alpha"})

    def test_an_override_replaces_only_the_role_it_names(self):
        result = plan(
            ["developer", "reviewer"],
            snapshot(alpha=60.0, zeta=55.0),
            role_costs={"tester": 99.0},
        )
        self.assertEqual(result["assignments"], {"developer": "alpha", "reviewer": "zeta"})

    def test_roles_nobody_weighed_keep_the_callers_order(self):
        result = plan(["scribe", "courier"], snapshot(alpha=90.0, zeta=60.0))
        self.assertEqual(result["assignments"], {"scribe": "alpha", "courier": "zeta"})

    def test_the_pick_maximises_the_rounds_minimum_projected_headroom(self):
        # developer costs 12 and tester 10, so giving the 70 to the heavier
        # seat would floor the round at 58; the other way floors it at 60.
        result = plan(["developer", "tester"], snapshot(alpha=70.0, zeta=72.0))
        self.assertEqual(result["assignments"], {"developer": "zeta", "tester": "alpha"})

    def test_the_rationale_names_the_weight_it_used(self):
        result = plan(["developer"], snapshot(grok=100.0))
        self.assertIn("weight 12", result["rationale"][0])
        self.assertIn("88% projected", result["rationale"][0])

    def test_the_rationale_names_an_overridden_weight(self):
        result = plan(["developer"], snapshot(grok=100.0), role_costs={"developer": 40.0})
        self.assertIn("weight 40", result["rationale"][0])


class ExclusionTest(unittest.TestCase):
    """Nobody reviews or verifies the branch they wrote."""

    def test_an_excluded_agent_does_not_get_that_role(self):
        result = plan(
            ROLES,
            snapshot(claude=92.0, codex=87.0, grok=100.0),
            exclude={"reviewer": ["grok"], "tester": ["grok"]},
        )
        self.assertEqual(result["assignments"]["developer"], "grok")
        self.assertNotIn(result["assignments"]["reviewer"], ["grok"])
        self.assertNotIn(result["assignments"]["tester"], ["grok"])

    def test_the_author_still_gets_the_one_seat_left_to_it(self):
        # grok has the LEAST headroom, so the plain headroom order would hand
        # developer to claude and then have nowhere to put grok.
        result = plan(
            ROLES,
            snapshot(claude=92.0, codex=87.0, grok=40.0),
            exclude={"reviewer": ["grok"], "tester": ["grok"]},
        )
        self.assertEqual(
            result["assignments"],
            {"developer": "grok", "tester": "claude", "reviewer": "codex"},
        )

    def test_several_agents_can_be_barred_from_one_role(self):
        result = plan(
            ["developer", "reviewer"],
            snapshot(alpha=90.0, zeta=60.0, mu=80.0),
            exclude={"reviewer": ["alpha", "mu"]},
        )
        self.assertEqual(result["assignments"]["reviewer"], "zeta")

    def test_an_exclusion_that_leaves_no_candidate_is_an_error(self):
        with self.assertRaises(PlanError) as caught:
            plan(
                ["developer", "reviewer"],
                snapshot(alpha=90.0, zeta=60.0),
                exclude={"reviewer": ["alpha", "zeta"]},
            )
        self.assertIn("reviewer", str(caught.exception))
        self.assertIn("alpha, zeta", str(caught.exception))

    def test_an_exclusion_set_no_assignment_satisfies_is_an_error(self):
        # Both roles can only go to alpha, and one agent cannot hold two.
        with self.assertRaises(PlanError) as caught:
            plan(
                ["developer", "reviewer"],
                snapshot(alpha=90.0, zeta=60.0),
                exclude={"reviewer": ["zeta"], "developer": ["zeta"]},
            )
        self.assertIn("Cannot fill role", str(caught.exception))
        self.assertIn("Drop an exclusion", str(caught.exception))

    def test_excluding_a_role_nobody_is_assigning_is_an_error(self):
        with self.assertRaises(PlanError) as caught:
            plan(
                ["developer", "tester"],
                snapshot(alpha=90.0, zeta=60.0),
                exclude={"reviewr": ["alpha"]},
            )
        self.assertIn("reviewr", str(caught.exception))
        self.assertIn("developer, tester", str(caught.exception))

    def test_excluding_an_agent_the_snapshot_lacks_warns_rather_than_refusing(self):
        # The author may be busy and therefore unmeasured; refusing would
        # block a round the exclusion does not actually affect.
        warnings = []
        result = plan(
            ["developer"],
            snapshot(alpha=90.0, zeta=60.0),
            exclude={"developer": ["ghost"]},
            warn=warnings.append,
        )
        self.assertEqual(result["assignments"]["developer"], "alpha")
        self.assertEqual(len(warnings), 1)
        self.assertIn("ghost", warnings[0])
        self.assertIn("ghost", result["rationale"][-1])
        self.assertIn("changed nothing", result["rationale"][-1])

    def test_the_rationale_names_the_exclusions_applied(self):
        result = plan(
            ["developer", "reviewer"],
            snapshot(alpha=90.0, zeta=60.0),
            exclude={"reviewer": ["alpha"]},
        )
        developer_line, reviewer_line = result["rationale"]
        self.assertIn("excluded: none", developer_line)
        self.assertIn("excluded: alpha", reviewer_line)

    def test_no_exclusions_is_the_same_plan_as_before(self):
        data = snapshot(claude=92.0, codex=87.0, grok=100.0)
        self.assertEqual(plan(ROLES, data), plan(ROLES, data, exclude={}))


class SkippedSnapshotTest(unittest.TestCase):
    """A worker measured as working carries the headroom of some earlier round."""

    def _snapshot(self, **skipped):
        data = snapshot(alpha=90.0, zeta=60.0)
        for name, value in skipped.items():
            data["agents"][name]["skipped"] = value
        return data

    def test_a_skipped_agent_is_named_in_the_rationale(self):
        result = plan(["developer"], self._snapshot(zeta=True))
        self.assertIn("stale headroom for zeta", result["rationale"][-1])

    def test_every_skipped_agent_is_named_once_in_one_note(self):
        result = plan(["developer"], self._snapshot(zeta=True, alpha=True))
        notes = [line for line in result["rationale"] if line.startswith("note:")]
        self.assertEqual(len(notes), 1)
        self.assertIn("alpha, zeta", notes[0])

    def test_a_snapshot_with_nothing_skipped_adds_no_note(self):
        result = plan(["developer"], self._snapshot(zeta=False))
        self.assertEqual(len(result["rationale"]), 1)

    def test_the_note_does_not_disturb_the_assignment_lines(self):
        result = plan(["developer", "reviewer"], self._snapshot(zeta=True))
        self.assertIn("developer -> alpha", result["rationale"][0])
        self.assertIn("reviewer -> zeta", result["rationale"][1])


class RefusalTest(unittest.TestCase):
    def test_no_roles_is_an_error(self):
        with self.assertRaises(PlanError):
            plan([], snapshot(grok=100.0))

    def test_duplicate_roles_are_an_error(self):
        with self.assertRaises(PlanError) as caught:
            plan(["developer", "developer"], snapshot(claude=1.0, grok=2.0))
        self.assertIn("developer", str(caught.exception))

    def test_empty_snapshot_is_an_error_naming_measure(self):
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, {"agents": {}})
        self.assertIn("teamlead measure", str(caught.exception))

    def test_missing_snapshot_is_an_error(self):
        with self.assertRaises(PlanError):
            plan(ROLES, None)

    def test_agents_that_is_not_an_object_is_a_plan_error_not_a_crash(self):
        # A hand-edited snapshot with `agents` as a list used to reach
        # `.items()` and die with AttributeError.
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, {"agents": ["claude", "grok", "codex"]})
        self.assertIn("not an object keyed by agent name", str(caught.exception))
        self.assertIn("teamlead measure", str(caught.exception))

    def test_a_snapshot_that_is_not_an_object_is_a_plan_error(self):
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, ["claude", "grok", "codex"])
        self.assertIn("not an object", str(caught.exception))

    def test_more_roles_than_agents_is_an_error_not_a_silent_drop(self):
        with self.assertRaises(PlanError) as caught:
            plan(ROLES, snapshot(grok=100.0, claude=92.0))
        self.assertIn("3 roles", str(caught.exception))




class MalformedHeadroomTest(unittest.TestCase):
    """A snapshot is a file on disk: hand-edited, stale, or truncated.

    `float()` on whatever it happens to hold used to crash the whole plan over
    one bad field. Tracked as gamussa/coding-policy#315.
    """

    def _plan(self, value, roles=("developer",), extra=None):
        self.warnings = []
        agents = {"broken": {"kind": "x", "state": "idle", "headroom_pct": value}}
        agents.update(extra or {"healthy": {"headroom_pct": 50.0}})
        return plan(
            list(roles),
            {"agents": agents},
            warn=self.warnings.append,
        )

    def test_a_string_that_is_not_a_number_is_unknown(self):
        result = self._plan("lots")
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_an_object_is_unknown(self):
        result = self._plan({"pct": 90})
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_a_list_is_unknown(self):
        result = self._plan([90])
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_a_boolean_is_unknown_not_one_percent(self):
        # bool subclasses int, so float(True) is 1.0 -- a silently wrong
        # ranking rather than a crash, which is worse.
        result = self._plan(True)
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_nan_is_unknown_because_it_cannot_be_ordered(self):
        result = self._plan(float("nan"))
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_infinity_is_unknown(self):
        result = self._plan(float("inf"))
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_a_numeric_string_is_coerced_not_discarded(self):
        result = self._plan("87")
        self.assertEqual(result["assignments"]["developer"], "broken")

    def test_an_agent_entry_that_is_not_an_object_is_unknown(self):
        result = plan(
            ["developer"],
            {"agents": {"broken": "idle", "healthy": {"headroom_pct": 50.0}}},
            warn=[].append,
        )
        self.assertEqual(result["assignments"]["developer"], "healthy")

    def test_the_warning_names_the_agent_and_the_value(self):
        self._plan("lots")
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("broken", self.warnings[0])
        self.assertIn("lots", self.warnings[0])

    def test_a_good_value_warns_about_nothing(self):
        self._plan(70.0)
        self.assertEqual(self.warnings, [])

    def test_a_null_headroom_is_not_a_warning(self):
        # Busy or skipped agents legitimately have none.
        self._plan(None)
        self.assertEqual(self.warnings, [])

    def test_the_bad_value_sorts_last_and_is_named_in_the_rationale(self):
        result = self._plan("lots", roles=("developer", "tester"))
        self.assertEqual(result["assignments"]["tester"], "broken")
        self.assertIn("broken=null", result["rationale"][0])

    def test_every_agent_being_malformed_still_produces_a_plan(self):
        result = plan(
            ["developer", "tester"],
            {"agents": {"zeta": {"headroom_pct": "?"}, "alpha": {"headroom_pct": "?"}}},
            warn=[].append,
        )
        self.assertEqual(
            result["assignments"], {"developer": "alpha", "tester": "zeta"}
        )


class NoDuplicateAgentTest(unittest.TestCase):
    """The planner must never hand one agent two roles.

    `apply` briefs one pane per role, so a duplicate would mean the second
    brief overwriting the first and one role silently going undone.
    """

    def test_every_role_gets_a_different_agent(self):
        result = plan(ROLES, snapshot(claude=92.0, codex=87.0, grok=100.0))
        agents = list(result["assignments"].values())
        self.assertEqual(len(set(agents)), len(agents))

    def test_a_full_tie_still_produces_distinct_agents(self):
        result = plan(ROLES, snapshot(claude=50.0, codex=50.0, grok=50.0))
        agents = list(result["assignments"].values())
        self.assertEqual(sorted(agents), ["claude", "codex", "grok"])

    def test_all_null_headroom_still_produces_distinct_agents(self):
        result = plan(ROLES, snapshot(claude=None, codex=None, grok=None))
        agents = list(result["assignments"].values())
        self.assertEqual(sorted(agents), ["claude", "codex", "grok"])

    def test_all_malformed_headroom_still_produces_distinct_agents(self):
        result = plan(
            ROLES,
            {"agents": {n: {"headroom_pct": "?"} for n in ("claude", "codex", "grok")}},
            warn=[].append,
        )
        agents = list(result["assignments"].values())
        self.assertEqual(sorted(agents), ["claude", "codex", "grok"])

    def test_more_agents_than_roles_leaves_the_extras_unassigned(self):
        result = plan(["developer"], snapshot(claude=92.0, codex=87.0, grok=100.0))
        self.assertEqual(list(result["assignments"].values()), ["grok"])

    def test_the_planner_output_is_accepted_by_apply(self):
        # The two halves of the contract meet here: whatever the planner
        # emits, assign.normalize_assignments must not reject it.
        from teamlead.assign import normalize_assignments

        result = plan(ROLES, snapshot(claude=50.0, codex=50.0, grok=50.0))
        self.assertEqual(
            normalize_assignments(result), result["assignments"]
        )


def pooled_snapshot(groups, **headrooms):
    """A snapshot whose agents declare shared usage windows.

    `groups` maps an agent name to the `window_group` it declares; an agent
    absent from it has a window of its own.
    """
    payload = snapshot(**headrooms)
    for name, record in payload["agents"].items():
        if name in groups:
            record["window_group"] = groups[name]
    return payload


class SharedWindowTest(unittest.TestCase):
    """Two workers on one subscription draw on one window.

    The judge worker runs the same Claude account as `claude`, so a seat's
    cost has to reduce what its pool-mates have left. Modelled as independent
    headrooms, the planner reads one window as two and over-commits it.
    """

    def test_a_seat_charges_every_worker_sharing_its_window(self):
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"},
            claude=75,
            judge=75,
            codex=70,
            grok=60,
        )
        result = plan(
            ["developer", "judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        # judge fills first (15.0, the heaviest seat) and spends the pool down
        # to 60, which puts codex at 70 ahead of claude for the developer seat.
        self.assertEqual(result["assignments"]["judge"], "judge")
        self.assertEqual(result["assignments"]["developer"], "codex")

    def test_unlinked_workers_keep_independent_headroom(self):
        payload = snapshot(claude=75, judge=75, codex=70, grok=60)
        result = plan(
            ["developer", "judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["developer"], "claude")

    def test_an_agent_declaring_no_group_is_never_charged(self):
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=90, judge=90, codex=50, grok=40
        )
        result = plan(
            ["developer", "tester", "judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
        )
        # codex holds a window of its own, so the judge round never touches it.
        self.assertEqual(result["assignments"]["tester"], "codex")


class PinnedJudgeSeatTest(unittest.TestCase):
    """The planner does not choose who judges."""

    def test_the_judge_seat_goes_to_the_pinned_worker(self):
        payload = snapshot(claude=99, judge=40, codex=80, grok=70)
        result = plan(
            ["developer", "judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        # Lowest headroom on the board still holds the seat: it is pinned.
        self.assertEqual(result["assignments"]["judge"], "judge")

    def test_the_pinned_worker_holds_no_other_seat(self):
        payload = snapshot(claude=10, judge=99, codex=20, grok=30)
        result = plan(ROLES, payload, warn=lambda message: None, judge_agent="judge")
        self.assertNotIn("judge", result["assignments"].values())

    def test_a_pinned_worker_absent_from_the_snapshot_is_refused(self):
        payload = snapshot(claude=80, codex=70)
        with self.assertRaises(PlanError) as caught:
            plan(["judge"], payload, warn=lambda message: None, judge_agent="judge")
        self.assertIn("pinned to worker 'judge'", str(caught.exception))

    def test_no_pin_leaves_the_planner_ranking_as_before(self):
        payload = snapshot(claude=90, codex=70, grok=60)
        result = plan(ROLES, payload, warn=lambda message: None)
        self.assertEqual(result["assignments"]["developer"], "claude")


class JudgeCostTest(unittest.TestCase):
    """The judge is weighed explicitly, never by the unweighed-role default."""

    def test_the_judge_outweighs_every_other_seat(self):
        from teamlead.planner import DEFAULT_ROLE_COST, DEFAULT_ROLE_COSTS

        self.assertEqual(DEFAULT_ROLE_COSTS["judge"], 15.0)
        self.assertGreater(DEFAULT_ROLE_COSTS["judge"], DEFAULT_ROLE_COSTS["developer"])
        self.assertNotEqual(DEFAULT_ROLE_COSTS["judge"], DEFAULT_ROLE_COST)


class JudgeWindowExhaustionTest(unittest.TestCase):
    """A judge round the pinned window cannot cover halts, never degrades.

    The seat has no substitute, so there is no second-best worker to fall
    back to when the window runs out. The planner refuses rather than
    dispatching a round it cannot finish.
    """

    def test_a_window_that_cannot_cover_a_ruling_is_refused(self):
        payload = snapshot(claude=90, judge=10, codex=80)
        with self.assertRaises(PlanError) as caught:
            plan(["judge"], payload, warn=lambda message: None, judge_agent="judge")
        message = str(caught.exception)
        self.assertIn("10% headroom", message)
        self.assertIn("no substitute judge", message)

    def test_exactly_enough_headroom_is_allowed(self):
        payload = snapshot(judge=15, codex=80)
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["judge"], "judge")

    def test_an_unmeasured_window_is_not_exhaustion(self):
        # Unknown is unmeasured, not spent; the existing unknown-headroom
        # handling names it in the rationale rather than halting the round.
        payload = snapshot(judge=None, codex=80)
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["judge"], "judge")

    def test_an_operator_weight_moves_the_threshold(self):
        payload = snapshot(judge=10, codex=80)
        result = plan(
            ["judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            role_costs={"judge": 5},
        )
        self.assertEqual(result["assignments"]["judge"], "judge")


class DivergentWindowReadingTest(unittest.TestCase):
    """One window, several readings: the lowest is the one that binds.

    Workers on a shared window are measured one after another, not at one
    instant, so two records for the same pool can disagree. A pool only
    drains, so the lower reading is the later truth. Trusting the judge's own
    record alone would dispatch a ruling the window cannot cover.
    """

    def test_a_spent_pool_mate_halts_a_healthy_looking_judge(self):
        payload = pooled_snapshot(
            {"judge": "pool", "claude": "pool"}, judge=20, claude=5, codex=90
        )
        with self.assertRaises(PlanError) as caught:
            plan(["judge"], payload, warn=lambda message: None, judge_agent="judge")
        message = str(caught.exception)
        self.assertIn("5% headroom", message)
        self.assertIn("'claude'", message)

    def test_the_details_name_the_record_the_reading_came_from(self):
        payload = pooled_snapshot(
            {"judge": "pool", "claude": "pool"}, judge=20, claude=5, codex=90
        )
        with self.assertRaises(PlanError) as caught:
            plan(["judge"], payload, warn=lambda message: None, judge_agent="judge")
        self.assertEqual(caught.exception.details["measured_through"], "claude")
        self.assertEqual(caught.exception.details["window_group"], "pool")

    def test_a_spent_worker_outside_the_window_does_not_halt(self):
        payload = pooled_snapshot(
            {"judge": "pool", "claude": "pool"}, judge=40, claude=40, codex=1
        )
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["judge"], "judge")

    def test_an_unmeasured_pool_mate_does_not_halt_a_funded_window(self):
        payload = pooled_snapshot(
            {"judge": "pool", "claude": "pool"}, judge=40, claude=None, codex=90
        )
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["judge"], "judge")


class LegacySnapshotTest(unittest.TestCase):
    """A version-1 snapshot carries no `window_group` and still plans.

    The snapshot bump to 2 is additive: an older document's records simply
    lack the field, which reads as "this agent has a window of its own". The
    planner never migrates a snapshot -- it is a measurement, not a ledger.
    """

    def test_a_version_1_snapshot_plans_without_window_groups(self):
        payload = snapshot(claude=90, codex=70, grok=60)
        payload["schema_version"] = 1
        result = plan(ROLES, payload, warn=lambda message: None)
        self.assertEqual(result["assignments"]["developer"], "claude")

    def test_a_version_1_snapshot_never_halts_the_judge(self):
        payload = snapshot(judge=40, codex=70)
        payload["schema_version"] = 1
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["assignments"]["judge"], "judge")


class JudgeTierEchoTest(unittest.TestCase):
    """The plan carries the tier the judge worker is launched on.

    Effort is a launch flag on both harnesses, so a tier switch is a worker
    start. Echoing the configured tier here is what keeps the config the
    single place a model swap happens: the caller builds the launch argv from
    this document rather than typing a model by hand.
    """

    def test_the_tier_is_echoed_when_the_judge_seat_is_planned(self):
        payload = snapshot(judge=50, codex=80)
        result = plan(
            ["judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            judge_tier={
                "model": "claude-fable-5-1",
                "effort": "max",
            },
        )
        self.assertEqual(
            result["judge"],
            {
                "agent": "judge",
                "model": "claude-fable-5-1",
                "effort": "max",
            },
        )

    def test_a_model_taking_no_effort_flag_echoes_null(self):
        # The caller omits --effort on null; an empty string would be typed
        # onto the command line as a flag with no value.
        payload = snapshot(judge=50, codex=80)
        result = plan(
            ["judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            judge_tier={"model": "claude-haiku-4-5", "effort": ""},
        )
        self.assertIsNone(result["judge"]["effort"])
        self.assertEqual(result["judge"]["model"], "claude-haiku-4-5")

    def test_a_plan_without_the_judge_seat_carries_no_tier(self):
        payload = snapshot(claude=90, codex=70, grok=60)
        result = plan(
            ROLES, payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertNotIn("judge", result)

    def test_the_judge_role_without_a_config_block_is_refused(self):
        # Ranking an ordinary worker into the seat would emit a plan with no
        # usable tier, which fails later at worker startup -- a long way from
        # the config that caused it.
        payload = snapshot(claude=90, codex=70)
        with self.assertRaises(PlanError) as caught:
            plan(["judge"], payload, warn=lambda message: None)
        self.assertIn("`judge` block", str(caught.exception))

    def test_other_roles_are_unaffected_by_a_missing_judge_block(self):
        payload = snapshot(claude=90, codex=70, grok=60)
        result = plan(ROLES, payload, warn=lambda message: None)
        self.assertEqual(result["assignments"]["developer"], "claude")

    def test_an_unconfigured_tier_still_names_the_agent(self):
        payload = snapshot(judge=50, codex=80)
        result = plan(
            ["judge"], payload, warn=lambda message: None, judge_agent="judge"
        )
        self.assertEqual(result["judge"]["agent"], "judge")
        self.assertIsNone(result["judge"]["model"])


class PlanSchemaVersionTest(unittest.TestCase):
    """The judge object is an additive bump readers absorb through absence."""

    def test_a_plan_with_no_judge_seat_carries_no_judge_key(self):
        # Indistinguishable from a version-1 plan, on purpose: a reader takes
        # the same path for both.
        result = plan(ROLES, snapshot(claude=90, codex=70, grok=60), warn=lambda m: None)
        self.assertEqual(result["schema_version"], 4)
        self.assertNotIn("judge", result)

    def test_the_assignments_shape_is_unchanged_by_the_bump(self):
        # `apply` reads `assignments` alone, which every plan version carries
        # identically; the bump must not disturb it.
        result = plan(ROLES, snapshot(claude=90, codex=70, grok=60), warn=lambda m: None)
        self.assertEqual(sorted(result["assignments"]), sorted(ROLES))
        for agent in result["assignments"].values():
            self.assertIsInstance(agent, str)


class JudgeAffordabilityAfterOtherSeatsTest(unittest.TestCase):
    """Affordability is judged after the seats ahead of it have spent.

    Checked once before the fill loop, a heavier seat on the same window
    passes its own check, spends the pool, and leaves the judge projected
    below zero -- a round the window cannot cover, planned anyway.
    """

    def test_a_heavier_seat_on_the_same_window_can_halt_the_judge(self):
        # Only the shared pool can hold the developer seat, so it must spend
        # from the window the judge needs: 25 - 20 = 5, and a ruling costs 15.
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=25, judge=25
        )
        with self.assertRaises(PlanError) as caught:
            plan(
                ["developer", "judge"],
                payload,
                warn=lambda message: None,
                judge_agent="judge",
                role_costs={"developer": 20, "judge": 15},
            )
        self.assertIn("5% headroom", str(caught.exception))

    def test_a_window_that_covers_both_seats_still_plans(self):
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=90, judge=90
        )
        result = plan(
            ["developer", "judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            role_costs={"developer": 20, "judge": 15},
        )
        self.assertEqual(result["assignments"]["judge"], "judge")
        self.assertEqual(result["assignments"]["developer"], "claude")

    def test_a_seat_off_the_window_does_not_halt_the_judge(self):
        # codex holds its own window, so the developer seat lands there and
        # the pool is untouched.
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=25, judge=25, codex=90
        )
        result = plan(
            ["developer", "judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            role_costs={"developer": 20, "judge": 15},
        )
        self.assertEqual(result["assignments"]["developer"], "codex")
        self.assertEqual(result["assignments"]["judge"], "judge")


class AssignedWorkerStillChargedTest(unittest.TestCase):
    """A worker that holds a seat keeps spending from its window.

    Charging only the unassigned workers leaves the assigned one carrying a
    stale reading, and affordability takes the minimum across the whole
    window -- so a pool an earlier seat spent to zero still looked affordable
    through its pool-mate's untouched number.
    """

    def test_divergent_readings_with_an_earlier_seat_halt_the_judge(self):
        # claude reads 20 and judge 100 on one window. The developer seat
        # costs 20 and lands on claude, spending the window to 0; a ruling
        # costs 15.
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=20, judge=100
        )
        with self.assertRaises(PlanError) as caught:
            plan(
                ["developer", "judge"],
                payload,
                warn=lambda message: None,
                judge_agent="judge",
                role_costs={"developer": 20, "judge": 15},
            )
        self.assertIn("0% headroom", str(caught.exception))

    def test_the_charge_reaches_a_worker_already_holding_a_seat(self):
        payload = pooled_snapshot(
            {"claude": "pool", "judge": "pool"}, claude=100, judge=100
        )
        result = plan(
            ["developer", "judge"],
            payload,
            warn=lambda message: None,
            judge_agent="judge",
            role_costs={"developer": 20, "judge": 15},
        )
        # Both fit: 100 - 20 - 15 = 65 left on the window.
        self.assertEqual(result["assignments"]["developer"], "claude")
        self.assertEqual(result["assignments"]["judge"], "judge")


if __name__ == "__main__":
    unittest.main()
