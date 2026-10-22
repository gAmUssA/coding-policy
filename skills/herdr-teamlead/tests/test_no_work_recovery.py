"""A closed no-work initial developer assignment, retried through the real CLI.

The stall evidence comes from the real `wait-report.sh --worktree` run against a
real git checkout; `recover-no-work`, `plan` and `supervision-resolve` run
through `teamlead.sh`. Only Herdr's process transport is fake: a shell stub for
the launcher and wait-report, the shared FakeRunner for in-process apply.
"""

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

from teamlead import recovery, retrospective as notes, retrospective_runtime as runtime, supervision
from teamlead.cli import main
from teamlead.config import load_config
from teamlead.state import empty_state, load_state, save_state
from tests import test_cli as fixture
from tests.fakes import FakeCompleted, FakeRunner

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "teamlead.sh"
WAIT_REPORT = ROOT / "wait-report.sh"
TASK = "no-work-retry"
AT = fixture.AT
#: Fixed instants after `AT`: the initial apply, its closure, the recovery, the retry.
FIRST_AT = "2026-02-03T10:01:00+00:00"
CLOSED_AT = "2026-02-03T10:05:00+00:00"
RECOVERED_AT = "2026-02-03T10:06:00+00:00"
RETRY_AT = "2026-02-03T10:07:00+00:00"
PANE_GONE = json.dumps({"error": {"code": "pane_not_found", "message": "pane w3:p1 not found"}})

#: A Herdr stand-in for wait-report and the launcher. `agent get` reads idle on
#: the original pane, the marker never matches, the pane shows no refusal, and
#: `pane get` answers not-found unless FAKE_PANE_LIVE is set.
FAKE_HERDR = r"""#!/usr/bin/env bash
set -euo pipefail
case "${1:-} ${2:-}" in
  "agent get")
    printf '{"result":{"type":"agent_info","agent":{"agent":"codex","agent_status":"idle","pane_id":"w3:p1","name":"%s"}}}\n' "$3"
    ;;
  "pane wait-output")
    printf '{"error":{"code":"timeout","message":"timed out waiting for output match"}}\n' >&2
    exit 1
    ;;
  "pane read")
    printf 'nothing here\n'
    ;;
  "pane get")
    if [[ -n "${FAKE_PANE_LIVE:-}" ]]; then
      printf '{"result":{"pane":{"pane_id":"%s","agent_status":"idle"}}}\n' "$3"
    else
      printf '{"error":{"code":"pane_not_found","message":"pane %s not found"}}\n' "$3" >&2
      exit 1
    fi
    ;;
  *)
    printf '{"error":{"code":"unsupported","message":"fake herdr: %s"}}\n' "$*" >&2
    exit 2
    ;;
esac
"""


def seed_task(state_path, base):
    """A registered task with no assignments yet."""
    state = empty_state()
    recovery.register_task(state["recovery"], {"task": TASK, "base_revision": base,
        "scope": "Repair parser", "allowed_paths": ["src/*"],
        "authorization": {"source": "fixture operator", "quote": "Repair the parser."}}, AT)
    save_state(state_path, state)


