"""Tester-5 acceptance tests for teamlead.parsers.parse_claude_usage.

The contract: every rate-limit window the pane shows parses with its own
percentage, or the read raises ParseError. A read that succeeds with a window
missing overstates headroom for every seat that window binds.

A non-window section can sit between two windows: the promo line in the
captured stacked dialog, or a credit section. A viewport that ends on that
section, with `↓` painted on its last row, looks the same as the footer
below the last window. Only the footer proves the windows have ended.

Every fixture is an inline string built in the test. No clock, no
randomness, no files on disk.
"""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so `teamlead` would not resolve. Under
# `-m unittest` from the root the insert is a no-op.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import unittest

from teamlead.errors import ParseError
from teamlead.parsers import headroom_pct, parse_claude_usage

UP = "↑"
DOWN = "↓"
TABS = "   Settings  Status   Config   Usage   Stats"
STATS = [
    "",
    "   Session",
    "",
    "   Total cost:            $7.70",
    "   Total duration (API):  16m 28s",
    "",
]
FOOTER = [
    "",
    "   What's contributing to your limits usage?",
    "   Approximate · sessions on this machine only",
    "",
    "   Last 24h · shares overlap",
]

# Stacked layout, 62 columns or more. The promo row is the one the captured
# OLD_STACKED_WITH_PROMO dialog shows between the account week and Fable.
S_SESSION = [
    "   Current session",
    "   ████                                               8% used",
    "   Resets 12:49am (Europe/Oslo)",
    "",
]
S_ALL_MODELS = [
    "   Current week (all models)",
    "   █                                                  2% used",
    "   Resets Sep 5 at 11:59pm (Europe/Oslo)",
]
PROMO = ["   +50% weekly limits promo through Sep 13 · clau.de/cc-50-promo"]
S_FABLE = [
    "",
    "   Current week (Fable)",
    "   ██████████████████████████████████████████████   100% used",
    "   Resets Sep 5 at 11:59pm (Europe/Oslo)",
]
S_EXPECTED = {
    "Current session": 8.0,
    "Current week (all models)": 2.0,
    "Current week (Fable)": 100.0,
}

# Inline layout, below 62 columns.
I_SESSION = [
    "   Current session · Resets 5pm (America/New_York)",
    "   ███▏",
    "",
    "   6% used",
    "",
]
I_CREDIT = [
    "   Usage credits · $5.00 / $50.00 spent · Resets December 1",
    "   ██",
    "",
    "   10% used",
    "",
]
I_ALL_MODELS = [
    "   Current week (all models) · Resets Nov 2 at 10am",
    "   (America/New_York)",
    "   █████████████████████▏",
    "",
    "   40% used",
    "",
]
I_FABLE = [
    "   Current week (Fable) · Resets Nov 2 at 10am",
    "   (America/New_York)",
    "   ███████████████████",
    "",
    "   36% used",
]
I_EXPECTED = {
    "Current session": 6.0,
    "Current week (all models)": 40.0,
    "Current week (Fable)": 36.0,
}

# Each dialog: body rows, expected windows, column the indicator is painted at.
DIALOGS = {
    "stacked": (STATS + S_SESSION + S_ALL_MODELS + S_FABLE + FOOTER, S_EXPECTED, 70),
    "stacked_promo_between": (
        STATS + S_SESSION + S_ALL_MODELS + PROMO + S_FABLE + FOOTER,
        S_EXPECTED,
        70,
    ),
    "inline_credit_between": (
        STATS + I_SESSION + I_CREDIT + I_ALL_MODELS + I_FABLE + FOOTER,
        I_EXPECTED,
        60,
    ),
}


def paint(row, arrow, column):
    """Paint a scroll indicator over the right edge of `row`."""
    return row.ljust(column) + arrow


def pane(rows):
    return "\n".join(rows) + "\n"


def viewport(body, first, stop, column):
    """Rows `first` to `stop` of `body` under the tab row, with indicators."""
    rows = list(body[first:stop])
    if first > 0:
        rows[0] = paint(rows[0], UP, column)
    if stop < len(body):
        rows[-1] = paint(rows[-1], DOWN, column)
    return pane([TABS] + rows)


def used_by_label(windows):
    return {label: window["used_pct"] for label, window in windows.items()}


class NoSilentDropCase(unittest.TestCase):
    def assert_parses_or_rejects(self, text, expected_used):
        """Either ParseError, or every expected window with its percentage."""
        try:
            windows = parse_claude_usage(text)["windows"]
        except ParseError:
            return None
        self.assertEqual(used_by_label(windows), expected_used)
        return windows


