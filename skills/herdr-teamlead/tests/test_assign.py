"""Tests for teamlead.assign.

The load-bearing test in this file is that a busy agent is never typed into,
and that a refused run sends nothing at all.
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

import inspect
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead.assign import (
    pane_label,
    apply as _apply,
    assignment_text,
    build_steps,
    check_all_ready,
    native_context_session,
    dry_run,
    normalize_assignments,
    resolve_paths,
)
from teamlead.config import parse_config
from teamlead.errors import AgentBusyError, HerdrError, UsageError
from teamlead.herdr import HerdrClient

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
    """Stand-in for time.sleep. Composer re-reads must never wait in tests."""


def apply(client, assignments, agents_by_name, paths, at, **kwargs):
    kwargs.setdefault("sleep", NO_SLEEP)
    return _apply(client, assignments, agents_by_name, paths, at, **kwargs)

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

BY_NAME = {agent.name: agent for agent in parse_config(CONFIG)}
ASSIGNMENTS = {"developer": "grok", "tester": "claude", "reviewer": "codex"}
PANES = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}

# Verbatim grok footers. The working one is the idle one plus `Esc:cancel`,
# which is why the probe checks working markers first.
GROK_WORKING_FOOTER = (
    "  ╭────────────────────────╮\n"
    "  │ ❯ do the thing         │\n"
    "  ╰────────────────────────╯\n"
    "  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"
)
GROK_IDLE_FOOTER = (
    "  ╭────────────────────────╮\n"
    "  │ ❯                      │\n"
    "  ╰────────────────────────╯\n"
    "  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"
)


def runner_with(statuses, footers=None, composer_screens=None):
    """Script agent statuses, and the pane footer the idle probe would read."""
    footers = footers or {}
    runner = FakeRunner()
    for name, status in statuses.items():
        runner.set("agent get " + name, agent_json(name, status, PANES[name]))
        runner.set("agent prompt " + name, ok_json("agent_prompt"))
        runner.set("agent wait " + name, ok_json("agent_wait"))
        runner.set(
            "agent read {} --source visible --lines 40".format(name),
            footers.get(name, GROK_WORKING_FOOTER),
        )
    runner.set("pane send-text", ok_json("pane_send_text"))
    runner.set("pane send-keys", ok_json("pane_send_keys"))
    runner.set("pane rename", ok_json("pane_rename"))
    # Composer recovery: ctrl+c for codex, esc for the others.
    runner.set("agent send-keys", ok_json("agent_send_keys"))
    # Composer empty throughout, and the screen changes after the clear --
    # which is what `cleared: true` is allowed to mean.
    for name in statuses:
        runner.responses[
            "agent read {} --source visible --lines 20".format(name)
        ] = (
            composer_reads(name, screens=composer_screens)
            if composer_screens
            else composer_reads(name)
        )
    return runner


class PromptTextTest(unittest.TestCase):
    def test_exact_wording(self):
        self.assertEqual(
            assignment_text("developer", "/w/COMMON.md", "/w/dev.md"),
            "New assignment from the team lead. Your role for this task is "
            "DEVELOPER. Read /w/COMMON.md in full, then read /w/dev.md in full, "
            "and execute that brief exactly. Finish with the REPORT line it "
            "specifies.",
        )

    def test_role_is_upper_cased(self):
        self.assertIn("Your role for this task is TESTER.", assignment_text("tester", "c", "b"))


class NormalizeAssignmentsTest(unittest.TestCase):
    def test_accepts_plan_output(self):
        self.assertEqual(
            normalize_assignments({"schema_version": 1, "assignments": ASSIGNMENTS}), ASSIGNMENTS
        )

    def test_accepts_a_bare_role_to_agent_mapping(self):
        self.assertEqual(normalize_assignments(ASSIGNMENTS), ASSIGNMENTS)

    def test_preserves_role_order(self):
        self.assertEqual(list(normalize_assignments(ASSIGNMENTS)), list(ASSIGNMENTS))

    def test_rejects_a_non_object(self):
        with self.assertRaises(UsageError):
            normalize_assignments(["developer", "grok"])

    def test_rejects_an_empty_object(self):
        with self.assertRaises(UsageError):
            normalize_assignments({})

    def test_rejects_a_non_string_agent(self):
        with self.assertRaises(UsageError) as caught:
            normalize_assignments({"developer": ["grok"]})
        self.assertIn("developer", str(caught.exception))


class ResolvePathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teamlead-brief-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.briefs = {}
        for role in ASSIGNMENTS:
            path = Path(self.tmp) / (role + ".md")
            path.write_text("# " + role, encoding="utf-8")
            self.briefs[role] = str(path)
        self.common = str(Path(self.tmp) / "COMMON.md")
        Path(self.common).write_text("# common", encoding="utf-8")

    def test_paths_are_made_absolute(self):
        paths = resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertTrue(os.path.isabs(paths["common"]))
        self.assertTrue(all(os.path.isabs(paths[role]) for role in ASSIGNMENTS))

    def test_missing_brief_for_a_role_names_the_role(self):
        del self.briefs["tester"]
        with self.assertRaises(UsageError) as caught:
            resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertIn("tester", str(caught.exception))

    def test_nonexistent_brief_file_is_refused(self):
        self.briefs["tester"] = str(Path(self.tmp) / "nope.md")
        with self.assertRaises(UsageError) as caught:
            resolve_paths(ASSIGNMENTS, self.briefs, self.common)
        self.assertIn("nope.md", str(caught.exception))

    def test_nonexistent_common_file_is_refused(self):
        with self.assertRaises(UsageError):
            resolve_paths(ASSIGNMENTS, self.briefs, str(Path(self.tmp) / "gone.md"))


class BuildStepsTest(unittest.TestCase):
    def setUp(self):
        self.client = HerdrClient(binary="herdr", runner=FakeRunner())
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_one_step_per_role_in_assignment_order(self):
        steps = build_steps(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertEqual([step["role"] for step in steps], ["developer", "tester", "reviewer"])
        self.assertEqual([step["agent"] for step in steps], ["grok", "claude", "codex"])

    def test_commands_are_check_clear_wait_assign(self):
        steps = build_steps(
            self.client,
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            panes={"grok": "w4:p1"},
            settle_timeout_ms=60000,
        )
        self.assertEqual(
            [command["shell"] for command in steps[0]["commands"]],
            [
                "herdr agent get grok",
                "herdr pane process-info --pane w4:p1",
                "herdr pane process-info --pane w4:p1",
                "herdr agent read grok --source visible --lines 20 --format ansi",
                "herdr pane process-info --pane w4:p1",
                "herdr pane send-text w4:p1 /new",
                "herdr pane process-info --pane w4:p1",
                "herdr pane send-keys w4:p1 enter",
                "herdr agent read grok --source visible --lines 20 --format ansi",
                "herdr agent wait grok --until idle --until done --timeout 60000",
                "herdr agent read grok --source visible --lines 20 --format ansi",
                "herdr pane process-info --pane w4:p1",
                "herdr agent prompt grok 'New assignment from the team lead. Your role "
                "for this task is DEVELOPER. Read /w/COMMON.md in full, then read "
                "/w/dev.md in full, and execute that brief exactly. Finish with the "
                "REPORT line it specifies.'",
                "herdr agent read grok --source visible --lines 20 --format ansi",
                "herdr agent wait grok --until working --timeout 15000",
            ],
        )

    def test_each_agent_gets_its_native_clear_by_its_configured_path(self):
        steps = build_steps(
            self.client,
            ASSIGNMENTS,
            BY_NAME,
            self.paths,
            panes={"grok": "w4:p1", "claude": "w2:p1", "codex": "w3:p1"},
        )
        clears = [
            command["shell"]
            for step in steps
            for command in step["commands"]
            if command["shell"].endswith(("/new", "/clear"))
        ]
        self.assertEqual(
            clears,
            [
                "herdr pane send-text w4:p1 /new",  # grok types
                "herdr agent prompt claude /clear",  # claude pastes
                "herdr pane send-text w3:p1 /new",  # codex types
            ],
        )

    def test_the_plan_shows_every_enter_the_agent_needs(self):
        # codex ships slash_enter_count 2; the plan must show both, or a
        # --dry-run reader would not know what actually happens.
        steps = build_steps(
            self.client,
            {"reviewer": "codex"},
            BY_NAME,
            self.paths,
            panes={"codex": "w3:p1"},
        )
        shells = [command["shell"] for command in steps[0]["commands"]]
        self.assertEqual(
            len([sh for sh in shells if sh == "herdr pane send-keys w3:p1 enter"]), 2
        )

    def test_a_single_enter_agent_shows_one(self):
        steps = build_steps(
            self.client,
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            panes={"grok": "w4:p1"},
        )
        shells = [command["shell"] for command in steps[0]["commands"]]
        self.assertEqual(
            len([sh for sh in shells if sh == "herdr pane send-keys w4:p1 enter"]), 1
        )

    def test_grok_never_has_its_clear_pasted(self):
        steps = build_steps(
            self.client, {"developer": "grok"}, BY_NAME, self.paths, panes={"grok": "w4:p1"}
        )
        prompts = [
            command["shell"]
            for command in steps[0]["commands"]
            if " agent prompt " in command["shell"]
        ]
        self.assertEqual(len(prompts), 1)  # the assignment message alone
        self.assertIn("New assignment from the team lead.", prompts[0])
        self.assertNotIn("/new", prompts[0])

    def test_an_unresolved_pane_renders_as_a_placeholder(self):
        # --dry-run resolves no panes because it makes no herdr calls.
        steps = build_steps(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        self.assertEqual(steps[0]["pane_id"], "PANE-ID-RESOLVED-AT-RUN-TIME")
        self.assertIn(
            "herdr pane send-text PANE-ID-RESOLVED-AT-RUN-TIME /new",
            [command["shell"] for command in steps[0]["commands"]],
        )

    def test_no_clear_drops_the_clear_and_the_settle_wait(self):
        steps = build_steps(
            self.client, {"developer": "grok"}, BY_NAME, self.paths, no_clear=True
        )
        shells = [command["shell"] for command in steps[0]["commands"]]
        # get, permission proof, the composer check, the message,
        # then the two checks that it landed -- --no-clear skips the clear, not
        # the verification.
        self.assertEqual(len(shells), 8)
        self.assertEqual(shells[0], "herdr agent get grok")
        self.assertEqual(
            shells[3], "herdr agent read grok --source visible --lines 20 --format ansi"
        )
        self.assertIn("DEVELOPER", shells[5])
        self.assertEqual(
            shells[6], "herdr agent read grok --source visible --lines 20 --format ansi"
        )
        self.assertEqual(shells[7], "herdr agent wait grok --until working --timeout 15000")
        self.assertEqual([s for s in shells if "/new" in s], [])

    def test_unknown_agent_name_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            build_steps(self.client, {"developer": "gemini"}, BY_NAME, self.paths)
        self.assertIn("gemini", str(caught.exception))


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.client = HerdrClient(binary="herdr", runner=self.runner)
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_dry_run_makes_no_herdr_calls_at_all(self):
        result = dry_run(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["sent"])
        self.assertEqual(self.runner.calls, [])

    def test_dry_run_plans_even_when_every_agent_is_busy(self):
        # No status check happens, so a dry run against a busy fleet still
        # shows the plan instead of refusing it.
        result = dry_run(self.client, ASSIGNMENTS, BY_NAME, self.paths)
        self.assertEqual(len(result["steps"]), 3)
        self.assertEqual(self.runner.writes(), [])

    def test_dry_run_lists_the_conditional_probe_read(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        conditional = result["steps"][0]["conditional_commands"]
        self.assertEqual(
            conditional[0]["shell"], "herdr agent read grok --source visible --lines 40"
        )
        self.assertIn("working", conditional[0]["when"])

    def test_dry_run_lists_the_conditional_recovery_and_second_enter(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        shells = [command["shell"] for command in result["steps"][0]["conditional_commands"]]
        self.assertIn("herdr agent send-keys grok esc", shells)
        self.assertIn(
            "herdr pane send-keys PANE-ID-RESOLVED-AT-RUN-TIME enter", shells
        )

    def test_the_recovery_command_says_it_runs_exactly_once(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        recovery = [
            command
            for command in result["steps"][0]["conditional_commands"]
            if "send-keys grok esc" in command["shell"]
        ]
        self.assertEqual(len(recovery), 1)
        self.assertIn("exactly once", recovery[0]["when"])

    def test_dry_run_shows_the_prompt_text_that_would_be_sent(self):
        result = dry_run(self.client, {"developer": "grok"}, BY_NAME, self.paths)
        self.assertIn("DEVELOPER", result["steps"][0]["prompt"])


class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def test_working_agent_is_refused_and_nothing_is_sent(self):
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertIn("grok (working)", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_blocked_agent_is_refused(self):
        runner = runner_with({"grok": "blocked"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_one_busy_agent_blocks_the_whole_round(self):
        # claude is idle, but codex is working: nothing goes out, so the round
        # is never half-applied.
        runner = runner_with({"claude": "idle", "codex": "working"})
        paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md", "tester": "/w/t.md"}
        with self.assertRaises(AgentBusyError):
            apply(
                HerdrClient(runner=runner),
                {"developer": "claude", "tester": "codex"},
                BY_NAME,
                paths,
                AT,
            )
        self.assertEqual(runner.writes(), [])

    def test_a_busy_agent_has_no_override(self):
        # rules/agent-team-operation.md Dispatch Safety is unconditional. The
        # refusal is the whole behavior: nothing is typed, and no argument
        # exists that would type it anyway.
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])
        # No override parameter exists to be passed. Asserted on the signature
        # rather than by calling with an invalid keyword, which a type checker
        # rightly rejects at the call site.
        self.assertNotIn("force", inspect.signature(apply).parameters)
        self.assertNotIn("force", inspect.signature(check_all_ready).parameters)

    def test_the_refusal_names_the_state_to_wait_for(self):
        runner = runner_with({"grok": "working"})
        with self.assertRaises(AgentBusyError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        message = str(caught.exception)
        self.assertIn("grok (working)", message)
        self.assertIn("idle or done", message)
        self.assertNotIn("--force", message)

    def test_stale_working_is_overturned_and_the_round_proceeds(self):
        # herdr's title-derived state says working; the pane footer says the
        # agent is sitting at an empty prompt. The pane wins.
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        applied = result["applied"][0]
        self.assertEqual(applied["state_before"], "idle")
        self.assertEqual(applied["herdr_state_before"], "working")
        self.assertEqual(applied["state_source"], "probe")

    def test_the_overturn_warns_that_herdr_state_was_stale(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        warnings = []
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=warnings.append,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale", warnings[0])

    def test_a_probe_confirming_working_still_refuses(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_WORKING_FOOTER})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_an_inconclusive_probe_still_refuses(self):
        runner = runner_with({"grok": "working"}, footers={"grok": "unknown screen\n"})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])

    def test_a_blocked_agent_is_refused_without_a_probe_read(self):
        runner = runner_with({"grok": "blocked"}, footers={"grok": GROK_IDLE_FOOTER})
        with self.assertRaises(AgentBusyError):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.commands(), ["agent get grok"])

    def test_check_all_ready_reports_the_probe_verdict(self):
        runner = runner_with({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        statuses = check_all_ready(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            warn=lambda message: None,
        )
        self.assertEqual(
            statuses["grok"],
            {
                "state": "idle",
                "herdr_state": "working",
                "state_source": "probe",
                "pane_id": "w4:p1",
                "context_session": None,
            },
        )

    def test_check_all_ready_returns_statuses_when_everyone_is_free(self):
        runner = runner_with({"grok": "idle", "claude": "done"})
        statuses = check_all_ready(
            HerdrClient(runner=runner), {"developer": "grok", "tester": "claude"}, BY_NAME
        )
        self.assertEqual(
            statuses,
            {
                "grok": {
                    "state": "idle",
                    "herdr_state": "idle",
                    "state_source": "herdr",
                    "pane_id": "w4:p1",
                    "context_session": None,
                },
                "claude": {
                    "state": "done",
                    "herdr_state": "done",
                    "state_source": "herdr",
                    "pane_id": "w2:p1",
                    "context_session": None,
                },
            },
        )


class SlashCommandDeliveryTest(unittest.TestCase):
    """Regression: `agent prompt` pastes, and a pasted /clear is a message."""

    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
            "reviewer": "/w/review.md",
        }

    def test_groks_clear_never_goes_through_agent_prompt(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        self.assertNotIn(
            "agent prompt grok /new", runner.commands()
        )
        self.assertIn("pane send-text w4:p1 /new", runner.commands())

    def test_grok_and_codex_type_their_clears(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("pane send-text")],
            ["pane send-text w4:p1 /new", "pane send-text w3:p1 /new"],
        )

    def test_claude_has_its_clear_pasted(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        self.assertIn("agent prompt claude /clear", runner.commands())
        self.assertNotIn("agent prompt codex /new", runner.commands())

    def test_every_role_still_gets_its_assignment_message_pasted(self):
        runner = runner_with({"grok": "idle", "claude": "idle", "codex": "idle"})
        apply(HerdrClient(runner=runner), ASSIGNMENTS, BY_NAME, self.paths, AT)
        messages = [
            text
            for text in runner.pasted_prompts()
            if "New assignment from the team lead." in text
        ]
        self.assertEqual(len(messages), 3)

    def test_every_typed_command_is_followed_by_enter(self):
        runner = runner_with({"grok": "idle"})
        apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        commands = runner.writes()
        index = commands.index("pane send-text w4:p1 /new")
        self.assertEqual(commands[index + 1], "pane send-keys w4:p1 enter")

    def test_a_silent_send_text_still_gets_its_enter(self):
        # herdr's `pane send-text` exits 0 with empty stdout on success.
        runner = runner_with({"grok": "idle"})
        runner.set("pane send-text", stdout="")
        runner.set("pane send-keys", stdout="")
        runner.set("pane rename", ok_json("pane_rename"))
        result = apply(
            HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT
        )
        commands = runner.writes()
        self.assertEqual(
            commands[commands.index("pane send-text w4:p1 /new") + 1],
            "pane send-keys w4:p1 enter",
        )
        self.assertEqual(len(result["applied"]), 1)

    def test_no_clear_types_nothing_at_all(self):
        runner = runner_with({"grok": "idle"})
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            no_clear=True,
        )
        self.assertEqual([c for c in runner.commands() if c.startswith("pane send-")], [])

    def test_a_typing_agent_with_no_pane_is_refused_rather_than_typed_at_blindly(self):
        runner = FakeRunner()
        runner.set(
            "agent get grok",
            '{"id":"cli:agent:get","result":{"agent":{"name":"grok","agent_status":"idle"}}}',
        )
        with self.assertRaises(UsageError) as caught:
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertIn("herdr agent list", str(caught.exception))
        self.assertEqual(runner.writes(), [])


class ComposerGateTest(unittest.TestCase):
    """The live failure: an unsent /new, then the assignment pasted onto it.

    Codex received `/newNew assignment from the team lead...` and rejected it
    twice, because `agent wait --until idle` returned instantly (the agent had
    been idle the whole time) and nothing ever checked the composer.
    """

    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def _runner(self, screens):
        runner = runner_with({"codex": "idle"})
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        return runner

    def test_an_unsent_clear_stops_the_round_before_the_assignment(self):
        held = composer_screen("codex", "transcript", "/new")
        runner = self._runner([composer_screen("codex"), held])
        with self.assertRaises(HerdrError) as caught:
            apply(
                HerdrClient(runner=runner),
                {"developer": "codex"},
                BY_NAME,
                self.paths,
                AT,
                warn=lambda message: None,
            )
        self.assertIn("/new", str(caught.exception))
        # The exact regression: no assignment is appended to the stuck text.
        self.assertEqual(runner.pasted_prompts(), [])

    def test_an_extra_enter_recovers_the_round(self):
        runner = self._runner(
            [
                composer_screen("codex", "old transcript"),
                composer_screen("codex", "old transcript", "/new"),
                composer_screen("codex", "fresh session banner"),
            ]
        )
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        self.assertEqual(len(result["applied"]), 1)
        self.assertTrue(result["applied"][0]["cleared"])
        # codex's configured 2, plus 1 more once the command was still shown
        self.assertEqual(
            len([c for c in runner.commands() if c == "pane send-keys w3:p1 enter"]), 3
        )
        self.assertEqual(len(runner.pasted_prompts()), 1)
        self.assertIn("DEVELOPER", runner.pasted_prompts()[0])

    def test_a_composer_holding_text_before_dispatch_is_refused_not_cleared(self):
        # Live, teamlead sent the one recovery ctrl+c into an idle Codex and
        # killed the process. By default it now refuses and names the pane.
        runner = self._runner([composer_screen("codex", "old", "leftover text")])
        with self.assertRaises(HerdrError) as caught:
            apply(
                HerdrClient(runner=runner),
                {"developer": "codex"},
                BY_NAME,
                self.paths,
                AT,
                warn=lambda message: None,
            )
        message = str(caught.exception)
        self.assertIn("leftover text", message)
        self.assertIn("w3:p1", message)  # the refusal names the pane
        self.assertEqual(runner.writes(), [])

    def test_an_agent_with_keys_is_still_refused_without_the_opt_in(self):
        runner = runner_with({"grok": "idle"})
        runner.responses["agent read grok --source visible --lines 20"] = ScriptedReads(
            [composer_screen("grok", "old", "somebody's draft")]
        )
        with self.assertRaises(HerdrError) as caught:
            apply(
                HerdrClient(runner=runner),
                {"developer": "grok"},
                BY_NAME,
                {"common": "/w/COMMON.md", "developer": "/w/dev.md"},
                AT,
                warn=lambda message: None,
            )
        message = str(caught.exception)
        self.assertIn("--allow-recovery", message)
        self.assertIn("somebody's draft", message)
        self.assertEqual(runner.writes(), [])

    def test_the_opt_in_lets_an_agent_with_keys_be_cleared(self):
        runner = runner_with({"grok": "idle"})
        runner.responses["agent read grok --source visible --lines 20"] = ScriptedReads(
            [
                composer_screen("grok", "old", "somebody's draft"),
                composer_screen("grok", "old"),
                composer_screen("grok", "fresh"),
                composer_screen("grok", "fresh"),
                "  > New assignment from the team lead.\n  │ ❯      │\n",
            ]
        )
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            {"common": "/w/COMMON.md", "developer": "/w/dev.md"},
            AT,
            allow_recovery=True,
            warn=lambda message: None,
        )
        self.assertIn("agent send-keys grok esc", runner.commands())

    def test_allow_recovery_still_sends_nothing_to_an_agent_without_keys(self):
        # codex ships with recover_keys [] precisely so the opt-in cannot
        # reach it.
        runner = self._runner([composer_screen("codex", "old", "leftover text")])
        with self.assertRaises(HerdrError) as caught:
            apply(
                HerdrClient(runner=runner),
                {"developer": "codex"},
                BY_NAME,
                self.paths,
                AT,
                allow_recovery=True,
                warn=lambda message: None,
            )
        self.assertIn("recover_keys", str(caught.exception))
        self.assertEqual(runner.writes(), [])

    def test_an_unrecoverable_composer_refuses_before_any_write(self):
        runner = self._runner([composer_screen("codex", "old", "stuck")])
        with self.assertRaises(HerdrError):
            apply(
                HerdrClient(runner=runner),
                {"developer": "codex"},
                BY_NAME,
                self.paths,
                AT,
                warn=lambda message: None,
            )
        self.assertEqual(runner.pasted_prompts(), [])
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("pane send-text")], []
        )

    def test_no_clear_still_gates_the_assignment_on_an_empty_composer(self):
        runner = self._runner([composer_screen("codex", "old", "leftover")])
        with self.assertRaises(HerdrError):
            apply(
                HerdrClient(runner=runner),
                {"developer": "codex"},
                BY_NAME,
                self.paths,
                AT,
                no_clear=True,
                warn=lambda message: None,
            )
        self.assertEqual(runner.pasted_prompts(), [])


class AssignmentLandingTest(unittest.TestCase):
    """Live: a leftover `/` turned the assignment into `/New assignment ...`.

    Claude Code answered "Args from unknown skill", no turn started, and the
    round was reported as applied.
    """

    UNKNOWN_SKILL = (
        "  ⏺ Args from unknown skill: assignment from the team lead. Your role\n"
        "    for this task is REVIEWER/ARCHITECT. Read /w/COMMON.md in full\n"
        "  │ ❯                                        │\n"
    )
    LANDED = (
        "  > New assignment from the team lead. Your role for this task is DEVELOPER.\n"
        "  │ ❯                                        │\n"
    )
    QUIET = "  nothing happened\n  │ ❯                                        │\n"

    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def _runner(self, screens, wait_ok=True):
        runner = runner_with({"grok": "idle"})
        runner.responses["agent read grok --source visible --lines 20"] = ScriptedReads(
            screens
        )
        if not wait_ok:
            runner.set(
                "agent wait grok --until working",
                stdout="",
                returncode=1,
                stderr='{"error":{"code":"timeout","message":"never left idle"}}',
            )
        return runner

    def _apply(self, runner, **kwargs):
        kwargs.setdefault("warn", lambda message: None)
        return apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            **kwargs
        )

    def test_a_landed_assignment_is_recorded_as_applied(self):
        runner = self._runner(
            [composer_screen("grok", "old"), composer_screen("grok", "fresh"),
             composer_screen("grok", "fresh"), self.LANDED]
        )
        record = self._apply(runner)["applied"][0]
        self.assertTrue(record["landed"])
        self.assertEqual(record["status"], "applied")

    def test_the_unknown_skill_transcript_fails_the_round(self):
        runner = self._runner(
            [composer_screen("grok", "old"), composer_screen("grok", "fresh"),
             composer_screen("grok", "fresh"), self.UNKNOWN_SKILL]
        )
        with self.assertRaises(HerdrError) as caught:
            self._apply(runner)
        message = str(caught.exception)
        self.assertIn("slash command", message)
        self.assertIn("esc", message)

    def test_a_silent_agent_is_reported_sent_but_not_started(self):
        runner = self._runner(
            [composer_screen("grok", "old"), composer_screen("grok", "fresh"),
             composer_screen("grok", "fresh"), self.QUIET],
            wait_ok=False,
        )
        record = self._apply(runner, landing_attempts=2)["applied"][0]
        self.assertFalse(record["landed"])
        self.assertFalse(record["started"])
        self.assertEqual(record["status"], "sent_but_not_started")

    def test_the_clear_settles_before_the_assignment_is_pasted(self):
        # The race that left a `/` behind: the clear's redraw against the next
        # paste. One sleep per clear, before the message goes out.
        slept = []
        runner = self._runner(
            [composer_screen("grok", "old"), composer_screen("grok", "fresh"),
             composer_screen("grok", "fresh"), self.LANDED]
        )
        self._apply(runner, sleep=slept.append)
        commands = runner.commands()
        self.assertLess(
            commands.index("pane send-text w4:p1 /new"),
            next(i for i, c in enumerate(commands) if c.startswith("agent prompt")),
        )
        self.assertTrue(slept)


class CodexPlaceholderTest(unittest.TestCase):
    """End to end: the live sequence that killed a Codex process.

    `agent get` reported idle, the composer showed `Ask Codex to do anything`
    -- Codex's empty-composer hint -- teamlead read it as typed text and sent
    the one recovery ctrl+c. Ctrl+C on an empty Codex composer exits Codex.
    """

    PLACEHOLDER = (
        "  Codex v1.2  ~/Projects/x\n"
        "  ─────────────\n"
        "  › \x1b[2mAsk Codex to do anything\x1b[0m\n"
    )
    FRESH = "  ╭─ Codex ─╮\n  │ new session │\n  ╰─────────╯\n  › \n"
    LANDED = (
        "  > New assignment from the team lead. Your role for this task is DEVELOPER.\n"
        "  › \n"
    )

    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def _runner(self, screens):
        runner = runner_with({"codex": "idle"})
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        return runner

    def test_no_ctrl_c_is_ever_sent(self):
        runner = self._runner([self.PLACEHOLDER, self.FRESH, self.FRESH, self.LANDED])
        apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])

    def test_no_recovery_keys_of_any_kind_are_sent(self):
        runner = self._runner([self.PLACEHOLDER, self.FRESH, self.FRESH, self.LANDED])
        apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        self.assertEqual(
            [c for c in runner.commands() if c.startswith("agent send-keys")], []
        )

    def test_the_round_completes_over_the_placeholder(self):
        runner = self._runner([self.PLACEHOLDER, self.FRESH, self.FRESH, self.LANDED])
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
        )
        record = result["applied"][0]
        self.assertEqual(record["status"], "applied")
        self.assertTrue(record["cleared"])
        self.assertIn("pane send-text w3:p1 /new", runner.commands())

    def test_the_opt_in_cannot_route_ctrl_c_to_codex_either(self):
        runner = self._runner([self.PLACEHOLDER, self.FRESH, self.FRESH, self.LANDED])
        apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            allow_recovery=True,
            warn=lambda message: None,
        )
        self.assertEqual([c for c in runner.commands() if "ctrl+c" in c], [])


class ClearedFlagTest(unittest.TestCase):
    """`cleared: true` means consumed AND the screen actually changed."""

    def setUp(self):
        self.paths = {"common": "/w/COMMON.md", "developer": "/w/dev.md"}

    def _apply(self, screens, **kwargs):
        runner = runner_with({"codex": "idle"})
        runner.responses["agent read codex --source visible --lines 20"] = ScriptedReads(
            screens
        )
        self.runner = runner
        return apply(
            HerdrClient(runner=runner),
            {"developer": "codex"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
            **kwargs
        )

    def test_a_fresh_session_banner_counts_as_cleared(self):
        result = self._apply(
            [composer_screen("codex", "old transcript"), composer_screen("codex", "fresh banner")]
        )
        self.assertTrue(result["applied"][0]["cleared"])

    def test_an_unchanged_screen_refuses_before_the_brief(self):
        # Gating, not advisory: briefing an agent that still holds the last
        # task's context is exactly what the clear exists to prevent.
        with self.assertRaises(HerdrError) as caught:
            self._apply(
                [composer_screen("codex", "same"), composer_screen("codex", "same")]
            )
        message = str(caught.exception)
        self.assertIn("did not change", message)
        self.assertIn("was not cleared", message)

    def test_nothing_is_pasted_after_that_refusal(self):
        with self.assertRaises(HerdrError):
            self._apply(
                [composer_screen("codex", "same"), composer_screen("codex", "same")]
            )
        self.assertEqual(self.runner.pasted_prompts(), [])

    def test_no_clear_skips_the_screen_gate_entirely(self):
        result = self._apply([composer_screen("codex", "same")], no_clear=True)
        self.assertEqual(len(result["applied"]), 1)

    def test_no_clear_reports_cleared_false(self):
        result = self._apply([composer_screen("codex")], no_clear=True)
        self.assertFalse(result["applied"][0]["cleared"])

    def test_a_cleared_round_always_reports_true(self):
        # `cleared` is now either True or the round raised, so a False here
        # can only mean --no-clear.
        result = self._apply(
            [composer_screen("codex", "old"), composer_screen("codex", "fresh")]
        )
        self.assertTrue(result["applied"][0]["cleared"])


class DuplicateAgentTest(unittest.TestCase):
    """One agent, one role. Two briefs to one pane means the second wins."""

    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
        }

    def test_the_same_agent_in_two_roles_is_rejected(self):
        with self.assertRaises(UsageError) as caught:
            normalize_assignments({"developer": "grok", "tester": "grok"})
        message = str(caught.exception)
        self.assertIn("grok", message)
        self.assertIn("developer", message)
        self.assertIn("tester", message)

    def test_the_rejection_explains_what_would_happen(self):
        with self.assertRaises(UsageError) as caught:
            normalize_assignments({"developer": "grok", "tester": "grok"})
        self.assertIn("overwrite", str(caught.exception))

    def test_plan_output_carrying_a_duplicate_is_rejected_too(self):
        with self.assertRaises(UsageError):
            normalize_assignments(
                {"schema_version": 1, "assignments": {"a": "grok", "b": "grok"}}
            )

    def test_three_roles_on_one_agent_are_all_named(self):
        with self.assertRaises(UsageError) as caught:
            normalize_assignments({"a": "grok", "b": "grok", "c": "grok"})
        details = caught.exception.details["doubled"]
        self.assertEqual(details, {"grok": ["a", "b", "c"]})

    def test_distinct_agents_pass(self):
        self.assertEqual(
            normalize_assignments({"developer": "grok", "tester": "claude"}),
            {"developer": "grok", "tester": "claude"},
        )

    def test_the_dry_run_plan_refuses_it_too(self):
        with self.assertRaises(UsageError):
            build_steps(
                HerdrClient(binary="herdr", runner=FakeRunner()),
                {"developer": "grok", "tester": "grok"},
                BY_NAME,
                self.paths,
            )

    def test_apply_refuses_before_any_herdr_call(self):
        runner = runner_with({"grok": "idle"})
        with self.assertRaises(UsageError):
            apply(
                HerdrClient(runner=runner),
                {"developer": "grok", "tester": "grok"},
                BY_NAME,
                self.paths,
                AT,
                warn=lambda message: None,
            )
        self.assertEqual(runner.calls, [])


class ApplyTest(unittest.TestCase):
    @staticmethod
    def replace_worker_without_yolo(runner, name):
        runner.set("pane process-info --pane " + PANES[name], json.dumps({"result": {"process_info": {
            "pane_id": PANES[name], "foreground_processes": [{"name": name, "pid": 999, "argv": [name]}]}}}))

    def test_later_worker_changed_after_first_result_receives_no_clear_or_prompt(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        results = []

        def preserve_result_and_replace_later_worker(record):
            results.append(record.copy())
            self.replace_worker_without_yolo(runner, "claude")

        with self.assertRaisesRegex(HerdrError, "do not prove YOLO"):
            apply(HerdrClient(runner=runner), {"developer": "grok", "tester": "claude"}, BY_NAME, self.paths, AT,
                  on_result=preserve_result_and_replace_later_worker)
        self.assertEqual([(row["agent"], row["status"]) for row in results], [("grok", "applied")])
        self.assertTrue(results[0]["landed"])
        self.assertTrue(results[0]["started"])
        self.assertFalse(any("claude" in command or PANES["claude"] in command for command in runner.writes()))

    def test_worker_changed_after_clear_receives_no_assignment(self):
        runner = runner_with({"grok": "idle"})
        results = []
        with self.assertRaisesRegex(HerdrError, "do not prove YOLO"):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT,
                  on_before_send=lambda *_: self.replace_worker_without_yolo(runner, "grok"), on_result=results.append)
        self.assertEqual(runner.writes(), ["pane send-text w4:p1 /new", "pane send-keys w4:p1 enter"])
        self.assertEqual(results, [])

    def test_worker_changed_during_composer_read_receives_no_recovery_or_clear(self):
        runner = runner_with({"grok": "idle"})
        client = HerdrClient(runner=runner)

        def read_replaced_worker(*_args, **_kwargs):
            self.replace_worker_without_yolo(runner, "grok")
            return composer_screen("grok", held="old operator text")

        with patch.object(client, "agent_read", side_effect=read_replaced_worker), self.assertRaisesRegex(HerdrError, "do not prove YOLO"):
            apply(client, {"developer": "grok"}, BY_NAME, self.paths, AT, allow_recovery=True, warn=lambda _: None)
        self.assertEqual(runner.writes(), [])

    def test_worker_replaced_after_slash_text_receives_no_enter(self):
        runner = runner_with({"grok": "idle"})
        client = HerdrClient(runner=runner)
        original_send_text = client.pane_send_text

        def type_then_replace(pane, text):
            result = original_send_text(pane, text)
            self.replace_worker_without_yolo(runner, "grok")
            return result

        with patch.object(client, "pane_send_text", side_effect=type_then_replace), self.assertRaisesRegex(HerdrError, "do not prove YOLO"):
            apply(client, {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), ["pane send-text w4:p1 /new"])

    def test_legacy_worker_permissions_are_proved_for_all_targets_before_any_input(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        runner.set("pane process-info --pane " + PANES["claude"], json.dumps({"result": {"process_info": {
            "pane_id": PANES["claude"], "foreground_processes": [{"name": "claude", "pid": 200, "argv": ["claude"]}]}}}))
        with self.assertRaisesRegex(HerdrError, "do not prove YOLO"):
            apply(HerdrClient(runner=runner), {"developer": "grok", "tester": "claude"}, BY_NAME, self.paths, AT)
        self.assertEqual(runner.writes(), [])
        self.assertIn("pane process-info --pane " + PANES["grok"], runner.commands())

    def test_legacy_no_clear_refuses_wrong_permissions_without_restarting(self):
        runner = runner_with({"grok": "idle"})
        runner.set("pane process-info", json.dumps({"result": {"process_info": {
            "pane_id": PANES["grok"], "foreground_processes": [{"name": "grok", "pid": 200,
                "argv": ["grok", "--permission-mode", "plan"]}]}}}))
        with self.assertRaisesRegex(HerdrError, "restrictive permission"):
            apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT, no_clear=True)
        self.assertEqual(runner.writes(), [])
        self.assertFalse(any(command.startswith(("agent start", "-TERM")) for command in runner.commands()))

    def test_legacy_unknown_or_unreadable_process_refuses_before_clear_or_send(self):
        for failure in ("missing", "unknown", "unreadable"):
            runner = runner_with({"grok": "idle"})
            process = {"pane_id": PANES["grok"], "foreground_processes": []}
            if failure == "unknown":
                process["foreground_processes"] = [{"name": "zsh", "pid": 200, "argv": ["zsh"]}]
            runner.set("pane process-info", json.dumps({"result": {"process_info": process}}),
                       returncode=7 if failure == "unreadable" else 0, stderr="process information unavailable")
            with self.subTest(failure=failure), self.assertRaises(HerdrError):
                apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
            self.assertEqual(runner.writes(), [])

    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
        }

    def test_clear_settle_then_assign_in_order(self):
        runner = runner_with({"grok": "idle"})
        apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertEqual(
            runner.commands(),
            [
                "agent get grok",
                "pane process-info --pane w4:p1",
                "pane process-info --pane w4:p1",
                "agent read grok --source visible --lines 20 --format ansi",
                "pane process-info --pane w4:p1",
                "pane send-text w4:p1 /new",
                "pane process-info --pane w4:p1",
                "pane send-keys w4:p1 enter",
                "agent read grok --source visible --lines 20 --format ansi",
                "agent wait grok --until idle --until done --timeout 60000",
                "agent read grok --source visible --lines 20 --format ansi",
                "pane process-info --pane w4:p1",
                "agent prompt grok 'New assignment from the team lead. Your role for "
                "this task is DEVELOPER. Read /w/COMMON.md in full, then read /w/dev.md "
                "in full, and execute that brief exactly. Finish with the REPORT line it "
                "specifies.'",
                # Sending is not starting.
                "agent read grok --source visible --lines 20 --format ansi",
                "agent wait grok --until working --timeout 15000",
                # Confirmed hand-off, so the sidebar says who is doing what.
                "pane rename w4:p1 developer",
            ],
        )

    def test_status_of_every_target_is_checked_before_the_first_write(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok", "tester": "claude"},
            BY_NAME,
            self.paths,
            AT,
        )
        commands = runner.commands()
        first_write = min(
            index
            for index, c in enumerate(commands)
            if c.startswith(runner.WRITE_PREFIXES)
        )
        # Both statuses are read before anything is sent; the composer check
        # that precedes the first dispatch is a read too.
        self.assertEqual(
            commands[:first_write],
            [
                "agent get grok",
                "agent get claude",
                "pane process-info --pane w4:p1",
                "pane process-info --pane w2:p1",
                "pane process-info --pane w4:p1",
                "agent read grok --source visible --lines 20 --format ansi",
                "pane process-info --pane w4:p1",
            ],
        )

    def test_no_clear_sends_only_the_assignment(self):
        runner = runner_with({"grok": "idle"})
        apply(
            HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT, no_clear=True
        )
        self.assertEqual(len(runner.writes()), 1)
        self.assertIn("DEVELOPER", runner.writes()[0])

    def test_result_records_what_was_applied(self):
        runner = runner_with({"grok": "idle"})
        result = apply(HerdrClient(runner=runner), {"developer": "grok"}, BY_NAME, self.paths, AT)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["applied_at"], AT)
        self.assertEqual(
            result["applied"],
            [
                {
                    "role": "developer",
                    "agent": "grok",
                    "state_before": "idle",
                    "herdr_state_before": "idle",
                    "state_source": "herdr",
                    "pane_id": "w4:p1",
                    "cleared": True,
                    "clear_reason": "automatic",
                    "task": None,
                    "fix_round": None,
                    "context_session": None,
                    "tier": None,
                    "landed": True,
                    "started": True,
                    "status": "applied",
                    "brief": "/w/dev.md",
                    "common": "/w/COMMON.md",
                    "at": AT,
                    "pane_label": "developer",
                    "context_transition": None,
                }
            ],
        )

    def test_the_ledger_callback_fires_once_per_hand_off(self):
        runner = runner_with({"grok": "idle", "claude": "idle"})
        seen = []
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok", "tester": "claude"},
            BY_NAME,
            self.paths,
            AT,
            on_assigned=lambda role, agent, at, status, context: seen.append(
                (role, agent, at, status, context["clear_reason"], context["cleared"])
            ),
        )
        self.assertEqual(
            seen,
            [
                ("developer", "grok", AT, "applied", "automatic", True),
                ("tester", "claude", AT, "applied", "automatic", True),
            ],
        )


class PaneLabelTest(unittest.TestCase):
    """The sidebar says who is doing what, once the hand-off is confirmed."""

    def setUp(self):
        self.paths = {
            "common": "/w/COMMON.md",
            "developer": "/w/dev.md",
            "tester": "/w/test.md",
        }

    def test_the_label_leads_with_the_role_and_omits_the_agent(self):
        # The workspace row already carries the agent's name.
        self.assertEqual(pane_label("developer"), "developer")

    def test_a_task_label_is_appended_with_a_hash(self):
        self.assertEqual(pane_label("developer", "12"), "developer #12")

    def test_a_task_that_already_carries_a_hash_is_not_doubled(self):
        self.assertEqual(pane_label("developer", "#12"), "developer #12")

    def test_the_model_label_trails_after_a_dot(self):
        self.assertEqual(
            pane_label("developer", "12", "gpt-5.6"), "developer #12 \u00b7 gpt-5.6"
        )

    def test_a_model_with_no_task_still_reads(self):
        self.assertEqual(pane_label("developer", None, "grok-4"), "developer \u00b7 grok-4")

    def test_the_task_reaches_the_pane(self):
        runner = runner_with({"grok": "idle"})
        apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            task="12",
        )
        self.assertIn("pane rename w4:p1 'developer #12'", runner.commands())

    QUIET_SCREEN = "  nothing happened\n  │ ❯                                        │\n"

    def test_a_hand_off_that_never_started_is_not_labelled(self):
        # A label claiming a role nobody started is worse than no label. Drive
        # the not-started path the way the landing tests do: a transcript that
        # never shows the message, and a `wait --until working` that times out.
        runner = runner_with({"grok": "idle"})
        runner.responses["agent read grok --source visible --lines 20"] = ScriptedReads(
            [
                composer_screen("grok", "old"),
                composer_screen("grok", "fresh"),
                composer_screen("grok", "fresh"),
                self.QUIET_SCREEN,
            ]
        )
        runner.set(
            "agent wait grok --until working",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"timeout","message":"never left idle"}}',
        )
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=lambda message: None,
            landing_attempts=2,
        )
        self.assertEqual(result["applied"][0]["status"], "sent_but_not_started")
        self.assertIsNone(result["applied"][0]["pane_label"])
        self.assertFalse([c for c in runner.commands() if c.startswith("pane rename")])

    def test_a_failed_rename_never_fails_the_dispatch(self):
        runner = runner_with({"grok": "idle"})
        runner.set("pane rename", stdout="", returncode=1, stderr='{"error":{"code":"no_pane"}}')
        warnings = []
        result = apply(
            HerdrClient(runner=runner),
            {"developer": "grok"},
            BY_NAME,
            self.paths,
            AT,
            warn=warnings.append,
        )
        self.assertEqual(result["applied"][0]["status"], "applied")
        self.assertIsNone(result["applied"][0]["pane_label"])
        self.assertTrue(any("only the sidebar name did not" in w for w in warnings))


class NativeContextSessionTest(unittest.TestCase):
    def test_official_native_reference_is_scoped_to_the_live_pane(self):
        for kind in ("claude", "codex", "grok"):
            for reference_kind in ("id", "path"):
                ref = {"source": "herdr:" + kind, "agent": kind,
                       "kind": reference_kind, "value": "native-session"}
                self.assertEqual(native_context_session({"pane_id": "w1:p2", "agent_session": ref}, kind),
                                 dict(ref, pane_id="w1:p2"))

    def test_missing_malformed_or_non_native_evidence_is_unknown(self):
        valid = {"source": "herdr:grok", "agent": "grok", "kind": "id", "value": "session"}
        for ref in (None, "session", {}, dict(valid, source="user:label"),
                    dict(valid, agent="claude"), dict(valid, kind="label"),
                    dict(valid, value=""), dict(valid, value=5)):
            self.assertIsNone(native_context_session({"pane_id": "w1:p2", "agent_session": ref}, "grok"))
        self.assertIsNone(native_context_session({"agent_session": valid}, "grok"))


if __name__ == "__main__":
    unittest.main()
