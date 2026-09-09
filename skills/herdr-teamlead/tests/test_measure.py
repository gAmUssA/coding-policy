"""Tests for teamlead.measure. The herdr client is always a fake."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import inspect
import copy
import unittest

from teamlead.config import parse_config
from teamlead.errors import HerdrError, ParseError
from teamlead.herdr import HerdrClient
from teamlead.measure import DEFAULT_READ_LINES, ready_agents
from teamlead.measure import measure as _measure
from teamlead.measure import measure_agent as _measure_agent
from teamlead.measure import skipped_record
from teamlead.measure import wait_for_usage_report as _wait_for_usage_report

from tests.fakes import (
    FakeRunner,
    ScriptedReads,
    agent_json,
    composer_reads,
    composer_screen,
    ok_json,
)

AT = "2026-02-03T10:00:00+00:00"


def NO_SLEEP(seconds):
    """Stand-in for time.sleep. The fallback poll must never wait in tests."""


def measure_agent(client, agent, **kwargs):
    kwargs.setdefault("sleep", NO_SLEEP)
    return _measure_agent(client, agent, **kwargs)


def measure(client, agents, measured_at, **kwargs):
    kwargs.setdefault("sleep", NO_SLEEP)
    return _measure(client, agents, measured_at, **kwargs)


def wait_for_usage_report(client, agent, pane_id, **kwargs):
    kwargs.setdefault("sleep", NO_SLEEP)
    return _wait_for_usage_report(client, agent, pane_id, **kwargs)

CONFIG = {
    "schema_version": 1,
    "agents": [
        {
            "name": "claude",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "slash_delivery": "paste",
            "composer_glyph": "❯ ",
            "recover_keys": ["esc"],
            "close_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "slash_enter_count": 2,
            "composer_glyph": "› ",
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": [],
            "clear_prompt": "/new",
        },
        {
            "name": "grok",
            "kind": "grok",
            "usage_prompt": "/usage",
            "usage_marker": "Weekly limit",
            "usage_read_source": "visible",
            "slash_delivery": "type",
            "composer_glyph": "│ ❯",
            "recover_keys": ["esc"],
            "close_keys": ["esc"],
            "clear_prompt": "/new",
            "dialog_next_tab_keys": ["tab"],
            "idle_markers": ["Shift+Tab:mode"],
            "working_markers": ["Esc:cancel"],
        },
    ],
}

CLAUDE_PANE = (
    "   Current session\n   ████    8% used\n   Resets 12:49am (Europe/Oslo)\n"
    "   Current week (all models)\n   █    2% used\n   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
)
CODEX_PANE = (
    "│  Account: jbaruch@sadogursky.com (Pro)  │\n"
    "│  Weekly limit: [███] 87% left (resets 17:26 on 7 Sep)  │\n"
)
GROK_PANE = "     Weekly limit: 0%\n     Next reset: September 6, 12:55\n     Credits: $16.42\n"
# Marker present, numbers absent: gets past the marker wait, fails the parse.
GROK_UNPARSEABLE_PANE = "  Weekly limit is a thing this agent has, apparently\n"
CLAUDE_UNPARSEABLE_PANE = "  Current week was fine, no numbers though\n"
GROK_DIALOG_PANE = (
    "│  Weekly limit (X Premium+)      │\n"
    "│  ░░░░░░░░░░░░░░░░░░░░░░  1%     │\n"
    "│  Resets: September 6, 12:55     │\n"
    "│  Credits: $16.42                │\n"
)
# Footers the idle probe reads. Working is idle plus `Esc:cancel`.
GROK_IDLE_FOOTER = "  │ ❯          │\n  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"
GROK_WORKING_FOOTER = "  │ ❯ go       │\n  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"

AGENTS = parse_config(CONFIG)
BY_NAME = {agent.name: agent for agent in AGENTS}


def runner_with(statuses, panes, footers=None):
    """A FakeRunner scripted for the given agent statuses and pane texts.

    `footers` scripts the idle probe's read, which is a longer and therefore
    more specific argv prefix than the usage read.
    """
    footers = footers or {}
    runner = FakeRunner()
    panes_by_name = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
    for name, status in statuses.items():
        runner.set("agent get " + name, agent_json(name, status, panes_by_name[name]))
        runner.set("agent prompt " + name, ok_json("agent_prompt"))
        runner.set("agent send-keys " + name, ok_json("agent_send_keys"))
        if name in panes:
            runner.set("agent read " + name, panes[name])
        if name in footers:
            runner.set(
                "agent read {} --source visible --lines 40".format(name), footers[name]
            )
    runner.set("pane wait-output", ok_json("output_matched"))
    runner.set("pane send-text", ok_json("pane_send_text"))
    runner.set("pane send-keys", ok_json("pane_send_keys"))
    # The composer check: an empty composer before and after the command.
    for name in statuses:
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = composer_reads(name)
    return runner


class MeasureAgentTest(unittest.TestCase):
    def test_idle_claude_is_measured_and_the_dialog_is_closed(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["headroom_pct"], 92.0)
        self.assertFalse(record["skipped"])
        self.assertIn("agent send-keys claude esc", runner.commands())

    def test_the_full_command_sequence_is_what_herdr_documents(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"], marker_timeout_ms=20000)
        self.assertEqual(
            runner.commands(),
            [
                "agent get claude",
                "agent read claude --source visible --lines 20 --format ansi",
                "agent prompt claude /usage",
                "agent read claude --source visible --lines 20 --format ansi",
                "pane wait-output --match 'Current week' --source visible --timeout 20000 w2:p1",
                "agent read claude --source visible --lines 80",
                "agent send-keys claude esc",
            ],
        )

    def test_the_usage_command_is_sent_before_the_wait(self):
        # The wait can only ever see a report the command asked for; ordering
        # them the other way round guarantees a timeout.
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        commands = runner.commands()
        self.assertLess(
            commands.index("agent prompt claude /usage"),
            next(i for i, c in enumerate(commands) if c.startswith("pane wait-output")),
        )

    def test_grok_types_its_usage_command(self):
        # Live, `agent prompt grok /usage` pasted the text through bracketed
        # paste and Grok answered it as a chat message about billing.
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.pasted_prompts(), [])
        self.assertIn("pane send-text w4:p1 /usage", runner.commands())
        self.assertIn("pane send-keys w4:p1 enter", runner.commands())

    def test_claude_pastes_its_usage_command(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(runner.pasted_prompts(), ["claude /usage"])
        self.assertEqual([c for c in runner.commands() if c.startswith("pane send-")], [])

    def test_codex_types_its_usage_command(self):
        # Pasting worked until the live apply: the autocomplete popup ate the
        # Enter and `/new` sat unsent in the composer.
        runner = runner_with({"codex": "idle"}, {"codex": CODEX_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(runner.pasted_prompts(), [])
        self.assertIn("pane send-text w3:p1 /status", runner.commands())
        self.assertIn("pane send-keys w3:p1 enter", runner.commands())

    def test_each_kind_uses_the_path_its_config_names(self):
        expected = {
            "claude": ("agent prompt claude /usage", CLAUDE_PANE),
            "codex": ("pane send-text w3:p1 /status", CODEX_PANE),
            "grok": ("pane send-text w4:p1 /usage", GROK_PANE),
        }
        for name, (command, pane) in expected.items():
            runner = runner_with({name: "idle"}, {name: pane})
            measure_agent(HerdrClient(runner=runner), BY_NAME[name])
            self.assertIn(command, runner.commands(), name)

    def test_the_text_and_the_enter_are_separate_calls(self):
        # Enter has to be a keystroke; appending a newline to the text would
        # be pasted along with it.
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        commands = runner.commands()
        self.assertEqual(
            commands[commands.index("pane send-text w4:p1 /usage") + 1],
            "pane send-keys w4:p1 enter",
        )

    def test_the_marker_is_matched_literally_not_as_a_regex(self):
        # A marker is literal config text. Escaping it into a regex adds an
        # engine that can disagree about what an escaped space means; --match
        # cannot.
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        waits = [c for c in runner.commands() if c.startswith("pane wait-output")]
        self.assertEqual(len(waits), 1)
        self.assertIn("--match 'Current week'", waits[0])
        self.assertNotIn("--regex", waits[0])

    def test_visible_reads_still_ask_for_a_line_count(self):
        # The read that works by hand names --lines explicitly; herdr's
        # default could clip the viewport short of the numbers.
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertIn(
            "agent read claude --source visible --lines {}".format(DEFAULT_READ_LINES),
            runner.commands(),
        )

    def test_grok_dialog_format_is_measured_the_same_as_the_inline_one(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(record["plan"], "X Premium+")
        self.assertEqual(record["credits"], 16.42)
        self.assertIn("agent send-keys grok esc", runner.commands())

    def test_scrollback_reads_ask_for_lines(self):
        runner = runner_with({"codex": "idle"}, {"codex": CODEX_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertIn(
            "agent read codex --source recent-unwrapped --lines {}".format(DEFAULT_READ_LINES),
            runner.commands(),
        )

    def test_agent_without_close_keys_gets_no_keystrokes(self):
        # codex prints its report inline and opens no dialog, so it configures
        # no close_keys and must receive no keystrokes.
        runner = runner_with({"codex": "idle"}, {"codex": CODEX_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent send-keys")], []
        )

    def test_grok_credits_are_carried_through(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["credits"], 16.42)
        self.assertEqual(record["headroom_pct"], 100.0)

    def test_done_counts_as_ready(self):
        runner = runner_with({"grok": "done"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["state"], "done")
        self.assertEqual(record["headroom_pct"], 100.0)


class BusyAgentTest(unittest.TestCase):
    def test_working_agent_is_skipped_without_a_single_write(self):
        runner = runner_with({"codex": "working"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertEqual(record["state"], "working")
        self.assertIsNone(record["windows"])
        self.assertIsNone(record["headroom_pct"])
        self.assertTrue(record["skipped"])
        self.assertEqual(runner.writes(), [])
        self.assertEqual(runner.commands(), ["agent get codex"])

    def test_blocked_agent_is_skipped_too(self):
        runner = runner_with({"codex": "blocked"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertTrue(record["skipped"])
        self.assertEqual(runner.writes(), [])

    def test_a_working_agent_has_no_override(self):
        # The usage command is a prompt. There is no argument that sends it to
        # an agent mid-turn (rules/agent-team-operation.md Dispatch Safety).
        runner = runner_with({"codex": "working"}, {"codex": CODEX_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertTrue(record["skipped"])
        self.assertEqual(runner.writes(), [])
        self.assertNotIn("force", inspect.signature(measure_agent).parameters)
        self.assertNotIn("force", inspect.signature(measure).parameters)

    def test_unknown_status_is_measured_rather_than_skipped(self):
        # `unknown` does not prove the agent is busy, and herdr's own prompt
        # path rejects a truly blocked agent before sending anything.
        runner = runner_with({"grok": "unknown"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertFalse(record["skipped"])


class ProbeIntegrationTest(unittest.TestCase):
    """herdr's title-derived state goes stale; the pane is the tie-breaker."""

    def test_stale_working_is_overturned_and_the_agent_is_measured(self):
        runner = runner_with(
            {"grok": "working"}, {"grok": GROK_PANE}, footers={"grok": GROK_IDLE_FOOTER}
        )
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], warn=lambda message: None
        )
        self.assertFalse(record["skipped"])
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["herdr_state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(record["headroom_pct"], 100.0)

    def test_the_overturn_is_warned_about_on_stderr(self):
        runner = runner_with(
            {"grok": "working"}, {"grok": GROK_PANE}, footers={"grok": GROK_IDLE_FOOTER}
        )
        warnings = []
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"], warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])

    def test_a_genuinely_working_agent_is_still_skipped(self):
        runner = runner_with(
            {"grok": "working"}, {}, footers={"grok": GROK_WORKING_FOOTER}
        )
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(runner.writes(), [])

    def test_an_inconclusive_probe_keeps_the_refusal(self):
        runner = runner_with(
            {"grok": "working"}, {}, footers={"grok": "some other program\n"}
        )
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(runner.writes(), [])

    def test_a_blocked_agent_is_never_probed(self):
        runner = runner_with({"grok": "blocked"}, {})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertTrue(record["skipped"])
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(runner.commands(), ["agent get grok"])

    def test_an_idle_agent_is_not_probed_at_all(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["state_source"], "herdr")
        # 40 lines is the probe's read; 20 is the composer check, which always
        # runs. Distinct counts keep the two apart in a --trace log.
        self.assertNotIn("agent read grok --source visible --lines 40", runner.commands())


class MarkerWaitFallbackTest(unittest.TestCase):
    """Regression: a `pane wait-output` that never delivers used to be fatal.

    Live, grok's modal opened but the wait timed out against it, and the
    measurement failed on the wait alone -- without ever trying the
    prompt-then-read sequence that works by hand.
    """

    def _timing_out_runner(self, pane):
        runner = runner_with({"grok": "idle"}, {"grok": pane})
        runner.set(
            "pane wait-output",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"timeout","message":"timed out waiting for output match"}}',
        )
        return runner

    def test_a_timed_out_wait_falls_back_to_reading_and_succeeds(self):
        runner = self._timing_out_runner(GROK_DIALOG_PANE)
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], warn=lambda message: None
        )
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(record["plan"], "X Premium+")

    def test_the_fallback_is_warned_about_and_names_the_command_that_failed(self):
        runner = self._timing_out_runner(GROK_DIALOG_PANE)
        warnings = []
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"], warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("pane wait-output", warnings[0])
        self.assertIn("agent read", warnings[0])

    def test_a_successful_wait_polls_no_further(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        # Usage reads only: the composer check reads 20 lines, not 80.
        reads = [
            c
            for c in runner.commands()
            if c.startswith("agent read grok --source visible --lines 80")
        ]
        self.assertEqual(len(reads), 1)

    def test_the_poll_is_bounded_and_then_fails_loudly(self):
        runner = self._timing_out_runner("no report here\n")
        with self.assertRaises(HerdrError) as caught:
            measure_agent(
                HerdrClient(runner=runner),
                BY_NAME["grok"],
                poll_attempts=3,
                max_tabs=0,
                warn=lambda message: None,
            )
        reads = [
            c
            for c in runner.commands()
            if c.startswith("agent read grok --source visible --lines 80")
        ]
        self.assertEqual(len(reads), 4)  # the first read plus three retries
        self.assertIn("--trace", str(caught.exception))

    def test_a_marker_that_arrives_late_is_still_caught(self):
        # The dialog takes two reads to paint; the third read sees it.
        runner = self._timing_out_runner("still rendering\n")
        runner.responses["agent read grok --source visible --lines 80"] = ScriptedReads(
            ["still rendering\n", "still rendering\n", GROK_DIALOG_PANE]
        )
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], warn=lambda message: None
        )
        self.assertEqual(record["headroom_pct"], 99.0)
        reads = [c for c in runner.commands() if c.startswith("agent read grok --source visible --lines 80")]
        self.assertEqual(len(reads), 3)

    def test_the_command_is_never_re_sent_by_the_fallback(self):
        # Re-sending would toggle the dialog shut. Only reads may repeat.
        runner = self._timing_out_runner(GROK_DIALOG_PANE)
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"], warn=lambda m: None)
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("pane send-text")],
            ["pane send-text w4:p1 /usage"],
        )


