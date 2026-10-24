"""Recorded pi contracts through real config, launch, CLI and wait consumers."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from collections.abc import Callable
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from teamlead import cli, pi_native
from teamlead.assign import apply, dry_run
from teamlead.composer import inspect_composer
from teamlead.config import parse_config
from teamlead.errors import ConfigError, HerdrError, ParseError, UsageError
from teamlead.herdr import HerdrClient
from teamlead.launch import restart_worker, start_worker, verify_running
from teamlead.measure import measure
from teamlead.parsers import parse_usage
from teamlead.planner import plan
from teamlead.errors import PlanError
from teamlead.state import add_assignment, empty_state, load_state_checked, save_state
from teamlead.tiers import EFFORTS, parse_tiers, require_role, select_tier
from tests.test_host import agent, pi_entry, sysctl
from tests.test_cli import CONFIG

FIXTURE = Path(__file__).parent / "fixtures" / "pi"
AT = "2026-10-24T15:29:25+00:00"
REAL_RUN = subprocess.run


def fixture(name):
    return json.loads((FIXTURE / name).read_text())


class PiTransport:
    """Subprocess boundary replay; start and foreground shapes are captured."""
    def __init__(self):
        self.started = False
        self.calls = []
        self.info = fixture("start.json")["result"]["agent"]
        self.info["name"] = "local"
        self.process = json.loads(fixture("process-info.json")["stdout"])["result"]["process_info"]
        self.composer = fixture("composer.json")["stdout"]
        self.bad_argv: list[str] | list[None] | None = None
        self.on_call: Callable[[list[str]], None] | None = None

    def __call__(self, argv):
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv)
        if argv[1:3] == ["agent", "start"]:
            self.started = True
            result = {"agent": self.info, "argv": self.bad_argv if self.bad_argv is not None else ["pi"] + argv[argv.index("--") + 1:]}
        elif argv[1:3] == ["agent", "get"]:
            if not self.started:
                return subprocess.CompletedProcess(argv, 1, "", '{"error":{"code":"agent_not_found"}}')
            result = {"agent": self.info}
        elif argv[1:3] == ["pane", "process-info"]:
            info = copy.deepcopy(self.process)
            if not self.started:
                info["foreground_processes"] = [{"pid": info["shell_pid"], "name": "zsh", "argv": ["zsh"]}]
            result = {"process_info": info}
        elif argv[1:3] == ["pane", "get"]:
            result = {"pane": self.info}
        elif argv[1:3] in (["agent", "read"], ["pane", "read"]):
            return subprocess.CompletedProcess(argv, 0, self.composer, "")
        elif argv[1:3] == ["agent", "prompt"]:
            self.composer = argv[-1] + "\n" + self.composer
            result = {}
        elif argv[1:3] in (["agent", "wait"], ["pane", "rename"]):
            result = {}
        elif argv[0] == "kill":
            self.started = False
            result = {}
        else:
            raise AssertionError("Unexpected pi boundary call: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": result}), "")


class PiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = agent()
        self.agent.state_dir = self.root
        self.tier = select_tier(self.agent, "tester", "test_plan")
        self.transport = PiTransport()
        self.client = HerdrClient("herdr", self.transport)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"schema_version": 5, "agents": [pi_entry()]}))
        self.provider = self.root / "pi-config"
        self.provider.mkdir()
        self.models = {"providers": {"ollama": {"baseUrl": "http://localhost:11434/v1", "api": "openai-completions", "models": [{"id": "flint:40b-a2b-ctx64k"}]}}}
        self.write_models()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.provider), "HERDR_ENV": "1"}).start()
        self.boundary = patch("subprocess.run", side_effect=self.system).start()
        self.chip, self.memory = "Apple M5 Max", "68719476736"
        self.version = fixture("version.json")["stdout"]

    def system(self, argv, **kwargs):
        if argv == ["pi", "--version"]:
            return subprocess.CompletedProcess(argv, 0, self.version, "")
        return sysctl(self.chip, self.memory)(argv, **kwargs)

    def write_models(self):
        (self.provider / "models.json").write_text(json.dumps(self.models))

    def launch(self):
        return start_worker(self.client, self.agent, "wA:p8", self.tier)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        rc = cli.main(["--state", str(self.root / "state.json"), "--config", str(self.config), *args], stdout=out, stderr=err, client=self.client)
        return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def call_json(self, *args):
        rc, output, error = self.call(*args)
        assert isinstance(output, dict), (rc, output, error)
        return rc, output, error

    def test_plan_skips_pi_for_fix_and_risk_rounds(self):
        self.launch()
        snapshot = measure(self.client, [self.agent], AT)
        config = json.loads((FIXTURE.parents[2] / "config.example.json").read_text())
        config["agents"] = [row for row in config["agents"] if row["kind"] != "pi"] + [pi_entry()]
        for row in config["agents"]:
            if row["kind"] != "pi":
                row["capabilities"] = ["repo-read", "command-run"]
                snapshot["agents"][row["name"]] = {"kind": row["kind"], "state": "idle", "headroom_pct": 80}
        self.config.write_text(json.dumps(config))
        path = self.root / "snapshot.json"
        path.write_text(json.dumps(snapshot))
        requirements = self.root / "requirements.json"
        requirements.write_text(json.dumps({"schema_version": 1, "assignments": {"investigator": {
            "specialty": "diagnosis", "required_capabilities": ["repo-read"],
            "independent": False, "engagement": "pi-review-fix"}}}))
        context = self.root / "context.json"
        context.write_text(json.dumps({"tester": {"risk_flags": ["security"]}}))
        for role, extra in (("investigator", ["--task", "issue-71", "--fix-round", "5", "--requirements", str(requirements)]),
                            ("tester", ["--round", "tester=test_plan", "--round-context", str(context)])):
            rc, result, error = self.call_json("plan", "--roles", role, "--snapshot", str(path), "--now", AT, *extra)
            self.assertEqual(rc, 0, error)
            self.assertNotEqual(result["assignments"][role], "local")
            candidates = cli._candidate_tiers([role], parse_config(config),
                {"tester": {"type": "test_plan", "context": {"risk_flags": ["security"]}}} if role == "tester" else {},
                fix_round=5 if role == "investigator" else None)
            self.assertEqual(set(candidates[role]), {"claude", "codex", "grok"})
        with self.assertRaises(UsageError):
            cli._candidate_tiers(["investigator"], [self.agent], {"investigator": {"type": "invalid"}})

    def test_symlinked_state_root_and_default_root_share_receipt(self):
        alias = self.root / "state-alias"
        actual = self.root / "actual-state"
        actual.mkdir()
        alias.symlink_to(actual, target_is_directory=True)
        self.agent.state_dir = alias
        proof = self.launch()
        self.assertEqual(pi_native.seat_dir(self.agent), pi_native.seat_dir(type("Seat", (), {"state_dir": actual.resolve(), "name": "local"})()))
        self.assertEqual(Path(proof["session_dir"]).parent.parent.parent.parent, actual.resolve())
        # The fallback must canonicalize too, without reading machine-local state.
        self.agent.state_dir = None
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(alias)}):
            self.assertEqual(pi_native.seat_dir(self.agent).parents[1], actual.resolve() / "teamlead")
        self.agent.state_dir = actual.resolve()
        self.assertTrue(pi_native.validate_proof(proof, self.tier, pi_native.seat_dir(self.agent)))
        escaped = copy.deepcopy(proof)
        escaped["session_dir"] = str(self.root / "outside" / "1")
        escaped["argv"][-1] = escaped["session_dir"]
        self.assertFalse(pi_native.validate_proof(escaped, self.tier, pi_native.seat_dir(self.agent)))
        # Pre-fix receipts can still carry the symlink spelling in exact argv.
        legacy = copy.deepcopy(proof)
        legacy["session_dir"] = str(alias / Path(proof["session_dir"]).relative_to(actual.resolve()))
        legacy["argv"][-1] = legacy["session_dir"]
        self.assertTrue(pi_native.validate_proof(legacy, self.tier, pi_native.seat_dir(self.agent)))
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        (Path(proof["session_dir"]) / "capture.jsonl").write_text(body)
        rc, result, error = self.call_json("probe-report", "--state", str(alias / "state.json"),
                                      "--agent", "local", "--pane", "wA:p8", "--report", str(report), "--lines", "40")
        self.assertEqual(rc, 0, error)
        self.assertTrue(result["found"], result)

    def test_restart_changes_tier_without_adopting_another_process(self):
        original = self.launch()
        self.agent.tiers["test_plan"]["effort"] = "high"
        tier_b = select_tier(self.agent, "tester", "test_plan")
        fresh = restart_worker(self.client, self.agent, "wA:p8", tier_b, sleep=lambda _: None)
        self.assertEqual(fresh["effort"], "high")
        self.assertNotEqual(fresh["session_dir"], original["session_dir"])
        self.assertTrue(Path(original["session_dir"]).is_dir())
        self.assertEqual(pi_native.read_launch(self.agent)["proof"], fresh)
        self.transport.process["foreground_processes"][0]["pid"] += 1
        before = len(self.transport.calls)
        with self.assertRaisesRegex(HerdrError, "continuity"):
            restart_worker(self.client, self.agent, "wA:p8", self.tier, sleep=lambda _: None)
        self.assertFalse(any(call[0] == "kill" for call in self.transport.calls[before:]))

    def test_malformed_start_results_are_actionable_and_leave_no_receipt(self):
        for argv in ([], [None]):
            self.transport.started = False
            self.transport.bad_argv = argv
            with self.subTest(argv=argv), self.assertRaisesRegex(HerdrError, "Preserve the live pane"):
                self.launch()
            self.assertFalse((pi_native.seat_dir(self.agent) / "launch.json").exists())
        for result in ({}, {"agent": None}, {"agent": {}, "argv": None}, None):
            with self.subTest(result=result), self.assertRaisesRegex(HerdrError, "start a fresh recorded worker"):
                pi_native.save_launch(self.client, self.agent, "wA:p8", self.tier, result)

    def test_configured_pi_kind_mismatch_refuses_cli_delivery(self):
        self.launch()
        _, _, report = self.native()
        for kind in ("codex", "unknown", None):
            self.transport.info["agent"] = kind
            rc, _, error = self.call("probe-report", "--agent", "local", "--pane", "wA:p8", "--report", str(report), "--lines", "40")
            self.assertEqual(rc, 1, error)
            self.assertIn("Configured pi seat differs", error)

    def test_start_local_cli_and_unmetered_measure(self):
        self.chip, self.memory = "Apple M5", "34359738368"
        rc, output, error = self.call_json("start-local", "--agent", "local", "--pane", "wA:p8", "--task", "issue-71", "--role", "tester", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(output["verified"]["pid"], 58701)
        rc, snapshot, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        row = snapshot["agents"]["local"]
        self.assertEqual((snapshot["schema_version"], row["budget_mode"], row["windows"], row["headroom_pct"]), (5, "local_unmetered", {}, None))
        self.assertFalse(any(call[1:3] in (["agent", "prompt"], ["agent", "send-keys"]) for call in self.transport.calls))
        self.assertFalse(any(call[0] == "ps" for call in self.transport.calls))
        self.memory = "34359738367"
        before = list(self.transport.calls)
        rc, rejected, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(rejected["agents"]["local"]["skip_reason"], "host_ineligible")
        self.assertEqual(self.transport.calls, before)
        self.memory = "34359738368"
        rc, snapshot, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(snapshot["agents"]["local"]["budget_mode"], "local_unmetered")
        rc, result, error = self.call_json("plan", "--roles", "tester", "--round", "tester=test_plan", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"tester": "local"})
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Brief")
        rc, applied, error = self.call_json("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "tester=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(applied["steps"][0]["tier"]["billing_window"], "local_unmetered")
        self.chip = "Apple M1"
        before = list(self.transport.calls)
        rc, _, error = self.call("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "tester=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 1)
        self.assertIn("host_ineligible", error)
        self.assertEqual(self.transport.calls, before)

    def test_host_skip_precedes_any_pi_contact_and_probe_failure_is_failed(self):
        self.chip = "Intel(R) Core(TM) i7"
        rc, snapshot, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(snapshot["agents"]["local"]["skip_reason"], "host_ineligible")
        self.assertEqual(self.transport.calls, [])
        rc, _, error = self.call("plan", "--roles", "tester")
        self.assertEqual(rc, 1)
        self.assertIn("'local' is host_ineligible and no other worker", error)
        self.chip = "unrecognized"
        rc, snapshot, _ = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 1)
        self.assertEqual(snapshot["failed_agents"], ["local"])
        self.assertEqual(self.transport.calls, [])

    def test_optional_absence_busy_and_unproven_identity_never_produce_free_capacity(self):
        row = measure(self.client, [self.agent], AT)["agents"]["local"]
        self.assertEqual(row["skip_reason"], "agent_not_running")
        self.transport.started = True
        self.transport.info["agent_status"] = "working"
        self.assertTrue(measure(self.client, [self.agent], AT)["agents"]["local"]["skipped"])
        self.transport.info["agent_status"] = "idle"
        self.assertEqual(measure(self.client, [self.agent], AT)["failed_agents"], ["local"])

    def test_launch_profile_and_pid_pane_terminal_guards(self):
        self.launch()
        proof = verify_running(self.client, self.agent, "wA:p8", self.tier)
        self.assertEqual(proof["pid"], 58701)
        for field, changed in (("pid", 999), ("terminal_id", "new-terminal"), ("pane_id", "wA:p9")):
            target = self.transport.process["foreground_processes"][0] if field == "pid" else self.transport.info
            old = target[field]
            target[field] = changed
            with self.subTest(field=field), self.assertRaises(HerdrError):
                verify_running(self.client, self.agent, "wA:p8", self.tier)
            target[field] = old
        self.transport.process["foreground_processes"].append({"pid": 999, "argv0": "pi"})
        with self.assertRaises(HerdrError):
            verify_running(self.client, self.agent, "wA:p8", self.tier)
        self.transport.process["foreground_processes"].pop()
        for flag in ("--print", "--no-session", "--resume", "--extension", "--provider"):
            value = copy.deepcopy(proof)
            value["argv"].extend([flag, "untrusted"])
            self.assertFalse(pi_native.validate_proof(value, self.tier))

    def test_host_refuses_start_and_restart_without_transitions(self):
        self.chip = "Apple M1"
        for function in (start_worker, restart_worker):
            with self.assertRaisesRegex(HerdrError, "host_ineligible"):
                function(self.client, self.agent, "wA:p8", self.tier)
        self.assertEqual(self.transport.calls, [])

    def test_receipt_version_and_old_local_snapshot_fail_closed(self):
        self.launch()
        measured = measure(self.client, [self.agent], AT)
        path = self.root / "snapshot.json"
        measured["schema_version"] = 4
        measured["agents"]["claude"] = {"kind": "claude", "state": "idle", "headroom_pct": 80}
        self.config.write_text(json.dumps({"schema_version": 5, "agents": [pi_entry(), CONFIG["agents"][0]]}))
        path.write_text(json.dumps(measured))
        rc, result, error = self.call_json("plan", "--roles", "tester", "--snapshot", str(path))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["ineligible_agents"]["local"]["reason"], "local_measurement_unproven")
        with self.assertRaises(PlanError):
            plan(["tester"], measured, local_agents=["local"], exclude={"tester": ["claude"]})
        receipt = pi_native.read_launch(self.agent)
        receipt["schema_version"] = True
        (pi_native.seat_dir(self.agent) / "launch.json").write_text(json.dumps(receipt))
        with self.assertRaises(HerdrError):
            pi_native.read_launch(self.agent)

    def test_missing_receipt_proof_and_session_directory_fail_closed(self):
        self.launch()
        _, _, report = self.native()
        receipt = pi_native.read_launch(self.agent)
        for proof in (None, {}, {**receipt["proof"], "session_dir": None}):
            malformed = {**receipt, "proof": proof}
            path = pi_native.seat_dir(self.agent) / "launch.json"
            path.write_text(json.dumps(malformed))
            before = list(self.transport.calls)
            with self.subTest(proof=proof), self.assertRaisesRegex(HerdrError, "receipt profile differs"):
                verify_running(self.client, self.agent, "wA:p8", self.tier)
            self.assertEqual(self.transport.calls, before)
            rc, output, error = self.call_json("probe-report", "--agent", "local", "--pane", "wA:p8",
                                                "--report", str(report), "--lines", "40")
            self.assertEqual(rc, 0, error)
            self.assertFalse(output["found"])
            self.assertEqual(output["reason"], "pi_marker_unconfirmed")

    def test_endpoint_and_permission_profile_fail_closed(self):
        self.launch()
        self.version = "0.99.2\n"
        self.assertEqual(measure(self.client, [self.agent], AT)["failed_agents"], ["local"])
        self.version = fixture("version.json")["stdout"]
        self.models["providers"]["ollama"]["models"][0]["baseUrl"] = "https://remote.example/v1"
        self.write_models()
        result = measure(self.client, [self.agent], AT)
        self.assertEqual(result["failed_agents"], ["local"])
        self.assertNotEqual(result["agents"]["local"]["budget_mode"], "local_unmetered")
        self.assertIn("pi_local_provider_unproven", result["agents"]["local"]["error"]["message"])
        with self.assertRaises(ParseError):
            parse_usage("pi", "")

    def test_tiers_restrict_roles_models_thinking_and_risk(self):
        with self.assertRaisesRegex(UsageError, "metered tester of record"):
            select_tier(self.agent, "tester")
        self.assertEqual(select_tier(self.agent, "investigator")["model"], "ollama/flint:40b-a2b-ctx64k")
        for effort in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
            self.assertEqual(parse_tiers({"consultation": {"model": "ollama/flint:tag", "effort": effort}}, "pi")["consultation"]["effort"], effort)
        for role in ("reviewer", "judge", "developer", "release"):
            with self.subTest(role=role), self.assertRaises(UsageError):
                require_role(self.agent, role)
            with self.subTest(role=role), self.assertRaises(UsageError):
                select_tier(self.agent, role)
        for model in ("ollama/flint:*", "remote/flint:tag", "ollama/flint:tag:high", "ollama/flint:tag;bash"):
            with self.subTest(model=model), self.assertRaises(ConfigError):
                parse_tiers({"consultation": {"model": model, "effort": "off"}}, "pi")
        for context in ({"risk_flags": ["security"]}, {"prior_high_miss": True}):
            with self.assertRaises(UsageError):
                select_tier(self.agent, "investigator", context=context)
        with self.assertRaises(UsageError):
            select_tier(self.agent, "tester", "mechanical")
        self.agent.capabilities = ("repo-read",)
        with self.assertRaises(UsageError):
            select_tier(self.agent, "investigator")

    def test_recorded_composer_and_occupied_composer(self):
        self.assertEqual(inspect_composer(self.transport.composer, self.agent).content, "")
        self.assertEqual(inspect_composer("─" * 55 + "\nx\n" + "─" * 55, self.agent).content, "x")
        self.assertFalse(inspect_composer("no editor", self.agent).visible)

    def native(self):
        rows = [json.loads(line) for line in (FIXTURE / "native-accept.jsonl").read_text().splitlines()]
        prompt = next(row["message"]["content"][0]["text"] for row in rows if row.get("message", {}).get("role") == "user")
        report = self.root / "report.md"
        report.write_text("ACCEPT_OK")
        old_report = rows[-1]["message"]["content"][-1]["text"].removeprefix("REPORT: ")
        body = (FIXTURE / "native-accept.jsonl").read_text().replace(old_report, str(report))
        prompt = prompt.replace(old_report, str(report))
        return body, prompt, report

    def test_source_bound_delivery_through_cli(self):
        proof = self.launch()
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        source = Path(proof["session_dir"]) / "capture.jsonl"
        source.write_text(body)
        args = ("probe-report", "--agent", "local", "--pane", "wA:p8", "--report", str(report), "--lines", "40")
        rc, result, error = self.call_json(*args)
        self.assertEqual(rc, 0, error)
        self.assertTrue(result["found"], result)
        for replacement in ("```\nREPORT: " + str(report) + "\n```", "> REPORT: " + str(report), "REPORT: /other.md"):
            rows = [json.loads(line) for line in body.splitlines()]
            rows[-1]["message"]["content"] = [{"type": "text", "text": replacement}]
            source.write_text("\n".join(json.dumps(row) for row in rows))
            self.assertFalse(self.call_json(*args)[1]["found"])
        source.write_text(body)
        report.unlink()
        self.assertFalse(self.call_json(*args)[1]["found"])

    def test_source_and_receipt_changes_during_probe_are_unconfirmed(self):
        proof = self.launch()
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        source = Path(proof["session_dir"]) / "capture.jsonl"
        receipt = (pi_native.seat_dir(self.agent) / "launch.json").read_text()
        for change in ("source", "receipt"):
            source.write_text(body)
            (pi_native.seat_dir(self.agent) / "launch.json").write_text(receipt)
            gets = 0
            def boundary(argv):
                nonlocal gets
                if argv[1:3] == ["agent", "get"]:
                    gets += 1
                    if gets == 3:
                        if change == "source":
                            source.write_text(body + "\n")
                        else:
                            path = pi_native.seat_dir(self.agent) / "launch.json"
                            value = json.loads(receipt)
                            value["dispatch_prompt"] = "different dispatch"
                            path.write_text(json.dumps(value))
            self.transport.on_call = boundary
            with self.subTest(change=change):
                self.assertFalse(pi_native.probe(self.client, self.agent, "wA:p8", str(report))["found"])
        self.transport.on_call = None

    def test_native_rejects_echo_stale_branch_failed_turn_and_ambiguous_source(self):
        proof = self.launch()
        body, prompt, report = self.native()
        self.assertIsNotNone(pi_native.conversation(body, prompt, self.tier))
        self.assertIsNone(pi_native.conversation(body, "another dispatch", self.tier))
        rows = [json.loads(line) for line in body.splitlines()]
        for change in ({"stopReason": "error"}, {"stopReason": "toolUse"}, {"model": "other"}):
            modified = copy.deepcopy(rows)
            modified[-1]["message"].update(change)
            parsed = pi_native.conversation("\n".join(json.dumps(row) for row in modified), prompt, self.tier)
            self.assertTrue(parsed is None or parsed[1] is None)
        modified = copy.deepcopy(rows)
        modified[-1]["parentId"] = "a different branch"
        self.assertIsNone(pi_native.conversation("\n".join(json.dumps(row) for row in modified), prompt, self.tier))
        pi_native.bind_prompt(self.agent, prompt)
        for name in ("one.jsonl", "two.jsonl"):
            (Path(proof["session_dir"]) / name).write_text(body)
        self.assertFalse(pi_native.probe(self.client, self.agent, "wA:p8", str(report))["found"])

    def test_fresh_apply_binds_prompt_and_rejects_busy_composer(self):
        self.launch()
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Brief")
        paths = {"common": str(common), "tester": str(brief)}
        result = apply(self.client, {"tester": "local"}, {"local": self.agent}, paths, AT,
                       tiers={"tester": self.tier}, sleep=lambda _: None, settle_sec=0)
        self.assertEqual(result["applied"][0]["status"], "applied")
        with self.assertRaisesRegex(UsageError, "fresh launch"):
            dry_run(self.client, {"tester": "local"}, {"local": self.agent}, paths, no_clear=True, tiers={"tester": self.tier})
        receipt = pi_native.read_launch(self.agent)
        self.assertIn("New assignment from the team lead", receipt["dispatch_prompt"])
        self.assertEqual(sum(call[1:3] == ["agent", "start"] for call in self.transport.calls), 2)
        self.transport.composer = "─" * 55 + "\npending input\n" + "─" * 55
        before = len(self.transport.calls)
        with self.assertRaises(HerdrError):
            apply(self.client, {"tester": "local"}, {"local": self.agent}, paths, AT,
                  tiers={"tester": self.tier}, sleep=lambda _: None, settle_sec=0)
        self.assertFalse(any(call[0] == "kill" or call[1:3] == ["agent", "prompt"] for call in self.transport.calls[before:]))

    def test_planner_preserves_legacy_candidates_and_local_rotation(self):
        self.launch()
        snapshot = measure(self.client, [self.agent], AT)
        snapshot["agents"]["metered"] = {"kind": "claude", "state": "idle", "headroom_pct": 90}
        local = snapshot["agents"]["local"]
        snapshot["agents"]["spare-local"] = copy.deepcopy(local)
        result = plan(["tester"], snapshot, local_agents=["local", "spare-local"], counts={"tester": {"local": 2}})
        self.assertEqual(result["assignments"], {"tester": "spare-local"})
        self.assertIn("local_unmetered", result["rationale"][0])
        for mutation in ({"skipped": True}, {"state": "working"}, {"budget_mode": "metered"}, {"error": {"code": "failure"}}):
            modified = copy.deepcopy(snapshot)
            for name in ("local", "spare-local"):
                modified["agents"][name].update(mutation)
            result = plan(["tester"], modified, local_agents=["local", "spare-local"])
            self.assertEqual(result["assignments"], {"tester": "metered"})
        forged = {"agents": {"claude": {"kind": "claude", "state": "idle", "budget_mode": "local_unmetered", "host_eligibility": {"eligible": True}}}}
        with self.assertRaises(PlanError):
            plan(["tester"], forged, local_agents=["claude"], roster=["claude"])
        self.config.write_text(json.dumps({"schema_version": 5, "agents": [*CONFIG["agents"], pi_entry()]}))
        snapshot["agents"].pop("spare-local")
        snapshot["agents"].pop("metered")
        snapshot["agents"].update({name: {"kind": name, "state": "idle", "headroom_pct": 80} for name in ("claude", "codex", "grok")})
        (self.root / "snapshot.json").write_text(json.dumps(snapshot))
        rc, result, error = self.call_json("plan", "--roles", "tester", "--round", "tester=test_plan", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"tester": "local"})
        rc, ordinary, error = self.call_json("plan", "--roles", "tester", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(ordinary["assignments"], {"tester": "claude"})
        self.chip = "Apple M1"
        rc, result, error = self.call_json("plan", "--roles", "tester", "--round", "tester=test_plan", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"tester": "claude"})
        self.assertEqual(result["ineligible_agents"]["local"]["reason"], "host_ineligible")

    def test_wait_report_source_only_and_bare_echo_rejection(self):
        proof = self.launch()
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        source = Path(proof["session_dir"]) / "capture.jsonl"
        source.write_text(body)
        responses = self.root / "responses.json"
        responses.write_text(json.dumps({"agent": self.transport.info, "process_info": self.transport.process}))
        fake = self.root / "herdr"
        fake.write_text("#!" + sys.executable + "\n" +
            "import json,sys\nfrom pathlib import Path\n" +
            "data=json.loads(Path(" + repr(str(responses)) + ").read_text())\n" +
            "command=sys.argv[1:3]\n" +
            "if command==['agent','get']: print(json.dumps({'result':{'agent':data['agent']}}))\n" +
            "elif command==['pane','get']: print(json.dumps({'result':{'pane':data['agent']}}))\n" +
            "elif command==['pane','process-info']: print(json.dumps({'result':{'process_info':data['process_info']}}))\n" +
            "elif command==['pane','wait-output']: pass\n" +
            "elif command==['pane','read']: print('REPORT: ' + " + repr(str(report)) + ")\n" +
            "else: raise SystemExit('Unexpected boundary call')\n")
        fake.chmod(0o755)
        command = ["bash", str(FIXTURE.parents[2] / "wait-report.sh"), "--once", "--config", str(self.config), "--state", str(self.root / "state.json"), "local", str(report)]
        environment = {**os.environ, "HERDR_BIN": str(fake), "PY_BIN": sys.executable, "TEAMLEAD_UNCONFIRMED_IDLE_READS": "1"}
        accepted = REAL_RUN(command, capture_output=True, text=True, env=environment)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(json.loads(accepted.stdout)["found"])
        mismatched = copy.deepcopy(self.transport.info)
        mismatched["agent"] = "codex"
        responses.write_text(json.dumps({"agent": mismatched, "process_info": self.transport.process}))
        refused = REAL_RUN(command, capture_output=True, text=True, env=environment)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual(refused.stdout, "")
        responses.write_text(json.dumps({"agent": self.transport.info, "process_info": self.transport.process}))
        source.write_text(body.replace('"stopReason": "stop"', '"stopReason": "error"'))
        rejected = REAL_RUN(command, capture_output=True, text=True, env=environment)
        self.assertEqual(rejected.returncode, 4, rejected.stderr)
        self.assertFalse(json.loads(rejected.stdout)["found"])

    def test_launch_evidence_roundtrips_and_invalid_pid_fails_reload(self):
        proof = self.launch()
        data = empty_state()
        add_assignment(data, "tester", "local", AT, tier={**self.tier, "launch_args": ["--no-extensions", "--no-mcp", "--tools", "read,bash,write"], "verified": proof})
        path = self.root / "state.json"
        save_state(path, data)
        self.assertTrue(load_state_checked(path)[1])
        data["assignments"][0]["tier"]["verified"]["pid"] = 0
        save_state(path, data)
        self.assertFalse(load_state_checked(path, warn=lambda _: None)[1])


if __name__ == "__main__":
    unittest.main()
