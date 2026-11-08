"""Rex-native team rounds: one session, one tab per worker, one state file.

Contract:
  argv  : `up`, `spawn`, `assign`, `wait`, `candidate`, `check`, `verdict`,
          `status`, `down` (see `--help`).
  stdout: one JSON object per command.
  stderr: `{"error":{"code","message"}}` on failure.
  exit  : 0 success; 1 failure; 2 usage error. `check` exits 0 once its runs
          are classified, whatever the verdict: the verdict is data.
  state : `$REX_TEAM_HOME/<task>.json` (default `~/.rex-team`), schema in
          skills/rex-teamlead/state-schema.md. Reports and check logs live
          beside it, under `$REX_TEAM_HOME/<task>/`.

Workers are interactive agent TUIs in their own Rex tab. A worker's state is
its own OSC 7501 program status, read from Rex events; delivery is that
status reaching `done` after the dispatch plus the report file carrying the
dispatch's nonce. Nothing parses pane text.
"""

import argparse
import datetime as dt
import itertools
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

from .rex import FINISHED, READY, Rex, RexError, Timeout

SCHEMA_VERSION = 2
READY_TIMEOUT_SEC = 90
# Below the shell-tool limit of an agent lead (Claude Code's Bash caps at 600 s),
# so a long turn ends as `state: timeout`, never as a killed command.
DEFAULT_WAIT_TIMEOUT_SEC = 300
#: Per-command budget of a `check` run; the lead's shell call needs longer.
DEFAULT_CHECK_TIMEOUT_SEC = 1800
#: Exit codes a check command maps to a verdict; every other outcome is `invalid`.
CHECK_EXITS = {0: "pass", 1: "fail"}
CHECK_VERDICTS = ("pass", "fail", "invalid")
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
        version = record.get("schema_version") if isinstance(record, dict) else None
        if type(version) is not int:
            version = None  # JSON `true` would otherwise compare equal to 1
        if version is not None and version > SCHEMA_VERSION:
            raise TeamError("state_newer", "{} has schema_version {}, newer than this plugin reads ({}) — update coding-policy and retry".format(path, version, SCHEMA_VERSION))
        if version not in (1, SCHEMA_VERSION):
            # Version 1 has no predecessor, so any other value is corrupt, not older.
            raise TeamError("state_corrupt", "{} has schema_version {!r}, which no writer produced — restore it, or remove it and run `team up` again".format(path, version))
        problem = invalid(record, version) or (None if record["task"] == task else "it records task {!r}".format(record["task"]))
        if problem:
            raise TeamError("state_corrupt", "{} is malformed: {} — restore it, or remove it and run `team up` again".format(path, problem))
        if version == 1:
            # The owner migrates: version 2 only adds the optional worker `check`.
            record["schema_version"] = SCHEMA_VERSION
            for worker in record["workers"].values():
                worker["check"] = None
            self.save(record)
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

    def checks(self, task, role):
        """A fresh log directory for one `check`: `<task>/checks/<role>-<k>/`."""
        parent = self.home / task / "checks"
        parent.mkdir(parents=True, exist_ok=True)
        for k in itertools.count(1):
            directory = parent / "{}-{}".format(role, k)
            try:
                directory.mkdir()
            except FileExistsError:
                continue
            return directory
        raise AssertionError("unreachable")


RECORD_FIELDS = {"task": str, "repo": str, "base": str, "session": str, "workers": dict}
WORKER_FIELDS = {"agent": str, "window": str, "block": str, "worktree": str, "commit": str, "dispatches": list}
#: What a finished `wait` shows on the worker's tab, by outcome.
OUTCOME_LABELS = {"delivered": "delivered", "undelivered": "no report", "blocked": "blocked", "error": "error"}
DISPATCH_FIELDS = {"nonce": str, "report": str, "sent_at": str}


def utc_stamp(text):
    """Whether `text` is an ISO-8601 date and time at offset +00:00."""
    stamp = parse_time(text)
    return stamp is not None and stamp.utcoffset() == dt.timedelta(0) and "T" in text