class NoWorkRecoveryTest(unittest.TestCase):
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
        self.herdr = self.root / "herdr"
        self.herdr.write_text(FAKE_HERDR)
        self.herdr.chmod(0o755)
        self.checkout = self.root / "checkout"
        (self.checkout / "src").mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.name", "No-work Fixture")
        self.git("config", "user.email", "no-work@example.invalid")
        (self.checkout / "src/parser.py").write_text("result = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial fixture")
        self.base = self.git("rev-parse", "HEAD")
        seed_task(self.state, self.base)
        self.first_report = str(self.root / "first.md")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.checkout), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def invoke(self, args, client=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--config", str(self.config), "--state", str(self.state), *args],
                    stdout=out, stderr=err, client=client)
        return code, out.getvalue(), err.getvalue()

    def args(self, agent, report, identifier, at, *extra, body):
        self.brief.write_text(body)
        return ["apply", "--assignments", json.dumps({"developer": agent}), "--task", TASK,
                "--common", str(self.common), "--brief", "developer=" + str(self.brief),
                "--report", "developer=" + report, "--dispatch-id", identifier, "--composer-settle", "0",
                "--now", at, *extra]

    def prepare_retrospective(self, agent, args, client, identifier):
        """Record the retrospective coverage the real Guard requires for `args`."""
        at = args[args.index("--now") + 1]
        request = {"transitions": [{"agent": agent, "role": "developer", "task": TASK,
            "context": "clear", "model": None, "effort": None,
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

    def body(self, report):
        return "Work in {0}.\nRepair the parser and report to `{1}`.\nREPORT: {1}\n".format(self.checkout, report)

    def launch(self, *argv, live=False):
        env = {**os.environ, "PY_BIN": sys.executable, "TEAMLEAD_HERDR_BIN": str(self.herdr)}
        if live:
            env["FAKE_PANE_LIVE"] = "1"
        return subprocess.run(["bash", str(LAUNCHER), "--config", str(self.config), "--state", str(self.state), *argv],
                              env=env, capture_output=True, text=True, check=False)

    def plan(self, fix=None):
        return self.launch("plan", "--roles", "developer", "--task", TASK, "--snapshot", str(self.snapshot),
                           "--now", RECOVERED_AT, *(["--fix-round", str(fix)] if fix is not None else []))

    def dispatch_initial(self):
        args = self.args("codex", self.first_report, "first", FIRST_AT, body=self.body(self.first_report))
        client = self._client({"codex": "idle"}, sessions={"codex": "codex-native"})
        self.prepare_retrospective("codex", args, client, "before-first")
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        return json.loads(out)["applied"][0]["dispatch_id"]

    def stall(self):
        """The saved output of the real wait-report stall classification."""
        result = subprocess.run(
            ["bash", str(WAIT_REPORT), "--worktree", str(self.checkout), "--base", self.base, "codex", self.first_report],
            env={**os.environ, "HERDR_ENV": "1", "HERDR_BIN": str(self.herdr), "TEAMLEAD_WAIT_INTERVAL_SEC": "0",
                 "TEAMLEAD_WAIT_BUDGET_SEC": "0", "TEAMLEAD_BLOCKED_CONFIRM_SEC": "0", "TEAMLEAD_REFUSAL_CONFIRM_SEC": "0"},
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["stall"]["class"], "no_work", result.stdout)
        path = self.root / "wait.json"
        path.write_text(result.stdout)
        return path

    def close(self, dispatch, wait):
        record = self.root / "resolve.json"
        record.write_text(json.dumps({"id": dispatch, "outcome": "Stalled with no work; pane closed",
                                      "evidence": [str(wait)]}))
        result = self.launch("supervision-resolve", "--record", str(record), "--now", CLOSED_AT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def record(self, dispatch, wait, **changes):
        data = {"dispatch": dispatch, "reason": "Initial developer stalled with no work and its pane closed",
                "authorization": {"source": "fixture operator", "quote": "Retry the initial assignment."},
                "evidence": str(wait), **changes}
        path = self.root / "recover-no-work.json"
        path.write_text(json.dumps(data))
        return path

    def recover(self, record, live=False):
        return self.launch("recover-no-work", "--record", str(record), "--now", RECOVERED_AT, live=live)

    def stalled_and_closed(self):
        dispatch = self.dispatch_initial()
        wait = self.stall()
        self.close(dispatch, wait)
        return dispatch, wait

    def retry_client(self, statuses):
        client = self._client(statuses)
        self.runner.responses["pane get w3:p1"] = FakeCompleted(1, "", PANE_GONE)
        return client

    def retry_args(self, agent, report, identifier, *extra, body=None):
        return self.args(agent, report, identifier, RETRY_AT, *extra, body=body or self.body(report))

    def test_without_recovery_the_initial_assignment_cannot_be_retried(self):
        self.stalled_and_closed()
        result = self.plan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("already has a developer assignment", result.stderr)

    def test_closed_no_work_initial_assignment_is_retried_once(self):
        dispatch, wait = self.stalled_and_closed()
        result = self.recover(self.record(dispatch, wait))
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual((receipt["id"], receipt["task"], receipt["retries"]), (dispatch, TASK, []))
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)

        retry_report = str(self.root / "retry.md")
        args = self.retry_args("claude", retry_report, "retry")
        client = self.retry_client({"claude": "idle"})
        self.prepare_retrospective("claude", args, client, "before-retry")
        code, out, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        applied = json.loads(out)["applied"][0]
        self.assertEqual((applied["agent"], applied["fix_round"]), ("claude", None))
        state = load_state(self.state)
        self.assertEqual([row["status"] for row in state["recovery"]["dispatches"]], ["applied", "applied"])
        self.assertEqual(state["recovery"]["no_work_recoveries"][0]["retries"], [applied["dispatch_id"]])

        # One recovery grants one retry: the initial slot is spent again, and
        # the correction count is untouched.
        result = self.plan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("already has a developer assignment", result.stderr)
        result = self.plan(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.launch("status")
        self.assertEqual(json.loads(result.stdout)["tasks"][TASK]["confirmed_fixes"], 0)

        # An identical replay returns the durable receipt and grants nothing,
        # even though the retry now owns the checkout.
        (self.checkout / "src/parser.py").write_text("result = 2\n")
        before = self.state.read_bytes()
        result = self.recover(self.record(dispatch, wait))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["retries"], [applied["dispatch_id"]])
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.plan().returncode, 1)
        # A different record under the same identity is refused.
        result = self.recover(self.record(dispatch, wait, reason="Another reason"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("already holds different", result.stderr)

    def test_the_retry_keeps_the_brief_and_takes_a_fresh_report(self):
        dispatch, wait = self.stalled_and_closed()
        self.assertEqual(self.recover(self.record(dispatch, wait)).returncode, 0)
        retry_report = str(self.root / "retry.md")
        for label, report, body, extra, message in (
                ("the original report path", self.first_report, None, (), "fresh report path"),
                ("a reworded brief", retry_report, "Rewrite the parser in {}.\nREPORT: {}\n".format(self.checkout, retry_report), (),
                 "unchanged brief"),
                ("a kept context", retry_report, None, ("--no-clear",), "fresh developer context")):
            with self.subTest(refused=label):
                before = self.state.read_bytes()
                client = self.retry_client({"claude": "idle"})
                code, out, err = self.invoke(self.retry_args("claude", report, "invalid", "--dry-run", *extra, body=body), client)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn(message, err)
                self.assertEqual(self.runner.writes(), [])
                self.assertEqual(self.state.read_bytes(), before)

    def test_work_in_the_checkout_or_a_live_pane_refuses_recovery(self):
        dispatch, wait = self.stalled_and_closed()
        record = self.record(dispatch, wait)
        before = self.state.read_bytes()
        for label, change, live, message in (
                ("a live pane", None, True, "still exists"),
                ("an untracked file", lambda: (self.checkout / "src/new.py").write_text("x = 1\n"), False, "changed or untracked"),
                ("a modified file", lambda: (self.checkout / "src/parser.py").write_text("result = 3\n"), False, "changed or untracked"),
                ("a staged file", lambda: self.git("add", "src/parser.py"), False, "changed or untracked"),
                ("a dispatch commit", lambda: (self.git("add", "-A"), self.git("commit", "-qm", "Worker commit")), False,
                 "beyond the recorded base")):
            with self.subTest(refused=label):
                if change is not None:
                    change()
                result = self.recover(record, live=live)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout, "")
                self.assertIn(message, result.stderr)
                self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.plan().returncode, 1)

    def test_an_open_enrollment_or_other_stall_class_refuses_recovery(self):
        dispatch = self.dispatch_initial()
        wait = self.stall()
        before = self.state.read_bytes()
        result = self.recover(self.record(dispatch, wait))
        self.assertEqual(result.returncode, 1)
        self.assertIn("supervision-resolve", result.stderr)
        self.close(dispatch, wait)
        partial = json.loads(wait.read_text())
        partial["stall"]["class"] = "partial_work"
        forged = self.root / "partial.json"
        forged.write_text(json.dumps(partial))
        result = self.recover(self.record(dispatch, forged))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no_work", result.stderr)
        self.assertEqual(self.state.read_bytes(), before)

    def test_an_unbound_second_initial_row_is_refused_on_read(self):
        dispatch, wait = self.stalled_and_closed()
        self.assertEqual(self.recover(self.record(dispatch, wait)).returncode, 0)
        args = self.retry_args("claude", str(self.root / "retry.md"), "retry")
        client = self.retry_client({"claude": "idle"})
        self.prepare_retrospective("claude", args, client, "before-retry")
        code, _, err = self.invoke(args, client)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.launch("status").returncode, 0)
        document = json.loads(self.state.read_text())
        document["recovery"]["no_work_recoveries"][0]["retries"] = []
        self.state.write_text(json.dumps(document))
        forged = self.state.read_bytes()
        result = self.launch("status")
        self.assertIn("consumed twice", result.stderr)
        self.assertNotIn(TASK, result.stdout)
        self.assertEqual(self.state.read_bytes(), forged)


class NoWorkStoreMigrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "state.json"
        seed_task(self.state, "a" * 40)
        document = json.loads(self.state.read_text())
        document["recovery"]["schema_version"] = 9
        del document["recovery"]["no_work_recoveries"]
        self.document = document

    def status(self):
        return subprocess.run(["bash", str(LAUNCHER), "--state", str(self.state), "status"],
                              env={**os.environ, "PY_BIN": sys.executable}, capture_output=True, text=True, check=False)

    def test_a_version_nine_store_gains_the_empty_collection(self):
        self.state.write_text(json.dumps(self.document))
        result = self.status()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(TASK, json.loads(result.stdout)["tasks"])
        migrated = json.loads(self.state.read_text())["recovery"]
        self.assertEqual((migrated.pop("schema_version"), migrated.pop("no_work_recoveries")), (10, []))
        self.assertEqual(migrated, {key: value for key, value in self.document["recovery"].items() if key != "schema_version"})

    def test_a_version_nine_store_carrying_receipts_is_refused_untouched(self):
        self.document["recovery"]["no_work_recoveries"] = []
        self.state.write_text(json.dumps(self.document))
        before = self.state.read_bytes()
        result = self.status()
        self.assertIn("unowned newer records", result.stderr)
        self.assertNotIn(TASK, result.stdout)
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
