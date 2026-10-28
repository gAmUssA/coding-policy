"""Tester-4 acceptance tests for teamlead.parsers.parse_claude_usage.

The contract: every rate-limit window the pane shows parses with its own
percentage, or the read raises ParseError. A read that succeeds with a window
missing, or with a percentage taken from another row, overstates headroom
for every seat that window binds.

The dialog paints its scroll indicator over the first (`↑`) or last (`↓`)
viewport row, whatever that row holds: a blank row, a stats row, a header,
or a percentage. The viewport tests below cut one inline dialog at every
row pair and paint the indicators the way the captured panes show them.

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

import textwrap
import unittest

from teamlead.errors import ParseError
from teamlead.parsers import headroom_pct, parse_claude_usage

# Rows above the dialog body; never scrolled.
CHROME = [
    "  (modules: hooks/register.ts unchanged; 2",
    "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔",
    "   Settings  Status   Config   Usage   Stats",
]

# The `/usage` dialog body below 62 columns, as the live capture shows it,
# plus a longer contributions footer.
STATS = [
    "",
    "   Session",
    "",
    "   Total cost:            $7.70",
    "   Total duration (API):  16m 28s",
    "   Usage by model:",
    "        claude-opus-5-5:  3.3k input, 89.9k output,",
    "   11.0m cache read, 461.1k cache write ($7.70)",
    "",
]
SESSION = [
    "   Current session · Resets 5pm (America/New_York)",
    "   ███▏",
    "",
    "   6% used",
    "",
]
ALL_MODELS = [
    "   Current week (all models) · Resets Nov 2 at 10am",
    "   (America/New_York)",
    "   █████████████████████▏",
    "",
    "   40% used",
    "",
]
FABLE = [
    "   Current week (Fable) · Resets Nov 2 at 10am",
    "   (America/New_York)",
    "   ███████████████████",
    "",
    "   36% used",
]
FOOTER = [
    "",
    "   What's contributing to your limits usage?",
    "   Approximate · sessions on this machine only",
    "",
    "   Last 24h · shares overlap",
    "   Opus 5.5        80%",
    "   Haiku           20%",
]
BODY = STATS + SESSION + ALL_MODELS + FABLE + FOOTER

EXPECTED_USED = {
    "Current session": 6.0,
    "Current week (all models)": 40.0,
    "Current week (Fable)": 36.0,
}
EXPECTED_RESETS = {
    "Current session": "5pm (America/New_York)",
    "Current week (all models)": "Nov 2 at 10am (America/New_York)",
    "Current week (Fable)": "Nov 2 at 10am (America/New_York)",
}

UP = "↑"
DOWN = "↓"
# The captured panes paint the indicator at column 53.
ARROW_COLUMN = 53

CREDIT = [
    "",
    "   Usage credits · $5.00 / $50.00 spent · Resets December 1",
    "   ██",
    "",
    "   10% used",
    "",
]


def paint(row, arrow):
    """Paint a scroll indicator over the right edge of `row`."""
    return row.ljust(ARROW_COLUMN) + arrow


def pane(rows):
    return "\n".join(rows) + "\n"


def viewport(first, stop):
    """Rows `first` to `stop` of the dialog body, with its scroll indicators."""
    rows = list(BODY[first:stop])
    if first > 0:
        rows[0] = paint(rows[0], UP)
    if stop < len(BODY):
        rows[-1] = paint(rows[-1], DOWN)
    return pane(CHROME + rows)


def used_by_label(windows):
    return {label: window["used_pct"] for label, window in windows.items()}


class NoSilentDropCase(unittest.TestCase):
    def assert_parses_or_rejects(self, text, expected_used=None):
        """Either ParseError, or every expected window with its percentage."""
        expected = EXPECTED_USED if expected_used is None else expected_used
        try:
            windows = parse_claude_usage(text)["windows"]
        except ParseError:
            return None
        self.assertEqual(used_by_label(windows), expected)
        return windows

    def assert_three_windows(self, text):
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {label: (EXPECTED_USED[label], EXPECTED_RESETS[label]) for label in EXPECTED_USED},
        )


class ViewportTest(NoSilentDropCase):
    """Every viewport the dialog can show, with its scroll indicators."""

    def test_whole_dialog_parses_every_window(self):
        self.assert_three_windows(viewport(0, len(BODY)))

    def test_whole_dialog_under_a_trailing_down_arrow_parses_every_window(self):
        rows = CHROME + STATS + SESSION + ALL_MODELS + FABLE + FOOTER[:5]
        rows[-1] = paint(rows[-1], DOWN)
        self.assert_three_windows(pane(rows))

    def test_no_viewport_drops_a_window(self):
        for first in range(len(BODY)):
            for stop in range(first + 1, len(BODY) + 1):
                with self.subTest(first=first, stop=stop):
                    self.assert_parses_or_rejects(viewport(first, stop))

    def test_down_arrow_on_the_last_visible_percentage_with_a_window_below(self):
        # Unscrolled, and the viewport ends on the all-models percentage:
        # the indicator says more rows follow, and the Fable window is there.
        stop = len(STATS) + len(SESSION) + len(ALL_MODELS) - 1
        self.assertEqual(BODY[stop - 1], "   40% used")
        with self.assertRaises(ParseError):
            parse_claude_usage(viewport(0, stop))

    def test_down_arrow_on_the_session_percentage_with_windows_below(self):
        stop = len(STATS) + len(SESSION) - 1
        self.assertEqual(BODY[stop - 1], "   6% used")
        with self.assertRaises(ParseError):
            parse_claude_usage(viewport(0, stop))

    def test_up_arrow_on_a_weekly_header_with_a_window_above(self):
        # Scrolled until the Fable header is the first row: the session and
        # all-models windows are above the viewport.
        first = len(STATS) + len(SESSION) + len(ALL_MODELS)
        self.assertTrue(BODY[first].startswith("   Current week (Fable)"))
        with self.assertRaises(ParseError):
            parse_claude_usage(viewport(first, len(BODY)))

    def test_up_arrow_on_the_all_models_header_with_the_session_above(self):
        first = len(STATS) + len(SESSION)
        self.assertTrue(BODY[first].startswith("   Current week (all models)"))
        with self.assertRaises(ParseError):
            parse_claude_usage(viewport(first, len(BODY)))

    def test_fable_headroom_is_never_read_from_a_partial_view(self):
        # The partial reads above would report Fable headroom from a single
        # weekly window. A read either fails or binds both weekly windows.
        first = len(STATS) + len(SESSION) + len(ALL_MODELS)
        try:
            windows = parse_claude_usage(viewport(first, len(BODY)))["windows"]
        except ParseError:
            return
        self.assertEqual(headroom_pct(windows, "Fable"), 60.0)


class ArrowPositionTest(NoSilentDropCase):
    """An indicator on its own row, or painted on a row, at every position."""

    ROWS = SESSION + ALL_MODELS + FABLE + FOOTER

    def test_arrow_row_inserted_at_every_position(self):
        for arrow in (UP, DOWN):
            for at in range(len(self.ROWS) + 1):
                rows = self.ROWS[:at] + [paint("", arrow)] + self.ROWS[at:]
                with self.subTest(arrow=arrow, at=at):
                    self.assert_parses_or_rejects(pane(rows))

    def test_arrow_painted_on_every_row(self):
        for arrow in (UP, DOWN):
            for at in range(len(self.ROWS)):
                rows = list(self.ROWS)
                rows[at] = paint(rows[at], arrow)
                with self.subTest(arrow=arrow, at=at):
                    self.assert_parses_or_rejects(pane(rows))

    def test_down_arrow_alone_after_the_last_window_is_rejected(self):
        rows = SESSION + ALL_MODELS + FABLE + ["", paint("", DOWN)]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_down_arrow_after_a_non_window_section_parses(self):
        rows = SESSION + ALL_MODELS + FABLE + FOOTER[:3] + [paint("", DOWN)]
        self.assert_three_windows(pane(rows))

    def test_down_arrow_after_a_credit_section_parses(self):
        # The parser cannot tell this pane from 'credit between windows
        # with the next window off-screen'.
        rows = SESSION + ALL_MODELS + FABLE + CREDIT + [paint("", DOWN)]
        self.assert_parses_or_rejects(pane(rows))

    def test_up_arrow_before_complete_windows_parses_or_rejects(self):
        # Rejecting is allowed: the parser cannot know which row scrolled off.
        rows = [paint("", UP)] + SESSION + ALL_MODELS + FABLE + FOOTER
        self.assert_parses_or_rejects(pane(rows))

    def test_up_arrow_on_the_stats_above_the_windows_is_rejected(self):
        rows = [paint(STATS[3], UP)] + STATS[4:] + SESSION + ALL_MODELS
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))


class CreditPlacementTest(NoSilentDropCase):
    def test_credit_section_before_the_windows(self):
        self.assert_parses_or_rejects(pane(CREDIT + SESSION + ALL_MODELS + FABLE))

    def test_credit_section_between_the_windows(self):
        self.assert_three_windows(pane(SESSION + ALL_MODELS + CREDIT + FABLE))

    def test_credit_section_after_the_windows(self):
        self.assert_three_windows(pane(SESSION + ALL_MODELS + FABLE + CREDIT))

    def test_credit_sections_around_every_window(self):
        rows = CREDIT + SESSION + CREDIT + ALL_MODELS + CREDIT + FABLE + CREDIT
        self.assert_parses_or_rejects(pane(rows))


class CurrentTitleTest(NoSilentDropCase):
    """Lines that start with `Current` but are not where a window belongs."""

    def test_footer_row_titled_current_session_with_a_percentage(self):
        # A second `Current session` must not overwrite the first window.
        rows = SESSION + ALL_MODELS + FABLE + FOOTER[:3] + [
            "   Current session",
            "   ██",
            "   1% used",
        ]
        self.assert_parses_or_rejects(pane(rows))

    def test_footer_row_titled_current_session_alone(self):
        rows = SESSION + ALL_MODELS + FABLE + FOOTER[:3] + ["   Current session"]
        self.assert_parses_or_rejects(pane(rows))

    def test_prose_starting_with_current_after_the_windows(self):
        rows = SESSION + ALL_MODELS + FABLE + ["", "   Currently showing local data only"]
        self.assert_three_windows(pane(rows))

    def test_prose_starting_with_current_between_the_windows(self):
        rows = SESSION + ["   Current plan: Max", ""] + ALL_MODELS + FABLE
        self.assert_parses_or_rejects(pane(rows))

    def test_prose_starting_with_current_before_the_windows(self):
        rows = ["   Current plan: Max", ""] + SESSION + ALL_MODELS + FABLE
        self.assert_parses_or_rejects(pane(rows))


class GlueTest(NoSilentDropCase):
    """Continuation glue never moves a percentage or swallows a header."""

    def test_open_reset_never_swallows_the_next_header(self):
        rows = [
            "   Current session · Resets",
            "   Current week (all models) · Resets Nov 2 at 10am (America/New_York)",
            "   ██",
            "",
            "   40% used",
        ]
        with self.assertRaises(ParseError):
            parse_claude_usage(pane(rows))

    def test_stacked_reset_without_a_zone_keeps_the_next_header(self):
        text = pane(
            [
                "   Current session",
                "   ███                                               6% used",
                "   Resets 5pm",
                "   Current week (all models)",
                "   ████████████████████                             40% used",
                "   Resets Nov 2 at 10am",
                "   Current week (Fable)",
                "   ███████████████████                              36% used",
                "   Resets Nov 2 at 10am",
            ]
        )
        self.assertEqual(used_by_label(parse_claude_usage(text)["windows"]), EXPECTED_USED)

    def test_promo_line_under_an_open_reset_keeps_the_percentage(self):
        rows = (
            ["   Current session · Resets 5pm", "   +50% weekly limits promo through Sep 13"]
            + SESSION[1:]
            + ALL_MODELS
            + FABLE
        )
        self.assert_parses_or_rejects(pane(rows))

    def test_status_line_under_an_open_reset_keeps_the_percentage(self):
        rows = (
            ["   Current session · Resets 5pm", "   Approximate · sessions on this machine only"]
            + SESSION[1:]
            + ALL_MODELS
            + FABLE
        )
        self.assert_parses_or_rejects(pane(rows))

    def test_credit_title_under_an_open_stacked_reset_is_not_its_percentage(self):
        text = pane(
            [
                "   Current session",
                "   ███                                               6% used",
                "   Resets 5pm",
                "   Usage credits · $5.00 / $50.00 spent",
                "   ██                                               10% used",
            ]
        )
        self.assert_parses_or_rejects(text, {"Current session": 6.0})

    def test_parenthetical_note_does_not_take_the_next_percentage(self):
        rows = ["   Current session · Resets 5pm", "   (promo note)"] + SESSION[1:]
        windows = parse_claude_usage(pane(rows))["windows"]
        self.assertEqual(used_by_label(windows), {"Current session": 6.0})


class EmptyPaneTest(unittest.TestCase):
    def assert_no_windows(self, text):
        with self.assertRaises(ParseError) as caught:
            parse_claude_usage(text)
        self.assertIn("No usage windows found", str(caught.exception))

    def test_empty_text(self):
        self.assert_no_windows("")

    def test_blank_rows_only(self):
        self.assert_no_windows("\n   \n\n")

    def test_bar_rows_only(self):
        self.assert_no_windows("   ████\n   ██▋\n")

    def test_scroll_indicators_only(self):
        self.assert_no_windows(paint("", UP) + "\n\n" + paint("", DOWN) + "\n")


def render(rows, width, break_long_words):
    """Logical rows word-wrapped to `width`; `""` stays a blank row."""
    lines = []
    for row in rows:
        if not row:
            lines.append("   ")
            continue
        lines.extend(
            "   " + part
            for part in textwrap.wrap(
                row, width, break_long_words=break_long_words, break_on_hyphens=False
            )
        )
    return lines


LOGICAL_WINDOWS = [
    "Current session · Resets 5pm (America/New_York)",
    "███▏",
    "",
    "6% used",
    "",
    "Current week (all models) · Resets Nov 2 at 10am (America/New_York)",
    "█████████████████████▏",
    "",
    "40% used",
    "",
    "Current week (Fable) · Resets Nov 2 at 10am (America/New_York)",
    "███████████████████",
    "",
    "36% used",
]
LOGICAL_FOOTER = [
    "",
    "What's contributing to your limits usage?",
    "Approximate · sessions on this machine only",
]
SWEEP_WIDTHS = range(6, 101)


class WidthSweepWithArrowsTest(NoSilentDropCase):
    def sweep(self):
        for width in SWEEP_WIDTHS:
            for break_long_words in (True, False):
                yield width, break_long_words

    def test_down_arrow_after_the_footer_never_drops_a_window(self):
        for width, breaks in self.sweep():
            lines = render(LOGICAL_WINDOWS + LOGICAL_FOOTER, width, breaks)
            lines.append(paint("", DOWN))
            with self.subTest(width=width, break_long_words=breaks):
                self.assert_parses_or_rejects(pane(lines))

    def test_down_arrow_on_the_footer_row_never_drops_a_window(self):
        for width, breaks in self.sweep():
            lines = render(LOGICAL_WINDOWS + LOGICAL_FOOTER, width, breaks)
            lines[-1] = paint(lines[-1], DOWN)
            with self.subTest(width=width, break_long_words=breaks):
                self.assert_parses_or_rejects(pane(lines))

    def test_down_arrow_right_after_the_windows_is_rejected(self):
        for width, breaks in self.sweep():
            lines = render(LOGICAL_WINDOWS, width, breaks) + [paint("", DOWN)]
            with self.subTest(width=width, break_long_words=breaks):
                with self.assertRaises(ParseError):
                    parse_claude_usage(pane(lines))

    def test_down_arrow_on_the_last_percentage_without_a_footer_is_rejected(self):
        # Nothing on screen says the Fable window is the last one.
        for width, breaks in self.sweep():
            lines = render(LOGICAL_WINDOWS[:9], width, breaks)
            lines[-1] = paint(lines[-1], DOWN)
            with self.subTest(width=width, break_long_words=breaks):
                with self.assertRaises(ParseError):
                    parse_claude_usage(pane(lines))

    def test_up_arrow_before_the_windows_is_rejected(self):
        for width, breaks in self.sweep():
            lines = [paint("", UP)] + render(LOGICAL_WINDOWS[5:], width, breaks)
            with self.subTest(width=width, break_long_words=breaks):
                with self.assertRaises(ParseError):
                    parse_claude_usage(pane(lines))

    def test_up_arrow_on_the_first_visible_header_is_rejected(self):
        for width, breaks in self.sweep():
            lines = render(LOGICAL_WINDOWS[5:] + LOGICAL_FOOTER, width, breaks)
            lines[0] = paint(lines[0], UP)
            with self.subTest(width=width, break_long_words=breaks):
                with self.assertRaises(ParseError):
                    parse_claude_usage(pane(lines))

    def test_unsplit_labels_with_a_trailing_arrow_parse_every_window(self):
        for width in range(70, 101):
            lines = render(LOGICAL_WINDOWS + LOGICAL_FOOTER, width, True)
            lines.append(paint("", DOWN))
            with self.subTest(width=width):
                self.assert_three_windows(pane(lines))


if __name__ == "__main__":
    unittest.main()
