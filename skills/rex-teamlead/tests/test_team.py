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
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from rexlead import team as teammod
from rexlead.rex import Rex, RexError, Timeout

SESSION = "session:01a1b968-0889-721b-bfeb-f94b8a6239b3"
LEAD = "block:01a1b968-0000-7000-8000-00000000000a"
SENT = dt.datetime(2026, 11, 7, 12, 0, 0, tzinfo=dt.timezone.utc)
# Rex 0.1.0 (1853) stamps `updated_at` to the microsecond, in local time.
AFTER = "2026-11-07T07:00:05.250117-05:00"   # 12:00:05.25Z, after the dispatch
BEFORE = "2026-11-07T06:59:00.004211-05:00"  # 11:59:00Z, before it


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

    def __init__(self, statuses=None, events=(), close_stream=False, live=(SESSION,), fail_run=False, new_session=SESSION, fail_label=False, bad_ls=False, bad_blocks=False):
        self.statuses = dict(statuses or {})
        self.fail_run = fail_run
        self.fail_label = fail_label
        self.bad_ls = bad_ls
        self.bad_blocks = bad_blocks
        self.tab_labels = {}
        self.new_session_id = new_session
        self.queued = list(events)
        self.close_stream = close_stream
        self.live = list(live)
        self.launched, self.writes, self.closed = [], [], []
        self.next_block = 1

    def runner(self, argv):
        verb = argv[1]
        if verb == "ls" and self.bad_ls:
            return Done(0, json.dumps({"error": "shape changed"}))
        if verb == "ls":
            return Done(0, json.dumps({"sessions": [{"session_id": s, "label": "team"} for s in self.live]}))
        if verb == "new":
            if self.new_session_id not in self.live:
                self.live.append(self.new_session_id)
            return Done(0, json.dumps({"session_id": self.new_session_id, "initial_windows": [{"window_id": "window:shell", "block_ids": [LEAD]}]}))
        if verb == "run" and self.fail_run:
            return Done(1, "", "Error: launch failed")
        if verb == "run":
            block = "block:01a1b969-0000-7000-8000-{:012d}".format(self.next_block)
            self.next_block += 1
            self.launched.append({"cwd": argv[argv.index("-c") + 1], "label": argv[argv.index("--label") + 1],
                                  "argv": argv[argv.index("--") + 1:], "block": block})
            self.statuses.setdefault(block, status("idle", at=BEFORE))
            return Done(0, json.dumps({"window_id": block.replace("block:", "window:"), "block_ids": [block], "revision": 2}))
        if verb == "do" and "set_window_label" in argv[5]:
            if self.fail_label:
                return Done(1, "", "Error: window not found")
            window, label = [json.loads(part.split("]]", 1)[0]) for part in argv[5].split("([[")[1:]]
            self.tab_labels[window] = label
            return Done(0, '[{"revision":3}]')
        if verb == "do" and "list_blocks" in argv[5] and self.bad_blocks:
            return Done(0, "{}")
        if verb == "do" and "list_blocks" in argv[5]:
            return Done(0, json.dumps({"blocks": [{"block_id": b} for b in self.statuses] + [{"block_id": LEAD}]}))
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
            self.statuses.pop(argv[-1], None)
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
        self.env = {"REX_TEAM_HOME": str(root / "state"), "HOME": str(root / "home")}
        self.nonces = itertools.count(0xA11CE)
        self.brief = root / "brief.md"
        self.brief.write_text("# Brief\n\nAdd a function.\n")

    def cli(self, fake: "FakeRex", *argv: str) -> "tuple[int, dict[str, Any], dict[str, Any]]":
        """Exit code, parsed stdout and parsed stderr; an empty stream parses as {}."""
        out, err = io.StringIO(), io.StringIO()
        code = teammod.main(list(argv), rex=fake.client(), stdout=out, stderr=err, env=self.env, now=lambda: SENT,
                            nonce=lambda: "{:016x}".format(next(self.nonces)))
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

    def test_up_reuses_a_live_session_and_names_its_workers(self):
        fake = FakeRex()
        self.spawned(fake)
        out = self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))[1]
        self.assertEqual((out["reused"], out["workers"]), (True, ["dev"]))

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
        candidate = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()
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
        self.assertIn("team status --task t1", err["error"]["message"])

    def test_an_agent_that_errors_at_startup_is_named_not_left_to_time_out(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        original = fake.runner

        def errors(argv):
            done = original(argv)
            if argv[1] == "run":
                fake.statuses[json.loads(done.stdout)["block_ids"][0]] = status("error")
            return done

        fake.runner = errors
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude", "--timeout", "5")
        self.assertEqual((code, err["error"]["code"]), (1, "agent_failed"))
        self.assertIn("team down --task t1 --role dev", err["error"]["message"])

    def test_the_reviewer_must_be_another_agent_kind_than_the_developer(self):
        fake = FakeRex()
        self.spawned(fake)
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "claude", "--read-only")
        self.assertEqual((code, err["error"]["code"]), (1, "not_independent"))
        self.assertNotIn("review", self.state()["workers"])
        code, out, _ = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "grok", "--read-only")
        self.assertEqual((code, out["branch"]), (0, None))

    def test_repeating_a_spawn_reconnects_to_the_live_worker(self):
        fake = FakeRex()
        first = self.spawned(fake)
        code, out, _ = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, out["block"], out["reused"], len(fake.launched)), (0, first["block"], True, 1))

    def test_role_held_by_another_agent_is_refused(self):
        fake = FakeRex()
        self.spawned(fake)
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "codex")
        self.assertEqual((code, err["error"]["code"]), (1, "role_taken"))

    def test_failed_launch_leaves_no_worktree_or_branch_and_can_be_retried(self):
        fake = FakeRex(fail_run=True)
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, err["error"]["code"]), (1, "rex_failed"))
        branches = subprocess.run(["git", "branch", "--list", "team/t1-dev"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        self.assertEqual(branches, "")
        self.assertFalse((Path(self.env["HOME"]) / ".worktrees" / "repo" / "t1-dev").exists())
        fake.fail_run = False
        self.assertEqual(self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")[0], 0)

    def test_after_a_rex_restart_the_task_resumes_on_its_worktree(self):
        fake = FakeRex()
        first = self.spawned(fake)
        (Path(first["worktree"]) / "work.txt").write_text("in progress\n")
        fake.live.clear()
        fake.statuses.clear()
        fake.new_session_id = "session:01a1b9ff-0000-7000-8000-000000000002"
        code, out, _ = self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        self.assertEqual((code, out["reused"], out["workers"], out["base"]), (0, False, ["dev"], self.state()["base"]))
        self.assertEqual(self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")[2]["error"]["code"], "role_taken")
        self.assertEqual((self.cli(fake, "down", "--task", "t1", "--role", "dev")[0], fake.closed), (0, []))
        code, out, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual(code, 0, err)
        self.assertEqual(out["worktree"], first["worktree"])
        self.assertEqual((Path(out["worktree"]) / "work.txt").read_text(), "in progress\n")

    def test_a_failed_launch_keeps_a_branch_it_did_not_create(self):
        git("branch", "team/t1-dev", cwd=self.repo)
        fake = FakeRex(fail_run=True)
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, err["error"]["code"]), (1, "rex_failed"))
        self.assertIn("launch failed", err["error"]["message"])
        branches = subprocess.run(["git", "branch", "--list", "team/t1-dev"], cwd=self.repo, capture_output=True, text=True, check=True).stdout
        self.assertIn("team/t1-dev", branches)

    def test_a_foreign_directory_at_the_worktree_path_is_refused(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        (Path(self.env["HOME"]) / ".worktrees" / "repo" / "t1-dev").mkdir(parents=True)
        code, _, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, err["error"]["code"]), (1, "worktree_conflict"))

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

    def test_an_errored_worker_is_never_delivered_even_with_the_nonce(self):
        fake = FakeRex()
        block, sent = self.dispatched(fake)
        Path(sent["report"]).write_text("task-nonce: {}\n".format(sent["nonce"]))
        fake.queued = [event(block, status("error"))]
        out = self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")[1]
        self.assertEqual((out["state"], out["delivered"]), ("error", False))

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


class TabLabelTest(Fixture):
    def tab(self, fake, block):
        return fake.tab_labels[block.replace("block:", "window:")]

    def test_the_session_s_own_shell_tab_names_the_repository(self):
        fake = FakeRex()
        code, out, _ = self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        self.assertEqual((code, out["tab_label"], fake.tab_labels["window:shell"]), (0, "shell · repo", "shell · repo"))

    def test_the_tab_names_role_and_agent_then_the_dispatch_and_its_outcome(self):
        fake = FakeRex()
        block = self.spawned(fake)["block"]
        self.assertEqual((self.tab(fake, block), fake.launched[0]["label"]), ("dev · claude", "t1-dev"))
        sent = self.cli(fake, "assign", "--task", "t1", "--role", "dev", "--brief", str(self.brief))[1]
        self.assertEqual((sent["tab_label"], self.tab(fake, block)), ("dev · claude · #1", "dev · claude · #1"))
        Path(sent["report"]).write_text("task-nonce: {}\n".format(sent["nonce"]))
        fake.queued = [event(block, status("done"))]
        self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")
        self.assertEqual(self.tab(fake, block), "dev · claude · #1 delivered")

    def test_outcomes_without_delivery_are_named(self):
        for record, label in ((status("done"), "dev · claude · #1 no report"),
                              (status("blocked", kind="permission"), "dev · claude · #1 blocked: permission"),
                              (status("error"), "dev · claude · #1 error")):
            with self.subTest(label=label):
                fake = FakeRex()
                block = self.spawned(fake, "dev")["block"]
                self.cli(fake, "assign", "--task", "t1", "--role", "dev", "--brief", str(self.brief))
                fake.queued = [event(block, record)]
                self.cli(fake, "wait", "--task", "t1", "--role", "dev", "--timeout", "5")
                self.assertEqual(self.tab(fake, block), label)
                self.cli(fake, "down", "--task", "t1")
                git("worktree", "remove", "--force", str(Path(self.env["HOME"]) / ".worktrees" / "repo" / "t1-dev"), cwd=self.repo)
                git("branch", "-D", "team/t1-dev", cwd=self.repo)

    def test_a_label_failure_never_fails_the_command(self):
        fake = FakeRex(fail_label=True)
        code, out, _ = self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        code, out, err = self.cli(fake, "spawn", "--task", "t1", "--role", "dev", "--agent", "claude")
        self.assertEqual((code, out["tab_label"]), (0, None), err)
        self.assertIn("window not found", out["tab_label_error"])


class GateTest(Fixture):
    def committed(self, fake):
        dev = self.spawned(fake)
        (Path(dev["worktree"]) / "lib.py").write_text("x = 2\n")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "change", cwd=dev["worktree"])
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=dev["worktree"], capture_output=True, text=True, check=True).stdout.strip()

    def test_the_branch_tip_is_the_candidate(self):
        fake = FakeRex()
        tip = self.committed(fake)
        code, out, _ = self.cli(fake, "candidate", "--task", "t1", "--role", "dev", "--sha", tip)
        self.assertEqual((code, out["candidate"]), (0, tip))

    def test_a_reported_sha_that_is_not_the_tip_is_refused(self):
        fake = FakeRex()
        self.committed(fake)
        base = self.state()["base"]
        for sha in (base, "not-a-sha"):
            code, _, err = self.cli(fake, "candidate", "--task", "t1", "--role", "dev", "--sha", sha)
            self.assertEqual((code, err["error"]["code"]), (1, "candidate_mismatch"), sha)

    def reviewed(self, fake, body):
        tip = self.committed(fake)
        block = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--ref", tip, "--read-only")[1]["block"]
        sent = self.cli(fake, "assign", "--task", "t1", "--role", "review", "--brief", str(self.brief))[1]
        Path(sent["report"]).write_text("task-nonce: {}\n\n{}".format(sent["nonce"], body.format(sha=tip)))
        fake.queued = [event(block, status("done"))]
        self.cli(fake, "wait", "--task", "t1", "--role", "review", "--timeout", "5")
        return tip

    def test_a_review_of_the_candidate_with_a_verdict_passes(self):
        fake = FakeRex()
        tip = self.reviewed(fake, "reviewed: `{sha}`\n\nverdict: approve\n")
        code, out, _ = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((code, out["verdict"], out["reviewed"]), (0, "approve", tip))

    def refused(self, body, code):
        fake = FakeRex()
        tip = self.reviewed(fake, body)
        result = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((result[0], result[2].get("error", {}).get("code")), (1, code), result)

    def test_a_review_of_another_commit_is_refused(self):
        self.refused("reviewed: " + "0" * 40 + "\nverdict: approve\n", "review_mismatch")

    def test_a_review_naming_no_commit_is_refused(self):
        self.refused("verdict: approve\n", "review_mismatch")

    def test_a_review_without_a_verdict_is_refused(self):
        self.refused("reviewed: {sha}\nlooks fine\n", "no_verdict")

    def test_a_review_with_two_verdicts_is_refused(self):
        self.refused("reviewed: {sha}\nverdict: approve\nverdict: changes-requested\n", "no_verdict")

    def test_a_review_from_a_checkout_of_another_commit_is_refused(self):
        fake = FakeRex()
        tip = self.committed(fake)
        block = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--read-only")[1]["block"]
        sent = self.cli(fake, "assign", "--task", "t1", "--role", "review", "--brief", str(self.brief))[1]
        Path(sent["report"]).write_text("task-nonce: {}\n\nreviewed: {}\nverdict: approve\n".format(sent["nonce"], tip))
        fake.queued = [event(block, status("done"))]
        self.cli(fake, "wait", "--task", "t1", "--role", "review", "--timeout", "5")
        code, _, err = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((code, err["error"]["code"]), (1, "review_mismatch"))

    def test_a_review_from_a_checkout_moved_after_spawn_is_refused(self):
        fake = FakeRex()
        tip = self.reviewed(fake, "reviewed: {sha}\nverdict: approve\n")
        review = self.state()["workers"]["review"]["worktree"]
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "drift", cwd=review)
        code, _, err = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((code, err["error"]["code"]), (1, "review_mismatch"))

    def test_a_developer_branch_that_moved_past_the_review_is_refused(self):
        fake = FakeRex()
        tip = self.reviewed(fake, "reviewed: {sha}\nverdict: approve\n")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "late", cwd=self.state()["workers"]["dev"]["worktree"])
        code, _, err = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((code, err["error"]["code"]), (1, "candidate_mismatch"))

    def test_an_undelivered_review_has_no_verdict_yet(self):
        fake = FakeRex()
        tip = self.committed(fake)
        self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--ref", tip, "--read-only")
        self.cli(fake, "assign", "--task", "t1", "--role", "review", "--brief", str(self.brief))
        code, _, err = self.cli(fake, "verdict", "--task", "t1", "--role", "review", "--candidate", tip)
        self.assertEqual((code, err["error"]["code"]), (1, "not_delivered"))