class SilentPaneCommandTest(unittest.TestCase):
    """Regression: send-text exits 0 with empty stdout, and that is success.

    Live, the strict JSON check turned that into a transport error and the run
    stopped between typing `/usage` and pressing Enter.
    """

    def _silent_runner(self, pane=GROK_DIALOG_PANE):
        runner = runner_with({"grok": "idle"}, {"grok": pane})
        # Overwrite the JSON stubs with what herdr actually does.
        runner.set("pane send-text", stdout="")
        runner.set("pane send-keys", stdout="")
        runner.set("agent send-keys", stdout="")
        return runner

    def test_the_enter_is_still_sent_after_a_silent_send_text(self):
        runner = self._silent_runner()
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        commands = runner.commands()
        self.assertEqual(
            commands[commands.index("pane send-text w4:p1 /usage") + 1],
            "pane send-keys w4:p1 enter",
        )

    def test_measurement_proceeds_to_a_parsed_result(self):
        runner = self._silent_runner()
        record = measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(record["plan"], "X Premium+")

    def test_the_dialog_is_still_dismissed(self):
        runner = self._silent_runner()
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_the_snapshot_records_no_failure(self):
        runner = self._silent_runner()
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], [])
        self.assertNotIn("error", snapshot["agents"]["grok"])

    def test_a_real_send_text_failure_still_stops_the_run(self):
        runner = self._silent_runner()
        runner.set(
            "pane send-text",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"pane_not_found","message":"pane not found"}}',
        )
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["grok"])
        # Nothing was typed, so no Enter follows it.
        self.assertNotIn("pane send-keys w4:p1 enter", runner.commands())


