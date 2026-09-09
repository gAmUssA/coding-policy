"""Tests for teamlead.cli.

Every test drives `main()` with an injected herdr client and captured streams,
so nothing here spawns a process or reads the real clock (`--now` is always
passed where a timestamp would otherwise be taken).
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

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from teamlead.cli import build_parser, main
from teamlead.herdr import HerdrClient
from teamlead.state import STATE_SCHEMA_VERSION, add_assignment, empty_state, save_state

from tests.fakes import (
    FakeRunner,
    ScriptedReads,
    agent_json,
    composer_reads,
    ok_json,
)

AT = "2026-02-03T10:00:00+00:00"

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

SNAPSHOT = {
    "schema_version": 1,
    "measured_at": AT,
    "agents": {
        "claude": {"kind": "claude", "state": "idle", "headroom_pct": 92.0},
        "codex": {"kind": "codex", "state": "idle", "headroom_pct": 87.0},
        "grok": {"kind": "grok", "state": "done", "headroom_pct": 100.0},
    },
}

CLAUDE_PANE = (
    "   Current session\n   ████    8% used\n   Resets 12:49am (Europe/Oslo)\n"
    "   Current week (all models)\n   █    2% used\n   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
)
GROK_PANE = "     Weekly limit: 0%\n     Next reset: September 6, 12:55\n     Credits: $16.42\n"

# Verbatim grok footers, as the idle probe reads them.
GROK_WORKING_FOOTER = "  │ ❯ go            │\n  Shift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts\n"
GROK_IDLE_FOOTER = "  │ ❯               │\n  Shift+Tab:mode  │  Ctrl+.:shortcuts\n"


class CliCase(unittest.TestCase):
    """Shared temp workspace: config, state, briefs, snapshot."""

    def setUp(self):
        # These fixtures isolate the existing CLI/dispatch contracts. Real
        # retrospective gates and storage are exercised in test_retrospective_runtime.
        retro = patch("teamlead.cli.retrospective_runtime.Guard")
        retro.start()
        self.addCleanup(retro.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="teamlead-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.state = self.tmp / "state.json"
        self.snapshot = self.tmp / "snapshot.json"
        self.snapshot.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        self.common = self.tmp / "COMMON.md"
        self.common.write_text("# common\n", encoding="utf-8")
        self.briefs = {}
        for role in ("developer", "tester", "reviewer"):
            path = self.tmp / (role + ".md")
            path.write_text("# " + role + "\n", encoding="utf-8")
            self.briefs[role] = path
        # A stand-in herdr that always fails, for the paths that build a real
        # client instead of taking an injected one.
        self.fake_herdr = self.tmp / "herdr-stub"
        self.fake_herdr.write_text(
            '#!/bin/sh\necho \'{"error":{"code":"agent_not_found","message":"no"}}\' >&2\nexit 1\n',
            encoding="utf-8",
        )
        self.fake_herdr.chmod(0o755)
        self.out = io.StringIO()
        self.err = io.StringIO()

    def run_cli(self, argv, client=None):
        code = main(argv, stdout=self.out, stderr=self.err, client=client)
        return code, self.out.getvalue(), self.err.getvalue()

    def base(self):
        return ["--config", str(self.config), "--state", str(self.state)]

    def brief_args(self, *roles):
        args = []
        for role in roles:
            args += ["--brief", "{}={}".format(role, self.briefs[role])]
        return args


class ParserTest(unittest.TestCase):
    def test_every_subcommand_is_registered(self):
        parser = build_parser()
        for command in ("measure", "plan", "apply", "state"):
            args = parser.parse_args([command, "--assignments", "{}", "--common", "x"] if command == "apply" else [command])
            self.assertEqual(args.command, command)

    def test_a_missing_subcommand_exits_two(self):
        # argparse prints its usage to the real stderr; capture it so a
        # deliberate failure does not litter the test run.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                build_parser().parse_args([])
        self.assertEqual(caught.exception.code, 2)

    def test_apply_requires_assignments_and_common(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["apply"])


class TraceFlagTest(CliCase):
    """--trace has to reach the real client, which tests never inject."""

    def test_the_flag_is_accepted_before_and_after_the_subcommand(self):
        parser = build_parser()
        self.assertTrue(parser.parse_args(["--trace", "state"]).trace)
        self.assertTrue(parser.parse_args(["state", "--trace"]).trace)

    def test_it_is_absent_by_default(self):
        self.assertFalse(getattr(build_parser().parse_args(["state"]), "trace", False))

    def test_every_herdr_command_is_printed_to_stderr(self):
        # No client injected: the CLI builds one and wires the trace sink to
        # the same stderr stream the warnings use.
        code, out, err = self.run_cli(
            self.base()
            + [
                "--trace",
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "grok",
                "--now",
                AT,
                "--herdr-bin",
                str(self.fake_herdr),
            ]
        )
        self.assertEqual(code, 1)  # the stub herdr refuses every command
        self.assertIn("herdr> {} agent get grok".format(self.fake_herdr), err)
        self.assertIn("exit=1", err)
        self.assertIn("agent_not_found", err)
        # stdout stays the machine-readable snapshot; tracing never pollutes it
        self.assertEqual(json.loads(out)["failed_agents"], ["grok"])

    def test_without_the_flag_nothing_is_traced(self):
        code, _, err = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "grok",
                "--now",
                AT,
                "--herdr-bin",
                str(self.fake_herdr),
            ]
        )
        self.assertEqual(code, 1)
        self.assertNotIn("herdr>", err)


class StateCommandTest(CliCase):
    def test_prints_a_fresh_document_when_no_state_exists(self):
        code, out, err = self.run_cli(self.base() + ["state"])
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out), empty_state()
        )
        self.assertEqual(err, "")

    def test_prints_what_was_written(self):
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [SNAPSHOT],
                    "assignments": [{"at": AT, "role": "developer", "agent": "grok"}],
                }
            ),
            encoding="utf-8",
        )
        code, out, _ = self.run_cli(self.base() + ["state"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"][0]["agent"], "grok")

    def test_routes_state_warnings_to_the_cli_stderr(self):
        self.state.write_text('{"schema_version": 2, broken', encoding="utf-8")
        code, out, err = self.run_cli(self.base() + ["state"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["snapshots"], [])
        self.assertIn("teamlead: state file", err)
        self.assertIn(str(self.state), err)


class PlanCommandTest(CliCase):
    def test_plans_from_a_snapshot_file(self):
        code, out, err = self.run_cli(
            self.base() + ["plan", "--roles", "developer,tester,reviewer", "--snapshot", str(self.snapshot)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out)["assignments"],
            {"developer": "grok", "tester": "claude", "reviewer": "codex"},
        )
        self.assertEqual(err, "")

    def test_routes_state_warnings_to_the_cli_stderr(self):
        self.state.write_text('{"schema_version": 2, broken', encoding="utf-8")
        code, out, err = self.run_cli(
            self.base() + ["plan", "--snapshot", str(self.snapshot)]
        )
        self.assertEqual(code, 0)
        self.assertIn("assignments", json.loads(out))
        self.assertIn("teamlead: state file", err)
        self.assertIn(str(self.state), err)

    def test_roles_default_to_developer_tester_reviewer(self):
        code, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(code, 0)
        self.assertEqual(list(json.loads(out)["assignments"]), ["developer", "tester", "reviewer"])

    def test_snapshot_ref_names_the_file(self):
        _, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(json.loads(out)["snapshot_ref"]["source"], str(self.snapshot))

    def test_falls_back_to_the_newest_snapshot_in_state(self):
        self.state.write_text(
            json.dumps({"schema_version": 1, "snapshots": [SNAPSHOT], "assignments": []}),
            encoding="utf-8",
        )
        code, out, _ = self.run_cli(self.base() + ["plan"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")

    def test_previous_assignments_break_a_headroom_tie(self):
        tied = json.loads(json.dumps(SNAPSHOT))
        for record in tied["agents"].values():
            record["headroom_pct"] = 50.0
        tied_path = self.tmp / "tied.json"
        tied_path.write_text(json.dumps(tied), encoding="utf-8")
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "snapshots": [],
                    "assignments": [
                        {"at": AT, "role": "developer", "agent": "claude"},
                        {"at": AT, "role": "developer", "agent": "codex"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        _, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(tied_path)])
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")

    def test_no_snapshot_anywhere_is_an_actionable_error(self):
        code, out, err = self.run_cli(self.base() + ["plan"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("teamlead measure", json.loads(err)["message"])

    def test_missing_snapshot_file_is_an_actionable_error(self):
        code, _, err = self.run_cli(self.base() + ["plan", "--snapshot", str(self.tmp / "no.json")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "plan_error")

    def test_plan_never_touches_herdr(self):
        runner = FakeRunner()
        code, _, _ = self.run_cli(
            self.base() + ["plan", "--snapshot", str(self.snapshot)],
            client=HerdrClient(runner=runner),
        )
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])

    def test_exclude_bars_an_agent_from_one_role(self):
        code, out, _ = self.run_cli(
            self.base()
            + ["plan", "--snapshot", str(self.snapshot), "--exclude", "developer=grok"]
        )
        self.assertEqual(code, 0)
        assignments = json.loads(out)["assignments"]
        self.assertEqual(assignments["developer"], "claude")
        self.assertNotEqual(assignments["developer"], "grok")

    def test_exclude_repeats_to_bar_the_author_from_two_seats(self):
        code, out, _ = self.run_cli(
            self.base()
            + [
                "plan",
                "--snapshot",
                str(self.snapshot),
                "--exclude",
                "reviewer=grok",
                "--exclude",
                "tester=grok",
            ]
        )
        self.assertEqual(code, 0)
        assignments = json.loads(out)["assignments"]
        self.assertEqual(assignments["developer"], "grok")
        self.assertNotIn("grok", [assignments["reviewer"], assignments["tester"]])

    def test_exclude_takes_a_comma_separated_list(self):
        code, out, _ = self.run_cli(
            self.base()
            + [
                "plan",
                "--roles",
                "developer,reviewer",
                "--snapshot",
                str(self.snapshot),
                "--exclude",
                "reviewer=grok,claude",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"]["reviewer"], "codex")

    def test_a_malformed_exclude_is_an_actionable_usage_error(self):
        code, out, err = self.run_cli(
            self.base() + ["plan", "--snapshot", str(self.snapshot), "--exclude", "grok"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["error"], "usage_error")
        self.assertIn("ROLE=AGENT", json.loads(err)["message"])

    def test_role_costs_in_the_config_reweigh_the_seats(self):
        config = json.loads(json.dumps(CONFIG))
        config["role_costs"] = {"reviewer": 40}
        self.config.write_text(json.dumps(config), encoding="utf-8")
        code, out, _ = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(code, 0)
        # reviewer now outweighs developer, so it is filled first and takes
        # the agent with the most headroom.
        self.assertEqual(json.loads(out)["assignments"]["reviewer"], "grok")

    def test_a_broken_role_costs_map_fails_naming_the_config(self):
        config = json.loads(json.dumps(CONFIG))
        config["role_costs"] = {"reviewer": "cheap"}
        self.config.write_text(json.dumps(config), encoding="utf-8")
        code, _, err = self.run_cli(self.base() + ["plan", "--snapshot", str(self.snapshot)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "config_error")
        self.assertIn(str(self.config), json.loads(err)["message"])

    def test_planning_works_on_a_machine_with_no_config_at_all(self):
        code, out, _ = self.run_cli(
            [
                "--config",
                str(self.tmp / "absent.json"),
                "--state",
                str(self.state),
                "plan",
                "--snapshot",
                str(self.snapshot),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["assignments"]["developer"], "grok")


class MeasureCommandTest(CliCase):
    CORRUPT_STATE = '{"schema_version": 2, "snapshots": [broken'

    def _client(self, statuses, footers=None):
        footers = footers or {}
        runner = FakeRunner()
        panes = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
        texts = {"claude": CLAUDE_PANE, "grok": GROK_PANE}
        for name, status in statuses.items():
            runner.set("agent get " + name, agent_json(name, status, panes[name]))
            runner.set("agent prompt " + name, ok_json("agent_prompt"))
            runner.set("agent send-keys " + name, ok_json("agent_send_keys"))
            if name in texts:
                runner.set("agent read " + name, texts[name])
            if name in footers:
                runner.set(
                    "agent read {} --source visible --lines 40".format(name), footers[name]
                )
        runner.set("pane wait-output", ok_json("output_matched"))
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("pane rename", ok_json("pane_rename"))
        for name in statuses:
            runner.responses[
                "agent read {} --source visible --lines 20".format(name)
            ] = composer_reads(name)
        self.runner = runner
        return HerdrClient(runner=runner)

    def test_measures_the_named_agents_only(self):
        client = self._client({"claude": "idle", "grok": "done"})
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "claude", "--agent", "grok", "--now", AT],
            client=client,
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(sorted(payload["agents"]), ["claude", "grok"])
        self.assertEqual(payload["measured_at"], AT)
        self.assertEqual(err, "")

    def test_snapshot_is_appended_to_the_state_file(self):
        client = self._client({"grok": "idle"})
        self.run_cli(self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(state["snapshots"]), 1)
        self.assertEqual(state["snapshots"][0]["agents"]["grok"]["headroom_pct"], 100.0)

    def test_a_busy_agent_is_skipped_and_never_written_to(self):
        client = self._client({"claude": "idle", "codex": "working", "grok": "done"})
        code, out, _ = self.run_cli(self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--now", AT], client=client)
        self.assertEqual(code, 0)
        codex = json.loads(out)["agents"]["codex"]
        self.assertEqual(codex["state"], "working")
        self.assertIsNone(codex["headroom_pct"])
        self.assertEqual([c for c in self.runner.writes() if "codex" in c], [])

    def test_a_parse_failure_exits_one_but_still_prints_the_snapshot(self):
        client = self._client({"claude": "idle"})
        self.runner.set("agent read claude", "nothing useful here\n")
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "claude", "--now", AT], client=client
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["failed_agents"], ["claude"])
        self.assertEqual(json.loads(err)["error"], "measure_incomplete")

    def test_records_which_signal_decided_the_state(self):
        client = self._client({"grok": "idle"})
        _, out, _ = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        record = json.loads(out)["agents"]["grok"]
        self.assertEqual(record["state_source"], "herdr")
        self.assertEqual(record["herdr_state"], "idle")

    def test_a_stale_herdr_state_is_overturned_and_warned_about_on_stderr(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        self.assertEqual(code, 0)
        record = json.loads(out)["agents"]["grok"]
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["herdr_state"], "working")
        self.assertEqual(record["state_source"], "probe")
        self.assertEqual(record["headroom_pct"], 100.0)
        self.assertIn("stale", err)

    def test_a_genuinely_working_agent_is_still_skipped(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_WORKING_FOOTER})
        code, out, _ = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "grok", "--now", AT], client=client
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["agents"]["grok"]["skipped"])
        self.assertEqual(self.runner.writes(), [])

    def test_each_agent_asks_for_usage_by_its_configured_path_end_to_end(self):
        client = self._client({"claude": "idle", "grok": "done"})
        code, _, _ = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval",
                "0",
                "--agent",
                "claude",
                "--agent",
                "grok",
                "--now",
                AT,
            ],
            client=client,
        )
        self.assertEqual(code, 0)
        commands = self.runner.commands()
        self.assertIn("agent prompt claude /usage", commands)  # claude pastes
        self.assertIn("pane send-text w4:p1 /usage", commands)  # grok types
        self.assertNotIn("agent prompt grok /usage", commands)

    def test_unknown_agent_name_is_an_actionable_error(self):
        code, out, err = self.run_cli(
            self.base() + ["measure", "--marker-poll-interval", "0", "--composer-settle", "0", "--agent", "gemini", "--now", AT],
            client=HerdrClient(runner=FakeRunner()),
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("gemini", json.loads(err)["message"])

    def test_missing_config_names_the_example_file(self):
        code, _, err = self.run_cli(
            ["--config", str(self.tmp / "gone.json"), "--state", str(self.state), "measure"],
            client=HerdrClient(runner=FakeRunner()),
        )
        self.assertEqual(code, 1)
        self.assertIn("config.example.json", json.loads(err)["message"])


    def test_an_unreadable_state_file_is_never_overwritten(self):
        # load_state leaves such a file exactly as found and hands back an
        # empty document. Saving that document over it would undo precisely
        # that preservation, taking the ledger with it.
        self.state.write_text(self.CORRUPT_STATE, encoding="utf-8")
        client = self._client({"grok": "done"})
        code, out, err = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval", "0",
                "--composer-settle", "0",
                "--agent", "grok",
                "--now", AT,
            ],
            client=client,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("would destroy its contents", err)
        self.assertIn(".bak", err)
        self.assertEqual(self.state.read_text(encoding="utf-8"), self.CORRUPT_STATE)
        self.assertEqual(out, "")

    def test_a_missing_state_file_still_writes(self):
        # Nothing to lose is not the same as something unreadable.
        client = self._client({"grok": "done"})
        code, _out, _err = self.run_cli(
            self.base()
            + [
                "measure",
                "--marker-poll-interval", "0",
                "--composer-settle", "0",
                "--agent", "grok",
                "--now", AT,
            ],
            client=client,
        )
        self.assertEqual(code, 0)
        self.assertTrue(self.state.exists())

class ApplyCommandTest(CliCase):
    CORRUPT_STATE = '{"schema_version": 2, "snapshots": [broken'

    def _client(self, statuses, footers=None, sessions=None):
        footers = footers or {}
        runner = FakeRunner()
        panes = {"claude": "w2:p1", "codex": "w3:p1", "grok": "w4:p1"}
        for name, status in statuses.items():
            runner.set("agent get " + name, agent_json(name, status, panes[name], (sessions or {}).get(name)))
            runner.set("agent prompt " + name, ok_json("agent_prompt"))
            runner.set("agent wait " + name, ok_json("agent_wait"))
            runner.set(
                "agent read {} --source visible --lines 40".format(name),
                footers.get(name, GROK_WORKING_FOOTER),
            )
        runner.set("pane send-text", ok_json("pane_send_text"))
        runner.set("pane send-keys", ok_json("pane_send_keys"))
        runner.set("pane rename", ok_json("pane_rename"))
        for name in statuses:
            runner.responses[
                "agent read {} --source visible --lines 20".format(name)
            ] = composer_reads(name)
        self.runner = runner
        return HerdrClient(binary="herdr", runner=runner)

    def test_dry_run_prints_commands_and_sends_nothing(self):
        client = self._client({})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--dry-run",
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(self.runner.calls, [])
        shells = [command["shell"] for command in payload["steps"][0]["commands"]]
        self.assertEqual(shells[0], "herdr agent get grok")
        self.assertIn("herdr pane send-text PANE-ID-RESOLVED-AT-RUN-TIME /new", shells)
        self.assertIn("herdr pane send-keys PANE-ID-RESOLVED-AT-RUN-TIME enter", shells)
        self.assertTrue(any("DEVELOPER" in shell for shell in shells))
        self.assertEqual(shells[-1], "herdr agent wait grok --until working --timeout 15000")
        self.assertEqual(err, "")

    def test_dry_run_writes_nothing_to_the_state_file(self):
        client = self._client({})
        self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--dry-run"]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertFalse(self.state.exists())

    def test_assignments_can_be_a_file_of_plan_output(self):
        plan_file = self.tmp / "plan.json"
        plan_file.write_text(
            json.dumps({"schema_version": 1, "assignments": {"developer": "grok"}}), encoding="utf-8"
        )
        client = self._client({})
        code, out, _ = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", str(plan_file), "--common", str(self.common), "--dry-run"]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["steps"][0]["agent"], "grok")

    def test_live_apply_clears_then_assigns_and_records_the_ledger(self):
        client = self._client({"grok": "idle", "claude": "done"})
        code, out, _ = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok", "tester": "claude"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer", "tester"),
            client=client,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["applied"]), 2)
        ledger = json.loads(self.state.read_text(encoding="utf-8"))["assignments"]
        self.assertEqual(
            ledger,
            [
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "at": AT,
                    "role": "developer",
                    "agent": "grok",
                    "status": "applied",
                    "cleared": True,
                    "clear_reason": "automatic",
                    "task": None,
                    "fix_round": None,
                    "context_session": None,
                    "tier": None,
                },
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "at": AT,
                    "role": "tester",
                    "agent": "claude",
                    "status": "applied",
                    "cleared": True,
                    "clear_reason": "automatic",
                    "task": None,
                    "fix_round": None,
                    "context_session": None,
                    "tier": None,
                },
            ],
        )

    def test_each_agent_clears_by_its_configured_path_end_to_end(self):
        client = self._client({"grok": "idle", "claude": "done"})
        code, _, _ = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok", "tester": "claude"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer", "tester"),
            client=client,
        )
        self.assertEqual(code, 0)
        commands = self.runner.commands()
        self.assertIn("pane send-text w4:p1 /new", commands)  # grok types
        self.assertIn("agent prompt claude /clear", commands)  # claude pastes
        self.assertNotIn("agent prompt grok /new", commands)

    def test_a_not_started_round_is_recorded_but_never_counted(self):
        # End to end: apply writes the row, plan does not count it as
        # experience of the role.
        client = self._client({"grok": "idle"}, footers={"grok": GROK_IDLE_FOOTER})
        # The clear must still change the screen (that gate is real); it is
        # the assignment that never lands.
        self.runner.responses[
            "agent read grok --source visible --lines 20"
        ] = ScriptedReads(
            [
                "  old transcript\n  │ ❯          │\n",
                "  fresh session\n  │ ❯          │\n",
                "  fresh session\n  │ ❯          │\n",
                "  fresh session\n  │ ❯          │\n",
            ]
        )
        self.runner.set(
            "agent wait grok --until working",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"timeout","message":"never left idle"}}',
        )
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["applied"][0]["status"], "sent_but_not_started")
        self.assertIn("sent_but_not_started", err)

        ledger = json.loads(self.state.read_text(encoding="utf-8"))["assignments"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["status"], "sent_but_not_started")

        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, _ = self.run_cli(
            self.base() + ["plan", "--roles", "developer", "--snapshot", str(self.snapshot)]
        )
        self.assertEqual(code, 0)
        self.assertIn("held this role 0x before", json.loads(out)["rationale"][0])

    def test_busy_agent_is_refused_with_a_json_error_and_no_writes(self):
        client = self._client({"grok": "working"})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["error"], "agent_busy")
        self.assertEqual(payload["details"]["busy"], {"grok": "working"})
        self.assertEqual(self.runner.writes(), [])

    def test_a_refused_round_leaves_the_ledger_untouched(self):
        client = self._client({"grok": "blocked"})
        self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--now", AT]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertFalse(self.state.exists())

    def test_apply_proceeds_when_the_probe_overturns_a_stale_herdr_state(self):
        client = self._client({"grok": "working"}, footers={"grok": GROK_IDLE_FOOTER})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(code, 0)
        applied = json.loads(out)["applied"][0]
        self.assertEqual(applied["state_source"], "probe")
        self.assertEqual(applied["herdr_state_before"], "working")
        self.assertIn("stale", err)

    def _rejects(self, argv):
        # argparse prints its usage to stderr on a bad flag; swallow it so the
        # suite's own output stays readable.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(argv)

    def test_apply_has_no_force_flag(self):
        # The override is gone from the surface, not merely discouraged.
        self._rejects(["apply", "--assignments", "{}", "--common", "x", "--force"])

    def test_measure_has_no_force_flag(self):
        self._rejects(["measure", "--force"])

    def test_a_busy_agent_refuses_the_whole_round(self):
        client = self._client({"grok": "working"})
        code, out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("Refusing to interrupt", err)

    def test_no_clear_skips_the_clear_prompt(self):
        client = self._client({"grok": "idle"})
        self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
                "--no-clear",
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertEqual(len(self.runner.writes()), 1)
        row = json.loads(self.state.read_text(encoding="utf-8"))["assignments"][-1]
        self.assertFalse(row["cleared"])
        self.assertEqual(row["clear_reason"], "hand")

    def _seed_context(self, *, task="repo#322", role="developer", status="applied", fix_round=None, session="task-session"):
        state = empty_state()
        add_assignment(
            state, AT, role, "grok", status=status, task=task, fix_round=fix_round,
            cleared=True, clear_reason="automatic",
            context_session={"pane_id": "w4:p1", "source": "herdr:grok", "agent": "grok",
                             "kind": "id", "value": session},
        )
        save_state(self.state, state)

    def _foreground(self, pane_id, argv):
        return json.dumps({"result": {"process_info": {"pane_id": pane_id,
            "foreground_processes": [{"name": argv[0], "pid": 200, "argv": argv}]}}})

    def _fix_args(self, round_number=1, *extra):
        return self.base() + [
            "apply", "--composer-settle", "0", "--assignments",
            json.dumps({"developer": "grok"}), "--common", str(self.common),
            "--now", AT, "--task", "repo#322", "--fix-round", str(round_number),
            *extra,
        ] + self.brief_args("developer")

    def test_retained_context_sends_one_prompt_and_persists_its_reason(self):
        self._seed_context()
        code, out, err = self.run_cli(
            self._fix_args(1, "--retain-context"), client=self._client({"grok": "idle"}, sessions={"grok": "task-session"})
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.runner.writes()), 1)
        row = json.loads(self.state.read_text(encoding="utf-8"))["assignments"][-1]
        self.assertFalse(row["cleared"])
        self.assertEqual(row["clear_reason"], "retained")
        self.assertEqual((row["task"], row["fix_round"]), ("repo#322", 1))
        self.assertEqual(json.loads(out)["applied"][0]["clear_reason"], "retained")

    def test_retained_fix_accepts_a_resumed_yolo_developer_and_keeps_its_identity(self):
        # coding-policy#382: the developer was restored with the documented resume form
        # and its explicit YOLO flag. The retained fix verifies that process instead of
        # demanding a fresh worker; task, native session and the fix count are unchanged.
        session = "3b1a2c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
        self._seed_context(session=session)
        client = self._client({"grok": "idle"}, sessions={"grok": session})
        self.runner.set("pane process-info --pane w4:p1",
                        self._foreground("w4:p1", ["grok", "--resume", session, "--always-approve", "--no-subagents"]))
        code, out, err = self.run_cli(self._fix_args(1, "--retain-context"), client=client)
        self.assertEqual(code, 0, err)
        record = json.loads(out)["applied"][0]
        self.assertEqual((record["task"], record["fix_round"], record["cleared"], record["clear_reason"]),
                         ("repo#322", 1, False, "retained"))
        self.assertEqual(record["context_session"]["value"], session)
        self.assertEqual(len(self.runner.writes()), 1)
        self.assertFalse(any(command.startswith(("agent start", "-TERM")) or "/new" in command
                             for command in self.runner.commands()))
        rows = json.loads(self.state.read_text(encoding="utf-8"))["assignments"]
        self.assertEqual([(row["task"], row["fix_round"]) for row in rows], [("repo#322", None), ("repo#322", 1)])
        self.assertEqual(rows[-1]["context_session"]["value"], session)

    def test_retained_fix_refuses_an_ambiguous_resume_before_any_input(self):
        session = "3b1a2c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
        for argv in (["grok", "--continue", "--always-approve"],
                     ["grok", "--resume", "--always-approve"],
                     ["grok", "--resume", session]):
            with self.subTest(argv=argv):
                self.out, self.err = io.StringIO(), io.StringIO()
                self._seed_context(session=session)
                before = self.state.read_bytes()
                client = self._client({"grok": "idle"}, sessions={"grok": session})
                self.runner.set("pane process-info --pane w4:p1", self._foreground("w4:p1", argv))
                code, out, err = self.run_cli(self._fix_args(1, "--retain-context"), client=client)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn("before dispatch", err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), before)

    def test_retention_rejects_wrong_task_role_status_or_round_before_herdr(self):
        for task, role, status, fix_round in (
            ("another", "developer", "applied", None),
            ("repo#322", "tester", "applied", None),
            ("repo#322", "developer", "sent_but_not_started", None),
            ("repo#322", "developer", "applied", 2),
        ):
            with self.subTest(task=task, role=role, status=status, fix_round=fix_round):
                self.out = io.StringIO()
                self.err = io.StringIO()
                self._seed_context(task=task, role=role, status=status, fix_round=fix_round)
                code, _, err = self.run_cli(
                    self._fix_args(1, "--retain-context"), client=self._client({"grok": "idle"})
                )
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(err)["error"], "usage_error")
                self.assertEqual(self.runner.calls, [])

    def test_retention_requires_confirmed_history(self):
        code, _, err = self.run_cli(
            self._fix_args(1, "--retain-context"), client=self._client({"grok": "idle"})
        )
        self.assertEqual(code, 1)
        self.assertIn("preceding confirmed", err)
        self.assertEqual(self.runner.calls, [])

    def test_fix_round_four_clears_context(self):
        self._seed_context(fix_round=3)
        code, out, err = self.run_cli(
            self._fix_args(4), client=self._client({"grok": "idle"})
        )
        self.assertEqual(code, 0, err)
        row = json.loads(out)["applied"][0]
        self.assertTrue(row["cleared"])
        self.assertEqual((row["clear_reason"], row["fix_round"]), ("automatic", 4))
        self.assertGreater(len(self.runner.writes()), 1)

    def test_retention_at_four_and_fix_round_six_are_refused(self):
        for number, extra in ((4, ["--retain-context"]), (6, [])):
            with self.subTest(number=number):
                self.out = io.StringIO()
                self.err = io.StringIO()
                code, _, _ = self.run_cli(
                    self._fix_args(number, *extra), client=self._client({"grok": "idle"})
                )
                self.assertEqual(code, 1)
                self.assertEqual(self.runner.calls, [])

    def test_retained_dry_run_has_no_clear_and_no_state_write(self):
        code, out, err = self.run_cli(
            self._fix_args(1, "--retain-context", "--dry-run"), client=self._client({})
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["clear_reason"], "retained")
        commands = [command["argv"] for command in payload["steps"][0]["commands"]]
        self.assertFalse(any("/new" in command for command in commands))
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.state.exists())

    def test_context_flags_are_mutually_exclusive(self):
        self._rejects(self._fix_args(1, "--retain-context", "--no-clear"))

    def test_task_padding_is_rejected_without_rewriting_existing_history(self):
        self._seed_context(task="repo#322 ")
        before = self.state.read_bytes()
        for task in (" repo#322", "repo#322 ", "\trepo#322", "repo#322\n", "repo#322\u00a0"):
            for dry_run in (False, True):
                with self.subTest(task=task, dry_run=dry_run):
                    self.out, self.err = io.StringIO(), io.StringIO()
                    args = self._fix_args(1, "--retain-context")
                    args[args.index("--task") + 1] = task
                    if dry_run:
                        args.append("--dry-run")
                    code, _, err = self.run_cli(args, client=self._client({}))
                    self.assertEqual(code, 1)
                    self.assertIn("leading or trailing whitespace", err)
                    self.assertEqual(self.runner.calls, [])
                    self.assertEqual(self.state.read_bytes(), before)

    def test_existing_task_with_internal_spaces_is_not_renamed(self):
        task = "repo task 322"
        self._seed_context(task=task)
        args = self._fix_args(1, "--retain-context")
        args[args.index("--task") + 1] = task
        code, out, err = self.run_cli(
            args, client=self._client({"grok": "idle"}, sessions={"grok": "task-session"})
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["task"], task)

    def test_retention_round_three_succeeds_after_confirmed_round_two(self):
        self._seed_context(fix_round=2)
        code, out, err = self.run_cli(
            self._fix_args(3, "--retain-context"), client=self._client({"grok": "idle"}, sessions={"grok": "task-session"})
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.runner.writes()), 1)
        self.assertEqual(json.loads(out)["applied"][0]["fix_round"], 3)

    def test_retention_still_refuses_a_live_busy_worker(self):
        self._seed_context()
        before = self.state.read_bytes()
        code, _, err = self.run_cli(
            self._fix_args(1, "--retain-context"), client=self._client({"grok": "blocked"})
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "agent_busy")
        self.assertEqual(self.runner.writes(), [])
        self.assertEqual(self.state.read_bytes(), before)

    def test_fresh_rounds_refuse_no_clear(self):
        for number in (4, 5):
            with self.subTest(number=number):
                self.out, self.err = io.StringIO(), io.StringIO()
                code, _, err = self.run_cli(
                    self._fix_args(number, "--no-clear"), client=self._client({})
                )
                self.assertEqual(code, 1)
                self.assertIn("automatic clear", err)
                self.assertEqual(self.runner.calls, [])

    def test_retention_cannot_use_migrated_history(self):
        self.state.write_text(json.dumps({"schema_version": 2, "snapshots": [],
            "assignments": [{"schema_version": 2, "at": AT, "agent": "grok",
                             "role": "developer", "status": "applied"}]}), encoding="utf-8")
        code, _, err = self.run_cli(
            self._fix_args(1, "--retain-context"), client=self._client({})
        )
        self.assertEqual(code, 1)
        self.assertIn("preceding confirmed", err)
        self.assertEqual(self.runner.calls, [])
        row = json.loads(self.state.read_text(encoding="utf-8"))["assignments"][0]
        self.assertEqual(row["clear_reason"], "unknown")

    def test_completed_fixes_cannot_restart_as_initial_development(self):
        self._seed_context(fix_round=5)
        args = self._fix_args(5)
        position = args.index("--fix-round")
        del args[position:position + 2]
        code, _, err = self.run_cli(args, client=self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("do not reset", err)
        self.assertEqual(self.runner.calls, [])

    def test_fresh_fix_cannot_skip_the_task_history(self):
        self._seed_context(fix_round=1)
        code, _, err = self.run_cli(self._fix_args(4), client=self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("next fix number 2", err)
        self.assertEqual(self.runner.calls, [])

    def test_early_developer_fix_requires_explicit_retention(self):
        self._seed_context()
        code, _, err = self.run_cli(self._fix_args(1), client=self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("require --retain-context", err)
        self.assertEqual(self.runner.calls, [])

    def test_fresh_worker_continues_another_workers_fix_count(self):
        state = empty_state()
        add_assignment(state, AT, "developer", "claude", task="repo#322",
                       fix_round=3, cleared=False, clear_reason="retained")
        save_state(self.state, state)
        code, out, err = self.run_cli(self._fix_args(4), client=self._client({"grok": "idle"}))
        self.assertEqual(code, 0, err)
        row = json.loads(out)["applied"][0]
        self.assertEqual((row["agent"], row["fix_round"]), ("grok", 4))
        self.assertTrue(row["cleared"])

    def test_intervening_role_prevents_retention_even_with_matching_task_history(self):
        state = empty_state()
        add_assignment(state, AT, "developer", "grok", task="repo#322",
                       cleared=True, clear_reason="automatic")
        add_assignment(state, "2026-02-03T10:00:01+00:00", "tester", "grok", task="another-task",
                       cleared=True, clear_reason="automatic")
        save_state(self.state, state)
        code, _, err = self.run_cli(self._fix_args(1, "--retain-context"), client=self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("Cannot retain", err)
        self.assertEqual(self.runner.calls, [])

    def test_retention_refuses_a_missing_or_changed_live_native_session(self):
        for session in (None, "after-manual-clear", "restarted-worker"):
            with self.subTest(session=session):
                self.out, self.err = io.StringIO(), io.StringIO()
                self._seed_context()
                before = self.state.read_bytes()
                code, _, err = self.run_cli(
                    self._fix_args(1, "--retain-context"),
                    client=self._client({"grok": "idle"}, sessions={"grok": session}),
                )
                self.assertEqual(code, 1)
                self.assertIn("native session continuity", err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), before)

    def test_native_session_change_between_readiness_and_send_refuses_retention(self):
        self._seed_context()
        before = self.state.read_bytes()
        client = self._client({"grok": "idle"})
        self.runner.responses["agent get grok"] = ScriptedReads([
            agent_json("grok", "idle", "w4:p1", "task-session"),
            agent_json("grok", "idle", "w4:p1", "new-session"),
        ])
        code, _, err = self.run_cli(self._fix_args(1, "--retain-context"), client=client)
        self.assertEqual(code, 1)
        self.assertIn("native session continuity", err)
        self.assertEqual(self.runner.writes(), [])
        after = json.loads(self.state.read_text())
        self.assertEqual(after["assignments"], json.loads(before)["assignments"])
        self.assertEqual(after["recovery"]["dispatches"][-1]["status"], "not_sent")

    def test_initial_dispatch_captures_post_clear_identity_for_a_real_retained_fix(self):
        client = self._client({"grok": "idle"})
        self.runner.responses["agent get grok"] = ScriptedReads([
            agent_json("grok", "idle", "w4:p1", "old-task"),
            agent_json("grok", "idle", "w4:p1", "new-task"),
        ])
        args = self._fix_args(1)
        position = args.index("--fix-round")
        del args[position:position + 2]
        code, out, err = self.run_cli(args, client=client)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["context_session"]["value"], "new-task")
        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, err = self.run_cli(
            self._fix_args(1, "--retain-context"),
            client=self._client({"grok": "idle"}, sessions={"grok": "new-task"}),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.runner.writes()), 1)
        row = json.loads(self.state.read_text(encoding="utf-8"))["assignments"][-1]
        self.assertEqual((row["fix_round"], row["clear_reason"]), (1, "retained"))
        self.assertEqual(row["context_session"]["value"], "new-task")

    def test_unchanged_pre_clear_session_is_not_saved_as_fresh_context(self):
        args = self._fix_args(1)
        position = args.index("--fix-round")
        del args[position:position + 2]
        code, out, err = self.run_cli(
            args, client=self._client({"grok": "idle"}, sessions={"grok": "stale-reference"}),
        )
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["applied"][0]["context_session"])
        self.assertIn("no verified post-clear", err)

    def test_identity_appearing_after_first_prompt_supports_the_next_retained_fix(self):
        client = self._client({"grok": "idle"})
        self.runner.responses["agent get grok"] = ScriptedReads([
            agent_json("grok", "idle", "w4:p1", "previous-task"),
            agent_json("grok", "idle", "w4:p1"),
            agent_json("grok", "working", "w4:p1"),
            agent_json("grok", "working", "w4:p1", "delayed-native-id"),
        ])
        args = self._fix_args(1)
        position = args.index("--fix-round")
        del args[position:position + 2]
        code, out, err = self.run_cli(args, client=client)
        self.assertEqual(code, 0, err)
        row = json.loads(self.state.read_text())["assignments"][-1]
        self.assertEqual(row["context_session"]["value"], "delayed-native-id")
        self.assertEqual(row["status"], "applied")
        self.assertEqual(sum(command.startswith("agent prompt grok 'New assignment")
                             for command in self.runner.commands()), 1)
        self.out, self.err = io.StringIO(), io.StringIO()
        code, out, err = self.run_cli(self._fix_args(1, "--retain-context"),
                                    client=self._client({"grok": "idle"}, sessions={"grok": "delayed-native-id"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["fix_round"], 1)
        self.assertEqual(len(self.runner.writes()), 1)

    def test_unprovable_post_dispatch_identity_preserves_the_sent_assignment(self):
        malformed = json.loads(agent_json("grok", "working", "w4:p1", "new-session"))
        malformed["result"]["agent"]["agent_session"]["source"] = "pane-label"
        for before, after in (
            (None, agent_json("grok", "working", "w4:p1")),
            (None, agent_json("grok", "working", "w4:p1", "old-session")),
            (None, json.dumps(malformed)),
            (None, agent_json("grok", "working", "different-pane", "new-session")),
            ("first-session", agent_json("grok", "working", "w4:p1", "replacement-session")),
        ):
            with self.subTest(before=before, after=after):
                save_state(self.state, empty_state())
                self.out, self.err = io.StringIO(), io.StringIO()
                client = self._client({"grok": "idle"})
                self.runner.responses["agent get grok"] = ScriptedReads([
                    agent_json("grok", "idle", "w4:p1", "old-session"),
                    agent_json("grok", "idle", "w4:p1", before), after,
                ])
                args = self._fix_args(1)
                position = args.index("--fix-round")
                del args[position:position + 2]
                code, out, err = self.run_cli(args, client=client)
                self.assertEqual(code, 0, err)
                records = json.loads(self.state.read_text())["assignments"]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], "applied")
                self.assertIsNone(records[0]["context_session"])
                self.assertTrue(err)
                self.assertEqual(sum(command.startswith("agent prompt grok 'New assignment")
                                     for command in self.runner.commands()), 1)

    def test_missing_brief_flag_is_an_actionable_error(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", json.dumps({"developer": "grok"}), "--common", str(self.common), "--dry-run"],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("--brief developer=", json.loads(err)["message"])

    def test_malformed_brief_pair_is_rejected(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--brief",
                "developer",
                "--dry-run",
            ],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("ROLE=PATH", json.loads(err)["message"])

    def test_nonexistent_brief_file_is_rejected_before_anything_is_sent(self):
        client = self._client({"grok": "idle"})
        code, _, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--composer-settle",
                "0",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--brief",
                "developer={}".format(self.tmp / "missing.md"),
                "--now",
                AT,
            ],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertIn("missing.md", json.loads(err)["message"])
        self.assertEqual(self.runner.calls, [])

    def test_malformed_assignments_json_is_rejected(self):
        client = self._client({})
        code, _, err = self.run_cli(
            self.base()
            + ["apply", "--composer-settle", "0", "--assignments", "{not json", "--common", str(self.common), "--dry-run"],
            client=client,
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "usage_error")


    def test_an_unreadable_state_file_is_never_overwritten(self):
        self.state.write_text(self.CORRUPT_STATE, encoding="utf-8")
        client = self._client({"grok": "idle"})
        code, _out, err = self.run_cli(
            self.base()
            + [
                "apply",
                "--assignments",
                json.dumps({"developer": "grok"}),
                "--common",
                str(self.common),
                "--now",
                AT,
            ]
            + self.brief_args("developer"),
            client=client,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("would destroy its contents", err)
        self.assertEqual(self.state.read_text(encoding="utf-8"), self.CORRUPT_STATE)

if __name__ == "__main__":
    unittest.main()