def invalid(record, version=SCHEMA_VERSION):
    """The first contract violation in a task record, or None (state-schema.md)."""
    for field, kind in RECORD_FIELDS.items():
        if not isinstance(record.get(field), kind):
            return "field {} is missing or not a {}".format(field, kind.__name__)
    for role, worker in record["workers"].items():
        if not isinstance(worker, dict):
            return "worker {} is not an object".format(role)
        for field, kind in WORKER_FIELDS.items():
            if not isinstance(worker.get(field), kind):
                return "worker {} field {} is missing or not a {}".format(role, field, kind.__name__)
        for field in ("branch", "model", "effort"):
            if worker.get(field) is not None and not isinstance(worker[field], str):
                return "worker {} field {} is not a string or null".format(role, field)
        for dispatch in worker["dispatches"]:
            if not isinstance(dispatch, dict) or any(not isinstance(dispatch.get(f), k) for f, k in DISPATCH_FIELDS.items()):
                return "worker {} has a dispatch without nonce, report and sent_at".format(role)
            if not utc_stamp(dispatch["sent_at"]):
                return "worker {} has a dispatch whose sent_at is not an ISO-8601 UTC timestamp".format(role)
        check = worker.get("check")
        if "check" in worker and version < 2:
            return "worker {} field check predates schema_version 2".format(role)
        if check is not None and (not isinstance(check, dict) or not isinstance(check.get("candidate"), str)
                                  or check.get("verdict") not in CHECK_VERDICTS or not isinstance(check.get("checked_at"), str)
                                  or not utc_stamp(check["checked_at"])):
            return "worker {} field check is not {{candidate, verdict, checked_at}} with a UTC checked_at".format(role)
    return None


def worktree_entry(listing, path):
    """`path`'s block in `git worktree list --porcelain` output, or None.
    Git reports resolved paths (`/private/var/…` for `/var/…`), so compare resolved."""
    target = Path(path).resolve()
    return next((block for block in listing.split("\n\n")
                 if block.startswith("worktree ") and Path(block.split("\n", 1)[0][len("worktree "):]).resolve() == target), None)


def detached_at(repo, path, commit):
    """Whether live git lists `path` as a detached checkout at `commit`.
    Whole porcelain lines are compared: a branch named `detached-fix` is a branch."""
    entry = worktree_entry(git(repo, "worktree", "list", "--porcelain"), path)
    lines = entry.splitlines() if entry else []
    return "detached" in lines and "HEAD {}".format(commit) in lines