class UnconsumedUsageCommandTest(unittest.TestCase):
    """A usage command left in the composer is a failure, not a slow report."""

    def _runner(self, name, screens, pane):
        runner = runner_with({name: "idle"}, {name: pane})
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def test_a_stuck_usage_command_fails_the_agent(self):
        runner = self._runner(
            "codex",
            [composer_screen("codex"), composer_screen("codex", held="/status")],
            CODEX_PANE,
        )
        with self.assertRaises(HerdrError) as caught:
            measure_agent(
                HerdrClient(runner=runner), BY_NAME["codex"], warn=lambda message: None
            )
        self.assertIn("/status", str(caught.exception))

    def test_it_never_reaches_the_wait_or_the_read(self):
        runner = self._runner(
            "codex",
            [composer_screen("codex"), composer_screen("codex", held="/status")],
            CODEX_PANE,
        )
        with self.assertRaises(HerdrError):
            measure_agent(
                HerdrClient(runner=runner), BY_NAME["codex"], warn=lambda message: None
            )
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("pane wait-output")], []
        )

    def test_an_extra_enter_rescues_it(self):
        runner = self._runner(
            "codex",
            [
                composer_screen("codex"),
                composer_screen("codex", held="/status"),
                composer_screen("codex", "status box"),
            ],
            CODEX_PANE,
        )
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["codex"], warn=lambda message: None
        )
        self.assertEqual(record["headroom_pct"], 87.0)
        # codex's configured 2, plus 1 more once the command was still shown
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 3
        )

    def test_the_snapshot_records_the_failure_for_that_agent(self):
        runner = self._runner(
            "codex",
            [composer_screen("codex"), composer_screen("codex", held="/status")],
            CODEX_PANE,
        )
        snapshot = measure(
            HerdrClient(runner=runner),
            [BY_NAME["codex"]],
            AT,
            warn=lambda message: None,
        )
        self.assertEqual(snapshot["failed_agents"], ["codex"])
        self.assertEqual(snapshot["agents"]["codex"]["error"]["code"], "herdr_error")


