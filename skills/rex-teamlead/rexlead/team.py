"""Rex-native team rounds: one session, one tab per worker, one state file.

Contract:
  argv  : `up`, `spawn`, `assign`, `wait`, `status`, `down` (see `--help`).
  stdout: one JSON object per command.
  stderr: `{"error":{"code","message"}}` on failure.
  exit  : 0 success; 1 failure; 2 usage error.
  state : `$REX_TEAM_HOME/<task>.json` (default `~/.rex-team`), schema in
          skills/rex-teamlead/state-schema.md. Reports live beside it.

Workers are interactive agent TUIs in their own Rex tab. A worker's state is
its own OSC 7501 program status, read from Rex events; delivery is that
status reaching `done` after the dispatch plus the report file carrying the
dispatch's nonce. Nothing parses pane text.
"""

import argparse
import datetime as dt
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from .rex import FINISHED, READY, Rex, RexError, Timeout

SCHEMA_VERSION = 1
READY_TIMEOUT_SEC = 90
DEFAULT_WAIT_TIMEOUT_SEC = 3600
#: Interactive launch per agent kind: the executable and its model/effort flags.
AGENTS = {
    "claude": lambda model, effort: ["claude"] + (["--model", model] if model else []) + (["--effort", effort] if effort else []),
    "codex": lambda model, effort: ["codex"] + (["-m", model] if model else []) + (["-c", "model_reasoning_effort=" + effort] if effort else []),
    "grok": lambda model, effort: ["grok"] + (["--model", model] if model else []) + (["--reasoning-effort", effort] if effort else []),
}
NAME = re.compile(r"[a-z][a-z0-9-]{0,30}")
DELIVERY = """

## Delivery

Write your complete report to this file:

    {report}

The report's first line must be exactly:

    task-nonce: {nonce}

Finish your turn once the report is written. Do not ask the lead questions:
decide, record the decision in the report, and continue. A genuine block goes
in a `## BLOCKED` section of the report, then stop.
"""


class UsageError(Exception):
    pass


class TeamError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(text):
    try:
        return dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


class Store:
    """One JSON record per task. Written whole, atomically."""

    def __init__(self, home):
        self.home = Path(home)

    def path(self, task):
        return self.home / (task + ".json")

    def load(self, task):
        path = self.path(task)
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            raise TeamError("no_task", "no team state for task {} — run `team up --task {}` first".format(task, task)) from None
        except json.JSONDecodeError:
            raise TeamError("state_corrupt", "{} is not JSON — restore it or remove it and run `team up` again".format(path)) from None
        if record.get("schema_version") != SCHEMA_VERSION:
            raise TeamError("state_version", "{} has schema_version {!r}; this tool reads {} — finish that task with the tool that wrote it".format(path, record.get("schema_version"), SCHEMA_VERSION))
        return record

    def save(self, record):
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.path(record["task"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2) + "\n")
        os.replace(tmp, path)

    def reports(self, task):
        directory = self.home / task / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise TeamError("git_failed", "`git {}` failed in {}: {}".format(" ".join(args[:2]), repo, done.stderr.strip()[:300]))
    return done.stdout.strip()


