"""Tester-3 acceptance tests for teamlead.parsers.parse_claude_usage.

The contract: every rate-limit window the pane shows parses with its own
percentage, or the read raises ParseError. A read that succeeds with a window
missing overstates headroom for every seat that window binds. Credit
sections and other non-window rows never fail a read whose windows are all
shown. An arrow on the first or last window's own row may fail the read,
because that pane looks the same as one that cut a window off.

The LIVE_* fixtures are `herdr agent read --source visible` captures of a
narrow Claude Code pane, copied verbatim. Every other fixture is built in
the test. No clock, no randomness, no files on disk.
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

# Full dialog: all three windows, then a non-window section and a trailing
# scroll-down indicator.
LIVE_FULL = """\
  (modules: hooks/register.ts unchanged; 2
  hooks: prompt.suggest, prompt.edit)
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   Settings  Status   Config   Usage   Stats

   Session

   Total cost:            $7.70
   Total duration (API):  16m 28s
   Total duration (wall): 14d 16h 53m
   Total code changes:    1018 lines added, 0 lines
   removed
   Usage by model:
       claude-haiku-5-5:  2.6k input, 42 output, 0
   cache read, 0 cache write ($0.0003)
        claude-opus-5-5:  3.3k input, 89.9k output,
   11.0m cache read, 461.1k cache write ($7.70)

   Current session · Resets 5pm (America/New_York)
   ███▏

   6% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   █████████████████████▏

   40% used

   Current week (Fable) · Resets Nov 2 at 10am
   (America/New_York)
   ███████████████████

   36% used

   What's contributing to your limits usage?
   Approximate · sessions on this machine only

   Last 24h · shares overlap
                                                     ↓
"""

# Scrolled to the top: the all-models percentage and the Fable window are
# below the viewport.
LIVE_CUT_ALL_MODELS = """\

✻ Baked for 14m 22s · done 1:47 PM
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   Settings  Status   Config   Usage   Stats

   Session

   Total cost:            $3.13
   Total duration (API):  5m 1s
   Total duration (wall): 29m 36s
   Total code changes:    348 lines added, 1 line
   removed
   Usage by model:
       claude-haiku-5-5:  1.3k input, 23 output, 0
   cache read, 0 cache write ($0.0001)
        claude-opus-5-5:  69.3k input, 30.4k output,
   5.2m cache read, 152.6k cache write ($3.13)
   Prompt cache (main):   33 requests · 97% of input
   tokens from cache · no misses · warm (1h TTL,
   last activity 14m 35s ago)


   Plugin skill-listing footprint
   What each plugin's skill descriptions add to the
   system prompt (cached input after the first
   turn). Agents and MCP tools not yet counted.

   watch                      1 skill · ~63 tok/turn

   Total                       ~63 tok/turn

   Current session · Resets 5pm (America/New_York)
   ████▊

   9% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   █████████████████████▋
                                                     ↓
"""

# Scrolled one row: the scroll-down indicator shares the row with the
# all-models percentage, and the Fable window is below the viewport.
LIVE_ARROW_ON_PERCENT = """\

✻ Baked for 14m 22s · done 1:47 PM
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   Settings  Status   Config   Usage   Stats

                                                     ↑
   Total cost:            $3.13
   Total duration (API):  5m 1s
   Total duration (wall): 29m 36s
   Total code changes:    348 lines added, 1 line
   removed
   Usage by model:
       claude-haiku-5-5:  1.3k input, 23 output, 0
   cache read, 0 cache write ($0.0001)
        claude-opus-5-5:  69.3k input, 30.4k output,
   5.2m cache read, 152.6k cache write ($3.13)
   Prompt cache (main):   33 requests · 97% of input
   tokens from cache · no misses · warm (1h TTL,
   last activity 14m 35s ago)


   Plugin skill-listing footprint
   What each plugin's skill descriptions add to the
   system prompt (cached input after the first
   turn). Agents and MCP tools not yet counted.

   watch                      1 skill · ~63 tok/turn

   Total                       ~63 tok/turn

   Current session · Resets 5pm (America/New_York)
   ████▊

   9% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   █████████████████████▋

   41% used                                          ↓
