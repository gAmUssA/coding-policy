"""Legacy owner recovery outcomes with actual local Git and controlled Herdr."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from teamlead.state import add_assignment, empty_state, save_state
from tests import test_cli as fixture
from tests import test_recovery_cli as recovery_fixture
from tests.fakes import FakeRunner

AT = fixture.AT
BEFORE = "2026-02-03T08:00:00+00:00"
CLEARED = "2026-02-03T08:30:00+00:00"
COMPLETED = "2026-02-03T09:00:00+00:00"
TASK = recovery_fixture.TASK
AUTH = recovery_fixture.AUTH
SCOPE = recovery_fixture.WORK["scope"]


class HistoricalCommandsTest(fixture.CliCase):
    _client = fixture.ApplyCommandTest._client
    invoke = recovery_fixture.RecoveryCommandTests.invoke
    owner = recovery_fixture.RecoveryCommandTests.owner
    saved = recovery_fixture.RecoveryCommandTests.saved
    apply_args = recovery_fixture.RecoveryCommandTests.apply_args
    fresh_client = recovery_fixture.RecoveryCommandTests.fresh_client

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner()
        self.checkout = self.tmp / "history checkout"
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Recovery Fixture")
        self.git("config", "user.email", "recovery@example.invalid")
        (self.checkout / "src").mkdir()
        (self.checkout / "src/parser.py").write_text("result = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")
        self.base_revision = self.git("rev-parse", "HEAD").strip()
        (self.checkout / "src/parser.py").write_text("result = 2\n")
        self.git("commit", "-qam", "Completed manual correction")
        self.head_revision = self.git("rev-parse", "HEAD").strip()
        self.report = self.tmp / "completed.md"
        self.report.write_text("Completed " + self.head_revision + "\nTests passed; independent review remains required.\n")
        self.auth_file = self.tmp / "authorization.md"
        self.auth_file.write_text(TASK + "\n" + SCOPE + "\n" + AUTH["quote"] + "\nApproved attempt bounds: 1 through 6.\n")
        self.transport_file = self.tmp / "transport.md"
        self.transport_file.write_text("Task " + TASK + "\nFresh conversation confirmed.\nWorker grok received the correction.\nWorker grok started the correction.\n")
        self.clear_file = self.tmp / "clear.md"
        self.clear_file.write_text("Verified fresh conversation release-session and empty composer before release.\n")

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_DATE=BEFORE, GIT_COMMITTER_DATE=BEFORE)
        return subprocess.run(["git", "-C", str(self.checkout), *args], env=env,
                              check=True, capture_output=True, text=True).stdout

    def seed(self, fixes=0, release=False):
        state = empty_state()
        add_assignment(state, BEFORE, "developer", "grok", task=TASK)
        for number in range(1, fixes + 1):
            add_assignment(state, "2026-02-03T08:00:0{}+00:00".format(number), "developer", "grok", task=TASK, fix_round=number,
                           context_session={"pane_id": "w4:p1", "source": "herdr:grok", "agent": "grok",
                                            "kind": "id", "value": "old-developer"})
        if release:
            add_assignment(state, COMPLETED, "release", "grok", task=TASK, cleared=False, clear_reason="hand")
        # A real version-4 document has no recovery section; migrate through
        # the public owner command without altering any historical evidence.
        state["schema_version"] = 4
        state.pop("recovery")
        for row in state["assignments"]:
            row["schema_version"] = 4
        self.state.write_text(json.dumps(state))
        original = copy.deepcopy(state["assignments"])
        code, _, err = self.invoke(["state"])
        self.assertEqual(code, 0, err)
        for expected, actual in zip(original, self.saved()["assignments"]):
            expected["schema_version"] = actual["schema_version"]
            self.assertEqual(actual, expected)
        code, _, err = self.owner("task", {"task": TASK, "base_revision": self.base_revision,
            "scope": SCOPE, "allowed_paths": ["src/*"], "authorization": AUTH})
        self.assertEqual(code, 0, err)
        return copy.deepcopy(self.saved()["assignments"])

    def attempt(self, number=1):
        return {"id": "manual-" + str(number), "task": TASK, "agent": "grok",
                "base_revision": self.base_revision, "scope": SCOPE, "allowed_paths": ["src/*"],
                "fix_round": number, "authorization": AUTH, "authorized_first_fix": number,
                "authorized_last_fix": number, "occurred_at": COMPLETED, "checkout": str(self.checkout),
                "previous_head": self.base_revision, "head_revision": self.head_revision,
                "authorization_evidence": str(self.auth_file), "transport_evidence": str(self.transport_file),
                "report": str(self.report), "transport": {"sent": True, "started": True, "clear": "fresh",
                    "sent_quote": "Worker grok received the correction.", "started_quote": "Worker grok started the correction.",
                    "clear_quote": "Fresh conversation confirmed."}}

    def clearance(self):
        return {"id": "release-clear", "task": TASK, "assignment_index": len(self.saved()["assignments"]) - 1,
                "cleared_at": CLEARED, "fresh_session": {"kind": "id", "value": "release-session"},
                "verified_empty_composer": True, "verified_fresh_conversation": True,
                "fresh_quote": "Verified fresh conversation release-session", "composer_quote": "empty composer before release",
                "evidence": str(self.clear_file), "reason": "Required automatic clear timed out during native startup"}

    def current_developer(self):
        """Start current task A through owner commands before importing task B."""
        self.seed()
        current = "current-session-task"
        code, _, err = self.owner("task", {"task": current, "base_revision": self.base_revision,
            "scope": SCOPE, "allowed_paths": ["src/*"], "authorization": AUTH})
        self.assertEqual(code, 0, err)
        for number in (None, 1, 2):
            self.briefs["developer"].write_text("Current task correction {}.\n".format(number or 0))
            args = self.apply_args("developer", number, "--now", "2026-02-03T09:3{}:00+00:00".format(number or 0))
            args[args.index("--task") + 1] = current
            if number is not None:
                args.append("--retain-context")
            client = self.fresh_client("previous-session", "current-developer") if number is None else self._client(
                {"grok": "idle"}, sessions={"grok": "current-developer"})
            code, _, err = self.invoke(args, client)
            self.assertEqual(code, 0, err)
        return current

    def retained_plan(self, task, number=3):
        code, out, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
            "--exclude", "developer=claude,codex", "--task", task, "--fix-round", str(number), "--now", AT])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["assignments"], {"developer": "grok"})
        plan = self.tmp / "retained-plan.json"
        plan.write_text(out)
        self.briefs["developer"].write_text("Address independent findings in counted correction {}.\n".format(number))
        args = self.apply_args("developer", number, "--retain-context")
        args[args.index("--task") + 1] = task
        args[args.index("--assignments") + 1] = str(plan)
        return args

    def test_older_other_task_import_preserves_live_retained_dispatch_and_replay(self):
        current = self.current_developer()
        before_import = copy.deepcopy(self.saved()["assignments"])
        code, _, err = self.owner("import-correction", self.attempt(), self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.saved()["assignments"][:-1], before_import)
        imported_index = len(before_import)
        self.assertEqual(self.saved()["recovery"]["historical_attempts"][0]["assignment_index"], imported_index)
        # Independent verification takes other workers through normal apply.
        for role, worker in (("reviewer", "claude"), ("tester", "codex")):
            args = self.apply_args(role, None, "--now", "2026-02-03T09:40:00+00:00")
            args[args.index("--task") + 1] = current
            args[args.index("--assignments") + 1] = json.dumps({role: worker})
            code, _, err = self.invoke(args, self._client({worker: "idle"}))
            self.assertEqual(code, 0, err)
        preserved = copy.deepcopy(self.saved()["assignments"])
        args = self.retained_plan(current)
        code, out, err = self.invoke(args, self._client({"grok": "idle"}, sessions={"grok": "current-developer"}))
        self.assertEqual(code, 0, err)
        result = json.loads(out)["applied"][0]
        self.assertEqual((result["fix_round"], result["clear_reason"], result["context_session"]["value"]),
                         (3, "retained", "current-developer"))
        self.assertEqual(len(self.runner.writes()), 1)
        self.assertTrue(self.runner.writes()[0].startswith("agent prompt grok "))
        self.assertEqual(self.saved()["assignments"][:-1], preserved)
        self.assertEqual(self.saved()["assignments"][imported_index], preserved[imported_index])
        self.assertEqual([row["fix_round"] for row in self.saved()["assignments"]
                          if row["task"] == current and row["role"] == "developer"], [None, 1, 2, 3])
        saved = self.state.read_bytes()
        code, out, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["applied"][0]["replayed"])
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.state.read_bytes(), saved)

    def test_newer_or_tied_other_task_import_refuses_retention_without_writes(self):
        for occurred_at, message in (("2026-02-03T09:33:00+00:00", "Cannot retain"),
                                     ("2026-02-03T10:32:00+01:00", "chronology is uncertain")):
            with self.subTest(occurred_at=occurred_at):
                current = self.current_developer()
                code, _, err = self.owner("import-correction", {**self.attempt(), "occurred_at": occurred_at}, self._client({}))
                self.assertEqual(code, 0, err)
                self.assertEqual(self.runner.calls, [])
                args = self.retained_plan(current)
                saved = self.state.read_bytes()
                code, _, err = self.invoke(args, self._client({"grok": "idle"}, sessions={"grok": "current-developer"}))
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), saved)

    def test_older_import_keeps_live_identity_and_readiness_checks(self):
        for status, session in (("idle", "different-native-session"), ("idle", None), ("blocked", "current-developer")):
            with self.subTest(status=status, session=session):
                current = self.current_developer()
                code, _, err = self.owner("import-correction", self.attempt(), self._client({}))
                self.assertEqual(code, 0, err)
                args = self.retained_plan(current)
                saved = self.state.read_bytes()
                code, _, _ = self.invoke(args, self._client({"grok": status}, sessions={"grok": session}))
                self.assertEqual(code, 1)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), saved)

    def test_same_task_import_preserves_actual_next_count_and_refuses_older_event(self):
        current = self.current_developer()
        self.auth_file.write_text(current + "\n" + SCOPE + "\n" + AUTH["quote"] + "\nApproved attempt bounds: 3 through 3.\n")
        data = {**self.attempt(3), "task": current}
        saved = self.state.read_bytes()
        code, _, err = self.owner("import-correction", data, self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("chronology", err)
        self.assertEqual(self.state.read_bytes(), saved)
        code, _, err = self.owner("import-correction", {**data, "occurred_at": "2026-02-03T09:33:00+00:00"}, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        saved = self.state.read_bytes()
        code, _, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
            "--task", current, "--fix-round", "3", "--now", AT])
        self.assertEqual(code, 1)
        self.assertIn("next fix number 4", err)
        for number in (3, 4):
            args = self.apply_args("developer", number, "--retain-context")
            args[args.index("--task") + 1] = current
            code, _, err = self.invoke(args, self._client({"grok": "idle"}, sessions={"grok": "current-developer"}))
            self.assertEqual(code, 1)
            self.assertEqual(self.runner.writes(), [])
            self.assertEqual(self.state.read_bytes(), saved)
        self.assertEqual([row["fix_round"] for row in self.saved()["assignments"]
                          if row["task"] == current and row["role"] == "developer"], [None, 1, 2, 3])

    def test_historical_developer_before_earlier_appended_release_allows_fresh_handoff(self):
        self.seed()
        state = self.saved()
        add_assignment(state, "2026-02-03T09:30:00+00:00", "release", "grok", task=TASK,
                       cleared=True, clear_reason="automatic")
        save_state(self.state, state)
        code, _, err = self.owner("import-correction", self.attempt(), self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        preserved = copy.deepcopy(self.saved()["assignments"])
        code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client("release", "fix-2"))
        self.assertEqual(code, 0, err)
        transition = json.loads(out)["applied"][0]["context_transition"]
        self.assertEqual(transition, {"reason": "release_handoff", "previous_developer": 2, "release_assignment": 1})
        self.assertEqual(self.saved()["assignments"][:-1], preserved)

    def test_checkpoint_compares_judge_event_to_later_appended_developer(self):
        for occurred_at, expected in ((CLEARED, 0), ("2026-02-03T09:01:00+00:00", 1)):
            with self.subTest(occurred_at=occurred_at):
                self.seed(4)
                state = self.saved()
                add_assignment(state, COMPLETED, "judge", "claude", task=TASK)
                save_state(self.state, state)
                config = json.loads(self.config.read_text())
                config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
                self.config.write_text(json.dumps(config))
                code, _, err = self.owner("import-correction", {**self.attempt(5), "occurred_at": occurred_at})
                self.assertEqual(code, 0, err)
                preserved = copy.deepcopy(self.saved()["assignments"])
                judge = self.tmp / "judge-chronology.md"
                judge.write_text("RULING: amend — resolve F1\nACTION: Correct the remaining boundary case\n")
                code, _, err = self.owner("checkpoint", {"id": "chronological-checkpoint", "task": TASK,
                    "defect": "F1 remains blocking", "previous_attempts": "Five completed fixes",
                    "progress": "Other findings resolved", "change_in_approach": "Correct the boundary case",
                    "judge_report": str(judge)})
                self.assertEqual(code, expected, err)
                if expected:
                    self.assertIn("pinned judge after the latest developer", err)
                self.assertEqual(self.saved()["assignments"], preserved)

    def test_migrated_manual_fix_import_is_idempotent_and_next_fix_is_two(self):
        original = self.seed()
        data = self.attempt()
        code, out, err = self.owner("import-correction", data, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        record = json.loads(out)
        self.assertIsNone(record["native_session_proof"])
        self.assertFalse(record["grants_future_attempts"])
        self.assertEqual(record["vcs"]["changed_paths"], ["src/parser.py"])
        self.assertEqual(self.saved()["assignments"][:-1], original)
        self.assertIsNone(self.saved()["assignments"][-1]["context_session"])
        self.assertEqual(self.saved()["recovery"]["dispatches"], [])
        before = self.state.read_bytes()
        code, _, err = self.owner("import-correction", data, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.runner.calls, [])
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["tasks"][TASK]["confirmed_fixes"], 1)
        code, out, err = self.invoke(["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                                      "--task", TASK, "--fix-round", "2", "--now", AT])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["task_context"]["fix_round"], 2)
        code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client("legacy-session", "fresh-fix-2"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["context_transition"]["reason"], "historical_correction_handoff")
        self.assertEqual(self.saved()["assignments"][:len(original)], original)
        self.assertEqual(self.saved()["assignments"][-1]["fix_round"], 2)

    def test_duplicate_conflict_and_changed_bytes_preserve_the_first_import(self):
        self.seed()
        data = self.attempt()
        self.assertEqual(self.owner("import-correction", data)[0], 0)
        before = self.state.read_bytes()
        for changed in ({**data, "id": "duplicate"}, {**data, "scope": "different scope"}):
            code, _, _ = self.owner("import-correction", changed, self._client({}))
            self.assertEqual(code, 1)
            self.assertEqual(self.state.read_bytes(), before)
            self.assertEqual(self.runner.calls, [])
        self.report.write_text(self.report.read_text() + "Changed later\n")
        code, _, err = self.owner("import-correction", data)
        self.assertEqual(code, 1)
        self.assertIn("different input or evidence bytes", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_import_six_counts_six_and_grants_no_seventh_attempt(self):
        original = self.seed(5)
        data = self.attempt(6)
        code, _, err = self.owner("import-correction", data, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.saved()["assignments"][:-1], original)
        self.assertEqual(self.saved()["recovery"]["plans"], [])
        code, out, err = self.invoke(["status"])
        self.assertEqual(code, 0, err)
        status = json.loads(out)["tasks"][TASK]
        self.assertEqual((status["confirmed_fixes"], status["remaining_fixes"], status["status"]), (6, 0, "judge_checkpoint_required"))
        before = self.state.read_bytes()
        for args in (self.apply_args("developer", 7), ["plan", "--roles", "developer", "--snapshot", str(self.snapshot),
                    "--task", TASK, "--fix-round", "7", "--now", AT]):
            code, _, err = self.invoke(args, self._client({}))
            self.assertEqual(code, 1)
            self.assertIn("five-fix budget is exhausted", err)
            self.assertEqual(self.runner.calls, [])
            self.assertEqual(self.state.read_bytes(), before)

    def test_split_original_archives_are_read_without_reformatting_or_relabeling(self):
        self.seed()
        self.auth_file.write_text(AUTH["quote"] + "\n")
        proposal = self.tmp / "original-plan.md"
        proposal.write_text(TASK + "\n" + SCOPE + "\nOne additional correction.\n")
        self.transport_file.write_text("Worker grok received the correction.\nWorker grok started the correction.\n")
        clear = self.tmp / "original-clear.md"
        clear.write_text("Fresh conversation confirmed.\n")
        data = {**self.attempt(), "authorization_evidence": [str(proposal), str(self.auth_file)],
                "transport_evidence": [str(clear), str(self.transport_file)]}
        original_bytes = {path: path.read_bytes() for path in (proposal, self.auth_file, clear, self.transport_file)}
        code, _, err = self.owner("import-correction", data, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual({path: path.read_bytes() for path in original_bytes}, original_bytes)
        before = self.state.read_bytes()
        relabeled = {**data, "id": "reused-as-2", "fix_round": 2, "authorized_first_fix": 2,
                     "authorized_last_fix": 2, "previous_head": self.head_revision}
        code, _, err = self.owner("import-correction", relabeled)
        self.assertEqual(code, 1)
        self.assertIn("do not relabel it", err)
        self.assertEqual(self.state.read_bytes(), before)
        report_copy = self.tmp / "renamed-report.md"
        report_copy.write_bytes(self.report.read_bytes())
        transport_copy = self.tmp / "renamed-transport.md"
        transport_copy.write_bytes(self.transport_file.read_bytes())
        clear_copy = self.tmp / "renamed-clear.md"
        clear_copy.write_bytes(clear.read_bytes())
        relabeled.update(report=str(report_copy), transport_evidence=[str(transport_copy), str(clear_copy)])
        code, _, err = self.owner("import-correction", relabeled)
        self.assertEqual(code, 1)
        self.assertIn("do not relabel it", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_unproven_wrong_count_scope_base_or_budget_imports_do_not_write(self):
        self.seed()
        data = self.attempt()
        before = self.state.read_bytes()
        cases = [{**data, "fix_round": True}, {**data, "fix_round": 2},
                 {**data, "authorized_last_fix": 0}, {**data, "allowed_paths": ["docs/*"]},
                 {**data, "base_revision": "c" * 40}, {**data, "head_revision": None},
                 {**data, "transport": {"sent": True, "started": False, "clear": "fresh"}},
                 {**data, "authorization_evidence": str(self.tmp / "absent")},
                 {**data, "occurred_at": "2026-02-02T10:00:00+00:00"}]
        for changed in cases:
            with self.subTest(changed=changed):
                code, _, err = self.owner("import-correction", changed, self._client({}))
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.calls, [])
        self.transport_file.write_text("The worker probably completed\n")
        code, _, err = self.owner("import-correction", data)
        self.assertEqual(code, 1)
        self.assertIn("quote the original archived evidence", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_git_tool_failure_is_actionable_and_does_not_import(self):
        self.seed()
        before = self.state.read_bytes()
        with patch("teamlead.historical.subprocess.run", side_effect=FileNotFoundError("git unavailable")):
            code, _, err = self.owner("import-correction", self.attempt())
        self.assertEqual(code, 1)
        self.assertIn("Restore Git", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_actual_git_scope_and_ancestry_failures_do_not_import(self):
        self.seed()
        before = self.state.read_bytes()
        data = self.attempt()
        reverse = {**data, "previous_head": self.head_revision, "head_revision": self.base_revision}
        self.report.write_text("Reported head " + self.base_revision + "\n")
        code, _, err = self.owner("import-correction", reverse)
        self.assertEqual(code, 1)
        self.assertIn("Historical Git verification failed", err)
        self.assertEqual(self.state.read_bytes(), before)
        (self.checkout / "outside.txt").write_text("Out-of-scope result\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Out-of-scope fixture")
        outside = self.git("rev-parse", "HEAD").strip()
        self.report.write_text("Reported head " + outside + "\n")
        code, _, err = self.owner("import-correction", {**data, "head_revision": outside})
        self.assertEqual(code, 1)
        self.assertIn("exceeds the recorded task or correction scope", err)
        self.assertEqual(self.state.read_bytes(), before)

    def test_import_with_remaining_existing_plan_still_requires_actual_blocking_review(self):
        self.seed(5)
        state = self.saved()
        add_assignment(state, COMPLETED, "judge", "claude", task=TASK)
        save_state(self.state, state)
        config = json.loads(self.config.read_text())
        config["judge"] = {"agent": "claude", "model": "claude-opus-4-6", "effort": "high"}
        self.config.write_text(json.dumps(config))
        judge = self.tmp / "judge.md"
        judge.write_text("RULING: amend — correct F1\nACTION: Fix the remaining parser case\n")
        code, _, err = self.owner("checkpoint", {"id": "cap-5", "task": TASK, "defect": "Blocking F1",
            "previous_attempts": "Five completed fixes", "progress": "Most fixtures pass",
            "change_in_approach": "Correct the remaining case", "judge_report": str(judge)})
        self.assertEqual(code, 0, err)
        code, _, err = self.owner("authorize-corrections", {"id": "two-fixes", "task": TASK, "checkpoint": "cap-5",
            "scope": SCOPE, "allowed_paths": ["src/*"], "additional_fixes": 2, "authorization": AUTH})
        self.assertEqual(code, 0, err)
        code, _, err = self.owner("import-correction", self.attempt(6))
        self.assertEqual(code, 0, err)
        work = self.tmp / "work.json"
        work.write_text(json.dumps({"base_revision": self.base_revision, "scope": SCOPE,
                                   "paths": ["src/parser.py"], "findings": ["F1"]}))
        args = self.apply_args("developer", 7, "--correction-plan", "two-fixes", "--work", str(work))
        code, _, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("record-historical-review", err)
        self.assertEqual(self.runner.calls, [])
        review_file = self.tmp / "review.md"
        review_file.write_text("Reviewed " + self.head_revision + "\nBlocking F1: boundary case still fails.\n")
        review = {"id": "review-6", "historical_attempt": "manual-6", "head_revision": self.head_revision,
                  "verdict": "blocking", "review_mode": "full", "reviewer": "codex", "report": str(review_file)}
        before = self.state.read_bytes()
        for changed in ({**review, "reviewer": "grok"}, {**review, "verdict": "approved", "review_mode": "scoped"}):
            code, _, _ = self.owner("record-historical-review", changed)
            self.assertEqual(code, 1)
            self.assertEqual(self.state.read_bytes(), before)
        code, _, err = self.owner("record-historical-review", review, self._client({}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.calls, [])
        saved = self.state.read_bytes()
        self.assertEqual(self.owner("record-historical-review", review)[0], 0)
        self.assertEqual(self.state.read_bytes(), saved)
        review_file.write_text(review_file.read_text() + "Changed bytes\n")
        code, _, err = self.invoke(args, self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("review artifact changed", err)
        self.assertEqual(self.runner.calls, [])
        code, _, err = self.owner("record-historical-review", {**review, "id": "review-6-current"})
        self.assertEqual(code, 0, err)
        code, _, err = self.invoke(args, self.fresh_client("manual-6-session", "fix-7"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.saved()["assignments"][-1]["fix_round"], 7)
        self.assertEqual(len(self.saved()["recovery"]["plans"]), 1)

    def test_verified_hand_clear_unlocks_correctly_counted_fresh_fix_without_permission(self):
        original = self.seed(1, release=True)
        data = self.clearance()
        code, _, err = self.invoke(self.apply_args("developer", 2), self._client({}))
        self.assertEqual(code, 1)
        self.assertIn("Early developer fix", err)
        self.assertEqual(self.runner.calls, [])
        code, out, err = self.owner("record-release-clear", data,
            self._client({"grok": "idle"}, sessions={"grok": "release-session"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.runner.writes(), [])
        self.assertEqual(json.loads(out)["observed_session"]["value"], "release-session")
        self.assertEqual(self.saved()["assignments"], original)
        self.assertEqual(self.saved()["recovery"]["context_permissions"], [])
        before = self.state.read_bytes()
        code, _, err = self.owner("record-release-clear", data,
            self._client({"grok": "idle"}, sessions={"grok": "release-session"}))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.state.read_bytes(), before)
        code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client("release-session", "fix-2"))
        self.assertEqual(code, 0, err)
        applied = json.loads(out)["applied"][0]
        self.assertEqual(applied["context_transition"]["reason"], "verified_hand_release_handoff")
        self.assertEqual((applied["fix_round"], applied["context_session"]["value"]), (2, "fix-2"))
        self.assertEqual(self.saved()["assignments"][:-1], original)

    def test_unverified_clear_wrong_session_and_busy_worker_leave_history_intact(self):
        self.seed(1, release=True)
        data = self.clearance()
        before = self.state.read_bytes()
        cases = [({**data, "verified_empty_composer": False}, "idle", "release-session"),
                 ({**data, "composer_quote": "Invented empty composer proof"}, "idle", "release-session"),
                 ({**data, "verified_fresh_conversation": False}, "idle", "release-session"),
                 ({**data, "fresh_session": {"kind": "id", "value": "old-developer"}}, "idle", "old-developer"),
                 (data, "working", "release-session"),
                 ({**data, "assignment_index": 1}, "idle", "release-session")]
        for changed, status, session in cases:
            with self.subTest(changed=changed, status=status):
                code, _, _ = self.owner("record-release-clear", changed, self._client({"grok": status}, sessions={"grok": session}))
                self.assertEqual(code, 1)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), before)

    def test_archived_release_clear_survives_a_later_changed_or_missing_session(self):
        for session in ("later-worker-session", None):
            with self.subTest(session=session):
                original = self.seed(1, release=True)
                code, out, err = self.owner("record-release-clear", self.clearance(),
                    self._client({"grok": "idle"}, sessions={"grok": session}))
                self.assertEqual(code, 0, err)
                observation = json.loads(out)["observed_session"]
                self.assertEqual(observation["value"] if observation else None, session)
                self.assertEqual(self.saved()["assignments"], original)
                self.assertEqual(self.runner.writes(), [])
                code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client(session, "fix-after-session-loss"))
                self.assertEqual(code, 0, err)
                result = json.loads(out)["applied"][0]
                self.assertEqual(result["context_transition"]["reason"], "verified_hand_release_handoff")
                self.assertEqual(result["fix_round"], 2)
                if session is None:
                    # Grok can report a stale ID after /new when its pre-clear
                    # identity was missing. The handoff still counts, not proof.
                    self.assertIsNone(result["context_session"])
                    self.assertIn("stale-ID recovery is unavailable", err)
                else:
                    self.assertEqual(result["context_session"]["value"], "fix-after-session-loss")
                self.assertEqual(self.saved()["assignments"][:-1], original)

    def test_corrupt_or_future_import_records_never_overwrite_history(self):
        self.seed()
        self.assertEqual(self.owner("import-correction", self.attempt())[0], 0)
        original = self.saved()
        malformed = []
        for key, value in (("schema_version", 2), ("receipts", []), ("vcs", []), ("native_session_proof", {"value": "invented"})):
            state = copy.deepcopy(original)
            state["recovery"]["historical_attempts"][0][key] = value
            malformed.append(state)
        state = copy.deepcopy(original)
        state["recovery"]["historical_attempts"][0]["input"]["allowed_paths"] = ["docs/*"]
        malformed.append(state)
        for state in malformed:
            with self.subTest(state=state):
                self.state.write_text(json.dumps(state))
                before = self.state.read_bytes()
                code, _, err = self.owner("import-correction", self.attempt(), self._client({}))
                self.assertEqual(code, 1, err)
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual(self.runner.calls, [])

    def test_verified_clear_without_an_archived_native_id_preserves_missing_proof(self):
        original = self.seed(1, release=True)
        self.clear_file.write_text("Verified fresh conversation and empty composer before release.\n")
        data = {**self.clearance(), "fresh_session": None, "fresh_quote": "Verified fresh conversation"}
        code, out, err = self.owner("record-release-clear", data,
            self._client({"grok": "idle"}, sessions={"grok": "observed-only-later"}))
        self.assertEqual(code, 0, err)
        record = json.loads(out)
        self.assertIsNone(record["input"]["fresh_session"])
        self.assertEqual(record["observed_session"]["value"], "observed-only-later")
        self.assertEqual(self.saved()["assignments"], original)
        code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client("observed-only-later", "fresh-fix-2"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["context_session"]["value"], "fresh-fix-2")
        self.assertIsNone(self.saved()["recovery"]["hand_clearances"][0]["input"]["fresh_session"])

    def test_clear_timeout_then_release_no_clear_and_external_fix_complete_the_workflow(self):
        self.seed(1)
        self.briefs["release"] = self.tmp / "release.md"
        self.briefs["release"].write_text("Release the independently verified tip; stop on source findings.\n")
        client = self._client({"grok": "idle"}, sessions={"grok": "old-developer"})
        self.runner.raises["agent wait grok"] = subprocess.TimeoutExpired("herdr agent wait", 1)
        code, _, err = self.invoke(self.apply_args("release", None, "--now", CLEARED), client)
        self.assertEqual(code, 1)
        self.assertIn("timeout", err.lower())
        self.assertFalse(any(command.startswith("agent prompt ") for command in self.runner.commands()))
        self.assertEqual(self.saved()["assignments"][-1]["role"], "developer")
        self.assertEqual(self.saved()["recovery"]["dispatches"][-1]["status"], "not_sent")
        code, _, err = self.invoke(self.apply_args("release", None, "--no-clear", "--now", COMPLETED),
            self._client({"grok": "idle"}, sessions={"grok": "release-session"}))
        self.assertEqual(code, 0, err)
        original = copy.deepcopy(self.saved()["assignments"])
        self.assertEqual((original[-1]["cleared"], original[-1]["clear_reason"]), (False, "hand"))
        code, _, err = self.owner("record-release-clear", self.clearance(),
            self._client({"grok": "idle"}, sessions={"grok": "release-session"}))
        self.assertEqual(code, 0, err)
        self.briefs["developer"].write_text("Fix external blocking finding F2, preserving previous attempt 1 and full verification.\n")
        code, out, err = self.invoke(self.apply_args("developer", 2), self.fresh_client("release-session", "fix-2"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["applied"][0]["context_transition"]["reason"], "verified_hand_release_handoff")
        self.assertEqual(self.saved()["assignments"][:-1], original)

    def test_recovery_v1_migration_preserves_every_existing_nested_record(self):
        self.seed()
        original = self.saved()
        original["recovery"]["schema_version"] = 1
        del original["recovery"]["hand_clearances"]
        del original["recovery"]["historical_attempts"]
        del original["recovery"]["role_clearances"]
        del original["recovery"]["delivery_recoveries"]
        self.state.write_text(json.dumps(original))
        code, _, err = self.invoke(["state"])
        self.assertEqual(code, 0, err)
        result = self.saved()
        self.assertEqual(result["assignments"], original["assignments"])
        expected = {**original["recovery"], "schema_version": 4, "hand_clearances": [], "historical_attempts": [], "role_clearances": [], "delivery_recoveries": []}
        self.assertEqual(result["recovery"], expected)


if __name__ == "__main__":
    unittest.main()