class DialogTabTest(unittest.TestCase):
    """A usage dialog can open on the wrong tab.

    Grok's has three (Context usage / Usage limit / Session info). It landed on
    the right one both live runs, but a dialog that opens elsewhere would look
    exactly like a missing report.
    """

    def _runner(self, texts):
        runner = runner_with({"grok": "idle"}, {})
        # The usage read is scripted per test; the composer read is not it.
        # A ScriptedReads is its own response object, not a stdout string.
        runner.responses["agent read grok --source visible --lines 80"] = ScriptedReads(
            texts
        )
        return runner

    def test_a_report_behind_one_tab_is_found(self):
        runner = self._runner(["wrong tab\n", GROK_DIALOG_PANE])
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], poll_attempts=0
        )
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent send-keys grok tab")],
            ["agent send-keys grok tab"],
        )

    def test_a_report_behind_two_tabs_is_found(self):
        runner = self._runner(["wrong\n", "still wrong\n", GROK_DIALOG_PANE])
        record = measure_agent(
            HerdrClient(runner=runner), BY_NAME["grok"], poll_attempts=0
        )
        self.assertEqual(record["headroom_pct"], 99.0)
        self.assertEqual(
            len([c for c in runner.commands() if c == "agent send-keys grok tab"]), 2
        )

    def test_tabbing_is_bounded_at_three(self):
        runner = self._runner(["never here\n"])
        with self.assertRaises(HerdrError) as caught:
            measure_agent(
                HerdrClient(runner=runner),
                BY_NAME["grok"],
                poll_attempts=0,
                warn=lambda message: None,
            )
        self.assertEqual(
            len([c for c in runner.commands() if c == "agent send-keys grok tab"]), 3
        )
        self.assertIn("Tabbed through the dialog 3 times", str(caught.exception))

    def test_no_tabbing_when_the_marker_is_already_there(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_DIALOG_PANE})
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(
            [c for c in runner.commands() if c == "agent send-keys grok tab"], []
        )

    def test_an_agent_with_no_tab_keys_never_tabs(self):
        # claude configures none; a missing report there is just a failure.
        runner = runner_with({"claude": "idle"}, {"claude": "no report\n"})
        with self.assertRaises(HerdrError):
            measure_agent(
                HerdrClient(runner=runner),
                BY_NAME["claude"],
                poll_attempts=0,
                warn=lambda message: None,
            )
        self.assertEqual(
            [c for c in runner.commands() if c.endswith(" tab")], []
        )

    def test_the_dialog_is_still_dismissed_after_tabbing_fails(self):
        runner = self._runner(["never here\n"])
        with self.assertRaises(HerdrError):
            measure_agent(
                HerdrClient(runner=runner),
                BY_NAME["grok"],
                poll_attempts=0,
                warn=lambda message: None,
            )
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_tabbing_happens_after_the_read_poll_not_before(self):
        # Reading again is free; pressing keys into somebody's dialog is not.
        runner = self._runner(["wrong tab\n", "wrong tab\n", GROK_DIALOG_PANE])
        measure_agent(HerdrClient(runner=runner), BY_NAME["grok"], poll_attempts=1)
        commands = runner.commands()
        reads_before_first_tab = commands.index("agent send-keys grok tab")
        self.assertEqual(
            len([c for c in commands[:reads_before_first_tab] if c.startswith("agent read grok --source visible --lines 80")]),
            2,
        )


