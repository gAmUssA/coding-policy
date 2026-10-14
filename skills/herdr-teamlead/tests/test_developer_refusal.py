"""Developer refusal recovery through the real owner, planner and dispatch CLI."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import recovery, retrospective as notes, retrospective_runtime as runtime, supervision
from teamlead.cli import main
from teamlead.config import load_config
from teamlead.state import add_assignment, empty_state, load_state, save_state
from tests import test_cli as fixture
from tests.fakes import FakeRunner, ScriptedReads, agent_json

AT = fixture.AT
TASK = "developer-refusal"
IMPORTED = "2026-02-03T11:00:00+00:00"
IMPORT_AT = "2026-02-03T12:00:00+00:00"


class DeveloperRefusalTest(unittest.TestCase):
    # Only Herdr's process transport is fake; all owner and retrospective gates run.
    _client = fixture.ApplyCommandTest._client
    #: `_client` binds the fake transport it built, exactly as its own class does.
    runner: FakeRunner

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")})
        environment.start()
        self.addCleanup(environment.stop)
        self.state = self.root / "state.json"
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(fixture.CONFIG))
        self.agents = {agent.name: agent for agent in load_config(self.config)}
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps(fixture.SNAPSHOT))
        self.common = self.root / "COMMON.md"
        self.common.write_text("Preserve task scope and report actual evidence.\n")
        self.brief = self.root / "developer.md"
        self.note = self.root / "retrospective.md"
        self.note.write_text("# Fixture retrospective\n" + "".join(
            "\n## " + section + "\nFixture observations and next action.\n" for section in notes.SECTIONS))
        supervision.bind(self.state, supervision.identity("fixture-lead", str(self.root), "fixture", pane_id="lead"),
                         AT, root=supervision.default_state_path().parent / "supervision-bindings")

    def invoke(self, args, client=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--config", str(self.config), "--state", str(self.state), *args],
                    stdout=out, stderr=err, client=client)
        return code, out.getvalue(), err.getvalue()

    def seed(self, fix, base="a" * 40):
        state = empty_state()
        recovery.register_task(state["recovery"], {"task": TASK, "base_revision": base,
            "scope": "Repair parser", "allowed_paths": ["src/*"],
            "authorization": {"source": "fixture operator", "quote": "Repair the parser."}}, AT)
        if fix is not None:
            for number in (None, *range(1, fix)):
                at = (datetime.fromisoformat(AT) - timedelta(minutes=10 - (number or 0))).isoformat()
                add_assignment(state, at, "developer", "codex", task=TASK, fix_round=number,
                               context_session={"source": "herdr:codex", "agent": "codex", "kind": "id",
                                                "value": "codex-native", "pane_id": "w3:p1"})
        save_state(self.state, state)

    def args(self, agent, report, identifier, fix, *extra, body=None):
        # The brief names its report path, and brief_identity masks that path,
        # so a fresh report keeps the unchanged brief's identity while `body`
        # supplies a genuinely reworded one.
        self.brief.write_text(body or "Repair the parser and report to `{0}`.\nREPORT: {0}\n".format(report))
        count = len(load_state(self.state)["assignments"])
        at = (datetime.fromisoformat(AT) + timedelta(seconds=count)).isoformat()
        args = ["apply", "--assignments", json.dumps({"developer": agent}), "--task", TASK,
                "--common", str(self.common), "--brief", "developer=" + str(self.brief),
                "--report", "developer=" + report, "--dispatch-id", identifier, "--composer-settle", "0", "--now", at]
        return args + (["--fix-round", str(fix)] if fix is not None else []) + list(extra)

    def prepare_retrospective(self, agent, args, client, identifier, role="developer", task=TASK):
        at = args[args.index("--now") + 1]
        request = {"transitions": [{"agent": agent, "role": role, "task": task,
            "context": "retain" if "--retain-context" in args else "clear", "model": None, "effort": None,
            "pane": {"codex": "w3:p1", "claude": "w2:p1", "grok": "w4:p1"}[agent],
            "brief": str(self.brief), "common": str(self.common), "report": None,
            "unavailable": "Fixture has no preceding report artifact."}]}
        check = runtime.check(self.state, load_state(self.state), client, self.agents, request, at)
        path = self.root / (identifier + "-check.json")
        path.write_text(json.dumps(check))
        with notes.lock(self.state):
            notes.record(self.state, {"id": identifier, "note": str(self.note), "period_start": AT,
                "period_end": at, "triggers": ["daily", "transition"], "tasks": [TASK], "participants": [agent],
                "unavailable": {}, "sources": [], "completed": True, "check": str(path)}, check["coverage"], at)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root / "checkout"), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def history(self):
        """A real checkout with one completed correction and its archived evidence (#23)."""
        (self.root / "checkout/src").mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.name", "Refusal Fixture")
        self.git("config", "user.email", "refusal@example.invalid")
        (self.root / "checkout/src/parser.py").write_text("result = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")
        base = self.git("rev-parse", "HEAD")
        (self.root / "checkout/src/parser.py").write_text("result = 2\n")
        self.git("commit", "-qam", "Completed manual correction")
        head = self.git("rev-parse", "HEAD")
        for name, body in (("authorization.md", TASK + "\nRepair parser\nRepair the parser.\n"),
                           ("transport.md", "Fresh conversation confirmed.\nWorker grok received the correction.\nWorker grok started the correction.\n"),
                           ("completed.md", "Completed " + head + "\n")):
            (self.root / name).write_text(body)
        return base, {"id": "manual-1", "task": TASK, "agent": "grok", "base_revision": base, "scope": "Repair parser",
                      "allowed_paths": ["src/*"], "fix_round": 1, "authorized_first_fix": 1, "authorized_last_fix": 1,
                      "authorization": {"source": "fixture operator", "quote": "Repair the parser."},
                      "occurred_at": IMPORTED, "checkout": str(self.root / "checkout"), "previous_head": base,
                      "head_revision": head, "authorization_evidence": str(self.root / "authorization.md"),
                      "transport_evidence": str(self.root / "transport.md"), "report": str(self.root / "completed.md"),
                      "transport": {"sent": True, "started": True, "clear": "fresh",
                                    "sent_quote": "Worker grok received the correction.",
                                    "started_quote": "Worker grok started the correction.",
                                    "clear_quote": "Fresh conversation confirmed."}}

    def import_correction(self, data):
        record = self.root / "import-correction.json"
        record.write_text(json.dumps(data))
        return self.invoke(["import-correction", "--record", str(record), "--now", IMPORT_AT])

    def refused_initial(self, base, fresh=False):
        """Dispatch the initial brief to codex and record the provider's refusal.

        `fresh` observes the cleared native session, so the refused row carries
        the developer proof a role-clear recovery requires.
        """
        self.seed(None, base)
        report = str(self.root / "first.md")
        args = self.args("codex", report, "first", None)
        client = self._client({"codex": "idle"}, sessions={"codex": "codex-native"})
        self.prepare_retrospective("codex", args, client, "before-first")
        if fresh:
            self.runner.responses["agent get codex"] = ScriptedReads([
                agent_json("codex", "idle", "w3:p1", "codex-previous"), agent_json("codex", "idle", "w3:p1", "codex-native")])
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        if fresh:
            self.assertIsNotNone(load_state(self.state)["assignments"][0]["context_session"])
        dispatch = json.loads(out)["applied"][0]["dispatch_id"]
        self.refuse(dispatch, "codex", report)
        return dispatch

    def refuse(self, dispatch, agent, report):
        receipt = self.root / (dispatch.replace(":", "-") + "-refusal.json")
        receipt.write_text(json.dumps({"agent": agent, "state": "idle", "report_path": report,
            "found": False, "elapsed_seconds": 12, "reason": "terminal_provider_refusal"}))
        record = self.root / "record-refusal.json"
        record.write_text(json.dumps({"dispatch": dispatch, "receipt": str(receipt)}))
        code, _, err = self.invoke(["record-refusal", "--record", str(record), "--now", AT])
        self.assertEqual(code, 0, err)

    def plan(self, fix):
        return self.invoke(["plan", "--roles", "developer", "--task", TASK, "--snapshot", str(self.snapshot),
                            "--now", AT] + (["--fix-round", str(fix)] if fix is not None else []))

    def workflow(self, fix):
        self.seed(fix)
        first_report, moved_report = (str(self.root / name) for name in ("first.md", "moved.md"))
        retained = ("--retain-context",) if fix in (1, 2, 3) else ()
        first_args = self.args("codex", first_report, "first", fix, *retained)
        client = self._client({"codex": "idle"}, sessions={"codex": "codex-native"})
        self.prepare_retrospective("codex", first_args, client, "before-first")
        code, out, err = self.invoke(first_args, client)
        self.assertEqual(code, 0, err)
        first = json.loads(out)["applied"][0]["dispatch_id"]
        original = load_state(self.state)["assignments"]
        self.refuse(first, "codex", first_report)
        code, _, err = self.plan(fix)
        self.assertEqual(code, 0, err)
        # Refusal does not authorize a reset or skipping the unavailable attempt.
        for wrong in ((1,) if fix is None else (None, fix + 1)):
            code, _, _ = self.plan(wrong)
            self.assertEqual(code, 1)
        unchanged = self.state.read_bytes()
        for label, agent, report, body, extra, message in (
                ("resend to the refusing provider", "codex", moved_report, None, (), "resend to the same provider is refused"),
                ("the refused attempt's report path", "claude", first_report, None, (), "already carries the refusal of dispatch"),
                ("a reworded brief", "claude", moved_report, "Rewrite the parser from scratch.\n", (), "reworded brief is not a move"),
                ("a retained context", "claude", moved_report, None, ("--retain-context",), "requires a fresh developer context"),
                ("a kept context", "claude", moved_report, None, ("--no-clear",), "requires a fresh developer context")):
            with self.subTest(refused=label):
                client = self._client({agent: "idle"})
                code, out, err = self.invoke(
                    self.args(agent, report, "invalid", fix, "--dry-run", *extra, body=body), client)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), unchanged)
        args = self.args("claude", moved_report, "replacement", fix)
        client = self._client({"claude": "idle"})
        before = self.state.read_bytes()
        code, out, err = self.invoke(args + ["--dry-run"], client)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["clear_reason"], "automatic")
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.runner.calls, [])
        self.prepare_retrospective("claude", args, client, "before-move")
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        moved = json.loads(out)["applied"][0]
        self.assertEqual((moved["fix_round"], moved["clear_reason"], moved["cleared"]), (fix, "automatic", True))
        state = load_state(self.state)
        self.assertEqual(state["assignments"][:-1], original)
        self.assertEqual(len(state["assignments"]), len(original) + 1)
        self.assertEqual(state["recovery"]["dispatches"][-1]["refusal_move"]["from"], first)
        # Each replay rebuilds its own brief: a completed dispatch returns its
        # original outcome, and the refused original replays as itself.
        for identifier, agent, report, extra in (("replacement", "claude", moved_report, ()),
                                                 ("first", "codex", first_report, retained)):
            with self.subTest(replayed=agent):
                code, out, err = self.invoke(self.args(agent, report, identifier, fix, *extra), self._client({}))
                self.assertEqual(code, 0, err)
                applied = json.loads(out)["applied"][0]
                self.assertTrue(applied["replayed"])
                self.assertEqual(applied["agent"], agent)
                self.assertEqual(self.runner.calls, [])
        code, _, _ = self.plan(fix)
        self.assertEqual(code, 1)
        if fix != 5:
            code, _, err = self.plan((fix or 0) + 1)
            self.assertEqual(code, 0, err)
        self.refuse(moved["dispatch_id"], "claude", moved_report)
        third_report = str(self.root / "third.md")
        before = self.state.read_bytes()
        code, _, err = self.invoke(self.args("grok", third_report, "third", fix), self._client({"grok": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("refused by 2 providers", err)
        self.assertEqual(self.runner.writes(), [])
        self.assertEqual(self.state.read_bytes(), before)
        return {"fix": fix, "moved": moved, "third_report": third_report}

    def test_initial_developer_brief_moves_without_becoming_fix_one(self):
        self.workflow(None)

    def test_first_fix_moves_to_a_fresh_provider_at_the_same_number(self):
        self.workflow(1)

    def test_later_fix_moves_without_consuming_another_round(self):
        self.workflow(4)

    def test_last_ordinary_fix_remains_inside_the_five_fix_budget(self):
        self.workflow(5)

    def test_refused_initial_dispatch_cannot_be_followed_by_an_imported_fix_one(self):
        base, data = self.history()
        self.refused_initial(base)
        before = self.state.read_bytes()
        code, out, err = self.import_correction(data)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no work-bearing developer attempt", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_moved_initial_work_is_followed_by_an_imported_fix_one(self):
        base, data = self.history()
        self.refused_initial(base)
        args = self.args("claude", str(self.root / "moved.md"), "replacement", None)
        client = self._client({"claude": "idle"})
        self.prepare_retrospective("claude", args, client, "before-move")
        code, _, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        code, out, err = self.import_correction(data)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fix_round"], 1)
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks"][TASK]["confirmed_fixes"], 1)

    def test_refused_initial_dispatch_cannot_open_a_role_clear_fix_one(self):
        dispatch = self.refused_initial("a" * 40, fresh=True)
        # The lead assessed the refusal, so codex is free for another role.
        supervision.resolve(self.state, {"id": dispatch, "outcome": "Provider refused; no report delivered",
            "evidence": [str(self.root / (dispatch.replace(":", "-") + "-refusal.json"))]}, AT)
        other = "another-authorized-task"
        state = load_state(self.state)
        recovery.register_task(state["recovery"], {"task": other, "base_revision": "a" * 40, "scope": "Verify another task",
            "allowed_paths": ["src/*"], "authorization": {"source": "fixture operator", "quote": "Verify it."}}, AT)
        save_state(self.state, state)
        brief = self.brief
        brief.write_text("Verify the other task.\n")
        at = (datetime.fromisoformat(AT) + timedelta(minutes=5)).isoformat()
        args = ["apply", "--assignments", json.dumps({"tester": "codex"}), "--task", other, "--common", str(self.common),
                "--brief", "tester=" + str(brief), "--report", "tester=" + str(self.root / "tester-report.md"),
                "--dispatch-id", "clearing", "--composer-settle", "0", "--now", at]
        client = self._client({"codex": "idle"}, sessions={"codex": "codex-cleared"})
        self.prepare_retrospective("codex", args, client, "before-tester", role="tester", task=other)
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        evidence = self.root / "clear-output.json"
        evidence.write_text(out)
        record = self.root / "recover-role-clear.json"
        record.write_text(json.dumps({"id": "role-clear-1", "task": TASK, "base_revision": "a" * 40,
            "assignment_index": 0, "clearing_assignment_index": 1, "next_fix": 1, "correction_plan": None,
            "work": {"base_revision": "a" * 40, "scope": "Repair parser", "paths": ["src/parser.py"],
                     "findings": ["Parser still returns the wrong result."]},
            "clearing_authorization": {"task": other}, "evidence": str(evidence),
            "reason": "The lead reused the developer for another authorized tester role"}))
        before = self.state.read_bytes()
        code, out, err = self.invoke(["recover-role-clear", "--record", str(record), "--now", IMPORT_AT],
                                     self._client({"codex": "idle"}, sessions={"codex": "later-observation"}))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("was refused and bore no work", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_operator_authorization_allows_one_more_dispatch_at_the_same_number(self):
        # Two independent refusals stop the line. The operator's decision
        # permits one further dispatch of the unchanged brief, still at the
        # same correction number and still without a fresh context flag.
        reached = self.workflow(2)
        fix, third_report = reached["fix"], reached["third_report"]
        record = self.root / "authorize.json"
        record.write_text(json.dumps({"id": "authorize-1", "task": TASK, "role": "developer", "fix_round": fix,
            "provider": "grok", "brief": "unchanged", "decision": "Dispatch the unchanged brief to grok.",
            "authorization": {"source": "fixture operator message", "quote": "Send it to grok unchanged."}}))
        code, _, err = self.invoke(["authorize-refused-dispatch", "--record", str(record), "--now", AT])
        self.assertEqual(code, 0, err)
        before = self.state.read_bytes()
        rejected = self.args("claude", third_report, "wrong-provider", fix, "--dry-run")
        code, _, err = self.invoke(rejected, self._client({"claude": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("approves provider grok", err)
        self.assertEqual(self.state.read_bytes(), before)
        args = self.args("grok", third_report, "third", fix)
        client = self._client({"grok": "idle"})
        self.prepare_retrospective("grok", args, client, "before-authorized")
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        applied = json.loads(out)["applied"][0]
        self.assertEqual((applied["agent"], applied["fix_round"], applied["clear_reason"]), ("grok", fix, "automatic"))
        state = load_state(self.state)
        moved = state["recovery"]["dispatches"][-1]
        self.assertEqual(moved["refusal_move"]["authorization"], "authorize-1")
        self.assertEqual(moved["refusal_move"]["from"], reached["moved"]["dispatch_id"])
        # The authorization is spent: a fourth dispatch stops for the operator.
        code, _, err = self.invoke(self.args("codex", str(self.root / "fourth.md"), "fourth", fix),
                                   self._client({"codex": "idle"}))
        self.assertEqual(code, 1)
        self.assertIn("refused by 2 providers", err)
        self.assertEqual(self.runner.writes(), [])
        # Three dispatches, one correction number: the budget did not grow.
        code, _, err = self.plan(fix + 1)
        self.assertEqual(code, 0, err)
        code, _, _ = self.plan(fix + 2)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