class Team:
    def __init__(self, rex, store, worktrees, now=utcnow):
        self.rex = rex
        self.store = store
        self.worktrees = Path(worktrees)
        self.now = now

    def up(self, task, repo):
        repo = Path(repo).resolve()
        git(repo, "rev-parse", "--git-dir")
        try:
            record = self.store.load(task)
        except TeamError as exc:
            if exc.code != "no_task":
                raise
            record = None
        if record and any(s.get("session_id") == record["session"] for s in self.rex.sessions()):
            return {"task": task, "session": record["session"], "reused": True}
        base = git(repo, "rev-parse", "HEAD")
        session, _ = self.rex.new_session("team-" + task, str(repo))
        record = {"schema_version": SCHEMA_VERSION, "task": task, "repo": str(repo), "base": base, "session": session, "workers": {}}
        self.store.save(record)
        return {"task": task, "session": session, "base": base, "reused": False}

    def spawn(self, task, role, agent, model=None, effort=None, ref=None, read_only=False, timeout=READY_TIMEOUT_SEC):
        if not NAME.fullmatch(role):
            raise UsageError("role must match [a-z][a-z0-9-]{0,30}")
        if agent not in AGENTS:
            raise UsageError("agent must be one of " + ", ".join(sorted(AGENTS)))
        record = self.store.load(task)
        if role in record["workers"]:
            raise TeamError("role_taken", "task {} already has a {} worker — `team down --role {}` first".format(task, role, role))
        repo = Path(record["repo"])
        path = self.worktrees / repo.name / "{}-{}".format(task, role)
        commit = git(repo, "rev-parse", ref or record["base"])
        if read_only:
            git(repo, "worktree", "add", "--detach", str(path), commit)
            branch = None
        else:
            branch = "team/{}-{}".format(task, role)
            git(repo, "worktree", "add", "-b", branch, str(path), commit)
        label = "{}-{}".format(task, role)
        # REX_TEAM_TASK/REX_TEAM_ROLE mark a Rex team worker (rules/agent-team-operation.md).
        argv = ["env", "REX_TEAM_TASK=" + task, "REX_TEAM_ROLE=" + role] + AGENTS[agent](model, effort)
        block = self.rex.launch(record["session"], label, str(path), argv)
        worker = {"agent": agent, "model": model, "effort": effort, "block": block, "worktree": str(path), "branch": branch, "commit": commit, "dispatches": []}
        record["workers"][role] = worker
        self.store.save(record)
        try:
            ready = self.rex.wait_status(record["session"], block, lambda r: bool(r) and r.get("state") in READY, timeout)
        except Timeout:
            raise TeamError("not_ready", "{} in tab {} reported no ready prompt within {}s — answer any startup dialog there (trust the parent of {} once), then `team wait`".format(agent, label, timeout, self.worktrees)) from None
        return {"task": task, "role": role, "block": block, "worktree": str(path), "branch": branch, "commit": commit, "state": ready.get("state")}

    def assign(self, task, role, brief):
        record = self.store.load(task)
        worker = self._worker(record, role)
        nonce = secrets.token_hex(8)
        report = self.store.reports(task) / "{}-{}.md".format(role, len(worker["dispatches"]) + 1)
        text = Path(brief).read_text() + DELIVERY.format(report=report, nonce=nonce)
        state = (self.rex.status(record["session"], worker["block"]) or {}).get("state")
        if state not in READY:
            raise TeamError("worker_busy", "{} reports {!r}, not a ready prompt — `team wait` for it, or answer its dialog in tab {}-{}".format(role, state, task, role))
        dispatch = {"nonce": nonce, "report": str(report), "sent_at": self.now().isoformat()}
        worker["dispatches"].append(dispatch)
        self.store.save(record)
        self.rex.paste(record["session"], worker["block"], text)
        self.rex.keys(record["session"], worker["block"], "enter")
        return {"task": task, "role": role, "report": str(report), "nonce": nonce}

    def wait(self, task, role, timeout=DEFAULT_WAIT_TIMEOUT_SEC):
        record = self.store.load(task)
        worker = self._worker(record, role)
        if not worker["dispatches"]:
            raise TeamError("not_dispatched", "{} has no assignment to wait for — `team assign` first".format(role))
        dispatch = worker["dispatches"][-1]
        sent = parse_time(dispatch["sent_at"])

        def settled(status):
            changed = parse_time((status or {}).get("updated_at"))
            fresh = changed is not None and sent is not None and changed >= sent
            return fresh and status.get("state") in FINISHED + ("blocked",)

        try:
            status = self.rex.wait_status(record["session"], worker["block"], settled, timeout)
        except Timeout:
            return {"task": task, "role": role, "state": "timeout", "delivered": False, "report": dispatch["report"]}
        delivered, reason = self._delivery(dispatch)
        return {"task": task, "role": role, "state": status.get("state"), "kind": status.get("kind"), "delivered": delivered,
                "reason": reason, "report": dispatch["report"]}

    def status(self, task):
        record = self.store.load(task)
        out = {}
        for role, worker in record["workers"].items():
            state = (self.rex.status(record["session"], worker["block"]) or {}).get("state", "unknown")
            last = worker["dispatches"][-1] if worker["dispatches"] else None
            out[role] = {"agent": worker["agent"], "state": state, "delivered": self._delivery(last)[0] if last else None}
        return {"task": task, "session": record["session"], "workers": out}

    def down(self, task, role=None):
        record = self.store.load(task)
        roles = [role] if role else list(record["workers"])
        closed = []
        for name in roles:
            worker = self._worker(record, name)
            self.rex.close(record["session"], worker["block"])
            del record["workers"][name]
            closed.append(name)
        self.store.save(record)
        return {"task": task, "closed": closed, "worktrees_kept": True}

    @staticmethod
    def _worker(record, role):
        worker = record["workers"].get(role)
        if worker is None:
            raise TeamError("no_worker", "task {} has no {} worker — `team spawn --role {}` first".format(record["task"], role, role))
        return worker

    @staticmethod
    def _delivery(dispatch):
        path = Path(dispatch["report"])
        try:
            first = path.read_text().splitlines()[:1]
        except FileNotFoundError:
            return False, "report file missing"
        if first != ["task-nonce: " + dispatch["nonce"]]:
            return False, "report's first line is not this dispatch's nonce"
        return True, "report present with this dispatch's nonce"