"""

# Scrolled two rows: the all-models percentage is clear of the indicator,
# which sits alone on the last row. The Fable window is below the viewport.
LIVE_FABLE_OFF_SCREEN = """\

✻ Baked for 14m 22s · done 1:47 PM
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   Settings  Status   Config   Usage   Stats

   Total cost:            $3.13                      ↑
   Total duration (API):  5m 1s
   Total duration (wall): 29m 36s
   Total code changes:    348 lines added, 1 line
   removed
   Usage by model:
       claude-haiku-5-5:  1.3k input, 23 output, 0
   cache read, 0 cache write ($0.0001)
        claude-opus-5-5:  69.3k input, 30.4k output,
   5.2m cache read, 152.6k cache write ($3.13)
   Prompt cache (main):   33 requests · 97% of input
   tokens from cache · no misses · warm (1h TTL,
   last activity 14m 35s ago)


   Plugin skill-listing footprint
   What each plugin's skill descriptions add to the
   system prompt (cached input after the first
   turn). Agents and MCP tools not yet counted.

   watch                      1 skill · ~63 tok/turn

   Total                       ~63 tok/turn

   Current session · Resets 5pm (America/New_York)
   ████▊

   9% used

   Current week (all models) · Resets Nov 2 at 10am
   (America/New_York)
   █████████████████████▋

   41% used
                                                     ↓
"""

INLINE = """\
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

STACKED = """\
   Current session
   ████                                               5% used
   Resets 5pm (America/New_York)
   Current week (all models)
   ████████████████████▋                              39% used
   Resets Nov 2 at 10am (America/New_York)
   Current week (Fable)
   ███████████████████                                36% used
   Resets Nov 2 at 10am (America/New_York)
"""

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

# Logical rows of the inline layout before the pane wraps them.
SESSION_ROW = ("Current session · Resets 5pm (America/New_York)", "██▋", "5% used")
ALL_MODELS_ROW = (
    "Current week (all models) · Resets Nov 2 at 10am (America/New_York)",
    "████████████████████▋",
    "39% used",
)
FABLE_ROW = (
    "Current week (Fable) · Resets Nov 2 at 10am (America/New_York)",
    "███████████████████",
    "36% used",
)
COWORK_CREDIT_ROW = (
    "Claude Code and Cowork credit · Resets Dec 1 (America/New_York)",
    "████",
    "12% used",
)
CREDIT_ROW = ("Credit · Resets Dec 1 (America/New_York)", "██", "4% used")
USAGE_CREDITS_ROW = (
    "Usage credits · $5.00 / $50.00 spent · Resets December 1",
    "██",
    "10% used",
)
SWEEP_WIDTHS = range(6, 101)
# At this width and wider no rate-limit label splits, so the read succeeds.
FULL_FIDELITY_WIDTH = 27


def render(rows, width, break_long_words):
    """Logical (header, bar, percentage) rows word-wrapped to `width`."""
    lines = []
    for header, bar, used in rows:
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


class CreditSectionWrapTest(unittest.TestCase):
    """Credit sections after the windows, wrapped the way a narrow pane wraps."""

    def assert_three_windows(self, text):
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {label: (EXPECTED_USED[label], EXPECTED_RESETS[label]) for label in EXPECTED_USED},
        )

    def test_inline_credit_header_wrapped_at_the_separator(self):
        self.assert_three_windows(
            INLINE
            + "   Claude Code and Cowork credit ·\n   Resets Dec 1\n"
            + "   (America/New_York)\n   ████\n\n   12% used\n"
        )

    def test_inline_credit_title_wrapped_mid_title(self):
        self.assert_three_windows(
            INLINE
            + "   Claude Code and Cowork\n   credit · Resets Dec 1 (America/New_York)\n"
            + "   ████\n\n   12% used\n"
        )

    def test_inline_credit_timezone_wrapped(self):
        self.assert_three_windows(
            INLINE + "   Credit · Resets Dec 1\n   (America/New_York)\n   ██\n\n   4% used\n"
        )

    def test_inline_usage_credits_wrapped_after_spent(self):
        self.assert_three_windows(
            INLINE
            + "   Usage credits · $5.00 / $50.00 spent ·\n   Resets December 1\n"
            + "   ██\n\n   10% used\n"
        )

    def test_stacked_credit_title_and_reset_wrapped(self):
        self.assert_three_windows(
            STACKED
            + "   Claude Code and\n   Cowork credit\n"
            + "   ████                                               12% used\n"
            + "   Resets Dec 1\n   (America/New_York)\n"
        )

    def test_stacked_two_credit_sections_with_a_wrapped_reset(self):
        self.assert_three_windows(
            STACKED
            + "   Credit\n   ██                                                 4% used\n"
            + "   Resets Dec 1 (America/New_York)\n"
            + "   Usage credits\n   ██                                                 10% used\n"
            + "   $5.00 / $50.00 spent · Resets\n   December 1\n"
        )


