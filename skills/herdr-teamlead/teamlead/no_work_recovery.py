"""Retry an initial developer assignment that stalled with no work and closed.

Recovery store version 10 owns schema-1 receipts in `no_work_recoveries`. A
receipt marks its source dispatch as an attempt that bore no work, so the
task's initial slot opens for one fresh assignment of the unchanged brief. The
source row and its assignment stay in history; the correction count never
moves. `record` verifies saved stall evidence plus the live checkout and pane;
`prepare` repeats the live checks before apply sends a retry; `reserve` binds
the retry's dispatch identity inside the reservation save; `validate` checks
the durable relationships on every read.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
from typing import NoReturn

from . import recovery as ledger
from . import supervision
from .chronology import latest_assignment, timestamp
from .errors import HerdrError, UsageError
from .herdr import error_code

RECEIPT_VERSION = 1
INPUT_FIELDS = frozenset({"dispatch", "reason", "authorization", "evidence"})
RECEIPT_FIELDS = frozenset({"schema_version", "id", "task", "at", "assignment_index", "input",
                            "evidence", "worktree", "source_sha256", "retries"})
#: wait-report.sh's terminal stall object, exactly; see its `--worktree` contract.
WAIT_FIELDS = frozenset({"agent", "state", "report_path", "found", "elapsed_seconds", "stall"})
STALL_EVIDENCE_FIELDS = frozenset({"worktree", "readable", "mid_operation", "staged", "modified", "untracked",
                                   "unpushed_commits", "base", "dispatch_commits", "dispatch_unpushed_commits"})
ZERO_FIELDS = ("staged", "modified", "untracked", "dispatch_commits", "dispatch_unpushed_commits")
GIT_MARKERS = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
               "rebase-merge", "rebase-apply", "sequencer")
PANE_GONE = "pane_not_found"
#: The worktree field skills/herdr-teamlead/templates/brief-developer.md renders.
BRIEF_WORKTREE_RE = re.compile(r"Your worktree already exists at `([^`\n]+)`")


def refuse(message) -> NoReturn:
    raise UsageError(message + " Preserve the original task and evidence; see skills/herdr-teamlead/references/dispatch-recovery.md No-Work Stalls.", {})


def receipts(store):
    return (store or {}).get("no_work_recoveries") or []


def recovered_indices(task, store):
    """Assignment indices of the task's dispatches recovered as no_work."""
    sources = {row["id"] for row in receipts(store) if row.get("task") == task}
    return {row["assignment_index"] for row in (store or {}).get("dispatches", [])
            if row.get("id") in sources and row.get("status") == "applied"}


def _source(store, identifier):
    row = next((item for item in store["dispatches"] if item["id"] == identifier), None)
    if (row is None or row["status"] != "applied" or row["role"] != "developer"
            or row["fix_round"] is not None or row.get("report") is not None or row.get("refusal") is not None):
        refuse("No-work recovery covers an applied initial developer dispatch with no recorded review or refusal.")
    return row


def _input(data):
    if not isinstance(data, dict) or set(data) != INPUT_FIELDS:
        refuse("No-work recovery requires dispatch, reason, authorization and evidence (the saved wait-report JSON).")
    for key in ("dispatch", "reason", "evidence"):
        ledger.text(data[key], key)
    ledger.authorization(data["authorization"])


def _git(tree, *args):
    try:
        result = subprocess.run(["git", "-C", tree, *args], capture_output=True, text=True, check=False)
    except OSError as exc:
        refuse("Cannot run git against the original checkout {}: {}.".format(tree, exc))
    if result.returncode:
        refuse("git {} failed in the original checkout {}: {}.".format(args[0], tree, result.stderr.strip()))
    return result.stdout.strip()


def _clean_checkout(tree, base):
    """The checkout holds no operation, change, untracked file or commit past base."""
    gitdir = Path(_git(tree, "rev-parse", "--absolute-git-dir"))
    if any((gitdir / marker).exists() for marker in GIT_MARKERS):
        refuse("The original checkout {} has a git operation in progress.".format(tree))
    if _git(tree, "status", "--porcelain", "--untracked-files=all"):
        refuse("The original checkout {} holds changed or untracked files; that is partial work, not no_work.".format(tree))
    if _git(tree, "rev-parse", "HEAD") != base:
        refuse("The original checkout {} is not at the recorded base {}; commits beyond the recorded base are work.".format(tree, base))


def _pane_gone(client, pane_id):
    try:
        client.pane_get(pane_id)
    except HerdrError as exc:
        if error_code(exc) != PANE_GONE:
            raise
        return
    refuse("The original worker pane {} still exists; close it before retrying, so it cannot keep writing.".format(pane_id))


def _brief_worktree(brief):
    """The one checkout the developer brief's worktree field names, as a real path."""
    named = {os.path.realpath(path) for path in BRIEF_WORKTREE_RE.findall(ledger.receipt(brief)[1])}
    if len(named) != 1:
        refuse("The original developer brief must name exactly one worktree in its `Your worktree already exists at` field.")
    return named.pop()


