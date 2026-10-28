"""Adversarial acceptance tests for teamlead.parsers.parse_claude_usage.

The contract under test: every window the pane shows either parses with its
own percentage, or the read raises ParseError. A read that succeeds with a
window missing, or with a wrong percentage, overstates headroom for every
seat that window binds.

Every fixture is an inline string built in the test. No clock, no randomness,
no files on disk.
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

# The captured `/usage` dialog below 62 columns, as logical rows, before
# the pane wraps them: (header, bar, percentage) per window.
LOGICAL_ROWS = (
    ("Current session · Resets 5pm (America/New_York)", "██▋", "5% used"),
    (
        "Current week (all models) · Resets Nov 2 at 10am (America/New_York)",
        "████████████████████▋",
        "39% used",
    ),
    (
        "Current week (Fable) · Resets Nov 2 at 10am (America/New_York)",
        "███████████████████",
        "36% used",
    ),
)
EXPECTED_USED = {
    "Current session": 5.0,
    "Current week (all models)": 39.0,
    "Current week (Fable)": 36.0,
}
EXPECTED_RESETS = {
    "Current session": "5pm (America/New_York)",
    "Current week (all models)": "Nov 2 at 10am (America/New_York)",
    "Current week (Fable)": "Nov 2 at 10am (America/New_York)",
}
# Widest header is 68 columns; the sweep runs past it.
SWEEP_WIDTHS = range(6, 101)
# At this width and wider every header keeps its label on one line, so a
# read must succeed and keep every reset.
FULL_FIDELITY_WIDTH = 27

# The captured dialog as the pane showed it.
LIVE_INLINE = """\
   Current session · Resets 5pm (America/New_York)
   ██▋

   5% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   ████████████████████▋

   39% used

   Current week (Fable) · Resets Nov 2 at 10am
   (America/New_York)
   ███████████████████

   36% used
"""

# The stacked layout rendered at a pane width of 62 columns or more, with
# an exhausted per-model week.
OLD_STACKED = """\
   Current session
   ████                                               8% used
   Resets 12:49am (Europe/Oslo)
   Current week (all models)
   █                                                  2% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
   Current week (Fable)
   ██████████████████████████████████████████████   100% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
"""

# The live dialog word-wrapped at 35 columns: the weekly header breaks after
# `Resets`, so the reset text starts the next line.
WRAPPED_AFTER_RESETS_WORD = """\
   Current session · Resets 5pm
   (America/New_York)
   ██▋

   5% used

   Current week (all models) · Resets
   Nov 2 at 10am (America/New_York)
   ████████████████████▋

   39% used
