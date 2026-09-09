"""Tests for teamlead.probe.

`probe_state` is a pure function, so every fixture here is an inline pane
snapshot. `resolve_status` is exercised through the fake transport.
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
from typing import TypedDict

from teamlead.config import parse_config
from teamlead.herdr import HerdrClient
from teamlead.probe import (
    IDLE,
    INCONCLUSIVE,
    WORKING,
    footer,
    probe_state,
    resolve_status,
)

from tests.fakes import FakeRunner

# --- verbatim footers -------------------------------------------------------

GROK_IDLE = """\
  ╭──────────────────────────────────────────────────────────────╮
  │ ❯                                                            │
  ╰────────────────────────── Grok 4.6 (high) · always-approve ──╯

  Shift+Tab:mode  │  Ctrl+.:shortcuts
"""

GROK_WORKING = """\
    ⠦ - Waiting for response…                                   2m21s [stop]

  ╭──────────────────────────────────────────────────────────────╮
  │ ❯                                                            │
  ╰────────────────────────── Grok 4.6 (high) · always-approve ──╯

  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts
"""

CLAUDE_IDLE = """\
────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────
  ~/Projects/herdr-team-lead  main Fable 5 ctx:83% | Est. usage: $1.47
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""

CLAUDE_WORKING = """\
✻ Inferring… (12s · ↑ 1.4k tokens · esc to interrupt)
────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────
  ~/Projects/herdr-team-lead  main Fable 5 ctx:83%
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""

CLAUDE_CHURNING = """\
  Churned for 4m 12s
────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────
  ? for shortcuts
"""

CODEX_IDLE = """\
  ─────────────────────────────────────────────────────
  › Ask Codex to do anything
  gpt-5.3-codex  ·  /status for limits
"""

CODEX_WORKING = """\
  Working… (esc to interrupt)
  ─────────────────────────────────────────────────────
  › Ask Codex to do anything
  gpt-5.3-codex  ·  /status for limits
