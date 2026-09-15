"""Specialist dispatch versions preserve legacy rows and recovered identity."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import UsageError
from teamlead.recovery import (
    abort_pre_send, empty_recovery, finish_dispatch, mark_sending, migrate_store,
    prior_dispatch, reconcile, reserve, validate_store,
)

AT = "2026-02-03T10:00:00+00:00"
AUTH = {"source": "Saved operator instruction", "quote": "Reconcile the saved consultation report."}
REQUIREMENT = {"specialty": "ux", "required_capabilities": ["interaction-design"],
               "independent": False, "engagement": "onboarding-design"}


class SpecialistRecoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = empty_recovery()
        self.history = []
        self.record = {"id": "consult-1", "fingerprint": "a" * 64, "role": "advisor",
                       "agent": "worker", "task": "task-1", "fix_round": None,
                       "plan": None, "work": None, "requirements": copy.deepcopy(REQUIREMENT)}

    def outcome(self, record=None, status="applied"):
        record = record or self.record
        return {"status": status, **{key: copy.deepcopy(record[key])
            for key in ("task", "role", "agent", "fix_round", "requirements", "reviewer_scope") if key in record}}

    def finish(self, record=None):
        record = record or self.record
        reserve(self.store, record, AT)
        result = self.outcome(record)
        self.history.append({"schema_version": 6, "at": AT, **result})
        finish_dispatch(self.store, record["id"], result, len(self.history) - 1, AT)
        return self.store["dispatches"][-1]

    def test_requirements_dispatch_and_saved_result_have_independent_version_two(self):
        saved = self.finish()
        self.assertEqual(self.store["schema_version"], 9)
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["result"]["schema_version"], 2)
        self.assertEqual(saved["requirements"], REQUIREMENT)
        self.assertEqual(saved["result"]["requirements"], REQUIREMENT)
        self.assertTrue(all(row["schema_version"] == 1 for row in self.store["events"]))
        validate_store(self.store, self.history)

    def test_legacy_dispatch_and_result_keep_version_one_and_no_new_fields(self):
        record = {key: value for key, value in self.record.items() if key != "requirements"}
        record["role"] = "reviewer"
        saved = self.finish(record)
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["result"]["schema_version"], 1)
        self.assertNotIn("requirements", saved)
        self.assertNotIn("requirements", saved["result"])
        validate_store(self.store, self.history)

    def test_persisted_requirements_do_not_alias_mutable_caller_inputs(self):
        reserve(self.store, self.record, AT)
        self.record["requirements"]["engagement"] = "edited caller input"
        saved = self.store["dispatches"][0]
        self.assertEqual(saved["requirements"], REQUIREMENT)
        result = self.outcome(saved)
        finish_dispatch(self.store, saved["id"], result, 0, AT)
        result["requirements"]["engagement"] = "edited result input"
        self.assertEqual(saved["requirements"], REQUIREMENT)
        self.assertEqual(saved["result"]["requirements"], REQUIREMENT)

    def test_new_dispatch_requires_the_migrated_owner_store(self):
        self.store["schema_version"] = 4
        before = copy.deepcopy(self.store)
        with self.assertRaises(UsageError):
            reserve(self.store, self.record, AT)
        self.assertEqual(self.store, before)

    def test_mixed_legacy_and_specialist_rows_remain_readable(self):
        legacy = {key: value for key, value in self.record.items() if key != "requirements"}
        legacy.update(id="legacy", role="reviewer", agent="other-worker")
        original = copy.deepcopy(self.finish(legacy))
        self.finish()
        validate_store(self.store, self.history)
        self.assertEqual(self.store["dispatches"][0], original)

    def test_reviewer_scope_alone_and_with_requirements_use_version_two(self):
        for scope in ("verification", "design"):
            for with_requirements in (False, True):
                with self.subTest(scope=scope, with_requirements=with_requirements):
                    self.store, self.history = empty_recovery(), []
                    record = {**self.record, "role": "reviewer", "reviewer_scope": scope}
                    if with_requirements:
                        record["requirements"] = {**REQUIREMENT, "independent": True}
                    else:
                        del record["requirements"]
                    row = self.finish(record)
                    self.assertEqual(row["schema_version"], 2)
                    self.assertEqual(row["result"]["schema_version"], 2)
                    self.assertEqual(row["result"]["reviewer_scope"], scope)
                    validate_store(self.store, self.history)

    def test_unknown_null_or_nonreviewer_scope_refuses_before_reservation(self):
        for role, scope in (("reviewer", "unknown"), ("reviewer", None), ("reviewer", []),
                            ("advisor", "design"), ("developer", "verification")):
            with self.subTest(role=role, scope=scope):
                record = {key: value for key, value in self.record.items() if key != "requirements"}
                record.update(role=role, reviewer_scope=scope)
                before = copy.deepcopy(self.store)
                with self.assertRaises(UsageError):
                    reserve(self.store, record, AT)
                self.assertEqual(self.store, before)

    def test_reviewer_scope_cannot_change_during_retry_result_or_assignment(self):
        record = {key: value for key, value in self.record.items() if key != "requirements"}
        record.update(role="reviewer", reviewer_scope="verification")
        reserve(self.store, record, AT)
        abort_pre_send(self.store, record["id"], AT, "No input sent")
        before = copy.deepcopy(self.store)
        with self.assertRaises(UsageError):
            reserve(self.store, {**record, "reviewer_scope": "design"}, AT)
        self.assertEqual(self.store, before)
        with self.assertRaises(UsageError):
            finish_dispatch(self.store, record["id"], {**self.outcome(record), "reviewer_scope": "design"}, 0, AT)
        self.assertEqual(self.store, before)
        self.finish(record)
        self.history[0]["reviewer_scope"] = "design"
        with self.assertRaises(UsageError):
            validate_store(self.store, self.history)

    def test_old_store_cannot_bless_new_reviewer_scope_on_dispatch_or_result(self):
        record = {key: value for key, value in self.record.items() if key != "requirements"}
        record["role"] = "reviewer"
        self.finish(record)
        for target in ("dispatch", "result"):
            old = copy.deepcopy(self.store)
            old["schema_version"] = 4
            row = old["dispatches"][0]
            if target == "result":
                row = row["result"]
            row["reviewer_scope"] = "verification"
            before = copy.deepcopy(old)
            with self.assertRaises(UsageError):
                migrate_store(old)
            self.assertEqual(old, before)

    def test_old_store_migration_preserves_every_nested_record(self):
        legacy = {key: value for key, value in self.record.items() if key != "requirements"}
        legacy["role"] = "reviewer"
        self.finish(legacy)
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                old = copy.deepcopy(self.store)
                old["schema_version"] = version
                del old["refusal_authorizations"]
                del old["diagnoses"]
                del old["legacy_ruling_recoveries"]
                for row in old["dispatches"]:
                    row.pop("provider", None)
                    row.pop("brief_identity", None)
                if version < 3:
                    for key in ("role_clearances", "delivery_recoveries"):
                        del old[key]
                if version == 1:
                    for key in ("hand_clearances", "historical_attempts"):
                        del old[key]
                before = copy.deepcopy(old)
                self.assertTrue(migrate_store(old))
                self.assertEqual(old["schema_version"], 9)
                self.assertEqual(old["refusal_authorizations"], [])
                self.assertEqual(old["diagnoses"], [])
                for key, value in before.items():
                    if key != "schema_version":
                        self.assertEqual(old[key], value)
                validate_store(old, self.history)
                self.assertFalse(migrate_store(old))

    def test_older_store_rejects_future_dispatch_metadata_without_mutation(self):
        self.finish()
        for version in (1, 2, 3, 4):
            for kind in ("dispatch-v2", "result-v2", "unversioned-requirements", "result-requirements"):
                with self.subTest(version=version, kind=kind):
                    old = copy.deepcopy(self.store)
                    old["schema_version"] = version
                    row = old["dispatches"][0]
                    if kind != "dispatch-v2":
                        row["schema_version"] = 1
                        if kind != "unversioned-requirements":
                            del row["requirements"]
                    if kind == "result-requirements":
                        row["result"]["schema_version"] = 1
                    before = json.dumps(old, sort_keys=True)
                    with self.assertRaises(UsageError):
                        migrate_store(old)
                    self.assertEqual(json.dumps(old, sort_keys=True), before)

    def test_unsupported_versions_and_malformed_requirements_are_rejected(self):
        self.finish()
        for mutation in ({"schema_version": 3}, {"schema_version": True}, {"requirements": None},
                         {"requirements": {**REQUIREMENT, "independent": "false"}},
                         {"requirements": {**REQUIREMENT, "engagement": " padded "}},
                         {"requirements": {**REQUIREMENT, "required_capabilities": []}},
                         {"requirements": {**REQUIREMENT, "required_capabilities": ["ux", "accessibility"]}}):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.store)
                changed["dispatches"][0].update(mutation)
                with self.assertRaises(UsageError):
                    validate_store(changed, self.history)

    def test_result_and_assignment_must_preserve_the_reserved_requirements(self):
        self.finish()
        for target in ("assignment", "result"):
            with self.subTest(target=target):
                store, history = copy.deepcopy(self.store), copy.deepcopy(self.history)
                row = history[0] if target == "assignment" else store["dispatches"][0]["result"]
                row["requirements"]["engagement"] = "other-consultation"
                with self.assertRaises(UsageError):
                    validate_store(store, history)
        for version in (1, 3, True):
            changed = copy.deepcopy(self.store)
            changed["dispatches"][0]["result"]["schema_version"] = version
            with self.assertRaises(UsageError):
                validate_store(changed, self.history)

    def test_pending_result_metadata_is_checked_before_any_retry(self):
        reserve(self.store, self.record, AT)
        finish_dispatch(self.store, self.record["id"], self.outcome(status="sent_but_not_started"), 0, AT)
        validate_store(self.store, self.history)
        self.store["dispatches"][0]["result"]["requirements"]["engagement"] = "changed"
        with self.assertRaises(UsageError):
            validate_store(self.store, self.history)

    def test_legacy_version_cannot_silently_acquire_requirements(self):
        legacy = {key: value for key, value in self.record.items() if key != "requirements"}
        legacy["role"] = "reviewer"
        self.finish(legacy)
        for target in ("dispatch", "result"):
            for requirement in (None, REQUIREMENT):
                with self.subTest(target=target, requirement=requirement):
                    changed = copy.deepcopy(self.store)
                    row = changed["dispatches"][0]
                    if target == "result":
                        row = row["result"]
                    row["requirements"] = copy.deepcopy(requirement)
                    with self.assertRaises(UsageError):
                        validate_store(changed, self.history)

    def test_exact_retry_preserves_metadata_and_changed_retry_refuses(self):
        reserve(self.store, self.record, AT)
        abort_pre_send(self.store, self.record["id"], AT, "No input sent")
        before = copy.deepcopy(self.store)
        replay = prior_dispatch(self.store, self.record["id"], self.record["fingerprint"])
        assert replay is not None
        self.assertEqual(replay["requirements"], REQUIREMENT)
        self.assertEqual(self.store, before)
        for requirement in ({**REQUIREMENT, "engagement": "different"}, None):
            changed = copy.deepcopy(self.record)
            if requirement is None:
                del changed["requirements"]
            else:
                changed["requirements"] = requirement
            with self.assertRaises(UsageError):
                reserve(self.store, changed, AT)
            self.assertEqual(self.store, before)
        reserve(self.store, self.record, AT)
        self.assertEqual(self.store["dispatches"][0]["requirements"], REQUIREMENT)
        self.assertEqual(len(self.store["dispatches"]), 1)

    def test_finishing_changed_or_missing_requirements_preserves_send_record(self):
        reserve(self.store, self.record, AT)
        mark_sending(self.store, self.record["id"], AT, {"clear_reason": "retained"})
        before = copy.deepcopy(self.store)
        for requirement in (None, {**REQUIREMENT, "engagement": "different"}):
            result = self.outcome()
            if requirement is None:
                del result["requirements"]
            else:
                result["requirements"] = requirement
            with self.assertRaises(UsageError):
                finish_dispatch(self.store, self.record["id"], result, 0, AT)
            self.assertEqual(self.store, before)

    def test_unknown_send_reconciliation_preserves_requirements_for_recovered_result(self):
        reserve(self.store, self.record, AT)
        mark_sending(self.store, self.record["id"], AT, {"clear_reason": "retained"})
        evidence = self.root / "report.md"
        evidence.write_text("Consultation completed; see the saved interaction assessment.\n")
        data = {"dispatch": self.record["id"], "outcome": "applied", "reason": "Verified saved report and delivery",
                "authorization": AUTH, "evidence": str(evidence)}
        saved = reconcile(self.store, self.history, data, AT, {"agent_status": "idle"})
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["requirements"], REQUIREMENT)
        self.assertEqual(self.history, [])
        result = self.outcome()
        self.history.append({"schema_version": 6, "at": AT, **result})
        finish_dispatch(self.store, self.record["id"], result, 0, AT)
        validate_store(self.store, self.history)
        self.assertEqual(self.store["dispatches"][0]["result"]["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
