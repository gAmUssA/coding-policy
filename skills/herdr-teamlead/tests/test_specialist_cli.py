"""Public specialist planning, dispatch, assessment and packaged brief contracts."""

import copy
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import engagement, recovery, supervision
from teamlead.config import load_config
from teamlead.state import add_assignment, empty_state, save_state
from teamlead.tiers import select_tier
from tests import test_cli as fixture
from tests import test_historical as historical_fixture
from tests.fakes import FakeRunner, ScriptedReads, agent_json
from tests.test_engagement import REQUIREMENT
from tests.test_qualification import AT, qualified_tier

SKILL = Path(__file__).resolve().parents[1]


class SpecialistCliTest(fixture.CliCase):
    _client = fixture.ApplyCommandTest._client

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner()
        self.settings = copy.deepcopy(fixture.CONFIG)
        self.settings["schema_version"] = 3
        for agent in self.settings["agents"]:
            agent["capabilities"] = ["interaction-design"] if agent["name"] == "claude" else []
        self.config.write_text(json.dumps(self.settings))
        self.briefs["advisor"] = self.tmp / "advisor.md"
        self.briefs["advisor"].write_text("Inspect onboarding and report the evidence.")
        self.requirements_file = self.tmp / "requirements.json"
        self.requirements_file.write_text(json.dumps({"schema_version": 1, "assignments": {"advisor": REQUIREMENT}}))
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.tmp / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        self.report = self.tmp / "advisor-report.md"

    def bind(self):
        who = supervision.identity("lead", str(self.tmp), "fixture", pane_id="lead-pane")
        supervision.bind(self.state, who, AT)

    def invoke(self, args, client=None):
        self.out, self.err = io.StringIO(), io.StringIO()
        return self.run_cli(self.base() + args, client=client)

    def document(self, worker="claude"):
        return {"assignments": {"advisor": worker}, "requirements": {"advisor": copy.deepcopy(REQUIREMENT)}}

    def apply_args(self, document=None, *, identifier="consultation", retained=False):
        document = document or self.document()
        args = ["apply", "--assignments", json.dumps(document), "--task", "task-1", "--now", AT,
                "--dispatch-id", identifier, "--common", str(self.common), "--composer-settle", "0"]
        for role in document["assignments"]:
            args += ["--brief", role + "=" + str(self.briefs[role]), "--report", role + "=" + str(self.report)]
        if retained:
            args.append("--retain-specialist")
        return args

    def saved(self):
        return json.loads(self.state.read_text())

    def test_plan_uses_explicit_capability_before_headroom_without_contacting_workers(self):
        code, out, err = self.invoke(["plan", "--roles", "advisor", "--requirements", str(self.requirements_file),
            "--task", "task-1", "--snapshot", str(self.snapshot), "--now", AT], self._client({}))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["assignments"], {"advisor": "claude"})
        self.assertEqual(plan["requirements"], {"advisor": REQUIREMENT})
        self.assertEqual(self.runner.calls, [])

    def test_missing_requirements_or_capabilities_refuses_before_worker_input(self):
        self.bind()
        for document in ({"assignments": {"advisor": "claude"}}, self.document("grok")):
            with self.subTest(document=document):
                client = self._client({"claude": "idle", "grok": "idle"})
                code, _, _ = self.invoke(self.apply_args(document), client)
                self.assertEqual(code, 1)
                self.assertEqual(self.runner.calls, [])
                self.assertEqual(supervision.load(self.state)["members"], [])

    def test_specialist_requires_bound_supervision_even_when_worker_is_idle(self):
        code, _, err = self.invoke(self.apply_args(), self._client({"claude": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("supervision-bind", err)
        self.assertEqual(self.runner.calls, [])

    def test_new_architect_plan_and_dispatch_require_engagement_before_worker_contact(self):
        self.briefs["architect"] = self.briefs["advisor"]
        document = {"assignments": {"architect": "claude"}}
        commands = [
            ["plan", "--roles", "architect", "--task", "task-1", "--snapshot", str(self.snapshot), "--now", AT],
            self.apply_args(document), self.apply_args(document) + ["--dry-run"],
        ]
        for args in commands:
            with self.subTest(command=args[0]):
                code, _, err = self.invoke(args, self._client({}))
                self.assertEqual(code, 1)
                self.assertIn("require explicit specialist requirements", err)
                self.assertEqual(self.runner.calls, [])
                self.assertFalse(self.state.exists())

    def test_completed_historical_architect_retry_preserves_original_receipt(self):
        self.briefs["architect"] = self.briefs["advisor"]
        paths = {"common": str(self.common), "architect": str(self.briefs["architect"])}
        identifier, fingerprint = recovery.dispatch_identity(
            "task-1", "architect", "claude", None, paths, "legacy",
            options={"task": "task-1", "fix_round": None, "plan": None, "work": None,
                     "rounds": {}, "retain_context": False, "no_clear": False})
        state = empty_state()
        recovery.reserve(state["recovery"], {"id": identifier, "fingerprint": fingerprint,
            "role": "architect", "agent": "claude", "task": "task-1", "fix_round": None,
            "plan": None, "work": None, "brief": paths["architect"], "common": paths["common"]}, AT)
        add_assignment(state, AT, "architect", "claude", task="task-1", cleared=True, clear_reason="automatic")
        result = {key: value for key, value in state["assignments"][0].items()
                  if key not in {"schema_version", "requirements", "reviewer_scope"}}
        recovery.finish_dispatch(state["recovery"], identifier, result, 0, AT)
        save_state(self.state, state)
        before = self.state.read_bytes()
        document = {"assignments": {"architect": "claude"}}
        code, out, err = self.invoke(self.apply_args(document, identifier="legacy"), self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.runner.calls, [])
        code, _, err = self.invoke(self.apply_args(document, identifier="fresh"), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("require explicit specialist requirements", err)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.runner.calls, [])

    def test_new_consultation_enrolls_and_persists_requirements_before_prompt(self):
        self.bind()
        client = self._client({"claude": "idle"}, sessions={"claude": "outgoing"})
        self.runner.responses["agent get claude"] = ScriptedReads([
            agent_json("claude", "idle", "w2:p1", "outgoing"),
            agent_json("claude", "idle", "w2:p1", "incoming"),
        ])
        original_prompt = client.agent_prompt
        def prompt(*args, **kwargs):
            fleet = supervision.load(self.state)
            self.assertEqual(fleet["members"][0]["id"], "consultation:advisor")
            self.assertTrue(fleet["members"][0]["active"])
            reserved = self.saved()["recovery"]["dispatches"][0]
            self.assertEqual(reserved["requirements"], REQUIREMENT)
            self.assertEqual(reserved["status"], "reserved" if args[1].startswith("/") else "sending")
            return original_prompt(*args, **kwargs)
        with patch.object(client, "agent_prompt", side_effect=prompt):
            code, out, err = self.invoke(self.apply_args(), client)
        self.assertEqual(code, 0, err)
        row = json.loads(out)["applied"][0]
        self.assertEqual(row["context_session"]["value"], "incoming")
        state = self.saved()
        self.assertEqual(state["assignments"][0]["requirements"], REQUIREMENT)
        self.assertEqual(state["recovery"]["dispatches"][0]["schema_version"], 2)
        self.assertEqual(state["recovery"]["dispatches"][0]["result"]["schema_version"], 2)

    def test_completed_retry_returns_saved_result_without_live_calls_or_second_assignment(self):
        self.bind()
        code, _, err = self.invoke(self.apply_args(), self._client({"claude": "idle"}))
        self.assertEqual(code, 0, err)
        before = self.state.read_bytes()
        code, out, err = self.invoke(self.apply_args(), self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.read_bytes(), before)
        changed = self.document()
        changed["requirements"]["advisor"]["engagement"] = "other-engagement"
        code, _, err = self.invoke(self.apply_args(changed), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("different inputs", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_prior_contributor_cannot_take_independent_review_even_after_role_clear(self):
        self.bind()
        state = empty_state()
        add_assignment(state, "2026-01-08T10:00:00Z", "advisor", "claude", task="task-1", requirements=REQUIREMENT)
        add_assignment(state, "2026-01-08T11:00:00Z", "reviewer", "claude", task="other-task", cleared=True, clear_reason="automatic")
        save_state(self.state, state)
        document = {"assignments": {"reviewer": "claude"}, "requirements": {"reviewer": {**REQUIREMENT, "independent": True}}}
        code, _, err = self.invoke(self.apply_args(document), self._client({"claude": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("ineligible", err)
        self.assertEqual(self.runner.calls, [])

    def test_new_verification_reviewer_can_review_again_without_becoming_a_designer(self):
        self.bind()
        document = {"assignments": {"reviewer": "claude"}}
        code, _, err = self.invoke(self.apply_args(document), self._client({"claude": "idle"}))
        self.assertEqual(code, 0, err)
        saved = self.saved()
        self.assertEqual(saved["assignments"][0]["reviewer_scope"], "verification")
        self.assertEqual(saved["recovery"]["dispatches"][0]["reviewer_scope"], "verification")
        self.report.write_text("Verified current tip without shaping the implementation.")
        supervision.resolve(self.state, {"id": "consultation:reviewer", "outcome": "Review read and assessed",
                                        "evidence": [str(self.report)]}, AT)
        self.report = self.tmp / "second-review-report.md"
        args = self.apply_args(document, identifier="second-review")
        args[args.index("--now") + 1] = "2026-01-08T12:00:01Z"
        code, _, err = self.invoke(args, self._client({"claude": "idle"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.saved()["assignments"]), 2)

    def seed_warm_consultation(self, *, assess=True, retire=True):
        self.bind()
        tier = json.loads(json.dumps(qualified_tier()).replace("sonnet-5", "opus-5"))
        tier["qualification"][0]["role"] = "advisor"
        self.settings["agents"] = self.settings["agents"][:1]
        self.settings["agents"][0]["tiers"] = {"architect": tier}
        self.config.write_text(json.dumps(self.settings))
        agent = load_config(self.config)[0]
        wanted = select_tier(agent, "advisor")
        argv = ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]
        proof = {"source": "process_argv", "model": "opus-5", "effort": "high", "pane_id": "w2:p1", "pid": 200, "argv": argv}
        native = {"source": "herdr:claude", "agent": "claude", "kind": "id", "value": "consult-session", "pane_id": "w2:p1"}
        state = empty_state()
        record = {"id": "prior:advisor", "fingerprint": "a" * 64, "task": "task-1", "role": "advisor", "agent": "claude",
                  "fix_round": None, "plan": None, "work": None, "requirements": copy.deepcopy(REQUIREMENT)}
        recovery.reserve(state["recovery"], record, AT)
        add_assignment(state, "2026-01-08T11:00:00Z", "advisor", "claude", task="task-1", requirements=REQUIREMENT,
            cleared=True, clear_reason="automatic", context_session=native,
            tier={**wanted, "verified": proof, "launch_args": ["--dangerously-skip-permissions"]})
        result = {key: value for key, value in state["assignments"][0].items() if key != "reviewer_scope"}
        recovery.finish_dispatch(state["recovery"], record["id"], {**result, "pane_id": "w2:p1"}, 0, AT)
        prior_report = self.tmp / "prior-report.md"
        prior_report.write_text("Proposed the interaction; implementation remains pending.")
        delivery = self.tmp / "prior-delivery.json"
        delivery.write_text(json.dumps({"found": True, "agent": "claude", "report_path": str(prior_report)}))
        supervision.enroll(self.state, {"id": record["id"], "agent": "claude", "task": "task-1", "report": str(prior_report),
                                       "pane_id": "w2:p1", "native_session": native}, AT)
        if assess:
            engagement.record_assessment(state, self.state, {"id": "assessed", "dispatch": record["id"], "report": str(prior_report),
                "delivery": str(delivery), "outcome": "Consultation delivered", "contribution": "design", "summary": "Interaction proposal read and assessed."}, AT)
        if retire:
            supervision.resolve(self.state, {"id": record["id"], "outcome": "Consultation ended", "evidence": [str(prior_report)]}, AT)
        save_state(self.state, state)
        client = self._client({"claude": "idle"}, sessions={"claude": "consult-session"})
        self.runner.set("pane process-info", json.dumps({"result": {"process_info": {"pane_id": "w2:p1",
            "foreground_processes": [{"name": "claude", "pid": 200, "argv": argv}]}}}))
        return client

    def test_assessed_resolved_consultation_can_follow_up_warm_with_new_enrollment(self):
        client = self.seed_warm_consultation()
        code, out, err = self.invoke(self.apply_args(retained=True), client)
        self.assertEqual(code, 0, err)
        result = json.loads(out)["applied"][0]
        self.assertFalse(result["cleared"])
        self.assertEqual(result["context_session"]["value"], "consult-session")
        self.assertEqual(len(self.runner.writes()), 1)
        members = supervision.load(self.state)["members"]
        self.assertEqual([(row["id"], row["active"]) for row in members], [("prior:advisor", False), ("consultation:advisor", True)])
        self.assertEqual(len(self.saved()["assignments"]), 2)

    def test_retained_followup_refuses_missing_assessment_before_contacting_worker(self):
        client = self.seed_warm_consultation(assess=False)
        code, _, err = self.invoke(self.apply_args(retained=True), client)
        self.assertEqual(code, 1)
        self.assertIn("assess-specialist", err)
        self.assertEqual(self.runner.calls, [])

    def test_retained_followup_refuses_active_supervision_before_contacting_worker(self):
        client = self.seed_warm_consultation(retire=False)
        code, _, err = self.invoke(self.apply_args(retained=True), client)
        self.assertEqual(code, 1)
        self.assertIn("enrollment", err)
        self.assertEqual(self.runner.calls, [])

    def test_assess_command_retrieves_real_receipts_without_worker_calls(self):
        self.seed_warm_consultation(assess=False, retire=False)
        data = {"id": "assessed-via-cli", "dispatch": "prior:advisor", "report": str(self.tmp / "prior-report.md"),
                "delivery": str(self.tmp / "prior-delivery.json"), "outcome": "Delivered consultation",
                "contribution": "none", "summary": "Read the report; no design contribution was made."}
        record = self.tmp / "assessment.json"
        record.write_text(json.dumps(data))
        code, out, err = self.invoke(["assess-specialist", "--record", str(record), "--now", AT], self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["contribution"], "none")
        self.assertEqual(self.runner.calls, [])

    def test_imported_correction_rejects_other_contributors_and_accepts_independent_reviewer(self):
        case = historical_fixture.HistoricalCommandsTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.seed()
        code, _, err = case.owner("import-correction", case.attempt())
        self.assertEqual(code, 0, err)
        state = case.saved()
        for role, name in (("architect", "design-worker"), ("developer", "implementation-worker")):
            add_assignment(state, historical_fixture.CLEARED, role, name, task=historical_fixture.TASK)
        save_state(case.state, state)
        before = case.state.read_bytes()
        review = {"id": "independent-review", "historical_attempt": "manual-1", "head_revision": case.head_revision,
                  "verdict": "approved", "review_mode": "full", "reviewer": "design-worker", "report": str(case.report)}
        for reviewer in ("design-worker", "implementation-worker"):
            with self.subTest(reviewer=reviewer):
                code, _, err = case.owner("record-historical-review", {**review, "reviewer": reviewer}, case._client({}))
                self.assertEqual(code, 1)
                self.assertIn("contributed", err)
                self.assertEqual(case.state.read_bytes(), before)
                self.assertEqual(case.runner.calls, [])
        code, out, err = case.owner("record-historical-review", {**review, "reviewer": "independent-worker"}, case._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["input"]["reviewer"], "independent-worker")
        self.assertEqual(case.runner.calls, [])

    def test_packaged_specialist_aliases_compose_concrete_briefs_with_distinct_reports(self):
        policy = self.tmp / "policy.md"
        policy.write_text("Verified policy index and release entrypoint fixture.")
        values = {"shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "Owned fixture repository",
            "TASK_AUTHORIZATION": "Read-only consultation on onboarding", "AUTHORIZED_ACTIONS": "Read and report",
            "EXTERNAL_PERMISSION": "No external actions", "POLICY_INDEX": str(policy), "RELEASE_SKILL": str(policy)}, "roles": {}}
        for role in ("advisor", "investigator", "architect"):
            values["roles"][role] = {"RESPONSIBILITY": role, "SPECIALTY": "ux", "TASK": "task-1", "ISSUE": "Onboarding",
                "BRANCH": "feat/onboarding", "OBJECTIVE": "Assess the account setup interaction",
                "ACCEPTANCE_CRITERIA": "A source-linked answer to the assigned question", "SCOPE_LIMITS": "Read source; write the report",
                "INPUTS": "Read the accepted interaction", "TOOLS_AND_SKILLS": "Available browser fixture",
                "KNOWLEDGE": "Saved project conventions", "CONTRIBUTION_HISTORY": "No prior contributions",
                "REPORT": "/r/" + role + ".md"}
        record = self.tmp / "values.json"
        record.write_text(json.dumps(values))
        output = self.tmp / "composed"
        result = subprocess.run(["bash", str(SKILL / "compose-briefs.sh"), str(SKILL / "templates"), str(record), str(output)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = json.loads(result.stdout)
        for role, path in files["briefs"].items():
            body = Path(path).read_text()
            self.assertIn("**" + role + "**", body)
            self.assertIn("REPORT: /r/" + role + ".md", body)
            self.assertIn("Assess the account setup interaction", body)
            self.assertNotIn("{{", body)
        self.assertEqual(set(files["briefs"]), {"advisor", "investigator", "architect"})


if __name__ == "__main__":
    unittest.main()