class CreditBetweenWindowsTest(NoSilentDropCase):
    """A credit row between two rate-limit windows."""

    def test_inline_credit_between_windows_keeps_both(self):
        text = render([SESSION_ROW, CREDIT_ROW, ALL_MODELS_ROW], 80, True)
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            used_by_label(windows),
            {"Current session": 5.0, "Current week (all models)": 39.0},
        )

    def test_stacked_credit_between_windows_keeps_both(self):
        text = (
            "Current session\n██   5% used\nResets 5pm (America/New_York)\n"
            "Credit\n██   4% used\nResets Dec 1 (America/New_York)\n"
            "Current week (all models)\n███  39% used\nResets Nov 2 at 10am (America/New_York)\n"
        )
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            used_by_label(windows),
            {"Current session": 5.0, "Current week (all models)": 39.0},
        )

    def test_inline_wrapped_header_after_a_credit_row_is_not_dropped(self):
        # The all-models label splits after a credit row closed the session
        # window. Returning the session window alone reports headroom 95
        # where the account week leaves 2.
        text = (
            "Current session · Resets 5pm (America/New_York)\n██▋\n5% used\n"
            "Credit · Resets Dec 1 (America/New_York)\n██\n4% used\n"
            "Current week (all\nmodels) · Resets Nov 2 at 10am\n███\n98% used\n"
        )
        self.assert_parses_or_rejects(
            text, {"Current session": 5.0, "Current week (all models)": 98.0}
        )

    def test_stacked_wrapped_header_after_a_credit_row_is_not_dropped(self):
        text = (
            "Current session\n██   5% used\nResets 5pm (America/New_York)\n"
            "Credit\n██   4% used\nResets Dec 1 (America/New_York)\n"
            "Current week (all\nmodels)\n███  98% used\nResets Nov 2 at 10am (America/New_York)\n"
        )
        self.assert_parses_or_rejects(
            text, {"Current session": 5.0, "Current week (all models)": 98.0}
        )

    def test_credit_rows_between_windows_at_every_width(self):
        rows = [SESSION_ROW, CREDIT_ROW, ALL_MODELS_ROW, COWORK_CREDIT_ROW, FABLE_ROW]
        for break_long_words in (True, False):
            for width in SWEEP_WIDTHS:
                with self.subTest(width=width, break_long_words=break_long_words):
                    self.assert_parses_or_rejects(
                        render(rows, width, break_long_words), EXPECTED_USED
                    )


class WrappedAfterResetsThenCreditTest(unittest.TestCase):
    def test_header_wrapped_after_resets_followed_by_a_credit_section(self):
        text = (
            "   Current session · Resets 5pm\n   (America/New_York)\n   ██▋\n\n   5% used\n\n"
            "   Current week (all models) · Resets\n   Nov 2 at 10am (America/New_York)\n"
            "   ████████████████████▋\n\n   39% used\n\n"
            "   Current week (Fable) · Resets\n   Nov 2 at 10am (America/New_York)\n"
            "   ███████████████████\n\n   36% used\n\n"
            "   Claude Code and Cowork credit · Resets\n   Dec 1 (America/New_York)\n"
            "   ████\n\n   12% used\n"
        )
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {label: (EXPECTED_USED[label], EXPECTED_RESETS[label]) for label in EXPECTED_USED},
        )
        self.assertEqual(headroom_pct(windows, "Opus 5.5"), 61.0)


