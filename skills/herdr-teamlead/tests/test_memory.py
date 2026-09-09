"""Curated evidence and handoff knowledge survive restarts without becoming authority."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import argparse
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import memory
from teamlead.errors import StateError, UsageError
from teamlead.state import state_lock


AT = "2026-09-01T12:00:00+00:00"
LATER = "2026-09-01T13:00:00+00:00"
EXPIRY = "2026-09-02T12:00:00+00:00"


class MemoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-memory-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state.json"
        self.source = self.root / "retrospective.md"
        self.source.write_text("The tester repeated setup when the brief omitted the test command.\n", encoding="utf-8")
        self.lesson = {
            "id": "lesson-1", "lesson_id": "test-command", "supersedes": None,
            "status": "active", "lesson": "Include the exact test command in each bug-fix brief.",
            "scopes": ["project:owner/repo", "role:developer"], "sources": [str(self.source)],
            "last_verified_at": AT, "expires_at": EXPIRY,
            "revalidate_when": "The project test entrypoint changes.",
            "reason": "A retrospective found avoidable setup repetition.",
        }
        self.capture = {
            "id": "stow-1", "capture": "The user expects a review before publication; this was only in conversation.",
            "unresolved_work": ["Ask for review after the artifact is ready; see attention item publish-review."],
            "gaps": [], "required_reads": [str(self.source)],
        }

    def record(self, data=None, at=AT):
        return memory.record(self.state, self.lesson if data is None else data, at)

    def stow(self, data=None, at=AT):
        return memory.stow(self.state, self.capture if data is None else data, at)

    def revision(self, **changes):
        return {**self.lesson, "id": "lesson-2", "supersedes": "lesson-1", **changes}

    def raw(self):
        return json.loads(memory.location(self.state).read_text(encoding="utf-8"))

    def write_raw(self, document):
        memory.location(self.state).parent.mkdir(exist_ok=True)
        memory.location(self.state).write_text(json.dumps(document), encoding="utf-8")

    def test_missing_readers_create_no_files(self):
        before = sorted(self.root.rglob("*"))
        with patch.object(memory, "state_lock", side_effect=AssertionError("reader must not lock")):
            self.assertEqual(memory.list_lessons(self.state, AT)["lessons"], [])
            with self.assertRaisesRegex(UsageError, "No saved memory"):
                memory.show(self.state, AT)
        self.assertEqual(sorted(self.root.rglob("*")), before)

    def test_record_and_read_preserve_declared_verification(self):
        result = self.record()
        self.assertFalse(result["replayed"])
        before = memory.location(self.state).read_bytes()
        with patch.object(memory, "state_lock", side_effect=AssertionError("reader must not lock")):
            row = memory.list_lessons(self.state, LATER)["lessons"][0]
        self.assertEqual(row["last_verified_at"], AT)
        self.assertTrue(row["use_requires_live_verification"])
        self.assertEqual(row["sources"][0]["observation"], "same_bytes")
        self.assertEqual(memory.location(self.state).read_bytes(), before)
        self.assertFalse(self.state.exists())

    def test_fresh_process_reads_saved_lesson_without_dispatch_state(self):
        self.record()
        result = subprocess.run(
            [_sys.executable, "-c", "import json,sys; from teamlead.memory import list_lessons; print(json.dumps(list_lessons(sys.argv[1], sys.argv[2])))", str(self.state), LATER],
            cwd=_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["lessons"][0]["id"], "lesson-1")

    def test_replay_keeps_original_receipt_after_source_changes(self):
        self.record()
        original = self.raw()
        self.source.write_text("Updated evidence.\n", encoding="utf-8")
        result = self.record(at=LATER)
        self.assertTrue(result["replayed"])
        self.assertEqual(result["record"]["sources"][0]["observation"], "changed")
        self.assertEqual(self.raw(), original)

    def test_conflicting_replay_refuses_to_replace_history(self):
        self.record()
        before = self.raw()
        with self.assertRaisesRegex(UsageError, "different content"):
            self.record({**self.lesson, "lesson": "A conflicting lesson."}, at=LATER)
        self.assertEqual(self.raw(), before)

    def test_archive_and_reactivate_retain_all_prior_revisions(self):
        self.record()
        archived = self.revision(status="archived", reason="The affected project was retired.")
        self.record(archived, at=LATER)
        self.assertEqual(memory.list_lessons(self.state, LATER)["lessons"], [])
        self.assertEqual(memory.list_lessons(self.state, LATER, include_archived=True)["lessons"][0]["status"], "archived")
        restored = self.revision(id="lesson-3", supersedes="lesson-2", reason="Project work resumed.")
        self.record(restored, at=LATER)
        result = memory.show(self.state, LATER, "lesson-3")
        self.assertEqual([row["id"] for row in result["history"]], ["lesson-1", "lesson-2", "lesson-3"])
        self.assertEqual(result["history"][1]["status"], "archived")
        self.assertEqual(memory.list_lessons(self.state, LATER)["lessons"][0]["status"], "active")

    def test_stale_revision_is_rejected_without_lost_update(self):
        self.record()
        self.record(self.revision(), at=LATER)
        with self.assertRaisesRegex(UsageError, "supersede the current"):
            self.record(self.revision(id="conflicting-revision"), at=LATER)
        self.assertEqual(len(self.raw()["records"]), 2)

    def test_cannot_archive_a_nonexistent_lesson(self):
        with self.assertRaisesRegex(UsageError, "before archiving"):
            self.record({**self.lesson, "status": "archived"})
        self.assertFalse(memory.location(self.state).exists())

    def test_scope_filter_includes_global_lessons_and_retains_expired_ones(self):
        self.record()
        self.record({**self.lesson, "id": "global-1", "lesson_id": "global", "scopes": ["*"]})
        self.assertEqual(len(memory.list_lessons(self.state, AT, scopes=["role:developer"])["lessons"]), 2)
        other = memory.list_lessons(self.state, EXPIRY, scopes=["project:another/repo"])["lessons"]
        self.assertEqual([row["id"] for row in other], ["global-1"])
        self.assertTrue(other[0]["expired"])
        self.assertEqual(len(self.raw()["records"]), 2)

    def test_expiry_is_inclusive_at_checkpoint(self):
        self.record()
        self.assertFalse(memory.list_lessons(self.state, AT)["lessons"][0]["expired"])
        self.assertTrue(memory.list_lessons(self.state, EXPIRY)["lessons"][0]["expired"])

    def test_missing_source_keeps_lesson_but_prevents_freshness_claim(self):
        self.record()
        self.source.unlink()
        row = memory.list_lessons(self.state, LATER)["lessons"][0]
        self.assertEqual(row["sources"][0]["observation"], "unavailable")
        self.assertTrue(row["use_requires_live_verification"])

    def test_https_sources_are_not_fetched_offline(self):
        self.record({**self.lesson, "sources": ["https://example.org/issues/1"]})
        with patch.object(memory, "_receipt", side_effect=AssertionError("URL read must remain offline")):
            row = memory.list_lessons(self.state, LATER)["lessons"][0]
        self.assertEqual(row["sources"][0]["observation"], "not_checked_offline")

    def test_explicit_reverification_creates_new_receipt_not_mutated_history(self):
        self.record()
        first = self.raw()["records"][0]
        self.source.write_text("Retrospective independently confirmed the repeated setup cause.\n", encoding="utf-8")
        self.record(self.revision(last_verified_at=LATER), at=LATER)
        rows = self.raw()["records"]
        self.assertEqual(rows[0], first)
        self.assertNotEqual(rows[0]["sources"], rows[1]["sources"])
        self.assertEqual(rows[1]["last_verified_at"], LATER)

    def test_stow_persists_capture_ordered_reads_and_unresolved_work(self):
        attention = self.root / "attention.json"
        attention.write_text('{"question":"Review before publication"}\n', encoding="utf-8")
        result = self.stow({**self.capture, "required_reads": [str(attention), str(self.source)]})
        self.assertTrue(result["record"]["reset_ready"])
        self.assertEqual(result["record"]["readiness_scope"], "local_capture_only")
        self.assertEqual(result["record"]["unresolved_work"], self.capture["unresolved_work"])
        recovered = memory.show(self.state, LATER)["record"]
        self.assertEqual(recovered["capture"], self.capture["capture"])
        self.assertEqual([row["path"] for row in recovered["required_reads"]], [str(attention), str(self.source)])

    def test_stow_with_gaps_never_claims_reset_readiness(self):
        result = self.stow({**self.capture, "gaps": ["The current task ledger is unavailable; restore it before replacing the lead."]})
        self.assertFalse(result["record"]["reset_ready"])
        self.assertFalse(memory.show(self.state, LATER)["record"]["reset_ready"])

    def test_stow_source_change_or_loss_invalidates_saved_readiness(self):
        self.stow()
        self.source.write_text("New unresolved work arrived.\n", encoding="utf-8")
        changed = memory.show(self.state, LATER)["record"]
        self.assertFalse(changed["reset_ready"])
        self.assertEqual(changed["required_reads"][0]["observation"], "changed")
        self.source.unlink()
        missing = memory.show(self.state, LATER)["record"]
        self.assertFalse(missing["reset_ready"])
        self.assertEqual(missing["capture"], self.capture["capture"])
        self.assertEqual(missing["required_reads"][0]["observation"], "unavailable")

    def test_stow_replay_retains_capture_even_when_original_sources_disappear(self):
        self.stow()
        before = self.raw()
        self.source.unlink()
        result = self.stow(at=LATER)
        self.assertTrue(result["replayed"])
        self.assertFalse(result["record"]["reset_ready"])
        self.assertEqual(self.raw(), before)

    def test_latest_stow_does_not_select_newer_lesson(self):
        self.stow()
        self.record(at=LATER)
        self.assertEqual(memory.show(self.state, LATER)["record"]["id"], "stow-1")

    def test_state_and_directory_aliases_share_one_history(self):
        self.state.write_text("opaque dispatch state\n", encoding="utf-8")
        alias = self.root / "state-alias.json"
        alias.symlink_to(self.state)
        directory_alias = self.root / "alias"
        directory_alias.symlink_to(self.root, target_is_directory=True)
        memory.record(alias, self.lesson, AT)
        self.assertEqual(memory.location(directory_alias / "state.json"), memory.location(self.state))
        self.assertEqual(memory.list_lessons(self.state, AT)["lessons"][0]["id"], "lesson-1")
        self.assertEqual(self.state.read_text(encoding="utf-8"), "opaque dispatch state\n")

    def test_atomic_failure_preserves_previous_history_and_retry_succeeds(self):
        self.record()
        before = memory.location(self.state).read_bytes()
        with patch("teamlead.state.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(StateError, "Cannot write"):
                self.record(self.revision(), at=LATER)
        self.assertEqual(memory.location(self.state).read_bytes(), before)
        self.record(self.revision(), at=LATER)
        self.assertEqual(len(self.raw()["records"]), 2)

    def test_live_owner_lock_refuses_concurrent_mutation(self):
        self.record()
        with state_lock(memory.location(self.state)):
            with self.assertRaisesRegex(StateError, "Another teamlead command"):
                self.record(self.revision(), at=LATER)
            self.assertEqual(len(memory.list_lessons(self.state, AT)["lessons"]), 1)
        self.assertEqual(len(self.raw()["records"]), 1)

    def test_dangling_index_link_is_preserved_until_history_is_restored(self):
        self.record()
        index = memory.location(self.state)
        original = index.read_bytes()
        missing = self.root / "unavailable-history.json"
        index.unlink()
        index.symlink_to(missing)
        for operation in (lambda: memory.list_lessons(self.state, LATER), lambda: memory.show(self.state, LATER),
                          lambda: self.record(self.revision(), at=LATER), lambda: self.stow(at=LATER)):
            with self.assertRaisesRegex(StateError, "dangling link.*restore"):
                operation()
            self.assertTrue(index.is_symlink())
            self.assertEqual(index.readlink(), missing)
            self.assertFalse(missing.exists())
        missing.write_bytes(original)
        self.assertEqual(memory.list_lessons(self.state, LATER)["lessons"][0]["id"], "lesson-1")
        self.assertTrue(index.is_symlink())
        self.assertEqual(missing.read_bytes(), original)

    def test_cli_reports_dangling_index_without_replacing_it(self):
        index = memory.location(self.state)
        index.parent.mkdir()
        missing = self.root / "missing-index.json"
        index.symlink_to(missing)
        draft = self.root / "input.json"
        draft.write_text(json.dumps(self.lesson), encoding="utf-8")
        for command in (["memory-list"], ["memory-record", "--record", str(draft)]):
            result = subprocess.run(
                [_sys.executable, "-m", "teamlead", "--state", str(self.state), *command, "--now", LATER],
                env={**_os.environ, "PYTHONPATH": _ROOT, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            diagnostic = json.loads(result.stderr)
            self.assertEqual(diagnostic["error"], "state_error")
            self.assertIn("restore", diagnostic["message"])
            self.assertTrue(index.is_symlink())
            self.assertEqual(index.readlink(), missing)
            self.assertFalse(missing.exists())

    def test_unknown_schema_preserves_bytes_for_reader_and_writer(self):
        self.record()
        document = self.raw()
        document["schema_version"] = 2
        self.write_raw(document)
        before = memory.location(self.state).read_bytes()
        for operation in (lambda: memory.list_lessons(self.state, LATER), lambda: self.stow(at=LATER)):
            with self.assertRaisesRegex(StateError, "unsupported schema"):
                operation()
        self.assertEqual(memory.location(self.state).read_bytes(), before)

    def test_corrupt_records_fail_visibly_without_overwriting(self):
        self.record()
        original = self.raw()
        mutations = [
            lambda row: row.update(schema_version=True),
            lambda row: row.update(status=[]),
            lambda row: row.update(recorded_at="not-a-date"),
            lambda row: row.update(supersedes="missing"),
            lambda row: row.update(sources=[{"schema_version": 1, "kind": "url", "url": "https://["}]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(original)
                mutation(document["records"][0])
                self.write_raw(document)
                before = memory.location(self.state).read_bytes()
                with self.assertRaises(StateError):
                    memory.list_lessons(self.state, LATER)
                self.assertEqual(memory.location(self.state).read_bytes(), before)

    def test_invalid_input_is_not_recorded(self):
        bad_values = [
            {"id": "../escape"}, {"last_verified_at": LATER}, {"expires_at": AT},
            {"scopes": []}, {"sources": []}, {"sources": ["relative.md"]},
            {"sources": ["https://name:password@example.org"]}, {"sources": ["https://["]},
            {"revalidate_when": " "}, {"status": []}, {"unrecognized": True},
        ]
        for changed in bad_values:
            with self.subTest(changed=changed):
                with self.assertRaises(UsageError):
                    self.record({**self.lesson, **changed})
                self.assertFalse(memory.location(self.state).exists())

    def test_stow_requires_explicit_gaps_and_durable_reads(self):
        for changes in ({"required_reads": []}, {"required_reads": [str(self.root / "missing")]}, {"gaps": None}, {"capture": " "}):
            with self.subTest(changes=changes):
                with self.assertRaises(UsageError):
                    self.stow({**self.capture, **changes})
                self.assertFalse(memory.location(self.state).exists())

    def test_self_receipt_is_rejected(self):
        self.record()
        with self.assertRaisesRegex(UsageError, "own changing index"):
            self.stow({**self.capture, "required_reads": [str(memory.location(self.state))]}, at=LATER)

    def test_earlier_clock_cannot_rewrite_or_misrepresent_later_history(self):
        self.record(at=LATER)
        for operation in (lambda: self.stow(at=AT), lambda: self.record(at=AT), lambda: memory.list_lessons(self.state, AT)):
            with self.assertRaisesRegex(UsageError, "precedes"):
                operation()
        self.assertEqual(len(self.raw()["records"]), 1)

    def test_command_adapter_runs_without_config_or_herdr(self):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        memory.register_commands(sub, argparse.ArgumentParser(add_help=False))
        draft = self.root / "input.json"
        draft.write_text(json.dumps(self.lesson), encoding="utf-8")
        args = parser.parse_args(["memory-record", "--record", str(draft)])
        self.assertFalse(memory.run_command(args, self.state, AT)["replayed"])
        args = parser.parse_args(["memory-list", "--scope", "role:developer"])
        self.assertEqual(memory.run_command(args, self.state, LATER)["lessons"][0]["id"], "lesson-1")
        draft.write_text(json.dumps(self.capture), encoding="utf-8")
        args = parser.parse_args(["memory-stow", "--record", str(draft)])
        self.assertTrue(memory.run_command(args, self.state, LATER)["record"]["reset_ready"])
        args = parser.parse_args(["memory-show"])
        self.assertEqual(memory.run_command(args, self.state, LATER)["record"]["id"], "stow-1")


if __name__ == "__main__":
    unittest.main()