class DialogAlwaysClosesTest(unittest.TestCase):
    """A usage dialog left open flips the agent to `working` and eats the
    next prompt, so the close keys must go out on every failure path."""

    def test_close_keys_are_sent_when_parsing_fails(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_UNPARSEABLE_PANE})
        with self.assertRaises(ParseError):
            measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_close_keys_are_sent_when_the_marker_never_appears(self):
        runner = runner_with({"grok": "idle"}, {"grok": "nothing useful here\n"})
        with self.assertRaises(HerdrError):
            measure_agent(
                HerdrClient(runner=runner), BY_NAME["grok"], poll_attempts=2, max_tabs=0
            )
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_close_keys_are_sent_when_the_read_itself_fails(self):
        runner = runner_with({"grok": "idle"}, {})
        runner.set(
            "agent read grok",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"pane_gone","message":"pane not found"}}',
        )
        with self.assertRaises(HerdrError):
            measure_agent(HerdrClient(runner=runner), BY_NAME["grok"])
        self.assertEqual(runner.commands()[-1], "agent send-keys grok esc")

    def test_the_failure_still_surfaces_on_the_snapshot(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_UNPARSEABLE_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["grok"])
        self.assertEqual(snapshot["agents"]["grok"]["error"]["code"], "parse_error")
        self.assertIn("agent send-keys grok esc", runner.commands())