def _report_absent(binding):
    path = Path(binding["report"])
    # `is_symlink` also catches a dangling link, which `exists` misses.
    if path.exists() or path.is_symlink():
        refuse("The enrolled report {} exists; record or recover that report instead.".format(binding["report"]))


def _live(binding, worktree, base, client):
    """Every live fact the recovery rests on, read now."""
    _report_absent(binding)
    _pane_gone(client, binding["pane_id"])
    _clean_checkout(worktree, base)


def _binding(state_path, row):
    member = next((item for item in supervision.load(state_path)["members"] if item["id"] == row["id"]), None)
    if member is None:
        refuse("Dispatch {} has no supervision enrollment binding its report and pane.".format(row["id"]))
    if member["active"]:
        refuse("Dispatch {} is still enrolled; resolve it with `teamlead supervision-resolve` once its pane is closed.".format(row["id"]))
    binding = supervision.expected_assignment(member)
    if not isinstance(binding.get("pane_id"), str) or not binding["pane_id"] or not isinstance(binding.get("report"), str):
        refuse("Dispatch {}'s enrollment does not bind a pane and report path.".format(row["id"]))
    return binding


def _stall(data, row, binding, base):
    evidence, body = ledger.receipt(data["evidence"])
    try:
        wait = json.loads(body)
    except json.JSONDecodeError:
        refuse("The evidence must be the saved wait-report JSON, unchanged.")
    stall = wait.get("stall") if isinstance(wait, dict) else None
    proof = stall.get("evidence") if isinstance(stall, dict) else None
    if (not isinstance(wait, dict) or set(wait) != WAIT_FIELDS or wait["found"] is not False
            or wait["agent"] not in (row["agent"], binding["pane_id"]) or wait["report_path"] != binding["report"]
            or wait["state"] not in ("idle", "done")
            or type(wait["elapsed_seconds"]) is not int or wait["elapsed_seconds"] < 0):
        refuse("The evidence must be wait-report's complete stall output for this dispatch's worker and enrolled report.")
    if (not isinstance(stall, dict) or set(stall) != {"class", "evidence"} or stall["class"] != "no_work"
            or not isinstance(proof, dict) or set(proof) != STALL_EVIDENCE_FIELDS
            or proof["base"] != base or proof["readable"] is not True or proof["mid_operation"] is not False
            or any(type(proof[key]) is not int or proof[key] != 0 for key in ZERO_FIELDS)
            or not isinstance(proof["worktree"], str) or not Path(proof["worktree"]).is_absolute()):
        refuse("The stall must classify as no_work against the task's recorded base, with --worktree and --base.")
    return evidence, proof["worktree"]


def _verify(state_path, state, data, client):
    """Read saved evidence and live state; mutate nothing."""
    store = state["recovery"]
    row = _source(store, data["dispatch"])
    task = ledger.task_record(store, row["task"])
    latest = latest_assignment(state["assignments"], task=row["task"], role="developer", status="applied")
    if latest is None or latest[0] != row["assignment_index"]:
        refuse("Recover only the task's latest developer assignment.")
    binding = _binding(state_path, row)
    _report_absent(binding)
    evidence, worktree = _stall(data, row, binding, task["base_revision"])
    if ledger.brief_identity({"common": row["common"], "developer": row["brief"]}, "developer", binding["report"]) != row.get("brief_identity"):
        refuse("The original brief changed since dispatch; restore it so the retry sends the same assignment.")
    if os.path.realpath(worktree) != _brief_worktree(row["brief"]):
        refuse("The stalled checkout {} is not the worktree the original developer brief names.".format(worktree))
    _live(binding, worktree, task["base_revision"], client)
    return row, binding, evidence, worktree


def record(state_path, state, data, at, client):
    """Record one retry permission; an identical record replays its receipt."""
    _input(data)
    store = state["recovery"]
    prior = next((item for item in receipts(store) if item["id"] == data["dispatch"]), None)
    if prior is not None:
        if prior["input"] != data:
            refuse("Dispatch {} already holds different no-work recovery evidence or authorization.".format(data["dispatch"]))
        # The retry may already own the checkout; replay grants nothing new.
        return deepcopy(prior)
    row, _binding_row, evidence, worktree = _verify(state_path, state, data, client)
    if timestamp(at, "No-work recovery") <= timestamp(row["at"], "Original dispatch"):
        refuse("No-work recovery must follow its original dispatch.")
    result = {"schema_version": RECEIPT_VERSION, "id": row["id"], "task": row["task"], "at": at,
              "assignment_index": row["assignment_index"], "input": deepcopy(data),
              "evidence": evidence, "worktree": worktree,
              "source_sha256": ledger.canonical_digest(row), "retries": []}
    store["no_work_recoveries"].append(result)
    ledger._event(store, at, "no_work_recovered", row["task"], {"dispatch": row["id"], "evidence": evidence})
    return deepcopy(result)


