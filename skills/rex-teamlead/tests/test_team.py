"""Tests for rexlead: the Rex client and the team round commands.

The `rex` executable is faked at the process boundary (`FakeRex`), and its
event stream is a real OS pipe so `Rex.wait_status` runs its own `select`
loop. `git` runs for real in temporary repositories. Program status records
copy the shape Rex 0.1.0 (1853) emits: `app`, `effective_app`, `owner`,
`state`, `updated_at`, and `kind` on `blocked`. No test starts Rex or an agent.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import datetime as dt
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from rexlead import team as teammod
from rexlead.rex import Rex, RexError, Timeout

SESSION = "session:01a1b968-0889-721b-bfeb-f94b8a6239b3"
LEAD = "block:01a1b968-0000-7000-8000-00000000000a"
SENT = dt.datetime(2026, 11, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
AFTER = "2026-11-07T07:00:05-05:00"   # 12:00:05Z, after the dispatch
BEFORE = "2026-11-07T06:59:00-05:00"  # 11:59:00Z, before it


def status(state, at=AFTER, **extra):
    return dict({"app": "claude-code", "effective_app": "claude-code", "owner": {"name": "2.1.319", "pid": 301},
                 "state": state, "updated_at": at}, **extra)


def event(block, record):
    return json.dumps({"type": "session_event", "payload": {"session_id": SESSION, "event": {
        "type": "block_event", "payload": {"block_id": block, "creator_name": "com.superlogical.terminal",
                                           "name": "program_status_changed",
                                           "payload": {"id": "", "record": record, "reason": "report"}}}}}) + "\n"


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class Stream:
    """A real pipe: queued lines are readable at once; with `close=False`
    the write end stays open, so a wait blocks until its deadline."""

    def __init__(self, lines, close):
        read, write = os.pipe()
        self.stdout = os.fdopen(read, "rb")
        self._write = os.fdopen(write, "w")
        self._write.write("".join(lines))
        self._write.flush()
        if close:
            self._write.close()

    def terminate(self):
        if not self._write.closed:
            self._write.close()
        self.stdout.close()


class FakeRex:
    """The `rex` CLI: sessions, blocks with a current status, and recorded writes."""

    def __init__(self, statuses=None, events=(), close_stream=False, live=(SESSION,)):
        self.statuses = dict(statuses or {})
        self.queued = list(events)
        self.close_stream = close_stream
        self.live = list(live)
        self.launched, self.writes, self.closed = [], [], []
        self.next_block = 1

    def runner(self, argv):
        verb = argv[1]
        if verb == "ls":
            return Done(0, json.dumps({"sessions": [{"session_id": s, "label": "team"} for s in self.live]}))
        if verb == "new":
            return Done(0, json.dumps({"session_id": SESSION, "initial_windows": [{"block_ids": [LEAD]}]}))
        if verb == "run":
            block = "block:01a1b969-0000-7000-8000-{:012d}".format(self.next_block)
            self.next_block += 1
            self.launched.append({"cwd": argv[argv.index("-c") + 1], "label": argv[argv.index("--label") + 1],
                                  "argv": argv[argv.index("--") + 1:], "block": block})
            self.statuses.setdefault(block, status("idle", at=BEFORE))
            return Done(0, json.dumps({"window_id": "window:x", "block_ids": [block], "revision": 2}))
        if verb == "do":
            block = argv[5].split("([[", 1)[1].split("]]", 1)[0].strip('"')
            record = self.statuses.get(block)
            return Done(0, json.dumps({"records": [record] if record else {}}))
        if verb == "api":
            payload = json.loads(argv[4])
            self.writes.append(("paste", payload["block_id"], payload["event"]["payload"]["text"]))
            return Done(0, "{}")
        if verb == "send-key":
            self.writes.append(("keys", argv[5], argv[6:]))
            return Done(0, "")
        if verb == "block":
            self.closed.append(argv[-1])
            return Done(0, "")
        raise AssertionError("unexpected rex call: {}".format(argv))

    def events(self, argv):
        lines, self.queued = self.queued, []
        return Stream(lines, self.close_stream)

    def client(self):
        return Rex(binary="rex", runner=self.runner, events=self.events)


def git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, text=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        (self.repo / "lib.py").write_text("x = 1\n")
        git("add", "-A", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=self.repo)
        self.env = {"REX_TEAM_HOME": str(root / "state"), "REX_TEAM_WORKTREES": str(root / "wt")}
        self.brief = root / "brief.md"
        self.brief.write_text("# Brief\n\nAdd a function.\n")

    def cli(self, fake: "FakeRex", *argv: str) -> "tuple[int, dict[str, Any], dict[str, Any]]":
        """Exit code, parsed stdout and parsed stderr; an empty stream parses as {}."""
        out, err = io.StringIO(), io.StringIO()
        code = teammod.main(list(argv), rex=fake.client(), stdout=out, stderr=err, env=self.env, now=lambda: SENT)
        return code, (json.loads(out.getvalue()) if out.getvalue() else {}), (json.loads(err.getvalue()) if err.getvalue() else {})

    def state(self):
        return json.loads((Path(self.env["REX_TEAM_HOME"]) / "t1.json").read_text())

    def spawned(self, fake, role="dev", *extra):
        self.assertEqual(self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))[0], 0)
        code, out, err = self.cli(fake, "spawn", "--task", "t1", "--role", role, "--agent", "claude", "--model", "haiku", *extra)
        self.assertEqual(code, 0, err)
        return out


class UpTest(Fixture):
    def test_up_opens_one_session_and_records_the_base(self):
        code, out, _ = self.cli(FakeRex(), "up", "--task", "t1", "--repo", str(self.repo))
        self.assertEqual((code, out["session"], out["reused"]), (0, SESSION, False))
        record = self.state()
        self.assertEqual((record["schema_version"], record["base"]), (1, out["base"]))

    def test_up_reuses_a_live_session(self):
        self.cli(FakeRex(), "up", "--task", "t1", "--repo", str(self.repo))
        self.assertTrue(self.cli(FakeRex(), "up", "--task", "t1", "--repo", str(self.repo))[1]["reused"])

    def test_up_outside_a_git_repo_fails(self):
        code, _, err = self.cli(FakeRex(), "up", "--task", "t1", "--repo", self.tmp.name)
        self.assertEqual((code, err["error"]["code"]), (1, "git_failed"))


class SpawnTest(Fixture):
    def test_writer_gets_its_own_branch_worktree_and_a_marked_agent(self):
        fake = FakeRex()
        out = self.spawned(fake)
        self.assertEqual((out["branch"], out["state"]), ("team/t1-dev", "idle"))
        self.assertTrue(Path(out["worktree"], "lib.py").is_file())
        launch = fake.launched[0]
        self.assertEqual(launch["argv"], ["env", "REX_TEAM_TASK=t1", "REX_TEAM_ROLE=dev", "claude", "--model", "haiku"])
        self.assertEqual((launch["label"], launch["cwd"]), ("t1-dev", out["worktree"]))

    def test_reader_gets_a_detached_checkout_at_the_candidate(self):
        fake = FakeRex()
        self.spawned(fake)
        candidate = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        code, out, err = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--ref", candidate, "--read-only")
        self.assertEqual(code, 0, err)
        self.assertEqual((out["branch"], out["commit"]), (None, candidate))
        self.assertEqual(fake.launched[1]["argv"][3:], ["codex"])

    def test_agent_that_never_reports_ready_is_not_ready(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        original = fake.runner

        def no_status(argv):
            done = original(argv)
            if argv[1] == "run":
                fake.statuses.pop(json.loads(done.stdout)["block_ids"][0])
            return done

        fake.runner = no_status
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude", "--timeout", "0")
        self.assertEqual((code, err["error"]["code"]), (1, "not_ready"))
        self.assertIn("startup dialog", err["error"]["message"])

    def test_role_already_held_is_refused(self):
        fake = FakeRex()
        self.spawned(fake)
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, err["error"]["code"]), (1, "role_taken"))

    def test_unknown_agent_and_bad_role_are_usage_errors(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        self.assertEqual(self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "vim")[0], 2)
        self.assertEqual(self.cli(fake, "spawn", "--task", "t1", "--role", "Dev", "--agent", "claude")[0], 2)


class AssignTest(Fixture):
    def test_pastes_the_brief_with_its_delivery_section_then_enter(self):
        fake = FakeRex()
        block = self.spawned(fake)["block"]
        code, out, _ = self.cli(fake, "assign", "--task", "t1", "--role", "dev", "--brief", str(self.brief))
        self.assertEqual(code, 0)
        kind, target, text = fake.writes[0]
        self.assertEqual((kind, target), ("paste", block))
        self.assertTrue(text.startswith("# Brief\n\nAdd a function.\n"))
        self.assertIn(out["report"], text)
        self.assertIn("task-nonce: " + out["nonce"], text)
        self.assertEqual(fake.writes[1], ("keys", block, ["enter"]))
        self.assertEqual(self.state()["workers"]["dev"]["dispatches"][0]["sent_at"], SENT.isoformat())

    def test_busy_worker_is_refused_before_any_write(self):
        fake = FakeRex()
        block = self.spawned(fake)["block"]
        fake.statuses[block] = status("working")
        code, _, err = self.cli(fake, "assign", "--task", "t1", "--role", "dev", "--brief", str(self.brief))
        self.assertEqual((code, err["error"]["code"], fake.writes), (1, "worker_busy", []))


class WaitTest(Fixture):
    def dispatched(self, fake):
        block = self.spawned(fake)["block"]
        out = self.cli(fake, "assign", "--task", "t1", "--role", "dev", "--brief", str(self.brief))[1]
        fake.statuses[block] = status("working")
        return block, out

    def test_done_with_the_nonce_in_the_report_is_delivered(self):
        fake = FakeRex()
        block, sent = self.dispatched(fake)
        Path(sent["report"]).write_text("task-nonce: {}\n\n# Report\n".format(sent["nonce"]))
        fake.queued = [event(block, status("working")), event(block, status("done"))]
        code, out, _ = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")
        self.assertEqual((code, out["state"], out["delivered"]), (0, "done", True))

    def test_done_left_from_before_the_dispatch_is_not_completion(self):
        fake = FakeRex()
        block, sent = self.dispatched(fake)
        fake.statuses[block] = status("done", at=BEFORE)
        Path(sent["report"]).write_text("task-nonce: {}\n".format(sent["nonce"]))
        fake.queued = [event(block, status("done", at=BEFORE))]
        code, out, _ = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "0")
        self.assertEqual((code, out["state"]), (0, "timeout"))

    def test_report_from_another_dispatch_is_not_delivery(self):
        fake = FakeRex()
        block, sent = self.dispatched(fake)
        Path(sent["report"]).write_text("task-nonce: 0000000000000000\n")
        fake.queued = [event(block, status("done"))]
        out = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")[1]
        self.assertEqual((out["state"], out["delivered"]), ("done", False))
        self.assertIn("nonce", out["reason"])

    def test_missing_report_is_not_delivery(self):
        fake = FakeRex()
        block, _ = self.dispatched(fake)
        fake.queued = [event(block, status("done"))]
        out = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")[1]
        self.assertEqual((out["delivered"], out["reason"]), (False, "report file missing"))

    def test_a_permission_prompt_returns_blocked_with_its_kind(self):
        fake = FakeRex()
        block, _ = self.dispatched(fake)
        fake.queued = [event(block, status("blocked", kind="permission"))]
        out = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")[1]
        self.assertEqual((out["state"], out["kind"], out["delivered"]), ("blocked", "permission", False))

    def test_another_blocks_completion_is_not_this_workers(self):
        fake = FakeRex()
        _, sent = self.dispatched(fake)
        Path(sent["report"]).write_text("task-nonce: {}\n".format(sent["nonce"]))
        fake.queued = [event("block:other", status("done"))]
        out = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "1")[1]
        self.assertEqual(out["state"], "timeout")

    def test_a_closed_event_stream_is_a_rex_failure(self):
        fake = FakeRex(close_stream=True)
        self.dispatched(fake)
        code, _, err = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")
        self.assertEqual((code, err["error"]["code"]), (1, "rex_failed"))

    def test_nothing_dispatched_is_refused(self):
        fake = FakeRex()
        self.spawned(fake)
        self.assertEqual(self.cli(fake, "wait", "--task", "t1", "--role", "dev")[2]["error"]["code"], "not_dispatched")


class StatusDownTest(Fixture):
    def test_status_then_down_closes_the_tab_and_keeps_the_worktree(self):
        fake = FakeRex()
        out = self.spawned(fake)
        self.assertEqual(self.cli(fake, "status", "--task", "t1")[1]["workers"]["dev"]["state"], "idle")
        code, result, _ = self.cli(fake, "down", "--task", "t1")
        self.assertEqual((code, result["closed"], fake.closed), (0, ["dev"], [out["block"]]))
        self.assertTrue(Path(out["worktree"]).is_dir())
        self.assertEqual(self.state()["workers"], {})


class StateTest(Fixture):
    def test_missing_task_is_named_with_the_fix(self):
        code, _, err = self.cli(FakeRex(), "status", "--task", "t1")
        self.assertEqual((code, err["error"]["code"]), (1, "no_task"))
        self.assertIn("team up --task t1", err["error"]["message"])

    def test_a_record_of_another_schema_version_is_refused(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        path = Path(self.env["REX_TEAM_HOME"]) / "t1.json"
        path.write_text(json.dumps({**self.state(), "schema_version": 2}))
        self.assertEqual(self.cli(fake, "status", "--task", "t1")[2]["error"]["code"], "state_version")

    def test_an_unexpected_failure_is_the_json_envelope(self):
        def boom(argv):
            raise RuntimeError("boom")

        self.spawned(FakeRex())
        out, err = io.StringIO(), io.StringIO()
        code = teammod.main(["status", "--task", "t1"], rex=Rex(runner=boom), stdout=out, stderr=err, env=self.env, now=lambda: SENT)
        self.assertEqual((code, out.getvalue(), json.loads(err.getvalue())["error"]["code"]), (1, "", "internal_error"))

    def test_usage_errors_are_the_json_envelope(self):
        code, _, err = self.cli(FakeRex(), "spawn", "--task", "t1")
        self.assertEqual((code, err["error"]["code"]), (2, "usage_error"))


class RexClientTest(unittest.TestCase):
    def test_failure_with_text_withholds_rex_stderr(self):
        rex = Rex(runner=lambda argv: Done(1, "", "rejected: hunter2"))
        with self.assertRaises(RexError) as caught:
            rex.paste(SESSION, LEAD, "hunter2")
        self.assertNotIn("hunter2", caught.exception.message)

    def test_read_failure_keeps_rex_stderr(self):
        rex = Rex(runner=lambda argv: Done(1, "", "session not found"))
        with self.assertRaises(RexError) as caught:
            rex.sessions()
        self.assertIn("session not found", caught.exception.message)

    def test_missing_binary(self):
        def missing(argv):
            raise FileNotFoundError(argv[0])

        with self.assertRaises(RexError):
            Rex(runner=missing).sessions()

    def test_wait_status_times_out_at_its_deadline(self):
        fake = FakeRex(statuses={LEAD: status("working")})
        with self.assertRaises(Timeout):
            fake.client().wait_status(SESSION, LEAD, lambda r: r.get("state") == "done", 0)

    def test_child_records_do_not_count(self):
        line = json.loads(event(LEAD, status("done")))
        line["payload"]["event"]["payload"]["payload"]["id"] = "build/test"
        self.assertIsNone(Rex._status_event(json.dumps(line), LEAD))


if __name__ == "__main__":
    unittest.main()