class StatusDownTest(Fixture):
    def test_down_keeps_the_writer_worktree_and_removes_the_reviewer_checkout(self):
        fake = FakeRex()
        dev = self.spawned(fake)
        self.assertEqual(self.cli(fake, "status", "--task", "t1")[1]["workers"]["dev"]["state"], "idle")
        review = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--read-only")[1]
        code, result, _ = self.cli(fake, "down", "--task", "t1")
        self.assertEqual((code, sorted(result["closed"]), sorted(fake.closed)), (0, ["dev", "review"], sorted([dev["block"], review["block"]])))
        self.assertEqual((result["worktrees_kept"], result["worktrees_removed"]), ([dev["worktree"]], [review["worktree"]]))
        self.assertTrue(Path(dev["worktree"]).is_dir())
        self.assertFalse(Path(review["worktree"]).exists())
        self.assertEqual(self.state()["workers"], {})

    def test_down_removes_a_reviewer_checkout_the_tests_left_files_in(self):
        fake = FakeRex()
        self.spawned(fake)
        review = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--read-only")[1]
        (Path(review["worktree"]) / ".coverage").write_text("data\n")
        code, result, _ = self.cli(fake, "down", "--task", "t1", "--role", "review")
        self.assertEqual((code, result["worktrees_removed"]), (0, [review["worktree"]]))

    def test_a_checkout_down_cannot_remove_fails_and_is_retried(self):
        fake = FakeRex()
        self.spawned(fake)
        review = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--read-only")[1]
        git("worktree", "lock", review["worktree"], cwd=self.repo)
        code, _, err = self.cli(fake, "down", "--task", "t1", "--role", "review")
        self.assertEqual((code, err["error"]["code"]), (1, "worktree_remove_failed"))
        self.assertIn("review", self.state()["workers"])
        git("worktree", "unlock", review["worktree"], cwd=self.repo)
        code, result, _ = self.cli(fake, "down", "--task", "t1", "--role", "review")
        self.assertEqual((code, result["worktrees_removed"]), (0, [review["worktree"]]))
        self.assertNotIn("review", self.state()["workers"])

    def moved_checkout_is_kept(self, *move):
        fake = FakeRex()
        self.spawned(fake)
        review = self.cli(fake, "spawn", "--task", "t1", "--role", "review", "--agent", "codex", "--read-only")[1]
        git(*move, cwd=review["worktree"])
        (Path(review["worktree"]) / "notes.txt").write_text("unsaved\n")
        code, _, err = self.cli(fake, "down", "--task", "t1", "--role", "review")
        self.assertEqual((code, err["error"]["code"]), (1, "worktree_remove_failed"))
        self.assertTrue((Path(review["worktree"]) / "notes.txt").exists())
        self.assertIn("review", self.state()["workers"])

    def test_down_keeps_a_reviewer_checkout_someone_put_on_a_branch(self):
        self.moved_checkout_is_kept("checkout", "-q", "-b", "keep-me")

    def test_down_keeps_a_reviewer_checkout_on_a_branch_named_detached(self):
        self.moved_checkout_is_kept("checkout", "-q", "-b", "detached-fix")

    def test_down_keeps_a_reviewer_checkout_with_a_new_commit(self):
        self.moved_checkout_is_kept("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "work")

    def test_down_refuses_when_rex_cannot_list_the_blocks(self):
        fake = FakeRex()
        self.spawned(fake)
        fake.bad_blocks = True
        code, _, err = self.cli(fake, "down", "--task", "t1")
        self.assertEqual((code, err["error"]["code"]), (1, "rex_failed"))
        self.assertIn("dev", self.state()["workers"])

    def test_down_of_a_tab_closed_by_hand_forgets_the_worker(self):
        fake = FakeRex()
        out = self.spawned(fake)
        fake.statuses.pop(out["block"])
        code, result, _ = self.cli(fake, "down", "--task", "t1", "--role", "dev")
        self.assertEqual((code, result["closed"], fake.closed), (0, ["dev"], []))
        self.assertEqual(self.state()["workers"], {})


class StateTest(Fixture):
    def test_missing_task_is_named_with_the_fix(self):
        code, _, err = self.cli(FakeRex(), "status", "--task", "t1")
        self.assertEqual((code, err["error"]["code"]), (1, "no_task"))
        self.assertIn("team up --task t1", err["error"]["message"])

    def test_record_versions_and_shapes(self):
        fake = FakeRex()
        self.cli(fake, "up", "--task", "t1", "--repo", str(self.repo))
        path = Path(self.env["REX_TEAM_HOME"]) / "t1.json"
        good = self.state()
        worker = {"agent": "claude", "window": "window:a", "block": "block:a", "worktree": "/w", "commit": "c", "dispatches": []}
        cases = [({**good, "schema_version": 2}, "state_newer"), ({**good, "schema_version": "1"}, "state_corrupt"),
                 ({**good, "schema_version": True}, "state_corrupt"), ({**good, "task": "t2"}, "state_corrupt"),
                 ({**good, "workers": {"dev": {**worker, "model": 5}}}, "state_corrupt"),
                 ({**good, "workers": {"dev": {**worker, "dispatches": [{"nonce": "n", "report": "/r", "sent_at": "yesterday"}]}}}, "state_corrupt"),
                 ({**good, "workers": {"dev": {**worker, "dispatches": [{"nonce": "n", "report": "/r", "sent_at": "2026-11-07T12:00:00"}]}}}, "state_corrupt"),
                 ({**good, "workers": {"dev": {**worker, "dispatches": [{"nonce": "n", "report": "/r", "sent_at": "2026-11-07"}]}}}, "state_corrupt"),
                 ({**good, "workers": {"dev": {**worker, "dispatches": [{"nonce": "n", "report": "/r", "sent_at": "2026-11-07T07:00:00-05:00"}]}}}, "state_corrupt"),
                 ({**good, "schema_version": 0}, "state_corrupt"), ({k: v for k, v in good.items() if k != "base"}, "state_corrupt"),
                 ({**good, "workers": {"dev": {"agent": "claude"}}}, "state_corrupt")]
        for record, code in cases:
            path.write_text(json.dumps(record))
            self.assertEqual(self.cli(fake, "status", "--task", "t1")[2]["error"]["code"], code, record)
        path.write_text(json.dumps(good))
        self.assertEqual(self.cli(fake, "status", "--task", "t1")[0], 0)

    def test_a_session_list_rex_did_not_answer_is_a_failure_not_an_empty_list(self):
        self.cli(FakeRex(), "up", "--task", "t1", "--repo", str(self.repo))
        code, _, err = self.cli(FakeRex(bad_ls=True), "up", "--task", "t1", "--repo", str(self.repo))
        self.assertEqual((code, err["error"]["code"]), (1, "rex_failed"))
        self.assertEqual(self.state()["session"], SESSION)

    def test_task_ids_are_validated(self):
        for task in ("../x", "a/b", "Up"):
            code, _, err = self.cli(FakeRex(), "up", "--task", task, "--repo", str(self.repo))
            self.assertEqual((code, err["error"]["code"]), (2, "usage_error"), task)
        self.assertFalse((Path(self.tmp.name) / "x.json").exists())

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


class EntryPointTest(unittest.TestCase):
    def test_python_m_rexlead_keeps_the_json_and_exit_code_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "REX_TEAM_HOME": str(Path(tmp) / "state")}
            skill = Path(__file__).resolve().parent.parent
            done = subprocess.run([sys.executable, "-m", "rexlead", "status", "--task", "t1"], cwd=skill, env=env,
                                  capture_output=True, text=True, check=False)
            self.assertEqual((done.returncode, done.stdout), (1, ""))
            self.assertEqual(json.loads(done.stderr)["error"]["code"], "no_task")
            done = subprocess.run([sys.executable, "-m", "rexlead", "status"], cwd=skill, env=env,
                                  capture_output=True, text=True, check=False)
            self.assertEqual((done.returncode, json.loads(done.stderr)["error"]["code"]), (2, "usage_error"))


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

    def test_wait_status_rereads_a_change_the_stream_missed(self):
        reads = []

        def runner(argv):
            if argv[1] == "do":
                reads.append(argv)
                return Done(0, json.dumps({"records": [status("done" if len(reads) > 1 else "working")]}))
            raise AssertionError(argv)

        rex = Rex(binary="rex", runner=runner, events=lambda argv: Stream([], close=False))
        record = rex.wait_status(SESSION, LEAD, lambda r: r.get("state") == "done", 5) or {}
        self.assertEqual((record.get("state"), len(reads)), ("done", 2))

    def test_a_missing_rex_binary_names_the_fix_when_waiting(self):
        def missing(argv):
            raise FileNotFoundError(2, "No such file or directory", argv[0])

        rex = Rex(binary="/nonexistent/rex", runner=lambda argv: Done(0, json.dumps({"records": []})), events=missing)
        with self.assertRaises(RexError) as caught:
            rex.wait_status(SESSION, LEAD, lambda r: False, 1)
        self.assertIn("--rex", caught.exception.message)

    def test_wait_status_accepts_a_status_from_the_stream(self):
        rex = Rex(binary="rex", runner=lambda argv: Done(0, json.dumps({"records": [status("working")]})),
                  events=lambda argv: Stream([event(LEAD, status("done"))], close=False))
        self.assertEqual((rex.wait_status(SESSION, LEAD, lambda r: r.get("state") == "done", 5) or {}).get("state"), "done")

    def test_child_records_do_not_count(self):
        line = json.loads(event(LEAD, status("done")))
        line["payload"]["event"]["payload"]["payload"]["id"] = "build/test"
        self.assertIsNone(Rex._status_event(json.dumps(line), LEAD))


if __name__ == "__main__":
    unittest.main()
