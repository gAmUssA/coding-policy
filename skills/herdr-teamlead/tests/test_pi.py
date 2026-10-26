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
from teamlead import cli, pi_native, composition
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
from teamlead.tiers import LocalRoleIneligible, EFFORTS, parse_tiers, require_role, select_tier
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
        self.tier = select_tier(self.agent, "developer", "build")
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
        self.version_error: OSError | None = None
        self.version_exit = 0

    def system(self, argv, **kwargs):
        if argv == ["pi", "--version"]:
            if self.version_error is not None:
                raise self.version_error
            return subprocess.CompletedProcess(argv, self.version_exit, self.version, "")
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
                snapshot["agents"][row["name"]] = {"kind": row["kind"], "state": "idle", "headroom_pct": 80}
        self.config.write_text(json.dumps(config))
        (self.root / "snapshot.json").write_text(json.dumps(snapshot))
        history = empty_state()
        add_assignment(history, AT, "developer", "local", task="issue-71")
        for fix in range(1, 5):
            add_assignment(history, f"2026-10-24T15:29:{25 + fix:02d}+00:00", "developer", "local", task="issue-71", fix_round=fix)
            save_state(self.root / "state.json", history)
            if fix < 3:
                continue
            rc, result, error = self.call_json("plan", "--roles", "developer", "--snapshot", str(self.root / "snapshot.json"),
                "--task", "issue-71", "--fix-round", str(fix + 1))
            self.assertEqual(rc, 0, error)
            self.assertNotEqual(result["assignments"]["developer"], "local")
        cases = [(fix, {}, None) for fix in (4, 5)] + [(None, context, None) for context in (
            {"risk_flags": ["security"]}, {"prior_high_miss": True}, {"input_bytes": 250001})] + [(None, {}, "correction-1")]
        for fix, context, correction in cases:
            with self.subTest(fix=fix, context=context, correction=correction), self.assertRaises(LocalRoleIneligible):
                select_tier(self.agent, "developer", context=context, fix_round=fix, correction_plan=correction)
            with self.subTest(fix=fix, context=context, correction=correction):
                candidates = cli._candidate_tiers(["developer"], parse_config(config),
                    {"developer": {"context": context}}, fix_round=fix, correction_plan=correction)
                assert candidates is not None
                self.assertEqual(set(candidates["developer"]), {"claude", "codex", "grok"})
        for context in ({"risk_flags": ["security"]}, {"prior_high_miss": True}, {"input_bytes": 250001}):
            (self.root / "context.json").write_text(json.dumps({"developer": context}))
            rc, result, error = self.call_json("plan", "--roles", "developer", "--snapshot", str(self.root / "snapshot.json"),
                "--round-context", str(self.root / "context.json"))
            self.assertEqual(rc, 0, error)
            self.assertNotEqual(result["assignments"]["developer"], "local")
        candidates = cli._candidate_tiers(["developer"], [self.agent], {"developer": {"type": "invalid"}})
        self.assertEqual(candidates, {"developer": {}})

    def test_pi_config_allows_only_developer_or_disabled(self):
        for roles in (["developer"], []):
            config = {"schema_version": 5, "agents": [{**pi_entry(), "allowed_roles": roles}]}
            self.config.write_text(json.dumps(config))
            self.assertEqual(parse_config(config)[0].allowed_roles, tuple(roles))
            rc, _, error = self.call("start-local", "--agent", "local", "--pane", "wA:p8", "--task", "issue-71", "--now", AT)
            self.assertEqual(rc, 0 if roles else 1, error)
            self.transport.started = False
        for role in ("reviewer", "tester", "judge", "release", "architect", "advisor", "investigator", "critic", "lead"):
            config = {"schema_version": 5, "agents": [{**pi_entry(), "allowed_roles": [role]}]}
            with self.subTest(role=role), self.assertRaisesRegex(ConfigError, "allowed_roles"):
                parse_config(config)
        # A forged in-memory declaration cannot grant pi another role.
        self.agent.allowed_roles = None
        for role in ("reviewer", "tester", "judge", "release", "architect", "advisor", "investigator", "critic", "lead"):
            with self.subTest(role=role), self.assertRaisesRegex(UsageError, "Use pi only as developer"):
                require_role(self.agent, role)

    def test_initial_and_early_developer_fixes_launch_fresh_with_full_brief(self):
        self.launch()
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Full handoff: previous changes, remaining findings and gates")
        history = []
        sessions = set()
        for fix in (None, 1, 2, 3):
            at = f"2026-10-24T15:29:{25 + (fix or 0):02d}+00:00"
            with self.subTest(fix=fix):
                self.transport.composer = fixture("composer.json")["stdout"]
                tier = select_tier(self.agent, "developer", fix_round=fix)
                self.assertEqual(tier["round"], "build" if fix is None else "fix")
                result = apply(self.client, {"developer": "local"}, {"local": self.agent},
                    {"common": str(common), "developer": str(brief)}, at, task="issue-71", fix_round=fix,
                    history=history, tiers={"developer": tier}, sleep=lambda _: None, settle_sec=0)
                row = result["applied"][0]
                self.assertEqual(row["status"], "applied")
                self.assertTrue(row["cleared"])
                self.assertEqual(row["clear_reason"], "automatic")
                self.assertIsNone(row["context_session"])
                receipt = pi_native.read_launch(self.agent)
                self.assertNotIn(receipt["proof"]["session_dir"], sessions)
                sessions.add(receipt["proof"]["session_dir"])
                self.assertIn(str(common), receipt["dispatch_prompt"])
                self.assertIn(str(brief), receipt["dispatch_prompt"])
                history.append(row)
                # The real CLI accepts a fresh early fix too, preserving the ledger.
                if fix is not None:
                    state = empty_state()
                    for prior in history[:-1]:
                        add_assignment(state, prior["at"], "developer", "local", task="issue-71",
                            fix_round=prior["fix_round"], tier=prior["tier"], cleared=True, clear_reason="automatic")
                    save_state(self.root / "state.json", state)
                    rc, output, error = self.call_json("apply", "--assignments", '{"developer":"local"}',
                        "--common", str(common), "--brief", "developer=" + str(brief), "--task", "issue-71",
                        "--fix-round", str(fix), "--dry-run", "--now", AT)
                    self.assertEqual(rc, 0, error)
                    self.assertEqual(output["steps"][0]["tier"]["round"], "fix")
        self.assertEqual(len(sessions), 4)
        self.assertEqual(sum(call[1:3] == ["agent", "start"] for call in self.transport.calls), 5)

    def test_pi_retained_dispatch_is_refused_before_worker_contact(self):
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Full handoff")
        for option in ("retain_context", "no_clear", "retain_specialist"):
            for runner in (apply, dry_run):
                with self.subTest(option=option, runner=runner.__name__), self.assertRaisesRegex(UsageError, "fresh launch"):
                    if runner is apply:
                        apply(self.client, {"developer": "local"}, {"local": self.agent},
                            {"common": str(common), "developer": str(brief)}, AT, task="issue-71", fix_round=1,
                            tiers={"developer": self.tier}, no_clear=option == "no_clear",
                            retain_context=option == "retain_context", retain_specialist=option == "retain_specialist")
                    else:
                        dry_run(self.client, {"developer": "local"}, {"local": self.agent},
                            {"common": str(common), "developer": str(brief)}, task="issue-71", fix_round=1,
                            tiers={"developer": self.tier}, no_clear=option == "no_clear",
                            retain_context=option == "retain_context", retain_specialist=option == "retain_specialist")
            rc, _, error = self.call("apply", "--assignments", '{"developer":"local"}', "--common", str(common),
                "--brief", "developer=" + str(brief), "--task", "issue-71", "--fix-round", "1", "--dry-run",
                "--" + option.replace("_", "-"))
            self.assertEqual(rc, 1, error)
            self.assertIn("fresh launch", error)
        self.assertEqual(self.transport.calls, [])

    def test_pi_developer_is_excluded_from_independent_verification(self):
        seats = parse_config({"schema_version": 5, "agents": [*CONFIG["agents"], pi_entry()]})
        history = [{"agent": "local", "role": "developer", "task": "issue-71", "status": "applied",
                    "tier": {"kind": "pi", "round": "build"}}]
        constraints = composition.selection_constraints(["reviewer", "tester"], seats, {}, history, "issue-71")
        self.assertEqual(constraints["exclude"], {"reviewer": ["local"], "tester": ["local"]})
        candidates = cli._candidate_tiers(["reviewer", "tester"], seats, {}, excludes=constraints["exclude"])
        assert candidates is not None
        self.assertEqual(set(candidates["reviewer"]), {"claude", "codex", "grok"})
        self.assertEqual(set(candidates["tester"]), {"claude", "codex", "grok"})

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
        self.agent.tiers["build"]["effort"] = "high"
        tier_b = select_tier(self.agent, "developer", "build")
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
        rc, output, error = self.call_json("start-local", "--agent", "local", "--pane", "wA:p8", "--task", "issue-71", "--role", "developer", "--now", AT)
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
        rc, result, error = self.call_json("plan", "--roles", "developer", "--round", "developer=build", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"developer": "local"})
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Brief")
        rc, applied, error = self.call_json("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "developer=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(applied["steps"][0]["tier"]["billing_window"], "local_unmetered")
        self.chip = "Apple M1"
        before = list(self.transport.calls)
        rc, _, error = self.call("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "developer=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 1)
        self.assertIn("host_ineligible", error)
        self.assertEqual(self.transport.calls, before)

    def test_host_skip_precedes_any_pi_contact_and_probe_failure_is_failed(self):
        self.chip = "Intel(R) Core(TM) i7"
        rc, snapshot, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(snapshot["agents"]["local"]["skip_reason"], "host_ineligible")
        self.assertEqual(self.transport.calls, [])
        rc, _, error = self.call("plan", "--roles", "developer")
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
        self.assertEqual(proof["argv"][proof["argv"].index("--tools") + 1], "read,bash,edit,write")
        self.assertIn("edit       - Edit files with find/replace", fixture("help.json")["stdout"])
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
        rc, result, error = self.call_json("plan", "--roles", "developer", "--snapshot", str(path))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["ineligible_agents"]["local"]["reason"], "local_measurement_unproven")
        with self.assertRaises(PlanError):
            plan(["developer"], measured, local_agents=["local"], exclude={"developer": ["claude"]})
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

    def assert_pi_install_guidance(self):
        rc, result, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 1, error)
        self.assertEqual(result["failed_agents"], ["local"])
        row = result["agents"]["local"]
        self.assertNotEqual(row["budget_mode"], "local_unmetered")
        message = row["error"]["message"]
        self.assertIn("pi_version_unsupported", message)
        self.assertIn("npm install -g @earendil-works/pi-coding-agent@1.4.0", message)
        self.assertNotIn("Ollama", message)
        return message

    def test_missing_pi_reports_install_guidance(self):
        self.launch()
        self.version_error = FileNotFoundError("pi executable missing")
        self.assertIn("not installed", self.assert_pi_install_guidance())

    def test_wrong_pi_version_reports_install_guidance(self):
        self.launch()
        self.version = "1.5.0\n"
        self.assertIn("found 1.5.0", self.assert_pi_install_guidance())

    def test_unreadable_pi_version_reports_install_guidance(self):
        self.launch()
        self.version_exit = 2
        self.assertIn("unreadable (pi --version exit 2)", self.assert_pi_install_guidance())

    def test_missing_models_keeps_provider_guidance(self):
        self.launch()
        (self.provider / "models.json").unlink()
        rc, result, error = self.call_json("measure", "--now", AT)
        self.assertEqual(rc, 1, error)
        self.assertEqual(result["failed_agents"], ["local"])
        message = result["agents"]["local"]["error"]["message"]
        self.assertIn("pi_local_provider_unproven", message)
        self.assertIn("Ollama", message)
        self.assertNotIn("npm install", message)

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
        self.assertEqual(select_tier(self.agent, "developer")["model"], "ollama/flint:40b-a2b-ctx64k")
        for effort in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
            self.assertEqual(parse_tiers({"build": {"model": "ollama/flint:tag", "effort": effort}}, "pi")["build"]["effort"], effort)
        for role in ("reviewer", "tester", "judge", "release", "architect", "advisor", "investigator", "critic", "lead"):
            with self.subTest(role=role), self.assertRaisesRegex(UsageError, "Use pi only as developer"):
                require_role(self.agent, role)
            rc, _, error = self.call("apply", "--assignments", json.dumps({role: "local"}), "--common", str(self.config), "--dry-run")
            self.assertEqual(rc, 1, error)
            self.assertIn("Use pi only as developer", error)
            with self.subTest(role=role), self.assertRaises(UsageError):
                select_tier(self.agent, role)
        for model in ("ollama/flint:*", "remote/flint:tag", "ollama/flint:tag:high", "ollama/flint:tag;bash"):
            with self.subTest(model=model), self.assertRaises(ConfigError):
                parse_tiers({"build": {"model": model, "effort": "off"}}, "pi")
        for row in ("consultation", "test_plan", "review", "mechanical", "hostile_verify", "recheck"):
            with self.subTest(row=row), self.assertRaises(LocalRoleIneligible):
                select_tier(self.agent, "developer", round_type=row)
            with self.subTest(row=row), self.assertRaises(ConfigError):
                parse_tiers({row: {"model": "ollama/flint:tag", "effort": "off"}}, "pi")
        for context in ({"risk_flags": ["security"]}, {"prior_high_miss": True}, {"input_bytes": 250001}):
            with self.assertRaises(UsageError):
                select_tier(self.agent, "developer", context=context)
        self.assertEqual(select_tier(self.agent, "developer", context={"input_bytes": 250000})["round"], "build")
        self.agent.capabilities = ("repo-read",)
        with self.assertRaises(UsageError):
            select_tier(self.agent, "developer")

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
        evidence = json.loads((Path(proof["session_dir"]) / "delivery.json").read_text())
        self.assertEqual(evidence["schema_version"], 1)
        self.assertTrue(all(row["schema_version"] == 1 for row in evidence["records"]))
        self.assertEqual(evidence["source_path"], str(source))
        self.assertTrue(self.call_json(*args)[1]["found"])
        for replacement in ("```\nREPORT: " + str(report) + "\n```", "> REPORT: " + str(report), "REPORT: /other.md"):
            rows = [json.loads(line) for line in body.splitlines()]
            rows[-1]["message"]["content"] = [{"type": "text", "text": replacement}]
            source.write_text("\n".join(json.dumps(row) for row in rows))
            self.assertFalse(self.call_json(*args)[1]["found"])
        source.write_text(body)
        report.unlink()
        self.assertFalse(self.call_json(*args)[1]["found"])

    def test_malformed_native_input_is_unconfirmed(self):
        proof = self.launch()
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        directory = Path(proof["session_dir"])
        source, evidence = directory / "capture.jsonl", directory / "delivery.json"
        args = ("probe-report", "--agent", "local", "--pane", "wA:p8", "--report", str(report), "--lines", "40")
        rows = [json.loads(line) for line in body.splitlines()]
        mutations: list[tuple[str, int, str, object]] = [
            ("system-content", 3, "message", {"role": "system", "content": [value]})
            for value in ("text", 1, None, True, ["text"])]
        mutations += [("header-content", 0, "message", {"role": "system", "content": ["text"]}),
                      ("row-type", 1, "type", []),
                      ("final-block-type", len(rows) - 1, "message", {**rows[-1]["message"], "content": [{"type": ["text"]}]})]
        cases = []
        for name, index, key, value in mutations:
            broken = copy.deepcopy(rows)
            broken[index][key] = value
            cases.append((name, "\n".join(json.dumps(row) for row in broken)))
        cases.append(("deep-nesting", body + '\n{"x":' + '[' * 200000 + ']' * 200000 + '}'))
        for prior in (False, True):
            evidence.unlink(missing_ok=True)
            source.write_text(body)
            if prior:
                self.assertTrue(self.call_json(*args)[1]["found"])
            before = evidence.read_bytes() if prior else None
            for name, malformed in cases:
                with self.subTest(case=name, prior=prior, native=malformed[:80]):
                    source.write_text(malformed)
                    rc, result, error = self.call_json(*args)
                    self.assertEqual(rc, 0, error)
                    self.assertFalse(result["found"])
                    self.assertEqual(result["reason"], "pi_marker_unconfirmed")
                    if prior:
                        self.assertEqual(evidence.read_bytes(), before)
                    else:
                        self.assertFalse(evidence.exists())

    def test_delivery_import_refuses_deep_native_nesting(self):
        proof = self.launch()
        body, _, _ = self.native()
        directory = Path(proof["session_dir"])
        source = directory / "capture.jsonl"
        source.write_text(body)
        malformed = body.encode() + b'\n{"x":' + b'[' * 200000 + b']' * 200000 + b'}'
        for prior in (False, True):
            with self.subTest(prior=prior):
                if prior:
                    self.assertIsNotNone(pi_native.delivery_evidence(directory, source, body.encode()))
                evidence = directory / "delivery.json"
                before = evidence.read_bytes() if prior else None
                self.assertIsNone(pi_native.delivery_evidence(directory, source, malformed))
                if prior:
                    self.assertEqual(evidence.read_bytes(), before)
                else:
                    self.assertFalse(evidence.exists())

    def test_delivery_artifact_refuses_unknown_versions_and_corruption(self):
        proof = self.launch()
        body, prompt, report = self.native()
        pi_native.bind_prompt(self.agent, prompt)
        directory = Path(proof["session_dir"])
        (directory / "capture.jsonl").write_text(body)
        args = ("probe-report", "--agent", "local", "--pane", "wA:p8", "--report", str(report), "--lines", "40")
        self.assertTrue(self.call_json(*args)[1]["found"])
        path = directory / "delivery.json"
        good = json.loads(path.read_text())
        cases = [{**good, "schema_version": version} for version in (0, 2, True, "1")]
        cases += [{**good, "records": [{**good["records"][0], "schema_version": version}]} for version in (0, 2, True, None)]
        cases += [{**good, "records": []}, {**good, "source_sha256": "forged"}, None,
                  {**good, "records": [{**good["records"][0], "id": False}]}]
        for key, value in (("type", []), ("version", True), ("id", "")):
            cases.append({**good, "records": [{**good["records"][0], key: value}]})
        for key, value in (("type", "unknown"), ("parentId", True), ("message", [])):
            broken = copy.deepcopy(good)
            broken["records"][-1][key] = value
            cases.append(broken)
        for key, value in (("role", []), ("content", [True]), ("content", [{"type": "text", "text": False}]), ("model", [])):
            broken = copy.deepcopy(good)
            broken["records"][-1]["message"][key] = value
            cases.append(broken)
        for value in cases:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                self.assertFalse(self.call_json(*args)[1]["found"])
                self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps(good))
        self.assertTrue(self.call_json(*args)[1]["found"])
        before = path.read_bytes()
        with patch("os.replace", side_effect=OSError("injected filesystem replacement failure")):
            self.assertFalse(self.call_json(*args)[1]["found"])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.call_json(*args)[1]["found"])

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
        paths = {"common": str(common), "developer": str(brief)}
        result = apply(self.client, {"developer": "local"}, {"local": self.agent}, paths, AT,
                       tiers={"developer": self.tier}, sleep=lambda _: None, settle_sec=0)
        self.assertEqual(result["applied"][0]["status"], "applied")
        with self.assertRaisesRegex(UsageError, "fresh launch"):
            dry_run(self.client, {"developer": "local"}, {"local": self.agent}, paths, no_clear=True, tiers={"developer": self.tier})
        receipt = pi_native.read_launch(self.agent)
        self.assertIn("New assignment from the team lead", receipt["dispatch_prompt"])
        self.assertEqual(sum(call[1:3] == ["agent", "start"] for call in self.transport.calls), 2)
        self.transport.composer = "─" * 55 + "\npending input\n" + "─" * 55
        before = len(self.transport.calls)
        with self.assertRaises(HerdrError):
            apply(self.client, {"developer": "local"}, {"local": self.agent}, paths, AT,
                  tiers={"developer": self.tier}, sleep=lambda _: None, settle_sec=0)
        self.assertFalse(any(call[0] == "kill" or call[1:3] == ["agent", "prompt"] for call in self.transport.calls[before:]))

    def test_planner_preserves_legacy_candidates_and_local_rotation(self):
        self.launch()
        snapshot = measure(self.client, [self.agent], AT)
        snapshot["agents"]["metered"] = {"kind": "claude", "state": "idle", "headroom_pct": 90}
        local = snapshot["agents"]["local"]
        snapshot["agents"]["spare-local"] = copy.deepcopy(local)
        result = plan(["developer"], snapshot, local_agents=["local", "spare-local"], counts={"developer": {"local": 2}})
        self.assertEqual(result["assignments"], {"developer": "spare-local"})
        self.assertIn("local_unmetered", result["rationale"][0])
        for role in ("reviewer", "tester", "release", "architect", "advisor", "investigator", "critic", "lead"):
            with self.subTest(role=role):
                independent = plan([role], snapshot, local_agents=["local", "spare-local"])
                self.assertEqual(independent["assignments"], {role: "metered"})
        for mutation in ({"skipped": True}, {"state": "working"}, {"budget_mode": "metered"}, {"error": {"code": "failure"}}):
            modified = copy.deepcopy(snapshot)
            for name in ("local", "spare-local"):
                modified["agents"][name].update(mutation)
            result = plan(["developer"], modified, local_agents=["local", "spare-local"])
            self.assertEqual(result["assignments"], {"developer": "metered"})
        forged = {"agents": {"claude": {"kind": "claude", "state": "idle", "budget_mode": "local_unmetered", "host_eligibility": {"eligible": True}}}}
        with self.assertRaises(PlanError):
            plan(["developer"], forged, local_agents=["claude"], roster=["claude"])
        self.config.write_text(json.dumps({"schema_version": 5, "agents": [*CONFIG["agents"], pi_entry()]}))
        snapshot["agents"].pop("spare-local")
        snapshot["agents"].pop("metered")
        snapshot["agents"].update({name: {"kind": name, "state": "idle", "headroom_pct": 80} for name in ("claude", "codex", "grok")})
        (self.root / "snapshot.json").write_text(json.dumps(snapshot))
        rc, result, error = self.call_json("plan", "--roles", "developer", "--round", "developer=build", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"developer": "local"})
        rc, ordinary, error = self.call_json("plan", "--roles", "tester", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(ordinary["assignments"], {"tester": "claude"})
        self.chip = "Apple M1"
        rc, result, error = self.call_json("plan", "--roles", "developer", "--round", "developer=build", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"developer": "claude"})
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
        # Override only the interpreter process boundary; real Herdr replies
        # above and the wait entry point still select native verification.
        probe_reply = self.root / "probe-reply.txt"
        interpreter = self.root / "probe-python"
        interpreter.write_text("#!" + sys.executable + "\n" +
            "import os,sys\nfrom pathlib import Path\n" +
            "if sys.argv[1:3]==['-m','teamlead'] and 'probe-report' in sys.argv:\n" +
            " print(Path(" + repr(str(probe_reply)) + ").read_text()); raise SystemExit(0)\n" +
            "os.execv(" + repr(sys.executable) + ", [" + repr(sys.executable) + "] + sys.argv[1:])\n")
        interpreter.chmod(0o755)
        probe_environment = {**environment, "PY_BIN": str(interpreter)}
        for reply in ('not json', '{"found":"yes"}', '{"found":"true"}', '{"found":null}', '{"found":1}',
                      '{"found":true}\nnot json', '{"found":true}\n{"found":false}'):
            probe_reply.write_text(reply)
            with self.subTest(probe_reply=reply):
                invalid = REAL_RUN(command, capture_output=True, text=True, env=probe_environment)
                self.assertEqual(invalid.returncode, 2, invalid.stderr)
                self.assertEqual(invalid.stdout, "")
                self.assertIn("native report-source result", invalid.stderr)
        for found, expected in ((True, 0), (False, 4)):
            probe_reply.write_text(json.dumps({"found": found}))
            with self.subTest(found=found):
                valid = REAL_RUN(command, capture_output=True, text=True, env=probe_environment)
                self.assertEqual(valid.returncode, expected, valid.stderr)
                self.assertEqual(json.loads(valid.stdout)["found"], found)
        evidence = Path(proof["session_dir"]) / "delivery.json"
        before = evidence.read_bytes()
        rows = [json.loads(line) for line in body.splitlines()]
        rows[3]["message"]["content"] = ["text"]
        source.write_text("\n".join(json.dumps(row) for row in rows))
        malformed = REAL_RUN(command, capture_output=True, text=True, env=environment)
        self.assertEqual(malformed.returncode, 4, malformed.stderr)
        self.assertFalse(json.loads(malformed.stdout)["found"])
        self.assertEqual(evidence.read_bytes(), before)
        source.write_text(body)
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
        add_assignment(data, AT, "developer", "local", tier={**self.tier, "launch_args": ["--no-extensions", "--no-mcp", "--tools", "read,bash,edit,write"], "verified": proof})
        path = self.root / "state.json"
        save_state(path, data)
        self.assertTrue(load_state_checked(path)[1])
        data["assignments"][0]["tier"]["verified"]["pid"] = 0
        save_state(path, data)
        self.assertFalse(load_state_checked(path, warn=lambda _: None)[1])


if __name__ == "__main__":
    unittest.main()