def run_check(command, cwd, log, timeout):
    """Run one check command with `bash -c` in `cwd`: no stdin, combined output
    to `log`, its whole process group killed at `timeout` seconds. The run is
    classified by how the process ended; the caller checks the worktree."""
    started = time.monotonic()

    def ran(code, verdict, reason):
        return {"command": command, "exit": code, "verdict": verdict, "reason": reason, "log": str(log),
                "seconds": round(time.monotonic() - started, 2)}

    with open(log, "wb") as out:
        try:
            proc = subprocess.Popen(["bash", "-c", command], cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            return ran(None, "invalid", "bash could not start: {}".format(exc.strerror or exc))
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The leader is not reaped yet, so its process group id is still ours.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # the group ended between the timeout and the kill
            proc.wait()
            return ran(None, "invalid", "timed out after {}s and was killed".format(timeout))
    if code < 0:
        return ran(None, "invalid", "killed by signal {}".format(signal.Signals(-code).name))
    if code in CHECK_EXITS:
        return ran(code, CHECK_EXITS[code], "exit {}".format(code))
    # 126 and 127 included: under `bash -c` the command may have run before
    # exiting with them, so the reason states only the observed status.
    return ran(code, "invalid", "exit {} is neither pass (0) nor fail (1)".format(code))


#: Forced on every clean-tree read. `-c` reaches the status processes git
#: runs for submodules too. Each setting could otherwise hide a change:
#: untracked files in a submodule, an fsmonitor that reports nothing changed,
#: a same-size rewrite with its mtime restored, and an executable-bit change.
STATUS_CONFIG = ("status.showUntrackedFiles=all", "core.fsmonitor=false", "core.trustctime=true", "core.fileMode=true")


def changes(worktree, prefix=""):
    """Every change `git status` can be made to hide, in `worktree` and each
    populated submodule below it, one line each; empty when clean. Untracked
    files, submodule changes and the settings in STATUS_CONFIG are forced
    whatever the user, repository, `.gitmodules` or a submodule sets. An
    index entry marked assume-unchanged, or skip-worktree with its file
    present, is reported: git skips its content, so its change is unseen.
    Ignored files stay unreported."""
    root = Path(worktree) / prefix
    where = "in {}: ".format(prefix) if prefix else ""
    config = [arg for setting in STATUS_CONFIG for arg in ("-c", setting)]
    found = [where + line.strip() for line in
             git(root, *config, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none").splitlines()]
    for entry in filter(None, git(root, "ls-files", "-v", "-z").split("\0")):
        tag, path = entry[0], entry[2:]
        if tag.islower():
            found.append("{}{} is marked assume-unchanged".format(where, path))
        elif tag == "S" and (root / path).exists():
            found.append("{}{} is marked skip-worktree but present".format(where, path))
    for entry in filter(None, git(root, "ls-files", "--stage", "-z").split("\0")):
        if entry.startswith("160000 "):
            sub = prefix + entry.split("\t", 1)[1] + "/"
            if (Path(worktree) / sub / ".git").exists():
                found.extend(filter(None, changes(worktree, sub).splitlines()))
    return "\n".join(found)


def worktree_change(worktree, candidate):
    """Why `worktree` is no longer a clean checkout of `candidate`, or None."""
    try:
        head = git(worktree, "rev-parse", "HEAD")
        dirty = changes(worktree)
    except TeamError as exc:
        return "the worktree is unreadable after the run: " + exc.message
    if head != candidate:
        return "the run moved HEAD to {}".format(head)
    if dirty:
        return "the run left uncommitted or untracked changes: {}".format("; ".join(dirty.splitlines()[:3]))
    return None


def check_name(value, what):
    if not NAME.fullmatch(value):
        raise UsageError("{} must match [a-z][a-z0-9-]{{0,30}}".format(what))


def git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise TeamError("git_failed", "`git {}` failed in {}: {}".format(" ".join(args[:2]), repo, done.stderr.strip()[:300]))
    return done.stdout.strip()


class Team:
    def __init__(self, rex, store, worktrees, now=utcnow, nonce=lambda: secrets.token_hex(8)):
        self.rex = rex
        self.store = store
        self.worktrees = Path(worktrees)
        self.now = now
        self.nonce = nonce

    def up(self, task, repo):
        repo = Path(repo).resolve()
        git(repo, "rev-parse", "--git-dir")
        try:
            record = self.store.load(task)
        except TeamError as exc:
            if exc.code != "no_task":
                raise
            record = None
        if record and record["repo"] != str(repo):
            raise TeamError("repo_mismatch", "task {} belongs to {} — use another task id for {}".format(task, record["repo"], repo))
        if record and any(s.get("session_id") == record["session"] for s in self.rex.sessions()):
            return {"task": task, "session": record["session"], "base": record["base"], "reused": True, "workers": sorted(record["workers"])}
        session, window, _ = self.rex.new_session("team-" + task, str(repo))
        shell_tab = "shell · " + repo.name
        try:
            self.rex.set_window_label(session, window, shell_tab)
            tab = {"tab_label": shell_tab}
        except RexError as exc:
            tab = {"tab_label": None, "tab_label_error": exc.message}
        if record:
            # The session died (a Rex restart): keep the base and the workers,
            # whose tabs are gone; `down` forgets them and `spawn` reuses their worktrees.
            record["session"] = session
        else:
            record = {"schema_version": SCHEMA_VERSION, "task": task, "repo": str(repo), "base": git(repo, "rev-parse", "HEAD"),
                      "session": session, "workers": {}}
        self.store.save(record)
        return {"task": task, "session": session, "base": record["base"], "reused": False, "workers": sorted(record["workers"]), **tab}

    def spawn(self, task, role, agent, model=None, effort=None, ref=None, read_only=False, timeout=READY_TIMEOUT_SEC):
        check_name(role, "role")
        if agent not in AGENTS:
            raise UsageError("agent must be one of " + ", ".join(sorted(AGENTS)))
        record = self.store.load(task)
        held = record["workers"].get(role)
        if held is not None:
            if held["agent"] == agent and held["block"] in self.rex.live_blocks(record["session"]):
                # Repeating a spawn reconnects to the worker it started.
                return {"task": task, "role": role, "block": held["block"], "worktree": held["worktree"], "branch": held.get("branch"),
                        "commit": held["commit"], "state": (self.rex.status(record["session"], held["block"]) or {}).get("state"), "reused": True}
            raise TeamError("role_taken", "task {} has a {} {} worker whose tab is gone or differs — `team down --task {} --role {}`, then spawn again".format(task, held["agent"], role, task, role))
        writers = {w["agent"] for w in record["workers"].values() if w.get("branch")}
        if read_only and agent in writers:
            raise TeamError("not_independent", "the developer is a {} agent; spawn the reviewer with a different `--agent` ({})".format(agent, ", ".join(sorted(set(AGENTS) - writers))))
        repo = Path(record["repo"])
        path = self.worktrees / repo.name / "{}-{}".format(task, role)
        commit = git(repo, "rev-parse", ref or record["base"])
        branch = None if read_only else "team/{}-{}".format(task, role)
        created = self._provision(repo, path, branch, commit)  # (worktree, branch) this call created
        label = "{}-{}".format(task, role)
        # REX_TEAM_TASK/REX_TEAM_ROLE mark a Rex team worker (rules/agent-team-operation.md).
        argv = ["env", "REX_TEAM_TASK=" + task, "REX_TEAM_ROLE=" + role] + AGENTS[agent](model, effort)
        try:
            window, block = self.rex.launch(record["session"], label, str(path), argv)
        except RexError as exc:
            try:
                self._unprovision(repo, path, branch, *created)
            except TeamError as undo:
                # Keep the launch failure as the cause; name what is left behind.
                raise RexError("{} (cleanup also failed: {})".format(exc.message, undo.message)) from None
            raise
        worker = {"agent": agent, "model": model, "effort": effort, "window": window, "block": block, "worktree": str(path),
                  "branch": branch, "commit": commit, "dispatches": [], "check": None}
        record["workers"][role] = worker
        self.store.save(record)
        tab = self._label(record, role)
        try:
            ready = self.rex.wait_status(record["session"], block, lambda r: bool(r) and r.get("state") in READY + ("error",), timeout)
        except Timeout:
            raise TeamError("not_ready", "{} in tab {} reported no ready prompt within {}s — answer its startup dialog there (trust the repository once), then `team status --task {}` shows it idle".format(agent, label, timeout, task)) from None
        if ready.get("state") == "error":
            raise TeamError("agent_failed", "{} in tab {} failed at startup — read the error there and fix the agent (login, install, flags), then `team down --task {} --role {}` and spawn again".format(agent, label, task, role))
        return {"task": task, "role": role, "block": block, "worktree": str(path), "branch": branch, "commit": commit, "state": ready.get("state"),
                "reused": False, **tab}

    def _provision(self, repo, path, branch, commit):
        """Create the worker's worktree, or adopt the one an interrupted spawn
        or an earlier session left at `path`. True when this call created it."""
        listing = git(repo, "worktree", "list", "--porcelain")
        entry = worktree_entry(listing, path)
        if entry is not None:
            lines = entry.splitlines()
            on_branch = "branch refs/heads/{}".format(branch) in lines if branch else "detached" in lines
            at_commit = branch is not None or "HEAD {}".format(commit) in lines
            if on_branch and at_commit:
                return (False, False)
            raise TeamError("worktree_conflict", "{} is already a worktree on another branch or commit — remove it with `git worktree remove {}` or use another role name".format(path, path))
        if path.exists():
            raise TeamError("worktree_conflict", "{} exists but is not a worktree of {} — move it away or use another role name".format(path, repo))
        if branch is None:
            git(repo, "worktree", "add", "--detach", str(path), commit)
            return (True, False)
        if git(repo, "branch", "--list", branch):
            git(repo, "worktree", "add", str(path), branch)
            return (True, False)
        git(repo, "worktree", "add", "-b", branch, str(path), commit)
        return (True, True)

    @staticmethod
    def _unprovision(repo, path, branch, created_worktree, created_branch):
        """Undo only what this spawn created, before any work landed in it."""
        if created_worktree:
            git(repo, "worktree", "remove", str(path))
        if created_branch and branch:
            git(repo, "branch", "-d", branch)

    def assign(self, task, role, brief):
        record = self.store.load(task)
        worker = self._worker(record, role)
        nonce = self.nonce()
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
        tab = self._label(record, role, "#{}".format(len(worker["dispatches"])))
        return {"task": task, "role": role, "report": str(report), "nonce": nonce, **tab}

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
        state = status.get("state")
        if state == "done":
            delivered, reason = self._delivery(dispatch)
        else:
            delivered, reason = False, "worker reported {!r}, not done".format(state)
        if state == "blocked":
            outcome = OUTCOME_LABELS["blocked"] + (": " + status["kind"] if status.get("kind") else "")
        elif state == "error":
            outcome = OUTCOME_LABELS["error"]
        else:
            outcome = OUTCOME_LABELS["delivered" if delivered else "undelivered"]
        tab = self._label(record, role, "#{} {}".format(len(worker["dispatches"]), outcome))
        return {"task": task, "role": role, "state": state, "kind": status.get("kind"), "delivered": delivered,
                "reason": reason, "report": dispatch["report"], **tab}

    def candidate(self, task, role, sha):
        """Is `sha` the tip of the writer's branch? The gate before review."""
        record = self.store.load(task)
        worker = self._worker(record, role)
        if not worker.get("branch"):
            raise UsageError("{} is a read-only worker; the candidate comes from the developer's branch".format(role))
        tip = git(Path(record["repo"]), "rev-parse", "refs/heads/" + worker["branch"])
        try:
            given = git(Path(record["repo"]), "rev-parse", "--verify", "--quiet", sha + "^{commit}")
        except TeamError:
            given = ""
        if given != tip:
            raise TeamError("candidate_mismatch", "{} is not the tip of {} ({}) — use the branch tip as the candidate, or ask the developer why its report differs".format(sha, worker["branch"], tip))
        return {"task": task, "role": role, "branch": worker["branch"], "candidate": tip}

    def check(self, task, role, sha, commands, timeout=DEFAULT_CHECK_TIMEOUT_SEC):
        """Run the lead's own check commands in the writer's worktree at the
        candidate and classify each run `pass`, `fail` or `invalid`."""
        if not commands:
            raise UsageError("name at least one check command with --run")
        if timeout < 1:
            raise UsageError("--timeout must be at least 1 second")
        record = self.store.load(task)
        worker = self._worker(record, role)
        if not worker.get("branch"):
            raise TeamError("check_mismatch", "{} is a read-only worker — run the checks in the developer's worktree, `--role` of the writer".format(role))
        try:
            candidate = self.candidate(task, role, sha)["candidate"]
        except TeamError as exc:
            if exc.code != "candidate_mismatch":
                raise
            raise TeamError("check_mismatch", exc.message) from None
        worktree = Path(worker["worktree"])
        head = git(worktree, "rev-parse", "HEAD")
        if head != candidate:
            raise TeamError("check_mismatch", "{}'s worktree {} is at {}, not the candidate {} — ask the developer to check out its branch tip, then check again".format(role, worktree, head, candidate))
        dirty = changes(worktree)
        if dirty:
            raise TeamError("check_dirty", "{}'s worktree {} has uncommitted or untracked changes ({}) — the run would not test the candidate; ask the developer to commit or remove them, then confirm the new tip as the candidate".format(
                role, worktree, "; ".join(dirty.splitlines()[:3])))
        logs = self.store.checks(task, role)
        runs, changed = [], False
        for index, command in enumerate(commands, 1):
            if changed:
                runs.append({"command": command, "exit": None, "verdict": "invalid", "reason": "not run: an earlier run changed the worktree",
                             "log": None, "seconds": 0.0})
                continue
            run = run_check(command, worktree, logs / "{}.log".format(index), timeout)
            moved = worktree_change(worktree, candidate)
            if moved:
                run.update(verdict="invalid", reason=moved)
                changed = True
            runs.append(run)
        verdicts = {run["verdict"] for run in runs}
        verdict = next(v for v in ("invalid", "fail", "pass") if v in verdicts)
        # Re-read before writing: the runs may have taken long.
        record = self.store.load(task)
        worker = self._worker(record, role)
        worker["check"] = {"candidate": candidate, "verdict": verdict, "checked_at": self.now().isoformat()}
        self.store.save(record)
        n = len(worker["dispatches"])
        tab = self._label(record, role, ("#{} ".format(n) if n else "") + "checks " + verdict)
        return {"task": task, "role": role, "candidate": candidate, "verdict": verdict, "runs": runs, **tab}

    def verdict(self, task, role, sha):
        """Read the reviewer's current report: it must review `sha` and give a verdict."""
        record = self.store.load(task)
        worker = self._worker(record, role)
        if not worker["dispatches"]:
            raise TeamError("not_dispatched", "{} has no report yet — `team assign` and `team wait` first".format(role))
        dispatch = worker["dispatches"][-1]
        delivered, reason = self._delivery(dispatch)
        if not delivered:
            raise TeamError("not_delivered", "{}'s current report is not delivered: {} — `team wait` for it first".format(role, reason))
        lines = [line.strip() for line in Path(dispatch["report"]).read_text().splitlines()]
        reviewed = [line[len("reviewed:"):].strip() for line in lines if line.startswith("reviewed:")]
        verdicts = [line[len("verdict:"):].strip() for line in lines if line.startswith("verdict:")]
        candidate = git(Path(record["repo"]), "rev-parse", sha)
        # The release ships the writer's branch, so it must still end at the reviewed commit.
        writers = [name for name, w in record["workers"].items() if w.get("branch")]
        if len(writers) != 1:
            raise TeamError("candidate_mismatch", "the task has {} writers ({}), not one — the verdict needs exactly one developer branch".format(len(writers), ", ".join(writers) or "none"))
        self.candidate(task, writers[0], candidate)
        # The recorded commit is a hint; the live checkout must still be the candidate.
        if worker.get("branch") is None and not detached_at(Path(record["repo"]), worker["worktree"], candidate):
            raise TeamError("review_mismatch", "{}'s checkout at {} is no longer a detached checkout of the candidate {} — spawn a fresh reviewer with `--ref {}`".format(role, worker["worktree"], candidate, candidate))
        if len(reviewed) != 1 or reviewed[0].strip("`") != candidate:
            raise TeamError("review_mismatch", "{}'s report does not review exactly {} (found {}) — brief a fresh reviewer on the candidate".format(role, candidate, reviewed or "no `reviewed:` line"))
        if len(verdicts) != 1 or verdicts[0] not in ("approve", "changes-requested"):
            raise TeamError("no_verdict", "{}'s report has no single `verdict: approve` or `verdict: changes-requested` line — send it one correction brief".format(role))
        return {"task": task, "role": role, "reviewed": candidate, "verdict": verdicts[0], "report": dispatch["report"]}

    def status(self, task):
        record = self.store.load(task)
        out = {}
        for role, worker in record["workers"].items():
            state = (self.rex.status(record["session"], worker["block"]) or {}).get("state", "unknown")
            last = worker["dispatches"][-1] if worker["dispatches"] else None
            out[role] = {"agent": worker["agent"], "state": state, "delivered": self._delivery(last)[0] if last else None,
                         "check": worker.get("check")}
        return {"task": task, "session": record["session"], "workers": out}

    def down(self, task, role=None):
        """Close worker tabs and forget the workers. A read-only checkout's
        lifecycle ends here and it is removed; a writer's worktree stays for
        the release. A tab already gone counts as closed."""
        record = self.store.load(task)
        roles = [role] if role else list(record["workers"])
        for name in roles:
            self._worker(record, name)
        live = self.rex.live_blocks(record["session"])
        repo = Path(record["repo"])
        closed, removed, kept, failed = [], [], [], []
        for name in roles:
            worker = record["workers"][name]
            if worker["block"] in live:
                self.rex.close(record["session"], worker["block"])
            if worker.get("branch") is None and Path(worker["worktree"]).exists():
                # A read-only checkout is disposable by contract; tests may leave
                # untracked output in it, so it is removed with --force — but only
                # once live git confirms it is still this worker's checkout.
                if not detached_at(repo, worker["worktree"], worker["commit"]):
                    failed.append({"role": name, "worktree": worker["worktree"],
                                   "error": "it is no longer this worker's detached checkout at {}".format(worker["commit"])})
                    continue
                try:
                    git(repo, "worktree", "remove", "--force", worker["worktree"])
                except TeamError as exc:
                    # Keep the worker so a later `down` retries the removal.
                    failed.append({"role": name, "worktree": worker["worktree"], "error": exc.message})
                    continue
                removed.append(worker["worktree"])
            elif worker.get("branch") is not None:
                kept.append(worker["worktree"])
            del record["workers"][name]
            self.store.save(record)
            closed.append(name)
        if failed:
            raise TeamError("worktree_remove_failed", "could not remove {} — fix what git names ({}), then run `team down --task {}` again; the worker stays recorded until it succeeds".format(
                ", ".join(f["worktree"] for f in failed), "; ".join(f["error"] for f in failed), task))
        return {"task": task, "closed": closed, "worktrees_removed": removed, "worktrees_kept": kept}

    def _label(self, record, role, suffix=None):
        """Name the worker's tab `<role> · <agent>[ · <suffix>]` (state-schema.md
        Naming). The label is cosmetic: a failure is reported, never raised."""
        worker = record["workers"][role]
        label = " · ".join([role, worker["agent"]] + ([suffix] if suffix else []))
        try:
            self.rex.set_window_label(record["session"], worker["window"], label)
        except RexError as exc:
            return {"tab_label": None, "tab_label_error": exc.message}
        return {"tab_label": label}

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
    cd = sub.add_parser("candidate")
    cd.add_argument("--task", required=True)
    cd.add_argument("--role", required=True)
    cd.add_argument("--sha", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("--task", required=True)
    ck.add_argument("--role", required=True)
    ck.add_argument("--candidate", required=True)
    ck.add_argument("--run", action="append", required=True, help="a check command, run with `bash -c`; repeat for more")
    ck.add_argument("--timeout", type=int, default=DEFAULT_CHECK_TIMEOUT_SEC, help="seconds per command")
    vd = sub.add_parser("verdict")
    vd.add_argument("--task", required=True)
    vd.add_argument("--role", required=True)
    vd.add_argument("--candidate", required=True)
    st = sub.add_parser("status")
    st.add_argument("--task", required=True)
    dn = sub.add_parser("down")
    dn.add_argument("--task", required=True)
    dn.add_argument("--role")
    return p


def envelope(stream, code, message):
    stream.write(json.dumps({"error": {"code": code, "message": message}}) + "\n")


def main(argv=None, rex=None, stdout=None, stderr=None, env=None, now=utcnow, nonce=None):
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    env = os.environ if env is None else env
    try:
        args = parser().parse_args(argv)
    except UsageError as exc:
        envelope(stderr, "usage_error", str(exc))
        return 2
    try:
        check_name(args.task, "task")
    except UsageError as exc:
        envelope(stderr, "usage_error", str(exc))
        return 2
    home = env.get("REX_TEAM_HOME") or str(Path.home() / ".rex-team")
    # Worker checkouts live under ~/.worktrees/ (rules/agent-team-operation.md Workers).
    team = Team(rex or Rex(binary=args.rex), Store(home), str(Path(env.get("HOME") or Path.home()) / ".worktrees"), now=now,
                **({"nonce": nonce} if nonce else {}))
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
        elif args.command == "candidate":
            result = team.candidate(args.task, args.role, args.sha)
        elif args.command == "check":
            result = team.check(args.task, args.role, args.candidate, args.run, args.timeout)
        elif args.command == "verdict":
            result = team.verdict(args.task, args.role, args.candidate)
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