def _open_receipt(store, assignments, task):
    """The receipt whose retry permission is still unspent, or None."""
    latest = latest_assignment(assignments, task=task, role="developer", status="applied")
    if latest is None:
        return None
    source = next((row for row in store["dispatches"] if row["status"] == "applied"
                   and row["role"] == "developer" and row.get("assignment_index") == latest[0]), None)
    saved = next((item for item in receipts(store) if source is not None and item["id"] == source["id"]), None)
    return saved


def prepare(state_path, state, task, fix_round, identity, report, no_clear, retain_context, client):
    """Recheck a pending retry's evidence before apply creates or sends anything."""
    if fix_round is not None or task is None:
        return
    saved = _open_receipt(state["recovery"], state["assignments"], task)
    if saved is None:
        return
    row = _source(state["recovery"], saved["id"])
    if no_clear or retain_context:
        refuse("A no-work retry requires a fresh developer context; omit --retain-context and --no-clear.")
    if identity != row.get("brief_identity"):
        refuse("A no-work retry sends the unchanged brief; only its report path may differ.")
    binding = _binding(state_path, row)
    if not isinstance(report, str) or Path(report).resolve() == Path(binding["report"]).resolve():
        refuse("A no-work retry needs a fresh report path; the original one belongs to dispatch {}.".format(row["id"]))
    _live(binding, saved["worktree"], ledger.task_record(state["recovery"], task)["base_revision"], client)


def recheck(state_path, state, task, fix_round, client):
    """Repeat the live checks immediately before a retry's reservation."""
    if fix_round is not None or task is None:
        return
    saved = _open_receipt(state["recovery"], state["assignments"], task)
    if saved is None:
        return
    binding = _binding(state_path, _source(state["recovery"], saved["id"]))
    _live(binding, saved["worktree"], ledger.task_record(state["recovery"], task)["base_revision"], client)


def reserve(store, record_row, at):
    """Bind a retry's identity to its receipt inside the reservation save."""
    if record_row["role"] != "developer" or record_row["fix_round"] is not None:
        return
    applied = [row for row in store["dispatches"] if row["task"] == record_row["task"]
               and row["role"] == "developer" and row["status"] == "applied"]
    if not applied:
        return
    previous = max(applied, key=lambda row: row["assignment_index"])
    saved = next((item for item in receipts(store) if item["id"] == previous["id"]), None)
    if saved is None:
        return
    if timestamp(at, "Retry reservation") < timestamp(saved["at"], "No-work recovery"):
        refuse("The retry reservation must follow its recovery receipt.")
    if record_row.get("brief_identity") != previous.get("brief_identity"):
        refuse("A no-work retry sends the unchanged brief; only its report path may differ.")
    for identifier in saved["retries"]:
        retry = next(row for row in store["dispatches"] if row["id"] == identifier)
        if identifier != record_row["id"] and retry["status"] != "not_sent":
            refuse("The no-work retry for dispatch {} is already reserved, uncertain or applied ({}).".format(previous["id"], identifier))
    if record_row["id"] not in saved["retries"]:
        saved["retries"].append(record_row["id"])


def follows(store, previous, row):
    """True when `row` is a bound retry of a no-work-recovered `previous`."""
    return any(item["id"] == previous["id"] and row["id"] in item["retries"] for item in receipts(store))


def validate(store, assignments):
    """Validate receipts against the ledger without treating old evidence as live."""
    for saved in receipts(store):
        if not isinstance(saved, dict) or set(saved) != RECEIPT_FIELDS or saved["schema_version"] != RECEIPT_VERSION:
            refuse("A no-work recovery receipt has an unsupported shape.")
        _input(saved["input"])
        row = _source(store, saved["id"])
        index = saved["assignment_index"]
        if (saved["input"]["dispatch"] != row["id"] or saved["task"] != row["task"]
                or type(index) is not int or index != row["assignment_index"]
                or not 0 <= index < len(assignments)
                or ledger.canonical_digest(row) != saved["source_sha256"]):
            refuse("A no-work recovery receipt lost its original dispatch or assignment binding.")
        ledger.validate_receipt(saved["evidence"])
        if not isinstance(saved["worktree"], str) or not Path(saved["worktree"]).is_absolute():
            refuse("A no-work recovery receipt lost its checkout.")
        retries = saved["retries"]
        if not isinstance(retries, list) or any(not isinstance(item, str) for item in retries) or len(set(retries)) != len(retries):
            refuse("No-work retry identities are malformed or duplicated.")
        used = 0
        for identifier in retries:
            retry = next((item for item in store["dispatches"] if item["id"] == identifier), None)
            if (retry is None or retry["id"] == row["id"] or retry["task"] != row["task"]
                    or retry["role"] != "developer" or retry["fix_round"] is not None
                    or retry.get("brief_identity") != row.get("brief_identity")
                    or timestamp(retry["at"], "Retry") < timestamp(saved["at"], "No-work recovery")):
                refuse("A no-work retry changed its original assignment.")
            used += retry["status"] != "not_sent"
        if used > 1:
            refuse("A no-work recovery permits one reserved, uncertain or applied retry.")