class WidthSweepWithCreditsTest(NoSilentDropCase):
    ROWS = [SESSION_ROW, ALL_MODELS_ROW, FABLE_ROW, COWORK_CREDIT_ROW, CREDIT_ROW, USAGE_CREDITS_ROW]

    def test_no_width_drops_a_window_with_credit_sections_appended(self):
        for break_long_words in (True, False):
            for width in SWEEP_WIDTHS:
                with self.subTest(width=width, break_long_words=break_long_words):
                    self.assert_parses_or_rejects(
                        render(self.ROWS, width, break_long_words), EXPECTED_USED
                    )

    def test_unsplit_labels_parse_every_window_with_credit_sections_appended(self):
        for break_long_words in (True, False):
            for width in range(FULL_FIDELITY_WIDTH, SWEEP_WIDTHS.stop):
                with self.subTest(width=width, break_long_words=break_long_words):
                    windows = parse_claude_usage(render(self.ROWS, width, break_long_words))[
                        "windows"
                    ]
                    self.assertEqual(
                        {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
                        {
                            label: (EXPECTED_USED[label], EXPECTED_RESETS[label])
                            for label in EXPECTED_USED
                        },
                    )


class LivePaneTest(unittest.TestCase):
    def test_full_live_pane_yields_every_window(self):
        windows = parse_claude_usage(LIVE_FULL)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {
                "Current session": (6.0, "5pm (America/New_York)"),
                "Current week (all models)": (40.0, "Nov 2 at 10am (America/New_York)"),
                "Current week (Fable)": (36.0, "Nov 2 at 10am (America/New_York)"),
            },
        )
        self.assertEqual(headroom_pct(windows, "Opus 5.5"), 60.0)

    def test_live_pane_cut_before_the_all_models_percentage_is_rejected(self):
        with self.assertRaises(ParseError):
            parse_claude_usage(LIVE_CUT_ALL_MODELS)

    def test_live_pane_with_the_arrow_on_the_percentage_is_rejected(self):
        # The Fable window is below the viewport, so a rejection is right.
        with self.assertRaises(ParseError):
            parse_claude_usage(LIVE_ARROW_ON_PERCENT)

    def test_live_pane_with_the_fable_window_off_screen_is_rejected(self):
        # The scroll-down indicator says the dialog continues; the Fable
        # week is not in the read. Two windows back reads Fable headroom as
        # the account week's 59 whatever the Fable week holds.
        with self.assertRaises(ParseError):
            parse_claude_usage(LIVE_FABLE_OFF_SCREEN)


class ScrollIndicatorTest(NoSilentDropCase):
    def test_session_window_above_the_viewport_is_rejected(self):
        # Scrolled down past the whole session window: only the weekly
        # windows remain, under a scroll-up indicator.
        text = "                                                     ↑\n" + INLINE[
            INLINE.index("   Current week (all") :
        ]
        with self.assertRaises(ParseError):
            parse_claude_usage(text)

    def test_arrow_on_the_last_percentage_does_not_reject_a_complete_read(self):
        # The parser cannot tell this pane from a viewport that ends on the
        # all-models percentage with the Fable window below it, so the read
        # may raise ParseError or return the three windows.
        text = INLINE.rstrip("\n") + "                                          ↓\n"
        self.assert_parses_or_rejects(text, EXPECTED_USED)

    def test_arrow_on_a_stacked_percentage_does_not_reject_a_complete_read(self):
        # Same limit as the inline case: a down arrow on the last percentage
        # is the viewport ending on the all-models percentage with Fable below.
        text = STACKED.replace("36% used", "36% used   ↓")
        self.assert_parses_or_rejects(text, EXPECTED_USED)

    def test_arrow_on_a_wrapped_timezone_is_not_reset_text(self):
        text = INLINE.replace(
            "   (America/New_York)\n   ████████████████████▋",
            "   (America/New_York)                     ↑\n   ████████████████████▋",
        )
        windows = parse_claude_usage(text)["windows"]
        self.assertEqual(
            windows["Current week (all models)"]["resets"],
            "Nov 2 at 10am (America/New_York)",
        )


if __name__ == "__main__":
    unittest.main()
