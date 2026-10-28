"""Adversarial layout tests for teamlead.parsers.parse_claude_usage.

Every fixture is an inline string. No clock, no randomness, no files on disk.
The herdr client in the measure test is the shared fake from test_measure.
"""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import unittest

from teamlead.errors import ParseError
from teamlead.herdr import HerdrClient
from teamlead.parsers import headroom_pct, parse_claude_usage, parse_usage

from tests.test_measure import BY_NAME, measure_agent, runner_with

# The captured newer `/usage` pane: reset inline after U+00B7, bar on its own
# row, `N% used` after the bar, weekly timezones wrapped onto the next line.
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

# The older stacked layout: header alone, then `N% used`, then `Resets`.
OLD_STACKED = """\
   Settings  Status   Config   Usage   Stats
   Current session
   ████                                               8% used
   Resets 12:49am (Europe/Oslo)
   Current week (all models)
   █                                                  2% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
   +50% weekly limits promo through Sep 13 · clau.de/cc-50-promo
   Current week (Fable)
   ██████████████████████████████████████████████   100% used
   Resets Sep 5 at 11:59pm (Europe/Oslo)
"""

# The live dialog word-wrapped at 35 columns. The weekly header breaks after
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


class LiveLayoutTest(unittest.TestCase):
    def test_captured_live_layout_yields_every_window(self):
        windows = parse_claude_usage(LIVE_INLINE)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["remaining_pct"], w["resets"]) for label, w in windows.items()},
            {
                "Current session": (5.0, 95.0, "5pm (America/New_York)"),
                "Current week (all models)": (39.0, 61.0, "Nov 2 at 10am (America/New_York)"),
                "Current week (Fable)": (36.0, 64.0, "Nov 2 at 10am (America/New_York)"),
            },
        )

    def test_captured_live_layout_through_the_dispatcher(self):
        parsed = parse_usage("claude", LIVE_INLINE)
        self.assertEqual(parsed["credits"], None)
        self.assertEqual(headroom_pct(parsed["windows"], "Opus 5.5"), 61.0)

    def test_measure_agent_reads_the_live_layout(self):
        runner = runner_with({"claude": "idle"}, {"claude": LIVE_INLINE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(record["windows"]["Current week (all models)"]["used_pct"], 39.0)
        self.assertEqual(record["headroom_pct"], 61.0)
        self.assertIn("agent send-keys claude esc", runner.commands())


class WrapTest(unittest.TestCase):
    def test_header_wrapped_at_the_separator_reads_the_next_resets_line(self):
        text = "Current session ·\nResets 5pm\n(America/New_York)\n██▋\n5% used\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["used_pct"], 5.0)
        self.assertEqual(window["resets"], "5pm (America/New_York)")

    def test_wrapped_timezone_after_a_blank_line_is_kept(self):
        text = "Current week (all models) · Resets Nov 2 at 10am\n\n(America/New_York)\n39% used\n"
        window = parse_claude_usage(text)["windows"]["Current week (all models)"]
        self.assertEqual(window["resets"], "Nov 2 at 10am (America/New_York)")

    def test_multi_segment_timezone_is_kept(self):
        text = "Current session · Resets 5pm\n(America/Argentina/Buenos_Aires)\n5% used\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["resets"], "5pm (America/Argentina/Buenos_Aires)")

    def test_parenthetical_without_a_slash_is_not_glued_on(self):
        text = "Current week (all models) · Resets Nov 2 at 10am\n(limits doubled)\n39% used\n"
        window = parse_claude_usage(text)["windows"]["Current week (all models)"]
        self.assertEqual(window["used_pct"], 39.0)
        self.assertEqual(window["resets"], "Nov 2 at 10am")

    def test_mid_phrase_time_wrap_keeps_the_window_and_its_percentage(self):
        text = "Current week (all models) · Resets Nov 2\nat 10am (America/New_York)\n39% used\n"
        window = parse_claude_usage(text)["windows"]["Current week (all models)"]
        self.assertEqual(window["used_pct"], 39.0)
        self.assertEqual(window["remaining_pct"], 61.0)

    def test_header_wrapped_after_the_resets_word_never_drops_a_binding_window(self):
        # A window the pane shows must either parse or fail the read; a silent
        # omission overstates headroom for every seat it binds.
        try:
            windows = parse_claude_usage(WRAPPED_AFTER_RESETS_WORD)["windows"]
        except ParseError:
            return
        self.assertIn("Current week (all models)", windows)
        self.assertEqual(windows["Current week (all models)"]["used_pct"], 39.0)
        self.assertEqual(headroom_pct(windows, "Opus 5.5"), 61.0)


class OrderTest(unittest.TestCase):
    def test_inline_reset_before_the_percentage(self):
        text = "Current session · Resets 5pm (America/New_York)\n██▋\n5% used\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["used_pct"], 5.0)
        self.assertEqual(window["resets"], "5pm (America/New_York)")

    def test_stacked_reset_before_the_percentage(self):
        text = "Current session\nResets 5pm (America/New_York)\n5% used\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["used_pct"], 5.0)
        self.assertEqual(window["resets"], "5pm (America/New_York)")

    def test_inline_reset_wins_over_a_later_resets_line(self):
        text = "Current session · Resets 5pm (America/New_York)\n5% used\nResets 6pm (Europe/Oslo)\n"
        window = parse_claude_usage(text)["windows"]["Current session"]
        self.assertEqual(window["resets"], "5pm (America/New_York)")


class OldLayoutTest(unittest.TestCase):
    def test_old_stacked_layout_still_parses(self):
        windows = parse_claude_usage(OLD_STACKED)["windows"]
        self.assertEqual(
            {label: (w["used_pct"], w["resets"]) for label, w in windows.items()},
            {
                "Current session": (8.0, "12:49am (Europe/Oslo)"),
                "Current week (all models)": (2.0, "Sep 5 at 11:59pm (Europe/Oslo)"),
                "Current week (Fable)": (100.0, "Sep 5 at 11:59pm (Europe/Oslo)"),
            },
        )


class RejectTest(unittest.TestCase):
    def test_empty_text_is_rejected(self):
        with self.assertRaises(ParseError) as caught:
            parse_claude_usage("")
        self.assertIn("No usage windows found", str(caught.exception))

    def test_whitespace_only_text_is_rejected(self):
        with self.assertRaises(ParseError):
            parse_claude_usage("   \n\n  \n")

    def test_separator_without_a_resets_clause_is_not_a_header(self):
        with self.assertRaises(ParseError):
            parse_claude_usage("Current session · 5pm (America/New_York)\n5% used\n")


if __name__ == "__main__":
    unittest.main()