class NonWindowBetweenWindowsTest(NoSilentDropCase):
    """A down arrow after a non-window section that is not the footer."""

    def test_viewport_ending_on_the_promo_row_is_rejected(self):
        # The Fable week is 100% used and sits below the promo row.
        rows = [TABS] + S_SESSION + S_ALL_MODELS + [paint(PROMO[0], DOWN, 70)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_viewport_ending_on_a_blank_row_under_the_promo_is_rejected(self):
        rows = [TABS] + S_SESSION + S_ALL_MODELS + PROMO + [paint("", DOWN, 70)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_fable_headroom_is_never_read_past_a_promo_row(self):
        rows = [TABS] + S_SESSION + S_ALL_MODELS + [paint(PROMO[0], DOWN, 70)]
        try:
            windows = parse_claude_usage(pane(rows))["windows"]
        except ParseError:
            return
        self.assertEqual(headroom_pct(windows, "Fable"), 0.0)

    def test_viewport_ending_on_a_credit_percentage_between_windows_is_rejected(self):
        rows = [TABS] + I_SESSION + I_CREDIT[:3] + [paint(I_CREDIT[3], DOWN, 60)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_viewport_ending_on_a_credit_title_between_windows_is_rejected(self):
        rows = [TABS] + I_SESSION + [paint(I_CREDIT[0], DOWN, 60)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))


class ViewportSweepTest(NoSilentDropCase):
    """Every row-pair viewport of three dialogs, with painted indicators."""

    def test_no_viewport_drops_a_window(self):
        for name, (body, expected, column) in DIALOGS.items():
            for first in range(len(body)):
                for stop in range(first + 1, len(body) + 1):
                    with self.subTest(dialog=name, first=first, stop=stop):
                        self.assert_parses_or_rejects(
                            viewport(body, first, stop, column), expected
                        )

    def test_every_whole_dialog_parses_every_window(self):
        for name, (body, expected, column) in DIALOGS.items():
            with self.subTest(dialog=name):
                windows = parse_claude_usage(viewport(body, 0, len(body), column))["windows"]
                self.assertEqual(used_by_label(windows), expected)

    def test_whole_dialog_under_a_down_arrow_on_the_footer_parses(self):
        for name, (body, expected, column) in DIALOGS.items():
            with self.subTest(dialog=name):
                stop = body.index("   Approximate · sessions on this machine only") + 1
                windows = parse_claude_usage(viewport(body, 0, stop, column))["windows"]
                self.assertEqual(used_by_label(windows), expected)


class RoundFourRegressionTest(NoSilentDropCase):
    """What the arrow-on-row and duplicate-label edits could have broken."""

    def test_complete_stacked_dialog_with_promo_still_parses(self):
        rows = [TABS] + S_SESSION + S_ALL_MODELS + PROMO + S_FABLE
        windows = parse_claude_usage(pane(rows))["windows"]
        self.assertEqual(used_by_label(windows), S_EXPECTED)
        self.assertEqual(windows["Current week (Fable)"]["resets"], "Sep 5 at 11:59pm (Europe/Oslo)")

    def test_arrow_on_a_stacked_reset_row_of_the_last_window_is_rejected(self):
        rows = [TABS] + S_SESSION + S_ALL_MODELS[:2] + [paint(S_ALL_MODELS[2], DOWN, 70)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_arrow_on_the_first_stacked_header_is_rejected(self):
        rows = [TABS, paint(S_ALL_MODELS[0], UP, 70)] + S_ALL_MODELS[1:] + S_FABLE
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_repeated_stacked_label_is_rejected_and_named(self):
        rows = [TABS] + S_SESSION + S_ALL_MODELS + S_FABLE + FOOTER + S_SESSION
        with self.assertRaises(ParseError) as caught:
            parse_claude_usage(pane(rows))
        self.assertIn("'Current session'", caught.exception.message)
        self.assertEqual(caught.exception.details, {"kind": "claude"})

    def test_transcript_window_above_the_dialog_never_replaces_the_dialog_value(self):
        # Transcript rows above the dialog that quote a window: the read may
        # reject, but it must never return the transcript's 99%.
        rows = ["   Current session", "   99% used"] + [TABS] + S_SESSION + S_ALL_MODELS + S_FABLE
        self.assert_parses_or_rejects(pane(rows), S_EXPECTED)

    def test_scroll_and_duplicate_failures_name_a_taller_pane(self):
        cases = {
            "scroll": [TABS] + S_SESSION + S_ALL_MODELS[:2] + [paint(S_ALL_MODELS[2], DOWN, 70)],
            "duplicate": [TABS] + S_SESSION + S_ALL_MODELS + S_FABLE + S_SESSION,
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ParseError) as caught:
                    parse_claude_usage(pane(rows))
                self.assertTrue(caught.exception.message.endswith("Read with a taller pane."))


if __name__ == "__main__":
    unittest.main()
