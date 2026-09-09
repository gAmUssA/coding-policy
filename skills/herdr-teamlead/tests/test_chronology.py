"""Event ordering preserves audit identity and refuses uncertain chronology."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import unittest

from teamlead.chronology import assignment_after, latest_assignment
from teamlead.errors import UsageError


class ChronologyTests(unittest.TestCase):
    def row(self, at, **extra):
        return {"at": at, "agent": "worker", "task": "task", "role": "developer", "status": "applied", **extra}

    def test_event_time_and_timezone_choose_original_row_without_mutation(self):
        history = [self.row("2026-02-03T10:00:00Z"), self.row("2026-02-03T10:30:00+01:00")]
        original = copy.deepcopy(history)
        latest = latest_assignment(history, agent="worker")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest[0], 0)
        self.assertIs(latest[1], history[0])
        self.assertEqual(history, original)
        self.assertTrue(assignment_after(history, 0, 1))
        self.assertFalse(assignment_after(history, 1, 0))

    def test_filters_exclude_unrelated_unknown_chronology(self):
        history = [self.row("2026-02-03T10:00:00Z"), self.row(None, task="other", agent="other")]
        self.assertEqual(latest_assignment(history, task="task", agent="worker", role="developer", status="applied"), (0, history[0]))
        self.assertIsNone(latest_assignment(history, role="judge"))
        self.assertIsNone(latest_assignment([]))

    def test_missing_invalid_naive_or_tied_latest_chronology_refuses(self):
        for at in (None, "", "invalid", "2026-02-03T10:00:00", "2026-02-03T11:00:00+01:00"):
            history = [self.row("2026-02-03T10:00:00Z"), self.row(at)]
            with self.subTest(at=at), self.assertRaises(UsageError):
                latest_assignment(history)
        with self.assertRaisesRegex(UsageError, "tied"):
            assignment_after([self.row("2026-02-03T10:00:00Z")] * 2, 1, 0)

    def test_older_ties_do_not_obscure_a_proven_latest_event(self):
        history = [self.row("2026-02-03T09:00:00Z"), self.row("2026-02-03T09:00:00Z"), self.row("2026-02-03T10:00:00Z")]
        self.assertEqual(latest_assignment(history), (2, history[2]))

    def test_before_selects_event_predecessor_including_later_append(self):
        history = [self.row("2026-02-03T10:00:00Z", role="release"), self.row("2026-02-03T09:00:00Z"), self.row("2026-02-03T11:00:00Z")]
        self.assertEqual(latest_assignment(history, role="developer", before=0), (1, history[1]))
        history.append(self.row("2026-02-03T10:00:00Z"))
        with self.assertRaisesRegex(UsageError, "tied"):
            latest_assignment(history, role="developer", before=0)


if __name__ == "__main__":
    unittest.main()
