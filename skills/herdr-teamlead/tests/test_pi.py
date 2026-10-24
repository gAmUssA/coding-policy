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
        self.bad_argv = None

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[1:3] == ["agent", "start"]:
            self.started = True
            result = {"agent": self.info, "argv": self.bad_argv or ["pi"] + argv[argv.index("--") + 1:]}
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
        self.tier = select_tier(self.agent, "tester")
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

    def system(self, argv, **kwargs):
        if argv == ["pi", "--version"]:
            return subprocess.CompletedProcess(argv, 0, fixture("version.json")["stdout"], "")
        return sysctl(self.chip, self.memory)(argv, **kwargs)

    def write_models(self):
        (self.provider / "models.json").write_text(json.dumps(self.models))

    def launch(self):
        return start_worker(self.client, self.agent, "wA:p8", self.tier)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        rc = cli.main([*args, "--state", str(self.root / "state.json"), "--config", str(self.config)], stdout=out, stderr=err, client=self.client)
        return rc, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def test_start_local_cli_and_unmetered_measure(self):
        rc, output, error = self.call("start-local", "--agent", "local", "--pane", "wA:p8", "--task", "issue-71", "--role", "tester", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(output["verified"]["pid"], 58701)
        rc, snapshot, error = self.call("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        row = snapshot["agents"]["local"]
        self.assertEqual((snapshot["schema_version"], row["budget_mode"], row["windows"], row["headroom_pct"]), (5, "local_unmetered", {}, None))
        self.assertFalse(any(call[1:3] in (["agent", "prompt"], ["agent", "send-keys"]) for call in self.transport.calls))
        self.assertFalse(any(call[0] == "ps" for call in self.transport.calls))
        rc, result, error = self.call("plan", "--roles", "tester", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"tester": "local"})
        common, brief = self.root / "COMMON.md", self.root / "brief.md"
        common.write_text("Common")
        brief.write_text("Brief")
        rc, applied, error = self.call("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "tester=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(applied["steps"][0]["tier"]["billing_window"], "local_unmetered")
        self.chip = "Apple M1"
        before = list(self.transport.calls)
        rc, _, error = self.call("apply", "--assignments", json.dumps(result), "--common", str(common), "--brief", "tester=" + str(brief), "--dry-run", "--now", AT)
        self.assertEqual(rc, 1)
        self.assertIn("host_ineligible", error)
        self.assertEqual(self.transport.calls, before)

    def test_host_skip_precedes_any_pi_contact_and_probe_failure_is_failed(self):
        self.chip = "Apple M1"
        rc, snapshot, error = self.call("measure", "--now", AT)
        self.assertEqual(rc, 0, error)
        self.assertEqual(snapshot["agents"]["local"]["skip_reason"], "host_ineligible")
        self.assertEqual(self.transport.calls, [])
        self.chip = "unrecognized"
        rc, snapshot, _ = self.call("measure", "--now", AT)
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

    def test_endpoint_and_permission_profile_fail_closed(self):
        self.launch()
        self.models["providers"]["ollama"]["models"][0]["baseUrl"] = "https://remote.example/v1"
        self.write_models()
        result = measure(self.client, [self.agent], AT)
        self.assertEqual(result["failed_agents"], ["local"])
        self.assertNotEqual(result["agents"]["local"]["budget_mode"], "local_unmetered")
        self.assertIn("pi_local_provider_unproven", result["agents"]["local"]["error"]["message"])
        with self.assertRaises(ParseError):
            parse_usage("pi", "")

    def test_tiers_restrict_roles_models_thinking_and_risk(self):
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
        rc, result, error = self.call(*args)
        self.assertEqual(rc, 0, error)
        self.assertTrue(result["found"], result)
        for replacement in ("```\nREPORT: " + str(report) + "\n```", "> REPORT: " + str(report), "REPORT: /other.md"):
            rows = [json.loads(line) for line in body.splitlines()]
            rows[-1]["message"]["content"] = [{"type": "text", "text": replacement}]
            source.write_text("\n".join(json.dumps(row) for row in rows))
            self.assertFalse(self.call(*args)[1]["found"])
        source.write_text(body)
        report.unlink()
        self.assertFalse(self.call(*args)[1]["found"])

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
        rc, result, error = self.call("plan", "--roles", "tester", "--snapshot", str(self.root / "snapshot.json"))
        self.assertEqual(rc, 0, error)
        self.assertEqual(result["assignments"], {"tester": "local"})
        self.chip = "Apple M1"
        rc, result, error = self.call("plan", "--roles", "tester", "--snapshot", str(self.root / "snapshot.json"))
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
            "elif command==['pane','read']: print('REPORT: ' + " + repr(str(report)) + ")\n" +
            "else: raise SystemExit('Unexpected boundary call')\n")
        fake.chmod(0o755)
        command = ["bash", str(FIXTURE.parents[2] / "wait-report.sh"), "--once", "--state", str(self.root / "state.json"), "local", str(report)]
        environment = {**os.environ, "HERDR_BIN": str(fake), "PY_BIN": sys.executable, "TEAMLEAD_UNCONFIRMED_IDLE_READS": "1"}
        accepted = REAL_RUN(command, capture_output=True, text=True, env=environment)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(json.loads(accepted.stdout)["found"])
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
