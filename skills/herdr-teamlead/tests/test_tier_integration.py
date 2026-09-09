"""Exercise tier planning, dispatch refusal, verified handoff, and old ledgers."""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.herdr import HerdrClient
from teamlead.planner import plan
from teamlead.state import add_assignment, empty_state, load_state_checked, role_counts
from tests.fakes import FakeRunner, ScriptedReads, agent_json, composer_reads, composer_screen, ok_json
from tests.test_cli import CliCase, CONFIG
from tests.test_qualification import AT, qualified_tier


class TierIntegrationTest(CliCase):
    def setUp(self):
        super().setUp()
        self.settings = copy.deepcopy(CONFIG)
        self.settings["schema_version"] = 2
        self.settings["agents"] = self.settings["agents"][:1]
        self.settings["agents"][0]["tiers"] = {"build": qualified_tier()}
        self.write_config()

    def write_config(self):
        self.config.write_text(json.dumps(self.settings), encoding="utf-8")

    def apply_args(self, document=None):
        return ["apply", *self.base(), "--assignments", json.dumps(document or {"developer": "claude"}),
                "--common", str(self.common), *self.brief_args("developer"), "--now", AT, "--composer-settle", "0"]

    def test_planner_skips_unqualified_seat_with_more_headroom(self):
        other = copy.deepcopy(self.settings["agents"][0])
        other["name"] = "spare"
        other["tiers"]["build"]["qualification"] = []
        self.settings["agents"].append(other)
        self.write_config()
        self.snapshot.write_text(json.dumps({"agents": {"claude": {"headroom_pct": 50}, "spare": {"headroom_pct": 99}}}))
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                        "--snapshot", str(self.snapshot), "--now", AT])
        self.assertEqual(rc, 0, error)
        self.assertEqual(json.loads(output)["assignments"], {"developer": "claude"})

    def test_unqualified_live_dispatch_refuses_before_any_herdr_call(self):
        self.settings["agents"][0]["tiers"]["build"]["qualification"] = []
        self.write_config()
        runner = FakeRunner()
        rc, output, error = self.run_cli(self.apply_args(), client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 1)
        self.assertIn("paired promotion", error)
        self.assertEqual(output, "")
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.state.exists())

    def test_unqualified_default_plan_refuses_with_preview_recovery(self):
        self.settings["agents"][0]["tiers"]["build"]["qualification"] = []
        self.write_config()
        rc, _, error = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                    "--snapshot", str(self.snapshot), "--now", AT])
        self.assertEqual(rc, 1)
        self.assertIn("--preview-tiers", error)

    def test_invalid_utf8_json_inputs_report_the_path_without_worker_calls(self):
        invalid = self.tmp / "invalid.json"
        invalid.write_bytes(b"\xff\xfe")
        cases = [
            ["plan", *self.base(), "--roles", "developer", "--snapshot", str(self.snapshot), "--round-context", str(invalid), "--now", AT],
            ["plan", *self.base(), "--roles", "developer", "--snapshot", str(invalid), "--now", AT],
            ["apply", *self.base(), "--assignments", str(invalid), "--common", str(self.common)],
            ["plan", *self.base(), "--config", str(invalid), "--roles", "developer", "--snapshot", str(self.snapshot), "--now", AT],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.out.seek(0)
                self.out.truncate()
                self.err.seek(0)
                self.err.truncate()
                runner = FakeRunner()
                rc, output, error = self.run_cli(args, client=HerdrClient("herdr", runner))
                self.assertEqual(rc, 1)
                self.assertEqual(output, "")
                self.assertIn(str(invalid), error)
                self.assertIn("UTF-8 JSON", error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(runner.calls, [])
                self.assertEqual(invalid.read_bytes(), b"\xff\xfe")
        warnings = []
        state, usable = load_state_checked(invalid, warn=warnings.append)
        self.assertFalse(usable)
        self.assertEqual(state, empty_state())
        self.assertIn("UTF-8 JSON", warnings[0])
        self.assertEqual(invalid.read_bytes(), b"\xff\xfe")

    def test_preview_plan_can_inspect_an_unqualified_table(self):
        self.settings["agents"][0]["tiers"]["build"]["qualification"] = []
        self.write_config()
        rc, output, error = self.run_cli(["plan", *self.base(), "--roles", "developer",
                                        "--snapshot", str(self.snapshot), "--now", AT, "--preview-tiers"])
        self.assertEqual(rc, 0, error)
        self.assertEqual(json.loads(output)["tiers"]["developer"]["model"], "sonnet-5")

    def test_dry_run_prints_flags_without_herdr_or_qualification(self):
        self.settings["agents"][0]["tiers"]["build"]["qualification"] = []
        self.write_config()
        runner = FakeRunner()
        rc, output, error = self.run_cli(self.apply_args() + ["--dry-run"], client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 0, error)
        step = json.loads(output)["steps"][0]
        launch = next(command["argv"] for command in step["commands"] if command["argv"][1:3] == ["agent", "start"])
        self.assertEqual(launch[-6:], ["--", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "high"])
        self.assertEqual(len(step["prompt_hash"]), 64)
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.state.exists())

    def test_edited_plan_tier_is_rejected_before_dispatch(self):
        runner = FakeRunner()
        document = {"assignments": {"developer": "claude"}, "tiers": {"developer": {"model": "haiku-4.5"}}}
        rc, _, error = self.run_cli(self.apply_args(document), client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 1)
        self.assertIn("Plan tiers differ", error)
        self.assertEqual(runner.calls, [])

    def test_unreadable_dispatch_input_names_recovery_and_sends_nothing(self):
        original_read = Path.read_bytes
        for unreadable in (self.common, self.briefs["developer"]):
            with self.subTest(path=unreadable):
                self.out.seek(0)
                self.out.truncate()
                self.err.seek(0)
                self.err.truncate()
                runner = FakeRunner().set("agent get claude", agent_json("claude", "idle", "w1:p2"))

                def read_bytes(path):
                    if path == unreadable:
                        raise PermissionError(13, "Permission denied", str(path))
                    return original_read(path)

                with patch("teamlead.assign.Path.read_bytes", autospec=True, side_effect=read_bytes):
                    rc, output, error = self.run_cli(self.apply_args(), client=HerdrClient("herdr", runner))
                self.assertEqual(rc, 1)
                self.assertEqual(output, "")
                self.assertIn(str(unreadable), error)
                self.assertIn("Restore readability", error)
                self.assertNotIn("Traceback", error)
                self.assertEqual(runner.writes(), [])
                self.assertFalse(any(command.startswith(("agent start", "-TERM")) for command in runner.commands()))
                self.assertFalse(self.state.exists())

    def test_fresh_dispatch_records_verified_tier_and_readable_state(self):
        runner = FakeRunner()
        info = json.loads(agent_json("claude", "idle", "w1:p2"))["result"]["agent"]
        info["terminal_id"] = "term-2"
        runner.set("agent get claude", json.dumps({"result": {"agent": info}}))
        process = {"pane_id": "w1:p2", "shell_pid": 100,
                   "foreground_processes": [{"name": "claude", "pid": 200, "argv": ["claude"]}]}
        shell = {**process, "foreground_processes": [{"name": "bash", "pid": 100}]}
        argv = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "high"]
        started = {**process, "foreground_processes": [{"name": "claude", "pid": 300, "argv": argv}]}
        runner.responses["pane process-info"] = ScriptedReads([
            json.dumps({"result": {"process_info": item}}) for item in (process, process, shell, started)])
        runner.set("-TERM 200")
        runner.set("agent start", json.dumps({"result": {"agent": info, "argv": argv}}))
        runner.responses["agent read"] = composer_reads("claude", ("ready", "ready", "> New assignment from the team lead."))
        runner.set("agent prompt", ok_json())
        runner.set("agent wait", ok_json())
        runner.set("pane rename", ok_json())
        rc, output, error = self.run_cli(self.apply_args(), client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 0, error)
        applied = json.loads(output)["applied"][0]
        self.assertTrue(applied["cleared"])
        self.assertEqual(applied["tier"]["verified"]["argv"], argv)
        self.assertIn(applied["tier"]["prompt_hash"], runner.pasted_prompts()[0])
        stored, usable = load_state_checked(self.state)
        self.assertTrue(usable)
        self.assertEqual(stored["schema_version"], 5)
        self.assertEqual(stored["assignments"][0]["tier"], applied["tier"])
        self.assertEqual(role_counts(stored), {"developer": {"claude": 1}})

    def test_tiered_worker_changed_during_composer_read_receives_no_prompt(self):
        runner = FakeRunner().set("agent get claude", agent_json("claude", "idle", "w1:p2"))
        process = {"pane_id": "w1:p2", "foreground_processes": [{"name": "claude", "pid": 200,
            "argv": ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "high"]}]}
        runner.set("pane process-info", json.dumps({"result": {"process_info": process}}))
        client = HerdrClient("herdr", runner)

        def read_replaced_worker(*_args, **_kwargs):
            process["foreground_processes"] = [{"name": "claude", "pid": 999, "argv": ["claude"]}]
            runner.set("pane process-info", json.dumps({"result": {"process_info": process}}))
            return composer_screen("claude")

        with patch.object(client, "agent_read", side_effect=read_replaced_worker):
            rc, output, error = self.run_cli(self.apply_args() + ["--no-clear"], client=client)
        self.assertEqual(rc, 1)
        self.assertEqual(output, "")
        self.assertIn("launch options", error)
        self.assertEqual(runner.writes(), [])

    def test_schema_three_migration_preserves_task_session_and_fix_counter(self):
        session = {"pane_id": "w1:p2", "source": "herdr:claude", "agent": "claude", "kind": "id", "value": "s1"}
        row = {"schema_version": 3, "at": AT, "role": "developer", "agent": "claude", "status": "applied",
               "task": "owner/repo#322", "fix_round": 2, "cleared": False, "clear_reason": "retained", "context_session": session}
        self.state.write_text(json.dumps({"schema_version": 3, "snapshots": [{"schema_version": 2,
            "agents": {"claude": {"window_group": "shared"}}}], "assignments": [row]}))
        migrated, usable = load_state_checked(self.state)
        self.assertTrue(usable)
        self.assertEqual(migrated["assignments"][0], {**row, "schema_version": 5, "tier": None})
        self.assertEqual(migrated["snapshots"][0]["agents"]["claude"], {"window_group": "shared", "tier_billing": {}})
        self.assertEqual(role_counts(migrated), {"developer": {"claude": 1}})

    def test_historical_permission_modes_stay_readable_without_becoming_live_proof(self):
        for options in ([], ["--permission-mode", "acceptEdits"]):
            with self.subTest(options=options):
                state = empty_state()
                argv = ["claude"] + options + ["--model", "sonnet-5", "--effort", "high"]
                add_assignment(state, AT, "developer", "claude", tier={
                    "kind": "claude", "model": "sonnet-5", "effort": "high", "launch_args": options,
                    "verified": {"source": "launch_argv", "model": "sonnet-5", "effort": "high", "pane_id": "w1:p2", "argv": argv}})
                original = json.dumps(state)
                self.state.write_text(original)
                stored, usable = load_state_checked(self.state)
                self.assertTrue(usable)
                self.assertEqual(stored, state)
                self.assertEqual(self.state.read_text(), original)

    def test_retained_fix_preserves_verified_higher_effort_without_restart(self):
        self.settings["agents"][0]["tiers"]["fix"] = {"model": "sonnet-5", "effort": "medium", "multiplier": 1}
        self.write_config()
        session = {"pane_id": "w1:p2", "source": "herdr:claude", "agent": "claude", "kind": "id", "value": "s1"}
        argv = ["claude", "--dangerously-skip-permissions", "--model", "sonnet-5", "--effort", "high"]
        tier = {"kind": "claude", "model": "sonnet-5", "effort": "high", "effective_multiplier": 2,
                "launch_args": ["--dangerously-skip-permissions"],
                "verified": {"model": "sonnet-5", "effort": "high", "source": "launch_argv", "pane_id": "w1:p2", "argv": argv}}
        self.state.write_text(json.dumps({"schema_version": 4, "snapshots": [], "assignments": [{
            "schema_version": 4, "at": AT, "role": "developer", "agent": "claude", "status": "applied",
            "task": "owner/repo#324", "fix_round": None, "cleared": True, "clear_reason": "automatic",
            "context_session": session, "tier": tier}]}))
        runner = FakeRunner().set("agent get claude", agent_json("claude", "idle", "w1:p2", session_id="s1"))
        runner.set("pane process-info", json.dumps({"result": {"process_info": {"pane_id": "w1:p2",
            "foreground_processes": [{"name": "claude", "pid": 200, "argv": argv}]}}}))
        runner.responses["agent read"] = composer_reads("claude", ("ready", "> New assignment from the team lead."))
        runner.set("agent prompt", ok_json()).set("agent wait", ok_json()).set("pane rename", ok_json())
        rc, output, error = self.run_cli(self.apply_args() + ["--retain-context", "--task", "owner/repo#324", "--fix-round", "1"],
                                        client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 0, error)
        record = json.loads(output)["applied"][0]
        self.assertFalse(record["cleared"])
        self.assertEqual(record["tier"]["effort"], "high")
        self.assertEqual(record["tier"]["effective_multiplier"], 2)
        self.assertEqual(record["tier"]["verified"]["source"], "process_argv")
        self.assertFalse(any(command.startswith(("agent start", "-TERM")) for command in runner.commands()))

    def test_measure_records_unknown_tier_billing_even_when_worker_is_skipped(self):
        runner = FakeRunner().set("agent get claude", agent_json("claude", "blocked", "w1:p2"))
        rc, output, error = self.run_cli(["measure", *self.base(), "--now", AT], client=HerdrClient("herdr", runner))
        self.assertEqual(rc, 0, error)
        result = json.loads(output)
        self.assertEqual(result["schema_version"], 3)
        self.assertEqual(result["agents"]["claude"]["tier_billing"]["build"]["window"], "unknown")
        self.assertTrue(result["agents"]["claude"]["skipped"])

    def test_invalid_saved_tier_never_discards_the_ledger(self):
        for effort, argv, source in (("high", ["claude", "--model", "sonnet-5"], "launch_argv"),
                                    ({}, [], "launch_argv"), ("high", [], [])):
            with self.subTest(effort=effort, source=source):
                state = empty_state()
                add_assignment(state, AT, "developer", "claude", tier={"kind": "claude", "model": "sonnet-5", "effort": effort,
                    "verified": {"source": source, "model": "sonnet-5", "effort": effort, "pane_id": "w1:p2", "argv": argv}})
                original = json.dumps(state)
                self.state.write_text(original)
                result, usable = load_state_checked(self.state, warn=lambda _: None)
                self.assertFalse(usable)
                self.assertEqual(result, empty_state())
                self.assertEqual(self.state.read_text(), original)


