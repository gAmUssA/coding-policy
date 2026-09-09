"""Tests for teamlead.composer.

The live failure this module exists to prevent: `agent prompt codex /new`
pasted `/new` into Codex's composer, the slash-autocomplete popup swallowed
the Enter, `agent wait --until idle` returned instantly because the agent had
never left idle, and the assignment was then pasted onto the unsent text.
Codex received `/newNew assignment from the team lead...` and rejected it.
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

from teamlead.composer import (
    DispatchSession,
    checkable,
    command_still_present,
    composer_text,
    ensure_ready,
    read_pane,
    screen_signature,
    inspect_composer,
    is_placeholder,
    recovery_allowed,
    send_command,
    send_message,
    strip_ansi,
    transcript_holds,
    unknown_skill_error,
)
from teamlead.config import parse_config
from teamlead.errors import HerdrError
from teamlead.herdr import HerdrClient

from tests.fakes import FakeRunner, ScriptedReads, composer_screen, ok_json


def NO_SLEEP(seconds):
    """Stand-in for time.sleep; nothing here waits on a real clock."""


CONFIG = {
    "schema_version": 1,
    "agents": [
        {
            "name": "codex",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": [],
            "clear_prompt": "/new",
        },
        {
            "name": "recoverable",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "composer_placeholders": ["Ask Codex to do anything"],
            "recover_keys": ["esc"],
            "clear_prompt": "/new",
        },
        {
            "name": "twoenter",
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
            "name": "blind",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "clear_prompt": "/new",
        },
        {
            "name": "claude",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "slash_delivery": "paste",
            "composer_glyph": "❯ ",
            "composer_ignore_dim": True,
            "recover_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "strict",
            "kind": "claude",
            "usage_prompt": "/usage",
            "usage_marker": "Current week",
            "usage_read_source": "visible",
            "slash_delivery": "paste",
            "composer_glyph": "❯ ",
            "composer_ignore_dim": False,
            "recover_keys": ["esc"],
            "clear_prompt": "/clear",
        },
        {
            "name": "stubborn",
            "kind": "codex",
            "usage_prompt": "/status",
            "usage_marker": "Weekly limit",
            "usage_read_source": "recent-unwrapped",
            "slash_delivery": "type",
            "composer_glyph": "› ",
            "clear_prompt": "/new",
        },
    ],
}
BY_NAME = {agent.name: agent for agent in parse_config(CONFIG)}

# The live rows, as Codex draws them.
CODEX_EMPTY = "  Codex v1.2  ~/Projects/x\n  ─────────────\n  › \n"
CODEX_HELD = "  Codex v1.2  ~/Projects/x\n  ─────────────\n  › /new\n"
CODEX_FRESH = "  ╭─ Codex ─╮\n  │ new session │\n  ╰─────────╯\n  › \n"


class ComposerTextTest(unittest.TestCase):
    def test_an_empty_composer_reads_as_empty_string(self):
        self.assertEqual(composer_text(CODEX_EMPTY, "› "), "")

    def test_a_held_command_is_returned(self):
        self.assertEqual(composer_text(CODEX_HELD, "› "), "/new")

    def test_the_appended_text_from_the_live_failure_is_visible(self):
        text = "  › /newNew assignment from the team lead. Your role is TESTER.\n"
        self.assertEqual(
            composer_text(text, "› "),
            "/newNew assignment from the team lead. Your role is TESTER.",
        )

    def test_the_last_composer_row_wins(self):
        self.assertEqual(composer_text(CODEX_HELD + CODEX_EMPTY, "› "), "")

    def test_a_missing_glyph_reads_as_unknowable(self):
        self.assertIsNone(composer_text("no glyph anywhere\n", "› "))

    def test_an_unconfigured_glyph_reads_as_unknowable(self):
        self.assertIsNone(composer_text(CODEX_HELD, ""))

    def test_grok_box_borders_are_stripped_from_the_remainder(self):
        self.assertEqual(composer_text("  │ ❯                    │\n", "│ ❯"), "")
        self.assertEqual(composer_text("  │ ❯ /usage             │\n", "│ ❯"), "/usage")

    def test_claude_composer(self):
        self.assertEqual(composer_text("────\n❯ /clear\n────\n", "❯ "), "/clear")
        self.assertEqual(composer_text("────\n❯\n────\n", "❯ "), "")

    def test_checkable_follows_the_glyph(self):
        self.assertTrue(checkable(BY_NAME["codex"]))
        self.assertFalse(checkable(BY_NAME["blind"]))


class ScreenSignatureTest(unittest.TestCase):
    def test_the_composer_row_is_excluded(self):
        self.assertEqual(
            screen_signature(CODEX_EMPTY, "› "), screen_signature(CODEX_HELD, "› ")
        )

    def test_a_fresh_session_banner_is_a_different_signature(self):
        self.assertNotEqual(
            screen_signature(CODEX_EMPTY, "› "), screen_signature(CODEX_FRESH, "› ")
        )

    def test_blank_rows_do_not_count_as_change(self):
        self.assertEqual(
            screen_signature("a\n\n\nb\n", "› "), screen_signature("a\nb\n", "› ")
        )


class ReadPaneTest(unittest.TestCase):
    def test_an_agent_with_no_glyph_is_never_read(self):
        runner = FakeRunner()
        self.assertEqual(
            read_pane(HerdrClient(runner=runner), BY_NAME["blind"]), ("", False)
        )
        self.assertEqual(runner.calls, [])

    def test_an_agent_with_a_glyph_reads_its_viewport(self):
        runner = FakeRunner()
        runner.set("agent read codex", CODEX_EMPTY)
        text, ansi = read_pane(HerdrClient(runner=runner), BY_NAME["codex"])
        self.assertTrue(ansi)
        self.assertEqual(
            runner.commands(),
            ["agent read codex --source visible --lines 20 --format ansi"],
        )


class EnsureReadyTest(unittest.TestCase):
    """Recovery keys are the most dangerous thing teamlead can send.

    On Codex the key that clears a composer is ctrl+c, and ctrl+c on an EMPTY
    Codex composer exits the process. Live, teamlead read Codex's placeholder
    `Ask Codex to do anything` as typed text, sent the one recovery ctrl+c,
    and killed the agent. Every gate below exists because of that.
    """

    def _runner(self, screens, name="recoverable"):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def _ready(self, runner, name="recoverable", **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return ensure_ready(HerdrClient(runner=runner), BY_NAME[name], **kwargs)

    def test_an_empty_composer_needs_no_recovery(self):
        runner = self._runner([CODEX_EMPTY])
        self._ready(runner)
        self.assertEqual(runner.writes(), [])

    def test_text_teamlead_did_not_type_is_refused_not_cleared(self):
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner)
        self.assertIn("--allow-recovery", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_command_teamlead_typed_this_run_may_be_cleared(self):
        session = DispatchSession()
        session.remember("/new")
        runner = self._runner([CODEX_HELD, CODEX_EMPTY])
        self._ready(runner, session=session)
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_allow_recovery_opts_in_to_clearing_a_strangers_text(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY])
        self._ready(runner, session=DispatchSession(allow_recovery=True))
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_recovery_is_sent_exactly_once_even_when_it_fails(self):
        # A second ctrl+c would exit Codex, so this must never loop.
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError):
            self._ready(runner, session=DispatchSession(allow_recovery=True))
        self.assertEqual(runner.writes(), ["agent send-keys recoverable esc"])

    def test_the_refusal_names_the_pane_and_the_text(self):
        runner = self._runner([CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner, pane_id="w3:p1")
        message = str(caught.exception)
        self.assertIn("/new", message)
        self.assertIn("w3:p1", message)

    def test_an_agent_with_no_recover_keys_refuses_without_sending(self):
        runner = self._runner([CODEX_HELD], name="codex")
        with self.assertRaises(HerdrError) as caught:
            self._ready(runner, name="codex", session=DispatchSession(allow_recovery=True))
        self.assertIn("recover_keys", str(caught.exception))
        self.assertIn("exits the process", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_a_caller_supplied_read_is_reused(self):
        runner = self._runner([CODEX_EMPTY])
        self._ready(runner, text=CODEX_EMPTY)
        self.assertEqual(runner.calls, [])


class SendCommandTest(unittest.TestCase):
    def _runner(self, screens, name="codex"):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def _send(self, runner, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_command(
            HerdrClient(runner=runner), BY_NAME["codex"], "w3:p1", "/new", **kwargs
        )

    def test_a_consumed_command_needs_no_second_enter(self):
        result = self._send(self._runner([CODEX_EMPTY, CODEX_FRESH]))
        self.assertTrue(result["consumed"])
        self.assertEqual(result["extra_enters"], 0)
        self.assertFalse(result["recovered"])
        self.assertTrue(result["screen_changed"])

    def test_the_autocomplete_popup_costs_a_second_enter(self):
        # First Enter accepts the completion; the second submits.
        runner = self._runner([CODEX_EMPTY, CODEX_HELD, CODEX_FRESH])
        result = self._send(runner)
        self.assertEqual(result["extra_enters"], 1)
        self.assertTrue(result["consumed"])
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 2
        )

    def test_live_guard_refuses_an_extra_enter_after_the_initial_delivery(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])

        def guard():
            if len(runner.writes()) >= 2:
                raise HerdrError("Foreground worker changed; inspect the pane before sending more input.", {})

        with self.assertRaisesRegex(HerdrError, "Foreground worker changed"):
            self._send(runner, before_input=guard)
        self.assertEqual(runner.writes(), ["pane send-text w3:p1 /new", "pane send-keys w3:p1 enter"])

    def test_a_command_that_will_not_submit_is_refused(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError) as caught:
            self._send(runner)
        message = str(caught.exception)
        self.assertIn("/new", message)
        self.assertIn("Nothing further was sent", message)
        self.assertIn("slash_enter_count", message)

    def test_the_extra_enters_are_bounded(self):
        runner = self._runner([CODEX_EMPTY, CODEX_HELD])
        with self.assertRaises(HerdrError):
            self._send(runner)
        # one from delivery, then MAX_EXTRA_ENTERS more, and no further
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 3
        )

    def test_a_composer_holding_a_strangers_text_refuses_before_sending(self):
        runner = self._runner([CODEX_HELD, CODEX_EMPTY, CODEX_FRESH])
        with self.assertRaises(HerdrError):
            self._send(runner)
        self.assertEqual(runner.writes(), [])

    def test_a_composer_holding_teamleads_own_command_is_recovered_first(self):
        session = DispatchSession()
        session.remember("/new")
        runner = self._runner([CODEX_HELD, CODEX_EMPTY, CODEX_FRESH], name="recoverable")
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["recoverable"],
            "w3:p1",
            "/new",
            session=session,
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["recovered"])
        self.assertTrue(result["consumed"])
        self.assertEqual(runner.commands()[1], "agent send-keys recoverable esc")

    def test_an_unchanged_screen_is_reported_not_assumed(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        result = self._send(runner, screen_attempts=2)
        self.assertTrue(result["consumed"])
        self.assertFalse(result["screen_changed"])

    def test_the_screen_change_wait_is_bounded(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        self._send(runner, screen_attempts=2)
        reads = [c for c in runner.commands() if c.startswith("agent read codex")]
        self.assertEqual(len(reads), 4)  # before, after, then two re-reads

    def test_zero_attempts_skips_the_screen_change_wait(self):
        runner = self._runner([CODEX_EMPTY, CODEX_EMPTY])
        self._send(runner, screen_attempts=0)
        reads = [c for c in runner.commands() if c.startswith("agent read codex")]
        self.assertEqual(len(reads), 2)

    def test_an_agent_with_no_glyph_is_sent_the_command_and_not_checked(self):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["blind"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["consumed"])
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent read")], []
        )




# --- Claude Code's dim ghost-text suggestion --------------------------------
#
# Nobody typed these. Claude Code pre-fills its input box after a task; Esc
# does not remove it, and the next thing typed replaces it.

DIM = "\x1b[2m"
GREY = "\x1b[38;5;242m"
BRIGHT_BLACK = "\x1b[90m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"
SUGGESTION = "check the other issues (#28, #30) for follow-up work"

CLAUDE_SUGGESTION = "  ───────\n  ❯ {}{}{}\n  ───────\n".format(DIM, SUGGESTION, RESET)
CLAUDE_GREY_SUGGESTION = "  ❯ {}{}{}\n".format(GREY, SUGGESTION, RESET)
CLAUDE_BRIGHT_BLACK = "  ❯ {}{}{}\n".format(BRIGHT_BLACK, SUGGESTION, RESET)
CLAUDE_TYPED = "  ❯ {}{}{}\n".format(BOLD, SUGGESTION, RESET)
CLAUDE_TYPED_AFTER = "  ❯ {}{}{}/clear\n".format(DIM, SUGGESTION, RESET)


class DimSuggestionTest(unittest.TestCase):
    """A dim suggestion is an empty composer wearing a costume."""

    def test_a_dim_suggestion_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_SUGGESTION, "❯ ", True), "")

    def test_a_grey_palette_suggestion_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_GREY_SUGGESTION, "❯ ", True), "")

    def test_bright_black_reads_as_empty(self):
        self.assertEqual(composer_text(CLAUDE_BRIGHT_BLACK, "❯ ", True), "")

    def test_the_same_text_in_bold_is_occupied(self):
        self.assertEqual(composer_text(CLAUDE_TYPED, "❯ ", True), SUGGESTION)

    def test_the_same_text_at_normal_weight_is_occupied(self):
        plain = "  ❯ {}\n".format(SUGGESTION)
        self.assertEqual(composer_text(plain, "❯ ", True), SUGGESTION)

    def test_a_typed_command_after_a_suggestion_is_occupied(self):
        self.assertEqual(composer_text(CLAUDE_TYPED_AFTER, "❯ ", True), "/clear")

    def test_without_the_setting_a_suggestion_still_counts(self):
        # A runtime with no ghost text must be unaffected.
        self.assertEqual(composer_text(CLAUDE_SUGGESTION, "❯ ", False), SUGGESTION)

    def test_the_earlier_live_suggestion_reads_as_empty_too(self):
        text = "  ❯ {}Wait for the architect's design note and phase-2 prompt{}\n".format(
            DIM, RESET
        )
        self.assertEqual(composer_text(text, "❯ ", True), "")

    def test_plain_text_with_no_escapes_is_unchanged(self):
        self.assertEqual(composer_text("  ❯ /clear\n", "❯ ", True), "/clear")

    def test_strip_ansi_leaves_only_visible_characters(self):
        self.assertEqual(strip_ansi("{}dim{}".format(DIM, RESET)), "dim")

    def test_the_screen_signature_ignores_styling(self):
        self.assertEqual(
            screen_signature("{}banner{}\n".format(DIM, RESET), "❯ "),
            screen_signature("banner\n", "❯ "),
        )


class DimAgentTest(unittest.TestCase):
    """The setting is per agent, and it reaches the dispatch gate."""

    def _runner(self, name, screens):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads(screens)
        return runner

    def test_a_suggestion_does_not_trigger_recovery(self):
        runner = self._runner("claude", [CLAUDE_SUGGESTION])
        ensure_ready(HerdrClient(runner=runner), BY_NAME["claude"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_the_same_pane_refuses_an_agent_that_does_not_ignore_dim(self):
        runner = self._runner("strict", [CLAUDE_SUGGESTION])
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["strict"],
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )

    def test_dim_text_is_never_cleared_even_with_allow_recovery(self):
        # Nobody typed it, so no key is warranted whatever the operator asked
        # for. This is the gate that would have saved the Codex process.
        runner = self._runner("strict", [CLAUDE_SUGGESTION])
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["strict"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertIn("dim", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_the_read_asks_for_ansi(self):
        runner = self._runner("claude", [CLAUDE_SUGGESTION])
        read_pane(HerdrClient(runner=runner), BY_NAME["claude"])
        self.assertEqual(
            runner.commands(),
            ["agent read claude --source visible --lines 20 --format ansi"],
        )

    def _fallback_runner(self, plain):
        runner = FakeRunner()
        runner.set(
            "agent read claude --source visible --lines 20 --format ansi",
            stdout="",
            returncode=2,
            stderr="unexpected argument '--format'",
        )
        runner.set("agent read claude --source visible --lines 20", plain)
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        return runner

    def test_a_failed_ansi_read_falls_back_to_plain_text(self):
        runner = self._fallback_runner("  ❯ /clear\n")
        warnings = []
        text, ansi = read_pane(
            HerdrClient(runner=runner), BY_NAME["claude"], warn=warnings.append
        )
        self.assertFalse(ansi)
        self.assertEqual(composer_text(text, "❯ ", True), "/clear")
        self.assertTrue(any("plain-text" in w for w in warnings))

    def test_the_plain_text_fallback_can_only_refuse_never_recover(self):
        # Without intensity, a dim placeholder is indistinguishable from typed
        # text -- so the fallback is never allowed to authorise a keystroke.
        runner = self._fallback_runner("  ❯ Ask Codex to do anything\n")
        with self.assertRaises(HerdrError) as caught:
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["claude"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertIn("plain text", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_the_fallback_refuses_even_text_teamlead_typed(self):
        session = DispatchSession()
        session.remember("/clear")
        runner = self._fallback_runner("  ❯ /clear\n")
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["claude"],
                session=session,
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertEqual(runner.writes(), [])


# --- the assignment has to land as a user message ---------------------------

UNKNOWN_SKILL = (
    "  ⏺ Args from unknown skill: assignment from the team lead. Your role for\n"
    "    this task is REVIEWER/ARCHITECT. Read /w/COMMON.md in full\n"
    "  ───────\n"
    "  ❯ \n"
)
LANDED = (
    "  > New assignment from the team lead. Your role for this task is REVIEWER.\n"
    "  ───────\n"
    "  ❯ \n"
)
WRAPPED = (
    "  > New assignment from the team\n"
    "    lead. Your role for this task is REVIEWER.\n"
    "  ❯ \n"
)
NOTHING = "  ───────\n  ❯ \n"

OPENING = "New assignment from the team lead."


class TranscriptTest(unittest.TestCase):
    def test_the_assignment_is_found_in_the_transcript(self):
        self.assertTrue(transcript_holds(LANDED, OPENING))

    def test_a_wrapped_message_is_still_found(self):
        self.assertTrue(transcript_holds(WRAPPED, OPENING))

    def test_an_empty_transcript_is_not_a_match(self):
        self.assertFalse(transcript_holds(NOTHING, OPENING))

    def test_styling_does_not_hide_the_match(self):
        self.assertTrue(transcript_holds("{}{}{}".format(DIM, LANDED, RESET), OPENING))

    def test_the_unknown_skill_shape_is_recognised(self):
        self.assertEqual(unknown_skill_error(UNKNOWN_SKILL), "unknown skill")

    def test_codexs_rejection_shape_is_recognised(self):
        text = "  Unrecognized command '/newNew'\n  › \n"
        self.assertEqual(unknown_skill_error(text), "Unrecognized command")

    def test_a_clean_transcript_reports_no_complaint(self):
        self.assertIsNone(unknown_skill_error(LANDED))


class SendMessageTest(unittest.TestCase):
    def _runner(self, screens, wait_ok=True):
        runner = FakeRunner()
        runner.set("agent prompt", ok_json("agent_prompt"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        if wait_ok:
            runner.set("agent wait", ok_json("agent_wait"))
        else:
            runner.set(
                "agent wait",
                stdout="",
                returncode=1,
                stderr='{"error":{"code":"timeout","message":"never left idle"}}',
            )
        runner.responses["agent read claude --source visible --lines 20"] = ScriptedReads(
            screens
        )
        self.runner = runner
        return HerdrClient(runner=runner)

    def _send(self, client, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_message(client, BY_NAME["claude"], "New assignment...", OPENING, **kwargs)

    def test_a_landed_assignment_is_confirmed(self):
        result = self._send(self._runner([NOTHING, LANDED]))
        self.assertTrue(result["landed"])
        self.assertTrue(result["started"])

    def test_the_unknown_skill_transcript_fails_the_agent(self):
        with self.assertRaises(HerdrError) as caught:
            self._send(self._runner([NOTHING, UNKNOWN_SKILL]))
        message = str(caught.exception)
        self.assertIn("slash command", message)
        self.assertIn("send-keys claude esc", message)

    def test_the_unknown_skill_path_stops_before_waiting_on_a_turn(self):
        with self.assertRaises(HerdrError):
            self._send(self._runner([NOTHING, UNKNOWN_SKILL]))
        self.assertEqual([c for c in self.runner.commands() if "agent wait" in c], [])

    def test_a_message_that_never_appears_and_never_starts_is_not_started(self):
        result = self._send(self._runner([NOTHING, NOTHING], wait_ok=False), attempts=2)
        self.assertFalse(result["landed"])
        self.assertFalse(result["started"])

    def test_that_case_warns(self):
        warnings = []
        self._send(
            self._runner([NOTHING, NOTHING], wait_ok=False),
            attempts=2,
            warn=warnings.append,
        )
        self.assertTrue(any("sent_but_not_started" in w for w in warnings))

    def test_a_turn_that_started_counts_even_without_a_transcript_match(self):
        result = self._send(self._runner([NOTHING, NOTHING]), attempts=1)
        self.assertFalse(result["landed"])
        self.assertTrue(result["started"])

    def test_the_landing_poll_is_bounded(self):
        self._send(self._runner([NOTHING, NOTHING], wait_ok=False), attempts=3)
        reads = [c for c in self.runner.commands() if c.startswith("agent read claude")]
        self.assertEqual(len(reads), 4)  # one for the composer gate, three polls

    def test_the_composer_is_checked_before_the_paste(self):
        client = self._runner([NOTHING, LANDED])
        self._send(client)
        commands = self.runner.commands()
        self.assertTrue(commands[0].startswith("agent read claude"))
        self.assertTrue(commands[1].startswith("agent prompt claude"))


# --- the live kill --------------------------------------------------------
#
# `apply` read Codex's empty-composer placeholder as typed text, sent the one
# recovery ctrl+c, and ctrl+c on an EMPTY Codex composer exits the process.
# The agent died and had to be restarted. Every assertion below is a gate that
# would have stopped it.

CODEX_PLACEHOLDER_DIM = "  Codex v1.2\n  ─────────\n  › {}Ask Codex to do anything{}\n".format(
    DIM, RESET
)
CODEX_PLACEHOLDER_PLAIN = "  Codex v1.2\n  ─────────\n  › Ask Codex to do anything\n"
CODEX_PLACEHOLDER_THEN_TYPED = "  › {}Ask Codex to do anything{}/new\n".format(DIM, RESET)


class PlaceholderTest(unittest.TestCase):
    def test_the_hint_matches_exactly_after_trimming(self):
        self.assertTrue(is_placeholder("  Ask Codex to do anything  ", BY_NAME["codex"]))

    def test_a_command_appended_to_the_hint_is_not_the_hint(self):
        self.assertFalse(
            is_placeholder("Ask Codex to do anything/new", BY_NAME["codex"])
        )

    def test_an_agent_that_declares_none_matches_nothing(self):
        self.assertFalse(is_placeholder("Ask Codex to do anything", BY_NAME["claude"]))

    def test_empty_text_is_not_a_placeholder(self):
        self.assertFalse(is_placeholder("", BY_NAME["codex"]))
        self.assertFalse(is_placeholder(None, BY_NAME["codex"]))

    def test_a_dim_placeholder_reads_as_an_empty_composer(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_DIM, BY_NAME["codex"])
        self.assertFalse(composer.occupied)
        self.assertEqual(composer.content, "")

    def test_a_normal_weight_placeholder_reads_as_empty_too(self):
        # Belt as well as braces: the exact-match list catches it even if the
        # runtime stops drawing the hint dim.
        composer = inspect_composer(CODEX_PLACEHOLDER_PLAIN, BY_NAME["codex"])
        self.assertFalse(composer.occupied)
        self.assertTrue(composer.placeholder)

    def test_a_command_typed_over_the_placeholder_is_occupied(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_THEN_TYPED, BY_NAME["codex"])
        self.assertTrue(composer.occupied)
        self.assertEqual(composer.content, "/new")

    def test_recovery_is_refused_for_a_placeholder(self):
        composer = inspect_composer(CODEX_PLACEHOLDER_PLAIN, BY_NAME["codex"])
        allowed, reason = recovery_allowed(
            BY_NAME["codex"], composer, DispatchSession(allow_recovery=True)
        )
        self.assertFalse(allowed)


class LiveKillSequenceTest(unittest.TestCase):
    """`agent get` ok, composer shows the placeholder -- send NO keys."""

    def _runner(self, screen, name="codex"):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = ScriptedReads([screen])
        return runner

    def test_the_placeholder_sends_no_keys_at_all(self):
        runner = self._runner(CODEX_PLACEHOLDER_DIM)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_no_ctrl_c_reaches_codex_on_the_live_pane(self):
        runner = self._runner(CODEX_PLACEHOLDER_DIM)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_the_undimmed_placeholder_sends_no_keys_either(self):
        runner = self._runner(CODEX_PLACEHOLDER_PLAIN)
        ensure_ready(HerdrClient(runner=runner), BY_NAME["codex"], sleep=NO_SLEEP)
        self.assertEqual(runner.writes(), [])

    def test_dispatch_proceeds_normally_over_the_placeholder(self):
        runner = FakeRunner()
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            [CODEX_PLACEHOLDER_DIM, CODEX_FRESH]
        )
        result = send_command(
            HerdrClient(runner=runner),
            BY_NAME["codex"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertTrue(result["consumed"])
        self.assertFalse(result["recovered"])
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_even_a_stranger_text_cannot_reach_ctrl_c_on_codex(self):
        # The last line of defence: codex ships with recover_keys [].
        runner = self._runner(CODEX_HELD)
        with self.assertRaises(HerdrError):
            ensure_ready(
                HerdrClient(runner=runner),
                BY_NAME["codex"],
                session=DispatchSession(allow_recovery=True),
                sleep=NO_SLEEP,
                warn=lambda message: None,
            )
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])




# --- the dim-slash-text kill ----------------------------------------------
#
# Codex renders a slash command in a dim/highlight style while its
# autocomplete popup is open. `composer_ignore_dim: true` filtered the real,
# unsent `/new` out as if it were ghost text, teamlead declared it consumed,
# and the brief was pasted onto it: "Unrecognized command '/newNew
# assignment...'". So while looking for the command it just typed, teamlead
# ignores dimness entirely.

CODEX_HELD_DIM = "  Codex v1.2\n  ─────────\n  › {}/new{}\n".format(DIM, RESET)
CODEX_HELD_GREY = "  Codex v1.2\n  ─────────\n  › {}/new{}\n".format(GREY, RESET)


class DimSlashTextTest(unittest.TestCase):
    """A dim slash command is still a slash command."""

    def test_a_dim_command_counts_as_still_present(self):
        self.assertTrue(command_still_present(CODEX_HELD_DIM, BY_NAME["codex"], "/new"))

    def test_a_grey_command_counts_as_still_present(self):
        self.assertTrue(command_still_present(CODEX_HELD_GREY, BY_NAME["codex"], "/new"))

    def test_a_normal_weight_command_counts_too(self):
        self.assertTrue(command_still_present(CODEX_HELD, BY_NAME["codex"], "/new"))

    def test_an_empty_composer_does_not(self):
        self.assertFalse(command_still_present(CODEX_EMPTY, BY_NAME["codex"], "/new"))

    def test_the_placeholder_does_not(self):
        self.assertFalse(
            command_still_present(CODEX_PLACEHOLDER_DIM, BY_NAME["codex"], "/new")
        )

    def test_the_appended_shape_from_the_live_failure_counts(self):
        text = "  › /newNew assignment from the team lead.\n"
        self.assertTrue(command_still_present(text, BY_NAME["codex"], "/new"))

    def test_the_dim_filter_would_have_hidden_it(self):
        # The bug, stated: with dim filtering the composer looks empty.
        self.assertEqual(composer_text(CODEX_HELD_DIM, "› ", True), "")
        self.assertEqual(composer_text(CODEX_HELD_DIM, "› ", False), "/new")


class DimSlashTextSendTest(unittest.TestCase):
    def _runner(self, screens):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("agent send-keys", ok_json("agent_send_keys"))
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        self.runner = runner
        return HerdrClient(runner=runner)

    def _send(self, client, **kwargs):
        kwargs.setdefault("sleep", NO_SLEEP)
        kwargs.setdefault("warn", lambda message: None)
        return send_command(client, BY_NAME["codex"], "w3:p1", "/new", **kwargs)

    def test_a_dim_command_triggers_another_enter_rather_than_success(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM, CODEX_FRESH])
        result = self._send(client)
        self.assertEqual(result["extra_enters"], 1)
        self.assertTrue(result["consumed"])

    def test_a_dim_command_that_never_submits_is_refused(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM])
        with self.assertRaises(HerdrError) as caught:
            self._send(client)
        self.assertIn("/new", str(caught.exception))

    def test_nothing_is_pasted_after_that_refusal(self):
        client = self._runner([CODEX_EMPTY, CODEX_HELD_DIM])
        with self.assertRaises(HerdrError):
            self._send(client)
        self.assertEqual(self.runner.pasted_prompts(), [])

    def test_a_dim_placeholder_after_the_command_is_still_consumed(self):
        # Once the command text is gone, placeholder and dim rules apply
        # again -- otherwise Codex could never be dispatched at all.
        client = self._runner([CODEX_EMPTY, CODEX_PLACEHOLDER_DIM, CODEX_FRESH])
        result = self._send(client)
        self.assertEqual(result["extra_enters"], 0)
        self.assertTrue(result["consumed"])


class SlashEnterCountTest(unittest.TestCase):
    def test_codex_ships_with_two_enters(self):
        import json as _json

        from teamlead.config import parse_config as _parse

        path = _os.path.join(_ROOT, "config.example.json")
        with open(path, encoding="utf-8") as handle:
            agents = {a.name: a for a in _parse(_json.load(handle))}
        self.assertEqual(agents["codex"].slash_enter_count, 2)
        self.assertEqual(agents["claude"].slash_enter_count, 1)
        self.assertEqual(agents["grok"].slash_enter_count, 1)

    def test_the_configured_count_is_what_gets_sent(self):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.responses["agent read twoenter --source visible --lines 20"] = ScriptedReads(
            [CODEX_EMPTY, CODEX_FRESH]
        )
        send_command(
            HerdrClient(runner=runner),
            BY_NAME["twoenter"],
            "w3:p1",
            "/new",
            sleep=NO_SLEEP,
            warn=lambda message: None,
        )
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 2
        )


if __name__ == "__main__":
    unittest.main()