class FailureTest(unittest.TestCase):
    def test_unparseable_pane_still_closes_the_dialog(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_UNPARSEABLE_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["claude"])
        self.assertEqual(snapshot["agents"]["claude"]["error"]["code"], "parse_error")
        self.assertIn("agent send-keys claude esc", runner.commands())

    def test_a_herdr_failure_is_recorded_on_that_agent_only(self):
        runner = runner_with({"claude": "idle", "grok": "idle"}, {"grok": GROK_PANE})
        runner.set("agent prompt claude", stdout="", returncode=1, stderr='{"error":{"code":"agent_blocked","message":"blocked"}}')
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"], BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["claude"])
        self.assertEqual(snapshot["agents"]["claude"]["error"]["code"], "herdr_error")
        self.assertEqual(snapshot["agents"]["grok"]["headroom_pct"], 100.0)

    def test_missing_pane_id_is_an_error_not_a_crash(self):
        runner = FakeRunner()
        runner.set(
            "agent get grok",
            '{"id":"cli:agent:get","result":{"agent":{"name":"grok","agent_status":"idle"}}}',
        )
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], AT)
        self.assertEqual(snapshot["failed_agents"], ["grok"])
        self.assertIn("no pane", snapshot["agents"]["grok"]["error"]["message"])
        self.assertEqual(runner.writes(), [])