class TierCostTest(unittest.TestCase):
    def test_candidate_multiplier_changes_winner(self):
        snapshot = {"agents": {"costly": {"headroom_pct": 95}, "cheap": {"headroom_pct": 90}}}
        tiers = {"developer": {"costly": {"effective_multiplier": 2}, "cheap": {"effective_multiplier": 1}}}
        selected = plan(["developer"], snapshot, tier_candidates=tiers)
        self.assertEqual(selected["assignments"], {"developer": "cheap"})
        self.assertEqual(selected["tiers"]["developer"]["effective_multiplier"], 1)

    def test_multiplier_burns_the_shared_pool_before_next_seat(self):
        snapshot = {"agents": {"a": {"headroom_pct": 90, "window_group": "shared"},
                               "b": {"headroom_pct": 90, "window_group": "shared"},
                               "c": {"headroom_pct": 80}}}
        tiers = {role: {name: {"effective_multiplier": 2 if role == "developer" else 1}
                       for name in "abc"} for role in ("developer", "reviewer")}
        selected = plan(["developer", "reviewer"], snapshot, tier_candidates=tiers, exclude={"developer": ["b", "c"]})
        self.assertEqual(selected["assignments"], {"developer": "a", "reviewer": "c"})


if __name__ == "__main__":
    unittest.main()