"""


def render(width, break_long_words):
    """The logical dialog word-wrapped to `width` columns, then indented."""
    lines = []
    for header, bar, used in LOGICAL_ROWS:
        for row in (header, bar, "", used, ""):
            if not row:
                lines.append("   ")
                continue
            wrapped = textwrap.wrap(
                row,
                width,
                break_long_words=break_long_words,
                break_on_hyphens=False,
            )
            lines.extend("   " + part for part in wrapped)
    return "\n".join(lines) + "\n"


class NoSilentDropMixin(unittest.TestCase):
    def assert_parses_or_rejects(self, text, expected_used):
        """Either ParseError, or every expected window with its percentage."""
        try:
            windows = parse_claude_usage(text)["windows"]
        except ParseError:
            return None
        self.assertEqual(
            {label: window["used_pct"] for label, window in windows.items()},
            expected_used,
        )
        return windows


class TightenedOracleTest(NoSilentDropMixin):
    def test_header_wrapped_after_resets_parses_every_window(self):
        # The changelog names this shape as supported, so a rejection is a
        # failure here, not an allowed outcome.
        windows = parse_claude_usage(WRAPPED_AFTER_RESETS_WORD)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {
                "Current session": (5.0, "5pm (America/New_York)"),
                "Current week (all models)": (39.0, "Nov 2 at 10am (America/New_York)"),
            },
        )
        self.assertEqual(headroom_pct(windows, "Opus 5.5"), 61.0)


class WidthSweepTest(NoSilentDropMixin):
    def test_no_width_drops_a_window_or_misreads_a_percentage(self):
        for break_long_words in (True, False):
            for width in SWEEP_WIDTHS:
                with self.subTest(width=width, break_long_words=break_long_words):
                    self.assert_parses_or_rejects(
                        render(width, break_long_words), EXPECTED_USED
                    )

    def test_no_width_invents_reset_text(self):
        for break_long_words in (True, False):
            for width in SWEEP_WIDTHS:
                with self.subTest(width=width, break_long_words=break_long_words):
                    windows = self.assert_parses_or_rejects(
                        render(width, break_long_words), EXPECTED_USED
                    )
                    for label, window in (windows or {}).items():
                        self.assertIn(window["resets"], (EXPECTED_RESETS[label], None))

    def test_unsplit_labels_parse_with_every_reset(self):
        for break_long_words in (True, False):
            for width in range(FULL_FIDELITY_WIDTH, SWEEP_WIDTHS.stop):
                with self.subTest(width=width, break_long_words=break_long_words):
                    windows = parse_claude_usage(render(width, break_long_words))["windows"]
                    self.assertEqual(
                        {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
                        {label: (EXPECTED_USED[label], EXPECTED_RESETS[label]) for label in EXPECTED_USED},
                    )


class ExtraPercentageTest(unittest.TestCase):
    """A `N% used` row outside the three rate-limit windows. Credit sections
    and a prose footer are not windows, so the read keeps exactly those three."""

    def test_extra_usage_section_after_the_inline_layout(self):
        text = (
            LIVE_INLINE
            + "\n   Extra usage\n   ████   12% used\n"
            + "   $6.00 / $50.00 spent · Resets Nov 1 (America/New_York)\n"
        )
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: window["used_pct"] for label, window in windows.items()},
            EXPECTED_USED,
        )

    def test_extra_usage_section_after_the_stacked_layout(self):
        text = OLD_STACKED + "   Extra usage\n   ██   12% used\n"
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: window["used_pct"] for label, window in windows.items()},
            {
                "Current session": 8.0,
                "Current week (all models)": 2.0,
                "Current week (Fable)": 100.0,
            },
        )

    def test_footer_percentage_line_after_the_last_window(self):
        text = LIVE_INLINE + "\n   Opus 5.5 is 20% used this week\n"
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: window["used_pct"] for label, window in windows.items()},
            EXPECTED_USED,
        )


class ClippedViewportTest(unittest.TestCase):
    """A `visible` read of a pane shorter than the dialog. The header or the
    percentage of an edge window is off screen; the read must fail rather
    than report the windows that remain."""

    def test_percentage_above_the_first_header_is_rejected(self):
        # Scrolled: the session header is off the top, its 98% row is not.
        # Reporting the two weekly windows alone overstates headroom 61 vs 2.
        text = "   98% used\n\n" + "\n".join(LIVE_INLINE.splitlines()[5:]) + "\n"
        with self.assertRaises(ParseError):
            parse_claude_usage(text)

    def test_stacked_percentage_above_the_first_header_is_rejected(self):
        text = (
            "   ████   98% used\n"
            "   Resets 12:49am (Europe/Oslo)\n"
            "   Current week (all models)\n"
            "   █   2% used\n"
            "   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
        )
        with self.assertRaises(ParseError):
            parse_claude_usage(text)

    def test_last_header_without_its_percentage_is_rejected(self):
        # Cut after the Fable bar: an exhausted Fable week would otherwise
        # read as headroom 61 for a Fable seat.
        text = "\n".join(LIVE_INLINE.splitlines()[:14]) + "\n"
        with self.assertRaises(ParseError):
            parse_claude_usage(text)

    def test_stacked_last_header_without_its_percentage_is_rejected(self):
        text = "\n".join(OLD_STACKED.splitlines()[:7]) + "\n"
        with self.assertRaises(ParseError):
            parse_claude_usage(text)

    def test_header_whose_percentage_never_arrives_is_rejected(self):
        # The session percentage sits on the header line, so the header regex
        # takes it as reset text and the session row has no percentage.
        text = (
            "Current session · Resets 5pm (America/New_York)   98% used\n"
            "Current week (all models) · Resets Nov 2 at 10am (America/New_York)\n"
            "39% used\n"
        )
        with self.assertRaises(ParseError):
            parse_claude_usage(text)


if __name__ == "__main__":
    unittest.main()
