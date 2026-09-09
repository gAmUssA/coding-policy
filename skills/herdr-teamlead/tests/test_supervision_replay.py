"""Retry saved acknowledgements and refuse bootstrap over lost owner history."""

import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import supervision as store
from teamlead import supervision_hook as hook
from teamlead.errors import StateError, UsageError

AT = "2026-09-01T12:00:00+00:00"
DEADLINE = "2026-09-01T12:00:30+00:00"
LATER = "2026-09-01T12:01:00+00:00"


class SupervisionReplayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="supervision-replay-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.bindings = self.root / "bindings"
        self.who = store.identity("lead", str(self.root), "fixture", pane_id="p0")
        store.bind(self.path, self.who, AT, root=self.bindings)
        self.proof = self.root / "TASK-LEDGER.md"
        self.proof.write_text("Report checkpoint pending; task is not accepted.\n", encoding="utf-8")
        store.enroll(self.path, {"id": "d1", "agent": "worker", "task": "task",
                                "report": str(self.root / "report.md"), "pane_id": "p1", "native_session": None}, AT)
        self.emit()

    def emit(self):
        return store.transaction(self.path, lambda data: store.append_event(data, AT, "d1", "report_observed", {}))

    def request(self, **changes):
        return {"through": 1, "outcomes": [{"event": "event-1", "outcome": "Report still pending",
                                          "evidence": [str(self.proof)], "pending": True, **changes}]}

    def acknowledge(self, request=None, at=AT):
        return store.acknowledge(self.path, self.request() if request is None else request, at)

    def stop(self, name="lead"):
        return hook.check({"cwd": str(self.root), "session_id": name},
                          {"HERDR_ENV": "fixture", "HERDR_PANE_ID": "p0"}, LATER, root=self.bindings)

    def test_retry_retains_original_receipt_after_ledger_append(self):
        self.acknowledge()
        original = store.load(self.path)["acknowledgements"]
        self.proof.write_text("First outcome retained; a second worker outcome was appended.\n", encoding="utf-8")
        self.acknowledge(at=LATER)
        self.assertEqual(store.load(self.path)["acknowledgements"], original)

    def test_retry_retains_original_receipt_after_evidence_disappears(self):
        self.acknowledge()
        original = store.load(self.path)["acknowledgements"]
        self.proof.unlink()
        self.acknowledge(at=LATER)
        self.assertEqual(store.load(self.path)["acknowledgements"], original)

    def test_explicit_recheck_retry_after_due_preserves_original_schedule(self):
        request = self.request(recheck_at=DEADLINE)
        self.acknowledge(request)
        original = store.load(self.path)["acknowledgements"]
        self.acknowledge(request, at=LATER)
        self.assertEqual(store.load(self.path)["acknowledgements"], original)
        self.assertEqual(original[0]["recheck_at"], DEADLINE)

    def test_conflicting_input_refuses_before_rereading_lost_evidence(self):
        self.acknowledge()
        before = store.store_path(self.path).read_bytes()
        self.proof.unlink()
        for change in ({"outcome": "Actually complete"}, {"evidence": [str(self.root / "different.md")]},
                       {"pending": False}, {"recheck_at": DEADLINE}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(UsageError, "different handled outcome"):
                    self.acknowledge(self.request(**change), at=LATER)
                self.assertEqual(store.store_path(self.path).read_bytes(), before)

    def test_new_ack_still_requires_readable_evidence(self):
        self.proof.unlink()
        with self.assertRaisesRegex(StateError, "Cannot read evidence"):
            self.acknowledge()
        self.assertEqual(store.load(self.path)["acknowledgements"], [])

    def test_new_ack_still_rejects_expired_explicit_recheck(self):
        with self.assertRaisesRegex(UsageError, "must not precede"):
            self.acknowledge(self.request(recheck_at=DEADLINE), at=LATER)
        self.assertEqual(store.load(self.path)["acknowledgements"], [])

    def test_mixed_replay_and_new_ack_preserve_event_arriving_after_drain(self):
        self.acknowledge()
        original = copy.deepcopy(store.load(self.path)["acknowledgements"][0])
        self.proof.unlink()
        self.emit()
        high = store.drain(self.path)["through"]
        self.emit()
        new_proof = self.root / "second-outcome.md"
        new_proof.write_text("Second observation reconciled.\n", encoding="utf-8")
        request = self.request()
        request["through"] = high
        request["outcomes"].append({"event": "event-2", "outcome": "Second observation handled", "evidence": [str(new_proof)]})
        result = self.acknowledge(request, at=LATER)
        self.assertEqual([row["id"] for row in result["remaining"]], ["event-3"])
        self.assertEqual(store.load(self.path)["acknowledgements"][0], original)

    def test_later_invalid_batch_member_does_not_partially_acknowledge(self):
        self.emit()
        request = self.request()
        request["through"] = 2
        request["outcomes"].append({"event": "event-2", "outcome": "Second outcome", "evidence": [str(self.root / "missing.md")]})
        with self.assertRaises(StateError):
            self.acknowledge(request)
        self.assertEqual(store.load(self.path)["acknowledgements"], [])

    def test_same_lead_cannot_rebind_missing_owner_into_empty_history(self):
        discovery = store.binding_path(self.who, self.bindings)
        before = discovery.read_bytes()
        store.store_path(self.path).unlink()
        self.assertEqual(self.stop()["decision"], "block")
        with self.assertRaisesRegex(StateError, "missing supervision owner"):
            store.bind(self.path, self.who, LATER, root=self.bindings)
        self.assertFalse(store.store_path(self.path).exists())
        self.assertEqual(discovery.read_bytes(), before)
        self.assertEqual(self.stop()["decision"], "block")

    def test_new_lead_cannot_bootstrap_over_previous_leads_missing_owner(self):
        store.store_path(self.path).unlink()
        new_who = store.identity("replacement", str(self.root), "fixture", pane_id="p0")
        with self.assertRaisesRegex(StateError, "missing supervision owner"):
            store.bind(self.path, new_who, LATER, root=self.bindings)
        self.assertFalse(store.store_path(self.path).exists())
        self.assertFalse(store.binding_path(new_who, self.bindings).exists())

    def test_resume_cannot_initialize_missing_owner_before_rebind(self):
        store.store_path(self.path).unlink()
        with self.assertRaisesRegex(StateError, "missing or unbound"):
            store.resume(self.path, LATER)
        self.assertFalse(store.store_path(self.path).exists())

    def test_dangling_owner_symlink_is_not_overwritten(self):
        target = store.store_path(self.path)
        target.unlink()
        target.symlink_to(self.root / "missing-owner.json")
        with self.assertRaisesRegex(StateError, "dangling link"):
            store.bind(self.path, self.who, LATER, root=self.bindings)
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.stop()["decision"], "block")

    def test_dangling_discovery_symlink_is_not_overwritten(self):
        target = store.binding_path(self.who, self.bindings)
        target.unlink()
        target.symlink_to(self.root / "missing-discovery.json")
        with self.assertRaisesRegex(StateError, "dangling link"):
            store.bind(self.path, self.who, LATER, root=self.bindings)
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.stop()["decision"], "block")

    def test_existing_owner_survives_interrupted_generation_handoff_retry(self):
        new_who = store.identity("replacement", str(self.root), "fixture", pane_id="p0")
        save = store.save_state
        def fail_owner(path, data):
            if Path(path) == store.store_path(self.path):
                raise StateError("Owner commit interrupted; retry it.", {})
            return save(path, data)
        with patch.object(store, "save_state", side_effect=fail_owner):
            with self.assertRaises(StateError):
                store.bind(self.path, new_who, LATER, root=self.bindings)
        self.assertEqual(self.stop("replacement")["decision"], "block")
        store.bind(self.path, new_who, LATER, root=self.bindings)
        self.assertIsNone(self.stop())
        self.assertEqual(self.stop("replacement")["decision"], "block")
        self.assertEqual(store.load(self.path)["members"][0]["id"], "d1")

    def test_unrelated_existing_bindings_allow_first_use_of_another_owner(self):
        other = self.root / "different-owner.json"
        other_who = store.identity("other-lead", str(self.root), "fixture", pane_id="p2")
        store.bind(other, other_who, LATER, root=self.bindings)
        self.assertEqual(store.load(other)["binding"]["identity"], other_who)
        self.assertEqual(store.load(self.path)["members"][0]["id"], "d1")

    def test_interrupted_first_bind_retries_from_unbound_empty_owner(self):
        other = self.root / "first-use.json"
        other_who = store.identity("first-lead", str(self.root), "fixture", pane_id="p2")
        save = store.save_state
        def fail_bound_owner(path, data):
            if Path(path) == store.store_path(other) and data["binding"] is not None:
                raise StateError("Initial binding commit interrupted; retry it.", {})
            return save(path, data)
        with patch.object(store, "save_state", side_effect=fail_bound_owner):
            with self.assertRaises(StateError):
                store.bind(other, other_who, AT, root=self.bindings)
        self.assertIsNone(store.load(other)["binding"])
        self.assertEqual(store.load(other)["members"], [])
        with self.assertRaisesRegex(StateError, "missing or unbound"):
            store.resume(other, LATER)
        store.bind(other, other_who, LATER, root=self.bindings)
        self.assertEqual(store.load(other)["binding"]["identity"], other_who)


if __name__ == "__main__":
    unittest.main()