class SnapshotTest(unittest.TestCase):
    def test_snapshot_shape(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        self.assertEqual(snapshot["schema_version"], 3)
        self.assertEqual(snapshot["measured_at"], AT)
        self.assertEqual(sorted(snapshot["agents"]), ["claude", "codex", "grok"])
        self.assertEqual(snapshot["failed_agents"], [])

    def test_busy_agent_appears_with_nulls_rather_than_being_dropped(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        codex = snapshot["agents"]["codex"]
        self.assertEqual(codex["state"], "working")
        self.assertIsNone(codex["windows"])
        self.assertIsNone(codex["headroom_pct"])

    def test_measured_at_is_taken_from_the_caller_not_the_clock(self):
        runner = runner_with({"grok": "idle"}, {"grok": GROK_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["grok"]], "1999-01-01T00:00:00+00:00")
        self.assertEqual(snapshot["measured_at"], "1999-01-01T00:00:00+00:00")

    def test_windows_survive_into_the_snapshot(self):
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        snapshot = measure(HerdrClient(runner=runner), [BY_NAME["claude"]], AT)
        windows = snapshot["agents"]["claude"]["windows"]
        self.assertEqual(windows["Current session"]["used_pct"], 8.0)
        self.assertEqual(windows["Current week (all models)"]["remaining_pct"], 98.0)

    def test_ready_agents_lists_only_the_settled_ones(self):
        runner = runner_with(
            {"claude": "idle", "codex": "working", "grok": "done"},
            {"claude": CLAUDE_PANE, "grok": GROK_PANE},
        )
        snapshot = measure(HerdrClient(runner=runner), AGENTS, AT)
        self.assertEqual(ready_agents(snapshot), ["claude", "grok"])


class WindowGroupPassthroughTest(unittest.TestCase):
    """`plan` reads a shared window off the snapshot, so `measure` writes it.

    Config declares which workers share a usage window; the planner charges a
    seat's cost against every worker in that window. The snapshot is the only
    thing passed between them, so a group that does not survive the hop
    silently restores the over-commit the group exists to prevent.
    """

    def _agent_with_group(self, group):
        payload = copy.deepcopy(CONFIG)
        payload["agents"] = [
            entry for entry in payload["agents"] if entry["name"] == "claude"
        ]
        payload["agents"][0]["window_group"] = group
        return parse_config(payload)[0]

    def test_a_measured_record_carries_the_declared_group(self):
        agent = self._agent_with_group("claude-max-weekly")
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        record = measure_agent(HerdrClient(runner=runner), agent)
        self.assertEqual(record["window_group"], "claude-max-weekly")

    def test_a_skipped_record_carries_it_too(self):
        # A pool-mate teamlead declined to interrupt still shares the window;
        # dropping the group here would unlink it for the rest of the round.
        agent = self._agent_with_group("claude-max-weekly")
        record = skipped_record(agent, "working", "working", "visible")
        self.assertEqual(record["window_group"], "claude-max-weekly")
        self.assertTrue(record["skipped"])

    def test_a_failed_measurement_keeps_its_window_group(self):
        # Pool membership is not a measurement result; dropping it on failure
        # unlinks the worker from its window, and affordability reads the
        # minimum across that window.
        agent = self._agent_with_group("claude-max-weekly")
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        # `agent get` is the first call on every agent's path, whatever its
        # slash delivery, so failing it exercises the error record directly.
        runner.set(
            "agent get claude",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"agent_not_found","message":"no such agent"}}',
        )
        snapshot_doc = measure(HerdrClient(runner=runner), [agent], AT)
        self.assertEqual(snapshot_doc["failed_agents"], ["claude"])
        record = snapshot_doc["agents"]["claude"]
        self.assertIn("error", record)
        self.assertEqual(record["window_group"], "claude-max-weekly")

    def test_an_agent_declaring_no_group_records_an_empty_one(self):
        agent = self._agent_with_group("")
        runner = runner_with({"claude": "idle"}, {"claude": CLAUDE_PANE})
        record = measure_agent(HerdrClient(runner=runner), agent)
        self.assertEqual(record["window_group"], "")


if __name__ == "__main__":
    unittest.main()
