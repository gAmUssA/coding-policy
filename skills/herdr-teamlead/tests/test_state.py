"""Tests for teamlead.state."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from teamlead.errors import StateError
from teamlead.state import (
    MAX_SNAPSHOTS,
    MIGRATIONS,
    STATE_SCHEMA_VERSION,
    UNVERSIONED,
    add_assignment,
    add_snapshot,
    default_state_path,
    empty_state,
    latest_snapshot,
    load_state,
    load_state_checked,
    role_counts,
    save_state,
)


class EmptyStateTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(
            empty_state(),
            {"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": [],
             "recovery": {"schema_version": 4, "tasks": {}, "checkpoints": [], "plans": [],
                          "dispatches": [], "context_permissions": [], "events": [],
                          "hand_clearances": [], "historical_attempts": [], "role_clearances": [], "delivery_recoveries": []}},
        )


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "sub" / "state.json"

    def test_missing_file_reads_as_fresh_state(self):
        self.assertEqual(load_state(self.path), empty_state())

    def test_save_creates_parent_directories_and_round_trips(self):
        state = empty_state()
        add_assignment(state, "2026-01-02T03:04:05+00:00", "developer", "grok")
        save_state(self.path, state)
        self.assertTrue(self.path.exists())
        self.assertEqual(load_state(self.path), state)

    def test_save_is_idempotent(self):
        state = empty_state()
        save_state(self.path, state)
        save_state(self.path, state)
        self.assertEqual(load_state(self.path), state)

    def test_save_leaves_no_temp_files_behind(self):
        save_state(self.path, empty_state())
        self.assertEqual(sorted(os.listdir(self.path.parent)), ["state.json"])

    def test_save_replaces_rather_than_appends(self):
        first = empty_state()
        add_assignment(first, "2026-01-02T03:04:05+00:00", "developer", "grok")
        save_state(self.path, first)
        save_state(self.path, empty_state())
        self.assertEqual(load_state(self.path)["assignments"], [])

    def test_file_ends_with_a_newline(self):
        save_state(self.path, empty_state())
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith("}\n"))


class MigrationTest(unittest.TestCase):
    """Older migrates and is rewritten; newer and corrupt start empty, untouched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "state.json"
        self.warnings = []

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def load(self):
        return load_state(self.path, warn=self.warnings.append)

    def on_disk(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    # -- older: migrate, then rewrite ---------------------------------------

    def test_an_unversioned_document_is_migrated_and_its_rows_stamped(self):
        self.write(
            {
                "snapshots": [],
                "assignments": [{"at": "2026-01-01T00:00:00+00:00", "role": "developer", "agent": "grok"}],
            }
        )
        state = self.load()
        self.assertEqual(state["schema_version"], STATE_SCHEMA_VERSION)
        self.assertEqual(state["assignments"][0]["schema_version"], STATE_SCHEMA_VERSION)
        self.assertEqual(state["assignments"][0]["agent"], "grok")

    def test_the_migrated_document_is_rewritten_to_disk(self):
        self.write(
            {
                "snapshots": [],
                "assignments": [{"at": "2026-01-01T00:00:00+00:00", "role": "tester", "agent": "codex"}],
            }
        )
        self.load()
        stored = self.on_disk()
        self.assertEqual(stored["schema_version"], STATE_SCHEMA_VERSION)
        self.assertEqual(stored["assignments"][0]["schema_version"], STATE_SCHEMA_VERSION)

    def test_an_unversioned_row_inside_a_current_document_is_stamped(self):
        self.write(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "snapshots": [],
                "assignments": [{"at": "2026-01-01T00:00:00+00:00", "role": "reviewer", "agent": "claude"}],
            }
        )
        self.load()
        self.assertEqual(self.on_disk()["assignments"][0]["schema_version"], STATE_SCHEMA_VERSION)

    def test_migrating_preserves_the_ledger_contents(self):
        rows = [
            {"at": "2026-01-01T00:00:00+00:00", "role": "developer", "agent": "grok"},
            {"at": "2026-01-02T00:00:00+00:00", "role": "developer", "agent": "claude"},
        ]
        self.write({"snapshots": [], "assignments": rows})
        state = self.load()
        self.assertEqual([r["agent"] for r in state["assignments"]], ["grok", "claude"])
        self.assertEqual(role_counts(state), {"developer": {"grok": 1, "claude": 1}})

    def test_a_current_document_is_not_rewritten(self):
        state = empty_state()
        add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
        save_state(self.path, state)
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.load(), state)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.warnings, [])

    def test_the_table_is_keyed_by_the_version_being_upgraded_from(self):
        # Shape check: adding 1->2 later must be one more entry, not a rewrite.
        self.assertIn(UNVERSIONED, MIGRATIONS)
        produced, upgrade = MIGRATIONS[UNVERSIONED]
        self.assertEqual(produced, 1)
        self.assertTrue(callable(upgrade))

    # -- newer: no usable prior state, file untouched ------------------------

    def test_v2_context_migration_preserves_counts_and_snapshot_version(self):
        snapshot = {"schema_version": 2, "agents": {"grok": {"window_group": "pool"}}}
        row = {"schema_version": 2, "at": "2026-01-01T00:00:00+00:00",
               "role": "developer", "agent": "grok", "status": "applied"}
        for version in (2, STATE_SCHEMA_VERSION):
            with self.subTest(document_version=version):
                self.write({"schema_version": version, "snapshots": [snapshot],
                            "assignments": [row]})
                migrated = self.load()
                self.assertEqual(migrated["schema_version"], STATE_SCHEMA_VERSION)
                self.assertEqual(migrated["snapshots"], [{"schema_version": 3, "agents": {"grok": {"window_group": "pool", "tier_billing": {}}}}])
                self.assertEqual(migrated["assignments"], [dict(
                    row, schema_version=STATE_SCHEMA_VERSION, cleared=None,
                    clear_reason="unknown", task=None, fix_round=None, context_session=None, tier=None,
                )])
                self.assertEqual(role_counts(migrated), {"developer": {"grok": 1}})
                self.assertEqual(self.on_disk(), migrated)
                self.assertEqual(self.load(), migrated)

    def test_context_modes_round_trip_without_conflating_their_evidence(self):
        state = empty_state()
        for cleared, reason in ((True, "automatic"), (False, "hand"),
                                (False, "retained"), (None, "unknown")):
            add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok",
                           cleared=cleared, clear_reason=reason, task="repo#322", fix_round=1)
        save_state(self.path, state)
        self.assertEqual(self.load(), state)

    def test_malformed_context_evidence_is_preserved_but_not_used(self):
        for fields in (
            {"cleared": False, "clear_reason": "automatic"},
            {"cleared": True, "clear_reason": "retained"},
            {"cleared": None, "clear_reason": "hand"},
            {"clear_reason": []}, {"task": " "}, {"fix_round": True},
            {"fix_round": 0}, {"fix_round": 6}, {"fix_round": "1"},
            {"context_session": {}}, {"context_session": "session"},
        ):
            with self.subTest(fields=fields):
                state = empty_state()
                add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
                state["assignments"][0].update(fields)
                self.write(state)
                before = self.path.read_bytes()
                self.assertEqual(self.load(), empty_state())
                self.assertEqual(self.path.read_bytes(), before)
                self.assertTrue(self.warnings)

    def test_a_newer_document_starts_empty_and_is_left_untouched(self):
        payload = {"schema_version": STATE_SCHEMA_VERSION + 1, "snapshots": [], "assignments": []}
        self.write(payload)
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.load(), empty_state())
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertTrue(any("schema_version" in w for w in self.warnings))

    def test_a_newer_row_makes_the_whole_document_unusable_rather_than_dropping_it(self):
        # Silently dropping the row would lose it on the next write.
        self.write(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "snapshots": [],
                "assignments": [
                    {
                        "schema_version": STATE_SCHEMA_VERSION + 1,
                        "at": "2026-01-01T00:00:00+00:00",
                        "role": "developer",
                        "agent": "grok",
                    }
                ],
            }
        )
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.load(), empty_state())
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_newer_snapshot_is_the_same_lagging_reader_case(self):
        self.write(
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "snapshots": [{"schema_version": STATE_SCHEMA_VERSION + 1, "agents": {}}],
                "assignments": [],
            }
        )
        self.assertEqual(self.load(), empty_state())

    # -- corrupt: no usable prior state, never an instruction to discard -----

    def test_malformed_json_starts_empty_with_a_warning(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.load(), empty_state())
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("not valid JSON", self.warnings[0])

    def test_a_corrupt_file_is_never_told_to_be_discarded(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.load()
        self.assertNotIn(".bak", self.warnings[0])
        self.assertNotIn("mv ", self.warnings[0])

    def test_a_corrupt_file_is_left_on_disk(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.load()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_a_non_object_document_starts_empty(self):
        self.path.write_text("[]", encoding="utf-8")
        self.assertEqual(self.load(), empty_state())

    def test_a_non_array_snapshots_field_starts_empty(self):
        self.write({"schema_version": STATE_SCHEMA_VERSION, "snapshots": {}, "assignments": []})
        self.assertEqual(self.load(), empty_state())

    def test_a_non_object_assignment_row_starts_empty(self):
        self.write({"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": ["nope"]})
        self.assertEqual(self.load(), empty_state())

    def test_a_non_integer_version_starts_empty(self):
        self.write({"schema_version": "1", "snapshots": [], "assignments": []})
        self.assertEqual(self.load(), empty_state())

    # -- environment faults still raise -------------------------------------

    def test_a_directory_in_the_way_is_still_a_tool_failure(self):
        directory = Path(self.tmp) / "dir-state"
        directory.mkdir()
        with self.assertRaises(StateError):
            load_state(directory, warn=self.warnings.append)


class AssignmentRecordTest(unittest.TestCase):
    def test_every_written_row_carries_its_own_version(self):
        state = empty_state()
        add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
        self.assertEqual(
            state["assignments"][0],
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "at": "2026-01-01T00:00:00+00:00",
                "role": "developer",
                "agent": "grok",
                "status": "applied",
                "cleared": None,
                "clear_reason": "unknown",
                "task": None,
                "fix_round": None,
                "context_session": None,
                "tier": None,
            },
        )


class SnapshotRingTest(unittest.TestCase):
    def test_keeps_only_the_last_twenty(self):
        state = empty_state()
        for index in range(MAX_SNAPSHOTS + 5):
            add_snapshot(state, {"measured_at": "2026-01-02T00:00:{:02d}+00:00".format(index)})
        self.assertEqual(len(state["snapshots"]), MAX_SNAPSHOTS)
        self.assertEqual(state["snapshots"][0]["measured_at"], "2026-01-02T00:00:05+00:00")
        self.assertEqual(state["snapshots"][-1]["measured_at"], "2026-01-02T00:00:24+00:00")

    def test_latest_snapshot_is_the_newest(self):
        state = empty_state()
        self.assertIsNone(latest_snapshot(state))
        add_snapshot(state, {"measured_at": "a"})
        add_snapshot(state, {"measured_at": "b"})
        self.assertEqual(latest_snapshot(state), {"measured_at": "b"})


class RoleCountsTest(unittest.TestCase):
    def test_counts_per_role_and_agent(self):
        state = empty_state()
        add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
        add_assignment(state, "2026-01-02T00:00:00+00:00", "developer", "grok")
        add_assignment(state, "2026-01-02T00:00:00+00:00", "tester", "claude")
        self.assertEqual(
            role_counts(state), {"developer": {"grok": 2}, "tester": {"claude": 1}}
        )

    def test_empty_ledger_yields_empty_counts(self):
        self.assertEqual(role_counts(empty_state()), {})

    def test_malformed_ledger_rows_are_ignored(self):
        state = empty_state()
        state["assignments"] = [{"at": "x"}, {"role": "developer", "agent": "grok"}]
        self.assertEqual(role_counts(state), {"developer": {"grok": 1}})


class DefaultPathTest(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("XDG_STATE_HOME")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.original is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self.original

    def test_honours_xdg_state_home(self):
        os.environ["XDG_STATE_HOME"] = "/somewhere/state"
        self.assertEqual(default_state_path(), Path("/somewhere/state/teamlead/state.json"))

    def test_falls_back_to_local_state(self):
        os.environ.pop("XDG_STATE_HOME", None)
        self.assertEqual(
            default_state_path(), Path.home() / ".local" / "state" / "teamlead" / "state.json"
        )




class AssignmentStatusTest(unittest.TestCase):
    """A hand-off that never started is recorded, but is not experience."""

    def test_a_row_defaults_to_applied(self):
        state = empty_state()
        add_assignment(state, "2026-01-01T00:00:00+00:00", "developer", "grok")
        self.assertEqual(state["assignments"][0]["status"], "applied")

    def test_a_not_started_row_is_still_written(self):
        # The ledger is what teamlead did, and a round that went out and died
        # is exactly what is worth looking up afterwards.
        state = empty_state()
        add_assignment(
            state,
            "2026-01-01T00:00:00+00:00",
            "developer",
            "grok",
            status="sent_but_not_started",
        )
        self.assertEqual(len(state["assignments"]), 1)
        self.assertEqual(state["assignments"][0]["status"], "sent_but_not_started")

    def test_a_not_started_row_does_not_count_toward_role_history(self):
        state = empty_state()
        add_assignment(state, "a", "developer", "grok", status="sent_but_not_started")
        self.assertEqual(role_counts(state), {})

    def test_applied_rows_count(self):
        state = empty_state()
        add_assignment(state, "a", "developer", "grok")
        add_assignment(state, "b", "developer", "grok")
        self.assertEqual(role_counts(state), {"developer": {"grok": 2}})

    def test_a_mixed_ledger_counts_only_the_started_rounds(self):
        state = empty_state()
        add_assignment(state, "a", "developer", "grok")
        add_assignment(state, "b", "developer", "grok", status="sent_but_not_started")
        add_assignment(state, "c", "developer", "grok")
        self.assertEqual(role_counts(state), {"developer": {"grok": 2}})

    def test_a_row_migrated_from_before_the_field_still_counts(self):
        # Version 1 rows were real hand-offs; `unknown` says teamlead cannot
        # prove the outcome, not that it should be forgotten.
        state = empty_state()
        state["assignments"] = [
            {"schema_version": 2, "at": "a", "role": "developer", "agent": "grok",
             "status": "unknown"}
        ]
        self.assertEqual(role_counts(state), {"developer": {"grok": 1}})

    def test_a_row_with_no_status_at_all_still_counts(self):
        state = empty_state()
        state["assignments"] = [{"at": "a", "role": "developer", "agent": "grok"}]
        self.assertEqual(role_counts(state), {"developer": {"grok": 1}})

    def test_a_non_object_row_is_skipped_rather_than_crashing(self):
        state = empty_state()
        state["assignments"] = ["nonsense", {"role": "developer", "agent": "grok"}]
        self.assertEqual(role_counts(state), {"developer": {"grok": 1}})


class StatusMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-status-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "state.json"
        self.warnings = []

    def test_a_version_one_ledger_is_upgraded_and_stamped_unknown(self):
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [],
                    "assignments": [
                        {"schema_version": 1, "at": "a", "role": "developer",
                         "agent": "grok"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = load_state(self.path, warn=self.warnings.append)
        self.assertEqual(state["schema_version"], STATE_SCHEMA_VERSION)
        row = state["assignments"][0]
        self.assertEqual(row["schema_version"], STATE_SCHEMA_VERSION)
        self.assertEqual(row["status"], "unknown")

    def test_the_upgraded_ledger_keeps_its_role_history(self):
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [],
                    "assignments": [
                        {"schema_version": 1, "at": "a", "role": "developer",
                         "agent": "grok"},
                        {"schema_version": 1, "at": "b", "role": "developer",
                         "agent": "grok"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = load_state(self.path, warn=self.warnings.append)
        self.assertEqual(role_counts(state), {"developer": {"grok": 2}})

    def test_an_unversioned_ledger_walks_the_whole_chain(self):
        self.path.write_text(
            json.dumps(
                {"snapshots": [], "assignments": [
                    {"at": "a", "role": "developer", "agent": "grok"}
                ]}
            ),
            encoding="utf-8",
        )
        state = load_state(self.path, warn=self.warnings.append)
        self.assertEqual(state["schema_version"], STATE_SCHEMA_VERSION)
        self.assertEqual(state["assignments"][0]["status"], "unknown")


class UsableFlagTest(unittest.TestCase):
    """A caller that will WRITE has to know the file could not be read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-state-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = Path(self.tmp) / "state.json"
        self.warnings = []

    def load(self):
        return load_state_checked(self.path, warn=self.warnings.append)

    def test_a_missing_file_is_usable(self):
        # Nothing to lose: an empty document is the honest starting point.
        state, usable = self.load()
        self.assertEqual(state, empty_state())
        self.assertTrue(usable)

    def test_a_good_file_is_usable(self):
        save_state(self.path, empty_state())
        _state, usable = self.load()
        self.assertTrue(usable)

    def test_a_corrupt_file_is_not_usable(self):
        self.path.write_text("{broken", encoding="utf-8")
        state, usable = self.load()
        self.assertEqual(state, empty_state())
        self.assertFalse(usable)

    def test_a_newer_file_is_not_usable(self):
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": STATE_SCHEMA_VERSION + 1,
                    "snapshots": [],
                    "assignments": [],
                }
            ),
            encoding="utf-8",
        )
        _state, usable = self.load()
        self.assertFalse(usable)

    def test_load_state_still_returns_the_document_alone(self):
        save_state(self.path, empty_state())
        self.assertEqual(load_state(self.path), empty_state())


class SnapshotMigrationTest(unittest.TestCase):
    """A version-1 snapshot is upgraded and rewritten, not read as-is.

    This module owns the file snapshots live in, so it owns their migration
    (rules/stateful-artifacts.md Migration Policy). The safe value for a
    window a v1 measurement never observed is "a window of its own": guessing
    a shared group would invent a link and could halt a judge round on another
    worker's headroom.
    """

    def _state_with_v1_snapshot(self):
        return {
            "schema_version": 2,
            "snapshots": [
                {
                    "schema_version": 1,
                    "measured_at": "2026-02-03T10:00:00+00:00",
                    "agents": {"claude": {"headroom_pct": 90}},
                }
            ],
            "assignments": [],
        }

    def test_a_v1_snapshot_is_stamped_and_given_an_empty_window_group(self):
        path = Path(self.tmp) / "state.json"
        path.write_text(json.dumps(self._state_with_v1_snapshot()), encoding="utf-8")
        state = load_state(path, warn=lambda message: None)
        snapshot = state["snapshots"][0]
        self.assertEqual(snapshot["schema_version"], 3)
        self.assertEqual(snapshot["agents"]["claude"]["window_group"], "")

    def test_the_upgrade_is_written_back(self):
        path = Path(self.tmp) / "state.json"
        path.write_text(json.dumps(self._state_with_v1_snapshot()), encoding="utf-8")
        load_state(path, warn=lambda message: None)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["snapshots"][0]["schema_version"], 3)
        self.assertEqual(
            on_disk["snapshots"][0]["agents"]["claude"]["window_group"], ""
        )

    def test_a_declared_group_survives_the_migration_untouched(self):
        payload = self._state_with_v1_snapshot()
        payload["snapshots"][0]["agents"]["claude"]["window_group"] = "pool"
        path = Path(self.tmp) / "state.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        state = load_state(path, warn=lambda message: None)
        self.assertEqual(
            state["snapshots"][0]["agents"]["claude"]["window_group"], "pool"
        )

    def test_a_snapshot_from_a_newer_build_is_no_usable_prior_state(self):
        payload = self._state_with_v1_snapshot()
        payload["snapshots"][0]["schema_version"] = 99
        path = Path(self.tmp) / "state.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        _state, usable = load_state_checked(path, warn=lambda message: None)
        self.assertFalse(usable)

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
