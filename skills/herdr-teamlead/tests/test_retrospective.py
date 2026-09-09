"""Durable retrospective history, crash recovery, and offline readback."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import retrospective as retro
from teamlead.errors import StateError, UsageError


AT = "2026-09-01T12:00:00+00:00"
LATER = "2026-09-01T13:00:00+00:00"
NOTE = """# Team retrospective

## Outcomes
Task owner/repo#1 passed its recorded tests; release approval remains open.

## Quality
The tester found a missing input check before the reviewer accepted the patch.

## Coordination
The developer repeated setup because the first brief omitted the test command.

## Seats and models
The assigned seats finished their checks. Cost and comparative capacity are unknown.

## Improvements
The lead will include the test command in the next brief; success is no setup retry.
"""


def descriptor(*, first=False):
    return {
        "schema_version": 1, "agent": "worker", "first_start": first,
        "transition_required": not first,
        "source": {
            "assignment_index": None if first else 0,
            "assignment_digest": None if first else "a" * 64,
            "dispatch_id": None if first else "dispatch-1",
            "dispatch_evidence": None,
            "task": None if first else "owner/repo#1",
            "role": None if first else "developer", "tier": None,
            "observation": {
                "pane_id": "w1:p1", "native": None if first else {"kind": "id", "value": "session-1"},
                "process": {"pid": 101, "argv": ["zsh"] if first else ["claude", "--dangerously-skip-permissions"]},
                "readiness": "shell" if first else "idle", "shell": first,
            },
            "report": None, "unavailable": "No report exists yet" if first else "Worker report unavailable",
        },
        "target": {
            "role": "reviewer", "model": None, "effort": None,
            "context": "start" if first else "clear", "task": "owner/repo#1", "brief": None, "common": None,
        },
    }


class RetrospectiveTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-retrospective-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"
        self.draft = self.root / "draft.md"
        self.source = self.root / "task-ledger.md"
        self.check = self.root / "check.json"
        self.draft.write_text(NOTE, encoding="utf-8")
        self.source.write_text("Task owner/repo#1: tested; release approval open.\n", encoding="utf-8")
        self.check.write_text("{}\n", encoding="utf-8")
        self.data = {
            "id": "retro-1", "note": str(self.draft),
            "period_start": "2026-09-01T10:00:00Z", "period_end": "2026-09-01T11:00:00Z",
            "triggers": ["daily"], "tasks": ["owner/repo#1"],
            "participants": ["developer"], "unavailable": {"tester": "Report missing"},
            "sources": [str(self.source)], "completed": True, "check": str(self.check),
        }

    @property
    def sidecar(self):
        return retro.directory(self.path)

    def record(self, *, data=None, coverage=None, at=AT):
        with retro.lock(self.path):
            return retro.record(self.path, self.data if data is None else data, [] if coverage is None else coverage, at)

    def index(self):
        return json.loads((self.sidecar / "index.json").read_text(encoding="utf-8"))

    def write_index(self, value):
        self.sidecar.mkdir(exist_ok=True)
        (self.sidecar / "index.json").write_text(json.dumps(value), encoding="utf-8")

    def test_missing_readback_does_not_create_state_or_sidecar(self):
        before = sorted(self.root.iterdir())
        self.assertEqual(retro.list_notes(self.path)["records"], [])
        with self.assertRaisesRegex(UsageError, "No saved retrospective"):
            retro.show(self.path)
        self.assertEqual(sorted(self.root.iterdir()), before)

    def test_note_is_self_contained_and_retry_preserves_original_completion(self):
        first = self.record()
        saved = Path(first["note"]["path"]).read_bytes()
        second = self.record(at=LATER)
        self.assertTrue(second["replayed"])
        self.assertEqual(second["completed_at"], AT)
        self.assertEqual(Path(first["note"]["path"]).read_bytes(), saved)
        self.assertEqual(len(self.index()["records"]), 1)
        header = json.loads(saved.decode().split("\n---\n", 1)[0][4:])
        self.assertEqual(header["schema_version"], 1)
        self.assertEqual(header["completed_at"], AT)
        self.assertEqual(header["tasks"], ["owner/repo#1"])
        self.assertIn(NOTE, retro.show(self.path)["markdown"])

    def test_offline_readback_survives_sources_cleanup_and_invalid_dispatch_state(self):
        recorded = self.record()
        self.draft.unlink()
        self.source.unlink()
        self.check.unlink()
        self.path.write_bytes(b"not dispatch JSON")
        before = {path: path.read_bytes() for path in self.sidecar.iterdir()}
        with patch.dict(os.environ, {"HERDR_ENV": ""}), patch.object(retro, "save_state", side_effect=AssertionError("readback wrote state")):
            self.assertEqual(retro.list_notes(self.path, task="owner/repo#1")["records"][0]["id"], recorded["id"])
            self.assertIn("release approval remains open", retro.show(self.path)["markdown"])
        self.assertEqual({path: path.read_bytes() for path in self.sidecar.iterdir()}, before)
        self.assertEqual(self.path.read_bytes(), b"not dispatch JSON")

    def test_filters_use_completion_and_latest_not_append_order(self):
        first = self.record()
        second_data = {**self.data, "id": "retro-2", "tasks": ["owner/repo#2"]}
        self.record(data=second_data, at=LATER)
        index = self.index()
        index["records"].reverse()
        self.write_index(index)
        self.assertEqual(retro.show(self.path)["record"]["id"], "retro-2")
        self.assertEqual(retro.show(self.path, task="owner/repo#1")["record"]["id"], first["id"])
        self.assertEqual([row["id"] for row in retro.list_notes(self.path, since=LATER)["records"]], ["retro-2"])
        self.assertEqual(retro.list_notes(self.path, task="absent")["records"], [])

    def test_canonical_aliases_share_history_and_other_states_do_not(self):
        self.record()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        via_alias = alias / "state.json"
        self.assertEqual(retro.directory(via_alias), self.sidecar)
        self.assertEqual(retro.show(via_alias)["record"]["id"], "retro-1")
        other = self.root / "other-state.json"
        self.assertEqual(retro.list_notes(other)["records"], [])
        self.assertFalse(retro.directory(other).exists())
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(retro.directory("~/state.json"), self.sidecar)

    def test_sidecar_lock_serializes_aliases_and_releases_without_stale_flags(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        script = """import sys
