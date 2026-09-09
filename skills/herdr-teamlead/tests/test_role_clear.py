"""Role-clear recovery exercises the public owner command and normal apply."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import json
import unittest

from teamlead import recovery
from teamlead.state import add_assignment, empty_state, save_state
from tests import test_recovery_cli as fixture

AUTH, BASE, TASK, WORK = fixture.AUTH, fixture.BASE, fixture.TASK, fixture.WORK

EARLIER = "2026-02-03T08:00:00+00:00"
DEVELOPER_AT = "2026-02-03T09:00:00+00:00"
CLEAR_AT = "2026-02-03T09:30:00+00:00"
CLEAR_TASK = "another-authorized-task"


class RoleClearTests(fixture.fixture.CliCase):
    _client = fixture.RecoveryCommandTests._client
    invoke = fixture.RecoveryCommandTests.invoke
    owner = fixture.RecoveryCommandTests.owner
    register = fixture.RecoveryCommandTests.register
    saved = fixture.RecoveryCommandTests.saved
    apply_args = fixture.RecoveryCommandTests.apply_args
    fresh_client = fixture.RecoveryCommandTests.fresh_client

    def setUp(self):
        super().setUp()
        self.runner = fixture.FakeRunner()
        self.evidence = self.tmp / "clear-output.json"
        self.work = self.tmp / "work.json"
        self.work.write_text(json.dumps(WORK))

    def seed_role_clear(self, fix=2):
        state = empty_state()
        for count in range(fix + 1):
            at = DEVELOPER_AT if count == fix else EARLIER.replace("08:00", "08:{:02d}".format(count))
            add_assignment(state, at, "developer", "grok", task=TASK, fix_round=count or None,
                           cleared=count == 0, clear_reason="automatic" if count == 0 else "retained",
                           context_session={"source": "herdr:grok", "kind": "id", "value": "original-developer", "pane_id": "w4:p1", "agent": "grok"})
        save_state(self.state, state)
        self.register()
        code, _, err = self.owner("task", {"task": CLEAR_TASK, "base_revision": BASE, "scope": "Verify another task",
            "allowed_paths": ["src/*"], "authorization": AUTH})
        self.assertEqual(code, 0, err)
        args = self.apply_args("tester")
        args[args.index(TASK)] = CLEAR_TASK
        args[args.index("--now") + 1] = CLEAR_AT
        code, output, err = self.invoke(args, self._client({"grok": "idle"}, sessions={"grok": "cleared-tester"}))
        self.assertEqual(code, 0, err)
        self.evidence.write_text(output)
        return {"id": "role-clear-next", "task": TASK, "base_revision": BASE, "assignment_index": fix,
                "clearing_assignment_index": fix + 1, "next_fix": fix + 1, "correction_plan": None,
                "work": copy.deepcopy(WORK), "clearing_authorization": {"task": CLEAR_TASK},
                "evidence": str(self.evidence), "reason": "The lead reused the developer for another authorized tester role"}

    def recover(self, data, state="idle"):
        return self.owner("recover-role-clear", data, self._client({"grok": state}, sessions={"grok": "later-observation"}))

    def test_verified_role_clear_recovers_once_preserving_proof_authority_and_counts(self):
        data = self.seed_role_clear()
        before = self.saved()
        code, output, err = self.recover(data)
        self.assertEqual(code, 0, err)
        record = json.loads(output)
        self.assertEqual(record["observed_session"]["value"], "later-observation")
        self.assertEqual(record["clear_authority"], {"task": CLEAR_TASK, "authorization": AUTH})
        self.assertFalse(record["grants_future_attempts"])
        self.assertEqual(self.saved()["assignments"], before["assignments"])
        self.assertEqual(self.saved()["recovery"]["plans"], [])
        self.assertEqual(self.runner.writes(), [])
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.saved()["recovery"]["role_clearances"]), 1)
        code, _, err = self.invoke(self.apply_args("developer", 3, "--retain-context", "--work", str(self.work)),
            self._client({"grok": "idle"}, sessions={"grok": "later-observation"}))
        self.assertEqual(code, 1)
        self.assertIn("Cannot retain", err)
        self.assertEqual(self.runner.writes(), [])
        code, _, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
            "--task", TASK, "--fix-round", "3", "--work", str(self.work), "--now", fixture.AT])
        self.assertEqual(code, 0, err)
        args = self.apply_args("developer", 3, "--work", str(self.work), "--dispatch-id", "recovered-third")
        code, output, err = self.invoke(args, self.fresh_client("later-observation", "fresh-third"))
        self.assertEqual(code, 0, err)
        result = json.loads(output)["applied"][0]
        self.assertEqual(result["context_transition"]["reason"], "verified_role_clear_handoff")
        self.assertEqual(result["fix_round"], 3)
        self.assertTrue(result["cleared"])
        self.assertEqual(sum(call.startswith("agent prompt grok ") for call in self.runner.writes()), 1)
        self.assertTrue(any("/new" in call for call in self.runner.writes()))
        self.assertEqual(self.saved()["assignments"][:-1], before["assignments"])
        self.assertEqual(self.saved()["assignments"][-1]["context_session"]["value"], "fresh-third")
        after = self.state.read_bytes()
        code, output, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(output)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.read_bytes(), after)
        code, output, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(output)["tasks"][TASK]["confirmed_fixes"], 3)

    def test_missing_or_forged_evidence_cannot_create_recovery(self):
        data = self.seed_role_clear()
        original = self.evidence.read_text()
        for body in ("", "a new claim that the worker cleared", "{}", json.dumps({"applied": [{"cleared": True}]})):
            with self.subTest(body=body):
                self.evidence.write_text(body)
                before = self.state.read_bytes()
                code, _, err = self.recover(data)
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.writes(), [])
        self.evidence.write_text(original)
        data["evidence"] = str(self.tmp / "missing-evidence.json")
        code, _, err = self.recover(data)
        self.assertEqual(code, 1)
        self.assertIn("Cannot read evidence", err)

    def test_incompatible_task_base_count_and_scope_are_rejected(self):
        original = self.seed_role_clear()
        cases = (("task", CLEAR_TASK), ("base_revision", "c" * 40), ("assignment_index", True),
                 ("assignment_index", 0), ("clearing_assignment_index", 1), ("next_fix", 2), ("next_fix", 4),
                 ("clearing_authorization", {"task": TASK}), ("clearing_authorization", {}),
                 ("work", {**WORK, "scope": "Unapproved rewrite"}),
                 ("work", {**WORK, "paths": ["secrets/credentials.py"]}),
                 ("work", {**WORK, "findings": []}))
        for key, value in cases:
            with self.subTest(key=key, value=value):
                data = {**original, key: value}
                before = self.state.read_bytes()
                code, _, err = self.recover(data)
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.writes(), [])

    def test_actual_recorded_clear_and_known_developer_proof_are_required(self):
        data = self.seed_role_clear()
        original = self.saved()
        for index, updates in ((2, {"context_session": None}), (3, {"cleared": False}),
                               (3, {"clear_reason": "hand"}), (3, {"agent": "codex"}),
                               (3, {"at": DEVELOPER_AT}), (3, {"at": EARLIER})):
            with self.subTest(index=index, updates=updates):
                state = copy.deepcopy(original)
                state["assignments"][index].update(updates)
                # Mutated source rows are rejected by owner validation or recovery.
                self.state.write_text(json.dumps(state))
                before = self.state.read_bytes()
                code, _, err = self.recover(data)
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.writes(), [])
        self.state.write_text(json.dumps(original))

    def test_pending_or_consumed_attempt_and_exhausted_budget_are_rejected(self):
        data = self.seed_role_clear()
        state = self.saved()
        add_assignment(state, "2026-02-03T09:45:00+00:00", "developer", "codex", task=TASK, fix_round=3)
        save_state(self.state, state)
        code, _, err = self.recover(data)
        self.assertEqual(code, 1)
        self.assertIn("preceding correction", err)
        data = self.seed_role_clear(5)
        code, _, err = self.recover(data)
        self.assertEqual(code, 1)
        self.assertIn("correction plan", err)
        self.assertEqual(self.runner.writes(), [])

    def test_missing_clear_authority_requires_its_explicit_archived_decision(self):
        data = self.seed_role_clear()
        state = self.saved()
        del state["recovery"]["tasks"][CLEAR_TASK]
        save_state(self.state, state)
        code, _, err = self.recover(data)
        self.assertEqual(code, 1)
        self.assertIn("no recorded base/scope", err)
        decision = self.tmp / "operator-decision.md"
        quote = "The tester assignment for another-authorized-task was authorized."
        decision.write_text(quote)
        data["clearing_authorization"] = {"authorization": {"source": "Original operator decision", "quote": quote}, "evidence": str(decision)}
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.saved()["recovery"]["plans"], [])

    def test_busy_workers_are_never_interrupted_and_apply_requires_readiness(self):
        data = self.seed_role_clear()
        for state in ("working", "blocked"):
            with self.subTest(state=state):
                before = self.state.read_bytes()
                code, _, err = self.recover(data, state)
                self.assertEqual(code, 1)
                self.assertIn("idle/done", err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), before)
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        for extra, state in (([], "working"), (["--no-clear"], "idle")):
            code, _, err = self.invoke(self.apply_args("developer", 3, "--work", str(self.work), *extra), self._client({"grok": state}))
            self.assertEqual(code, 1, err)
            self.assertEqual(self.runner.writes(), [])

    def test_recorded_bounds_and_evidence_are_rechecked_by_plan_and_apply(self):
        data = self.seed_role_clear()
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        for changed in ({**WORK, "paths": ["other/file.py"]}, {**WORK, "findings": ["different finding"]}, None):
            with self.subTest(work=changed):
                self.work.write_text(json.dumps(changed))
                for command in (["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                                 "--task", TASK, "--fix-round", "3", "--work", str(self.work), "--now", fixture.AT],
                                self.apply_args("developer", 3, "--work", str(self.work))):
                    code, _, err = self.invoke(command, self._client({}))
                    self.assertEqual(code, 1)
                    self.assertEqual(self.runner.calls, [])
        self.work.write_text(json.dumps(WORK))
        self.evidence.write_text("{}")
        code, _, err = self.invoke(self.apply_args("developer", 3, "--work", str(self.work)), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("Archived clear evidence", err)
        self.assertEqual(self.runner.calls, [])

    def test_unresolved_dispatch_blocks_recovery_without_counting_or_input(self):
        data = self.seed_role_clear()
        state = self.saved()
        recovery.reserve(state["recovery"], {"id": "uncertain:developer", "fingerprint": "pending-fixture",
            "role": "developer", "agent": "codex", "task": TASK, "fix_round": 3, "plan": None, "work": WORK}, fixture.AT)
        save_state(self.state, state)
        before = self.state.read_bytes()
        code, _, err = self.recover(data)
        self.assertEqual(code, 1)
        self.assertIn("pending dispatch", err)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.runner.writes(), [])

    def test_recovery_receipt_tampering_is_rejected_without_rewriting_history(self):
        data = self.seed_role_clear()
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        original = self.saved()
        changes = ({"next_fix": 4}, {"clearing_dispatch": "fabricated:tester"},
                   {"grants_future_attempts": True}, {"at": EARLIER},
                   {"receipts": {"clear": {"path": "/invented.json", "sha256": "a" * 64}, "authorization": None}})
        for change in changes:
            with self.subTest(change=change):
                state = copy.deepcopy(original)
                state["recovery"]["role_clearances"][0].update(change)
                self.state.write_text(json.dumps(state))
                before = self.state.read_bytes()
                code, _, err = self.invoke(self.apply_args("developer", 3, "--work", str(self.work)), self._client({}))
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.calls, [])

    def test_conflicting_recovery_identity_or_second_identity_cannot_replace_receipt(self):
        data = self.seed_role_clear()
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        for change in ({"reason": "Changed claim"}, {"id": "second-identity"}):
            before = self.state.read_bytes()
            code, _, err = self.recover({**data, **change})
            self.assertEqual(code, 1, err)
            self.assertEqual(self.state.read_bytes(), before)
            self.assertEqual(self.runner.writes(), [])

    def test_tier_selection_still_refuses_ineligible_recovered_developer(self):
        data = self.seed_role_clear()
        code, _, err = self.recover(data)
        self.assertEqual(code, 0, err)
        config = json.loads(self.config.read_text())
        config["schema_version"] = 2
        config["agents"][2]["tiers"] = {"mechanical": {"model": "grok-code-fast-1", "effort": "high"}}
        self.config.write_text(json.dumps(config))
        code, _, err = self.invoke(self.apply_args("developer", 3, "--work", str(self.work)), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("has no 'fix' tier", err)
        self.assertEqual(self.runner.calls, [])

    def test_schema_two_migration_preserves_history_and_adds_empty_recovery_arrays(self):
        self.seed_role_clear()
        state = self.saved()
        state["recovery"]["schema_version"] = 2
        del state["recovery"]["role_clearances"]
        del state["recovery"]["delivery_recoveries"]
        self.state.write_text(json.dumps(state))
        code, _, err = self.invoke(["state"])
        self.assertEqual(code, 0, err)
        expected = copy.deepcopy(state)
        expected["recovery"].update(schema_version=4, role_clearances=[], delivery_recoveries=[])
        self.assertEqual(self.saved(), expected)



if __name__ == "__main__":
    unittest.main()