"""

class Markers(TypedDict):
    """The two marker lists, typed so `**MARKERS` unpacks to named parameters.

    A plain dict would widen to `dict[str, list[str]]`, and pyright then reads
    `**MARKERS` as possibly filling `probe_state`'s `limit` int with a list.
    """

    idle_markers: list[str]
    working_markers: list[str]


GROK_MARKERS: Markers = {"idle_markers": ["Shift+Tab:mode"], "working_markers": ["Esc:cancel"]}
CLAUDE_MARKERS: Markers = {
    "idle_markers": ["⏵⏵ bypass permissions on", "? for shortcuts"],
    "working_markers": ["Churned for"],
}
CODEX_MARKERS: Markers = {
    "idle_markers": ["› Ask Codex to do anything"],
    "working_markers": [],
}


class FooterTest(unittest.TestCase):
    def test_returns_the_last_non_empty_rows_stripped(self):
        self.assertEqual(footer("a\n\n  b  \n\n\nc\n", limit=2), ["b", "c"])

    def test_shorter_input_returns_everything(self):
        self.assertEqual(footer("only\n", limit=15), ["only"])

    def test_empty_input_returns_nothing(self):
        self.assertEqual(footer("\n\n   \n"), [])


class GrokProbeTest(unittest.TestCase):
    def test_idle_footer_reads_idle(self):
        self.assertEqual(probe_state(GROK_IDLE, **GROK_MARKERS), IDLE)

    def test_working_footer_reads_working(self):
        # The working footer is the idle footer plus `Esc:cancel`, so the
        # idle marker matches too. Working has to win.
        self.assertEqual(probe_state(GROK_WORKING, **GROK_MARKERS), WORKING)

    def test_spinner_row_alone_reads_working(self):
        text = "  ⠦ - Waiting for response…\n  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"
        self.assertEqual(probe_state(text, **GROK_MARKERS), WORKING)


class ClaudeProbeTest(unittest.TestCase):
    def test_status_line_reads_idle(self):
        self.assertEqual(probe_state(CLAUDE_IDLE, **CLAUDE_MARKERS), IDLE)

    def test_spinner_verb_reads_working_even_with_the_status_line_present(self):
        self.assertEqual(probe_state(CLAUDE_WORKING, **CLAUDE_MARKERS), WORKING)

    def test_churned_for_reads_working(self):
        self.assertEqual(probe_state(CLAUDE_CHURNING, **CLAUDE_MARKERS), WORKING)

    def test_the_shortcuts_hint_is_an_idle_marker_too(self):
        text = "────────\n❯\n────────\n  ? for shortcuts\n"
        self.assertEqual(probe_state(text, **CLAUDE_MARKERS), IDLE)


class CodexProbeTest(unittest.TestCase):
    def test_prompt_row_reads_idle(self):
        self.assertEqual(probe_state(CODEX_IDLE, **CODEX_MARKERS), IDLE)

    def test_spinner_reads_working_despite_the_prompt_row(self):
        self.assertEqual(probe_state(CODEX_WORKING, **CODEX_MARKERS), WORKING)


class InconclusiveTest(unittest.TestCase):
    def test_unrecognised_screen_is_inconclusive(self):
        self.assertEqual(probe_state("some other program\n", **GROK_MARKERS), INCONCLUSIVE)

    def test_empty_pane_is_inconclusive(self):
        self.assertEqual(probe_state("", **GROK_MARKERS), INCONCLUSIVE)

    def test_no_markers_configured_can_never_read_idle(self):
        self.assertEqual(probe_state(GROK_IDLE), INCONCLUSIVE)

    def test_an_idle_marker_far_above_the_footer_does_not_count(self):
        # Scoping to the footer keeps stale transcript text from being read as
        # a live status line.
        noise = "\n".join("transcript row {}".format(index) for index in range(40))
        self.assertEqual(
            probe_state("  Shift+Tab:mode  │  Ctrl+.:shortcuts\n" + noise, **GROK_MARKERS),
            INCONCLUSIVE,
        )


CONFIG = {
    "schema_version": 1,
    "agents": [
        dict(
            {
                "name": "grok",
                "kind": "grok",
                "usage_prompt": "/usage",
                "usage_marker": "Weekly limit",
                "usage_read_source": "visible",
                "close_keys": ["esc"],
                "clear_prompt": "/new",
            },
            **GROK_MARKERS
        ),
        {
            "name": "bare",
            "kind": "grok",
            "usage_prompt": "/usage",
            "usage_marker": "Weekly limit",
            "usage_read_source": "visible",
            "clear_prompt": "/new",
        },
    ],
}
BY_NAME = {agent.name: agent for agent in parse_config(CONFIG)}


class ResolveStatusTest(unittest.TestCase):
    def _client(self, text):
        self.runner = FakeRunner()
        self.runner.set("agent read", text)
        return HerdrClient(runner=self.runner)

    def test_idle_from_herdr_is_taken_at_face_value_without_a_read(self):
        client = self._client(GROK_IDLE)
        self.assertEqual(resolve_status(client, BY_NAME["grok"], "idle"), ("idle", "herdr"))
        self.assertEqual(self.runner.calls, [])

    def test_done_from_herdr_is_taken_at_face_value(self):
        client = self._client(GROK_IDLE)
        self.assertEqual(resolve_status(client, BY_NAME["grok"], "done"), ("done", "herdr"))
        self.assertEqual(self.runner.calls, [])

    def test_blocked_is_never_probed(self):
        # herdr recognising an approval dialog is a positive signal, not a
        # stale one, so it is never second-guessed.
        client = self._client(GROK_IDLE)
        self.assertEqual(
            resolve_status(client, BY_NAME["grok"], "blocked"), ("blocked", "herdr")
        )
        self.assertEqual(self.runner.calls, [])

    def test_stale_working_is_overturned_by_an_idle_footer(self):
        client = self._client(GROK_IDLE)
        warnings = []
        self.assertEqual(
            resolve_status(client, BY_NAME["grok"], "working", warn=warnings.append),
            ("idle", "probe"),
        )
        self.assertEqual(
            self.runner.commands(), ["agent read grok --source visible --lines 40"]
        )

    def test_overturning_warns_that_herdr_state_was_stale(self):
        client = self._client(GROK_IDLE)
        warnings = []
        resolve_status(client, BY_NAME["grok"], "working", warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])
        self.assertIn("grok", warnings[0])

    def test_genuine_working_is_confirmed_and_not_overturned(self):
        client = self._client(GROK_WORKING)
        warnings = []
        self.assertEqual(
            resolve_status(client, BY_NAME["grok"], "working", warn=warnings.append),
            ("working", "probe"),
        )
        self.assertEqual(warnings, [])

    def test_inconclusive_keeps_herdr_working_and_warns_nothing(self):
        client = self._client("some unrecognised screen\n")
        warnings = []
        self.assertEqual(
            resolve_status(client, BY_NAME["grok"], "working", warn=warnings.append),
            ("working", "herdr"),
        )
        self.assertEqual(warnings, [])

    def test_an_agent_with_no_markers_is_never_probed(self):
        client = self._client(GROK_IDLE)
        self.assertEqual(
            resolve_status(client, BY_NAME["bare"], "working"), ("working", "herdr")
        )
        self.assertEqual(self.runner.calls, [])

    def test_the_probe_only_reads_and_never_writes(self):
        client = self._client(GROK_IDLE)
        resolve_status(client, BY_NAME["grok"], "working", warn=lambda message: None)
        self.assertEqual(self.runner.writes(), [])


if __name__ == "__main__":
    unittest.main()