class Parser(argparse.ArgumentParser):
    """Usage errors become the JSON envelope, never argparse's text."""

    def error(self, message):
        raise UsageError(message)


def parser():
    p = Parser(prog="team", description="Rex-native team rounds")
    p.add_argument("--rex", default=os.environ.get("REX_TEAM_REX_BIN", "rex"))
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    up = sub.add_parser("up")
    up.add_argument("--task", required=True)
    up.add_argument("--repo", required=True)
    sp = sub.add_parser("spawn")
    sp.add_argument("--task", required=True)
    sp.add_argument("--role", required=True)
    sp.add_argument("--agent", required=True)
    sp.add_argument("--model")
    sp.add_argument("--effort")
    sp.add_argument("--ref", help="commit the worktree starts from (default: the task base)")
    sp.add_argument("--read-only", action="store_true", help="detached worktree, no branch (reviewers)")
    sp.add_argument("--timeout", type=int, default=READY_TIMEOUT_SEC)
    asg = sub.add_parser("assign")
    asg.add_argument("--task", required=True)
    asg.add_argument("--role", required=True)
    asg.add_argument("--brief", required=True)
    wt = sub.add_parser("wait")
    wt.add_argument("--task", required=True)
    wt.add_argument("--role", required=True)
    wt.add_argument("--timeout", type=int, default=DEFAULT_WAIT_TIMEOUT_SEC)
    st = sub.add_parser("status")
    st.add_argument("--task", required=True)
    dn = sub.add_parser("down")
    dn.add_argument("--task", required=True)
    dn.add_argument("--role")
    return p


def envelope(stream, code, message):
    stream.write(json.dumps({"error": {"code": code, "message": message}}) + "\n")


def main(argv=None, rex=None, stdout=None, stderr=None, env=None, now=utcnow):
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    env = os.environ if env is None else env
    try:
        args = parser().parse_args(argv)
    except UsageError as exc:
        envelope(stderr, "usage_error", str(exc))
        return 2
    home = env.get("REX_TEAM_HOME") or str(Path.home() / ".rex-team")
    team = Team(rex or Rex(binary=args.rex), Store(home), env.get("REX_TEAM_WORKTREES") or str(Path.home() / ".worktrees"), now=now)
    try:
        if args.command == "up":
            result = team.up(args.task, args.repo)
        elif args.command == "spawn":
            result = team.spawn(args.task, args.role, args.agent, args.model, args.effort, args.ref, args.read_only, args.timeout)
        elif args.command == "assign":
            result = team.assign(args.task, args.role, args.brief)
        elif args.command == "wait":
            result = team.wait(args.task, args.role, args.timeout)
        elif args.command == "status":
            result = team.status(args.task)
        else:
            result = team.down(args.task, args.role)
    except UsageError as exc:
        envelope(stderr, "usage_error", str(exc))
        return 2
    except TeamError as exc:
        envelope(stderr, exc.code, exc.message)
        return 1
    except RexError as exc:
        envelope(stderr, "rex_failed", exc.message)
        return 1
    except OSError as exc:
        envelope(stderr, "io_failed", "{} failed: {} — check the paths under REX_TEAM_HOME and the brief".format(type(exc).__name__, exc.strerror or exc))
        return 1
    # outer-boundary-process-contract: the lead and rex-team.sh read a failure
    # only as this JSON envelope on stderr; this emits it with code
    # `internal_error` and no stdout, where a propagating traceback would be
    # unparseable text the lead could misread as a result.
    except Exception as exc:  # noqa: BLE001 — outer-boundary-process-contract
        envelope(stderr, "internal_error", "team failed unexpectedly ({}) — rerun the same command; if it repeats, report it with the command".format(type(exc).__name__))
        return 1
    stdout.write(json.dumps(result) + "\n")
    return 0