from teamlead import retrospective
from teamlead.errors import StateError
try:
    with retrospective.lock(sys.argv[1]):
        pass
except StateError:
    sys.exit(2)
"""
        def attempt(path):
            return subprocess.run([_sys.executable, "-B", "-c", script, str(path)], cwd=_ROOT, capture_output=True, timeout=10)
        with retro.lock(self.path):
            self.assertEqual(attempt(alias / "state.json").returncode, 2)
            self.assertEqual(attempt(self.root / "other.json").returncode, 0)
        released = attempt(alias / "state.json")
        self.assertEqual(released.returncode, 0, released.stderr.decode())

    def test_changed_note_sources_and_coverage_cannot_replay_same_identity(self):
        coverage = [descriptor()]
        self.record(coverage=coverage)
        before = (self.sidecar / "index.json").read_bytes()
        self.source.write_text("Changed outcome evidence", encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "different metadata"):
            self.record(coverage=coverage, at=LATER)
        self.source.write_text("Task owner/repo#1: tested; release approval open.\n", encoding="utf-8")
        changed = copy.deepcopy(coverage)
        changed[0]["source"]["observation"]["process"]["pid"] = 202
        with self.assertRaisesRegex(UsageError, "different metadata"):
            self.record(coverage=changed, at=LATER)
        self.draft.write_text(NOTE + "\nA later assessment.\n", encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "different metadata"):
            self.record(coverage=coverage, at=LATER)
        self.assertEqual((self.sidecar / "index.json").read_bytes(), before)

    def test_snapshot_empty_sections_and_placeholders_never_record(self):
        cases = ["# Status\nworker idle", "# Retrospective\n" + "\n".join("## " + name for name in retro.SECTIONS)]
        for placeholder in ("<!-- Fill this in -->", "TODO", "TBD", "n/a", "..."):
            cases.append(NOTE.replace("The tester found a missing input check before the reviewer accepted the patch.", placeholder))
        for draft in cases:
            with self.subTest(draft=draft):
                self.draft.write_text(draft, encoding="utf-8")
                with self.assertRaisesRegex(UsageError, "nonempty"):
                    self.record()
                self.assertFalse((self.sidecar / "index.json").exists())
                self.assertFalse((self.sidecar / "pending.json").exists())

    def test_nonempty_quiet_synthesis_uses_evidence_without_forcing_incidents(self):
        note = "# Quiet interval\n" + "\n".join("## {}\nThe saved task ledger has no new work; no change is warranted.\n".format(section) for section in retro.SECTIONS)
        self.draft.write_text(note, encoding="utf-8")
        with self.assertRaisesRegex(UsageError, "actual ledger"):
            self.record(data={**self.data, "sources": []})
        self.record()
        self.assertIn("no change is warranted", retro.show(self.path)["markdown"])

    def test_invalid_input_shapes_are_diagnostics_before_installation(self):
        cases = {"triggers": [["daily"]], "participants": ["developer", "developer"], "unavailable": {"": "unknown"}, "note": 123, "check": "relative.json"}
        for field, value in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(UsageError):
                    self.record(data={**self.data, field: value})
                self.assertFalse((self.sidecar / "index.json").exists())

    def test_index_malformed_or_newer_preserves_all_bytes(self):
        self.record()
        valid = self.index()
        mutations = [
            lambda value: value.update(schema_version=2),
            lambda value: value.update(schema_version=True),
            lambda value: value.pop("baseline_at"),
            lambda value: value["records"][0].update(note=None),
            lambda value: value["records"][0].pop("tasks"),
            lambda value: value["records"][0].update(unavailable={"tester": []}),
            lambda value: value["records"][0].update(period_start=LATER),
            lambda value: value["records"][0].update(completed_at="2026-09-01"),
            lambda value: value["records"][0].update(sources=[{"path": [], "sha256": "x", "size": -1}]),
            lambda value: value["records"].append(copy.deepcopy(value["records"][0])),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                broken = copy.deepcopy(valid)
                mutate(broken)
                self.write_index(broken)
                before = (self.sidecar / "index.json").read_bytes()
                with self.assertRaises(StateError):
                    retro.list_notes(self.path, task="owner/repo#1")
                self.assertEqual((self.sidecar / "index.json").read_bytes(), before)

    def test_corrupt_json_and_missing_index_with_orphan_note_are_preserved(self):
        self.record()
        index = self.sidecar / "index.json"
        index.write_bytes(b'{"schema_version":')
        with self.assertRaises(StateError):
            retro.show(self.path)
        self.assertEqual(index.read_bytes(), b'{"schema_version":')
        index.unlink()
        saved = (self.sidecar / "retro-1.md").read_bytes()
        with self.assertRaisesRegex(StateError, "index is missing"):
            self.record(at=LATER)
        self.assertEqual((self.sidecar / "retro-1.md").read_bytes(), saved)
        self.assertFalse(index.exists())

    def test_altered_note_and_contradictory_index_metadata_fail_readback(self):
        first = self.record()
        saved = Path(first["note"]["path"])
        original = saved.read_bytes()
        saved.write_bytes(original + b"changed")
        with self.assertRaisesRegex(StateError, "changed"):
            retro.show(self.path)
        saved.write_bytes(original)
        index = self.index()
        index["records"][0]["completed_at"] = LATER
        self.write_index(index)
        with self.assertRaisesRegex(StateError, "note metadata"):
            retro.show(self.path)

    def test_partial_record_before_note_installation_can_resume(self):
        with patch.object(retro, "_install_note", side_effect=StateError("injected note failure", {})):
            with self.assertRaises(StateError):
                self.record()
        self.assertFalse((self.sidecar / "index.json").exists())
        with self.assertRaises(StateError):
            retro.list_notes(self.path)
        self.record(at=LATER)
        self.assertEqual(retro.show(self.path)["record"]["completed_at"], AT)
        self.assertFalse((self.sidecar / "pending.json").exists())

    def test_index_failure_keeps_installed_note_uncommitted_until_identical_retry(self):
        original_save = retro.save_state
        def fail_index(path, value):
            if Path(path).name == "index.json":
                raise StateError("injected index failure", {})
            return original_save(path, value)
        with patch.object(retro, "save_state", side_effect=fail_index):
            with self.assertRaises(StateError):
                self.record()
        installed = (self.sidecar / "retro-1.md").read_bytes()
        with self.assertRaises(StateError):
            retro.show(self.path)
        self.record(at=LATER)
        self.assertEqual((self.sidecar / "retro-1.md").read_bytes(), installed)
        self.assertEqual(len(self.index()["records"]), 1)

    def test_index_atomic_replace_failure_preserves_previous_completed_history(self):
        self.record()
        before = (self.sidecar / "index.json").read_bytes()
        original_replace = os.replace
        def fail_replace(source, target):
            if Path(target).name == "index.json":
                raise OSError("injected replacement failure")
            return original_replace(source, target)
        second = {**self.data, "id": "retro-2"}
        with patch.object(os, "replace", side_effect=fail_replace):
            with self.assertRaises(StateError):
                self.record(data=second, at=LATER)
        self.assertEqual((self.sidecar / "index.json").read_bytes(), before)
        self.assertEqual(retro.show(self.path)["record"]["id"], "retro-1")
        self.record(data=second, at="2026-09-01T14:00:00Z")
        self.assertEqual(retro.show(self.path)["record"]["completed_at"], LATER)

    def leave_committed_journal(self):
        original_unlink = Path.unlink
        def fail_pending(path, *args, **kwargs):
            if path.name == "pending.json":
                raise OSError("injected cleanup failure")
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_pending):
            with self.assertRaisesRegex(StateError, "reconcile"):
                self.record()
        self.assertTrue((self.sidecar / "pending.json").exists())

    def test_crash_after_index_commit_is_readable_and_replay_removes_journal(self):
        self.leave_committed_journal()
        self.assertEqual(retro.show(self.path)["record"]["id"], "retro-1")
        with self.assertRaisesRegex(StateError, "pending"):
            retro.require_no_pending(self.path)
        self.assertTrue(self.record(at=LATER)["replayed"])
        self.assertFalse((self.sidecar / "pending.json").exists())
        retro.require_no_pending(self.path)
        self.record(data={**self.data, "id": "retro-2"}, at=LATER)
        self.assertEqual(len(self.index()["records"]), 2)

    def test_new_record_can_reconcile_previous_committed_journal(self):
        self.leave_committed_journal()
        self.record(data={**self.data, "id": "retro-2"}, at=LATER)
        self.assertEqual([row["completed_at"] for row in self.index()["records"]], [AT, LATER])
        self.assertFalse((self.sidecar / "pending.json").exists())

    def test_pending_conflict_or_malformed_journal_is_never_overwritten(self):
        with patch.object(retro, "_install_note", side_effect=StateError("injected note failure", {})):
            with self.assertRaises(StateError):
                self.record()
        pending = self.sidecar / "pending.json"
        original = pending.read_bytes()
        with self.assertRaisesRegex(StateError, "different retrospective transaction"):
            self.record(data={**self.data, "id": "retro-2"}, at=LATER)
        self.assertEqual(pending.read_bytes(), original)
        for content in (b"[]", b'{"schema_version":2}', b"not json"):
            pending.write_bytes(content)
            with self.assertRaises(StateError):
                self.record(at=LATER)
            self.assertEqual(pending.read_bytes(), content)

    def test_baseline_cannot_overwrite_pending_ancestry(self):
        with patch.object(retro, "_install_note", side_effect=StateError("injected note failure", {})):
            with self.assertRaises(StateError):
                self.record()
        index = retro.load(self.path, allow_pending=True)
        with self.assertRaisesRegex(StateError, "pending"):
            retro.establish_baseline(self.path, index, AT)
        self.assertIsNone(index["baseline_at"])
        self.assertFalse((self.sidecar / "index.json").exists())

    def test_first_work_baseline_is_persisted_once_without_rewriting_dispatch(self):
        self.path.write_bytes(b"original dispatch bytes")
        with retro.lock(self.path):
            index = retro.load(self.path)
            retro.establish_baseline(self.path, index, AT)
            saved = (self.sidecar / "index.json").read_bytes()
            retro.establish_baseline(self.path, index, LATER)
        self.assertEqual((self.sidecar / "index.json").read_bytes(), saved)
        self.assertEqual(retro.load(self.path)["baseline_at"], AT)
        self.assertEqual(self.path.read_bytes(), b"original dispatch bytes")

    def test_orphan_note_conflict_does_not_strand_a_new_transaction(self):
        self.record()
        orphan = self.sidecar / "retro-2.md"
        orphan.write_bytes(b"preserve original orphan")
        with self.assertRaisesRegex(StateError, "choose a new id"):
            self.record(data={**self.data, "id": "retro-2"}, at=LATER)
        self.assertFalse((self.sidecar / "pending.json").exists())
        self.assertEqual(orphan.read_bytes(), b"preserve original orphan")
        self.record(data={**self.data, "id": "retro-3"}, at=LATER)
        self.assertEqual(retro.show(self.path)["record"]["id"], "retro-3")

    def test_changed_evidence_cannot_resume_pending_coverage(self):
        with patch.object(retro, "_install_note", side_effect=StateError("injected note failure", {})):
            with self.assertRaises(StateError):
                self.record()
        pending = (self.sidecar / "pending.json").read_bytes()
        self.source.write_text("new source bytes", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "different retrospective transaction"):
            self.record(at=LATER)
        self.assertEqual((self.sidecar / "pending.json").read_bytes(), pending)
        self.assertFalse((self.sidecar / "index.json").exists())

    def test_record_cannot_move_completion_clock_backwards(self):
        self.record()
        with self.assertRaisesRegex(UsageError, "precedes a saved completion"):
            self.record(data={**self.data, "id": "retro-2"}, at="2026-09-01T11:30:00Z")
        self.assertEqual(len(self.index()["records"]), 1)

    def test_failed_pending_write_never_installs_or_completes_note(self):
        with patch.object(retro, "save_state", side_effect=StateError("injected journal failure", {})):
            with self.assertRaises(StateError):
                self.record()
        self.assertFalse((self.sidecar / "retro-1.md").exists())
        self.assertEqual(retro.list_notes(self.path)["records"], [])

    def test_note_install_io_failure_is_structured_and_retryable(self):
        with patch.object(os, "link", side_effect=OSError("injected link failure")):
            with self.assertRaisesRegex(StateError, "Cannot install"):
                self.record()
        self.assertEqual(list(self.sidecar.glob("note-*.tmp")), [])
        self.record(at=LATER)
        self.assertEqual(retro.show(self.path)["record"]["completed_at"], AT)

    def test_transition_requires_saved_coverage_or_proven_first_start(self):
        coverage = descriptor()
        self.record(coverage=[coverage])
        index = self.index()
        transition = {"schema_version": 1, "at": AT, "agent": "worker", "descriptor": coverage,
                      "incoming": {**coverage["source"]["observation"], "native": {"kind": "id", "value": "session-2"}}}
        transition["id"] = retro.digest({key: value for key, value in transition.items() if key != "at"})
        index["transitions"].append(transition)
        self.write_index(index)
        self.assertEqual(len(retro.load(self.path)["transitions"]), 1)
        index["transitions"][0]["descriptor"] = descriptor(first=True)
        row = index["transitions"][0]
        row["id"] = retro.digest({key: value for key, value in row.items() if key not in {"id", "at"}})
        self.write_index(index)
        self.assertEqual(len(retro.load(self.path)["transitions"]), 1)
        row["descriptor"]["source"]["observation"]["shell"] = False
        row["id"] = retro.digest({key: value for key, value in row.items() if key not in {"id", "at"}})
        self.write_index(index)
        with self.assertRaisesRegex(StateError, "first-start"):
            retro.load(self.path)

    def test_malformed_coverage_is_diagnostic(self):
        for mutate in (
            lambda row: row.update(schema_version=True),
            lambda row: row["source"].update(assignment_index=True),
            lambda row: row["target"].update(context=[]),
            lambda row: row["source"]["observation"]["process"].update(pid=False),
            lambda row: row["source"].update(dispatch_evidence={"sha256": "bad", "report": None}),
        ):
            with self.subTest(mutation=mutate):
                row = descriptor()
                mutate(row)
                with self.assertRaises(StateError):
                    self.record(coverage=[row])
                self.assertFalse((self.sidecar / "index.json").exists())

    def test_dispatch_evidence_receipt_is_saved_without_rehashing_on_readback(self):
        covered = descriptor()
        covered["source"]["dispatch_evidence"] = {"sha256": "b" * 64, "report": retro.receipt(str(self.source))}
        self.record(coverage=[covered])
        self.source.unlink()
        self.assertEqual(retro.show(self.path)["record"]["coverage"], [covered])


class CadenceTest(unittest.TestCase):
    def setUp(self):
        self.index = retro.empty("/unused/retrospective-state.json")

    def test_exact_24_hours_and_equivalent_timezone_boundary(self):
        self.index["baseline_at"] = AT
        self.assertFalse(retro.cadence(self.index, "2026-09-02T11:59:59.999999Z", existing_work=True)["due"])
        due = retro.cadence(self.index, "2026-09-02T14:00:00+02:00", existing_work=True)
        self.assertTrue(due["due"])
        self.assertEqual(due["next_due_at"], "2026-09-02T12:00:00+00:00")

    def test_empty_team_and_existing_work_without_history_are_distinct(self):
        self.assertFalse(retro.cadence(self.index, AT)["due"])
        self.assertTrue(retro.cadence(self.index, AT, existing_work=True)["due"])
        self.assertIsNone(self.index["baseline_at"])

    def test_latest_completed_record_owns_cadence_even_when_appended_earlier(self):
        self.index["baseline_at"] = "2026-08-30T12:00:00Z"
        self.index["records"] = [{"completed_at": LATER}, {"completed_at": AT}]
        self.assertFalse(retro.cadence(self.index, "2026-09-02T12:59:59Z")["due"])
        self.assertTrue(retro.cadence(self.index, "2026-09-02T13:00:00Z")["due"])

    def test_future_baseline_or_completion_and_naive_clock_are_rejected(self):
        self.index["baseline_at"] = LATER
        with self.assertRaisesRegex(UsageError, "baseline"):
            retro.cadence(self.index, AT)
        self.index["baseline_at"] = None
        self.index["records"] = [{"completed_at": LATER}]
        with self.assertRaisesRegex(UsageError, "completion"):
            retro.cadence(self.index, AT)
        with self.assertRaisesRegex(UsageError, "timezone"):
            retro.cadence(self.index, "2026-09-01T12:00:00")


if __name__ == "__main__":
    unittest.main()
