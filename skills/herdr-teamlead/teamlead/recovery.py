"""Owner-managed task recovery, bounded approval, and dispatch accounting.

This module owns the recovery section of teamlead's state file. Mutations add
audit events; authorization and original assignment rows are never rewritten.
The CLI serializes access and saves before any dispatch side effect. A send
whose outcome is unknown holds its attempt number until explicit recovery.
"""

import hashlib
import json
import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from .errors import UsageError
from .chronology import assignment_after, latest_assignment


RECOVERY_SCHEMA_VERSION = 1
RECOVERY_STORE_VERSION = 4
DEFAULT_FIX_LIMIT = 5
PENDING_STATUSES = frozenset({"reserved", "sending", "sent_but_not_started"})
DISPATCH_STATUSES = PENDING_STATUSES | {"applied", "not_sent"}
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def empty_recovery():
    return {"schema_version": RECOVERY_STORE_VERSION, "tasks": {}, "checkpoints": [],
            "plans": [], "dispatches": [], "context_permissions": [], "events": [],
            "hand_clearances": [], "historical_attempts": [], "role_clearances": [], "delivery_recoveries": []}


def migrate_store(store):
    """Upgrade only the enclosing recovery document; old record shapes persist."""
    if not isinstance(store, dict) or type(store.get("schema_version")) is not int:
        return False
    version = store["schema_version"]
    if version not in {1, 2, 3}:
        return False
    if version == 3:
        deliveries = store.get("delivery_recoveries")
        if not isinstance(deliveries, list):
            raise UsageError("Older recovery requires a delivery_recoveries array; restore the original owner-written store.", {})
        if any(not isinstance(row, dict) or row.get("schema_version") != 1 for row in deliveries):
            raise UsageError("Older recovery contains unowned newer delivery records; preserve it for owner recovery.", {})
    added = ["role_clearances", "delivery_recoveries"] if version < 3 else []
    if version == 1:
        added.extend(["hand_clearances", "historical_attempts"])
    if any(name in store for name in added):
        raise UsageError("Older recovery contains unowned newer records; preserve it for owner recovery.", {})
    store.update({name: [] for name in added})
    store["schema_version"] = RECOVERY_STORE_VERSION
    return True


def text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise UsageError("{} must be non-empty text without surrounding whitespace; preserve existing identities.".format(label), {})
    return value


def revision(value, label):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise UsageError("{} must be a full lowercase commit SHA; resolve the original revision before recording it.".format(label), {})
    return value


def positive(value, label):
    if type(value) is not int or value < 1:
        raise UsageError("{} must be a positive integer; specify a bounded count.".format(label), {})
    return value


def paths(value, label):
    if not isinstance(value, list) or not value:
        raise UsageError("{} must list the permitted repository-relative paths or globs.".format(label), {})
    for entry in value:
        text(entry, label)
        if entry.startswith("/") or ".." in PurePosixPath(entry).parts or any(ord(char) < 32 for char in entry):
            raise UsageError("{} contains an unsafe path; use repository-relative paths without parent traversal.".format(label), {})
    return value


def authorization(value):
    if not isinstance(value, dict) or set(value) != {"source", "quote"}:
        raise UsageError("Record explicit operator authorization as source and quote; silence or a generic release instruction cannot waive a correction budget.", {})
    text(value["source"], "authorization source")
    text(value["quote"], "operator's authorization")
    return value


def receipt(path):
    """Read an artifact now and bind the audit entry to its actual bytes."""
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise UsageError("Evidence must name an absolute report path; write the report before recording it.", {})
    try:
        data = Path(path).read_bytes()
        body = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read evidence {}: {}. Restore the original readable UTF-8 report before continuing.".format(path, exc), {}) from None
    if not body.strip():
        raise UsageError("Evidence {} is empty; collect the actual report before continuing.".format(path), {})
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}, body


def validate_receipt(value):
    if (not isinstance(value, dict) or set(value) != {"path", "sha256"}
            or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or not isinstance(value["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
        raise UsageError("Recorded evidence needs an absolute artifact path and byte digest; restore the owner-written receipt.", {})


def _event(store, at, kind, task, details):
    store["events"].append({"schema_version": RECOVERY_SCHEMA_VERSION, "sequence": len(store["events"]) + 1,
                            "at": at, "kind": kind, "task": task, "details": details})


def _item(items, identifier, label):
    value = next((row for row in items if row.get("id") == identifier), None)
    if value is None:
        raise UsageError("Unknown {} {!r}; inspect `teamlead state` and use its recorded identity.".format(label, identifier), {})
    return value


def confirmed_fix(assignments, task):
    return max((row.get("fix_round") or 0 for row in assignments
                if row.get("task") == task and row.get("role") == "developer" and row.get("status") == "applied"), default=0)


def register_task(store, data, at):
    if not isinstance(data, dict) or set(data) != {"task", "base_revision", "scope", "allowed_paths", "authorization"}:
        raise UsageError("Task record requires task, base_revision, scope, allowed_paths and authorization; preserve the original task and base.", {})
    task = text(data["task"], "task")
    revision(data["base_revision"], "base_revision")
    text(data["scope"], "scope")
    paths(data["allowed_paths"], "allowed_paths")
    authorization(data["authorization"])
    existing = store["tasks"].get(task)
    if existing:
        if any(existing[key] != value for key, value in data.items()):
            raise UsageError("Task {!r} is already recorded with another base or scope; do not replace or relabel its history.".format(task), {})
        return existing
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data}
    store["tasks"][task] = record
    _event(store, at, "task_registered", task, {"base_revision": data["base_revision"]})
    return record


def task_record(store, task):
    record = store["tasks"].get(task)
    if record is None:
        raise UsageError("Task {!r} has no recorded base/scope. Run `teamlead task --record FILE` with its original brief and authorization; do not reset the task.".format(task), {})
    return record


def checkpoint(store, assignments, data, at, judge_agent):
    required = {"id", "task", "defect", "previous_attempts", "progress", "change_in_approach", "judge_report"}
    if not isinstance(data, dict) or set(data) != required:
        raise UsageError("Checkpoint requires id, task, defect, previous_attempts, progress, change_in_approach and judge_report; describe the concrete remaining work.", {})
    for key in required:
        text(data[key], key)
    task = task_record(store, data["task"])
    count = confirmed_fix(assignments, data["task"])
    if count < DEFAULT_FIX_LIMIT:
        raise UsageError("The normal correction budget is not exhausted; continue within it.", {})
    if any(row["task"] == data["task"] and row["status"] in PENDING_STATUSES for row in store["dispatches"]):
        raise UsageError("A dispatch outcome is still unknown; reconcile it before proposing another correction budget.", {})
    developer = latest_assignment(assignments, task=data["task"], role="developer", status="applied")
    judge = latest_assignment(assignments, task=data["task"], role="judge", agent=judge_agent, status="applied")
    if not judge_agent or developer is None or judge is None or not assignment_after(assignments, judge[0], developer[0]):
        raise UsageError("Dispatch the configured pinned judge after the latest developer attempt before recording this checkpoint.", {})
    evidence, body = receipt(data["judge_report"])
    if not re.search(r"^RULING: (?:uphold A|uphold B|amend)(?:\s|$)", body, re.MULTILINE) or not re.search(r"^ACTION: \S", body, re.MULTILINE):
        raise UsageError("The judge report must contain its completed RULING and ACTION; a blocked judge requires the operator's answer first.", {})
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data, "fix_round": count,
              "base_revision": task["base_revision"], "judge_evidence": evidence, "judge_agent": judge_agent}
    prior = next((row for row in store["checkpoints"] if row["id"] == data["id"]), None)
    if prior:
        if any(prior[key] != value for key, value in record.items() if key != "at"):
            raise UsageError("Checkpoint identity already describes different evidence; record a new checkpoint without rewriting the old one.", {})
        return prior
    store["checkpoints"].append(record)
    _event(store, at, "waiting_for_operator", data["task"], {"checkpoint": data["id"], "paused_work": "implementation", "defect": data["defect"]})
    return record


def authorize_plan(store, assignments, data, at):
    required = {"id", "task", "checkpoint", "scope", "allowed_paths", "additional_fixes", "authorization"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"supersedes"}:
        raise UsageError("Correction approval requires id, task, checkpoint, scope, allowed_paths, additional_fixes and explicit authorization.", {})
    for key in ("id", "task", "checkpoint", "scope"):
        text(data[key], key)
    if "supersedes" in data:
        text(data["supersedes"], "supersedes")
    paths(data["allowed_paths"], "allowed_paths")
    positive(data["additional_fixes"], "additional_fixes")
    authorization(data["authorization"])
    prior = next((row for row in store["plans"] if row["id"] == data["id"]), None)
    if prior:
        if any(prior.get(key) != value for key, value in data.items()) or prior.get("supersedes") != data.get("supersedes"):
            raise UsageError("This approval identity already has different bounds; request a new bounded plan instead of editing it.", {})
        return prior
    source = _item(store["checkpoints"], data["checkpoint"], "checkpoint")
    count = confirmed_fix(assignments, data["task"])
    if source["task"] != data["task"] or source["fix_round"] != count:
        raise UsageError("Approval must match this task's current exhausted checkpoint; collect a fresh ruling and correction proposal.", {})
    active = next((row for row in active_plans(store) if row["task"] == data["task"] and row["last_fix"] > count), None)
    if active and data.get("supersedes") != active["id"]:
        raise UsageError("This task still has an approved plan. Use its bounds, or explicitly name it in supersedes with the operator's changed decision.", {})
    if data.get("supersedes") and (active is None or data["supersedes"] != active["id"]):
        raise UsageError("Supersedes must name this task's current unexhausted plan; inspect teamlead status before recording the changed decision.", {})
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data,
              "base_revision": source["base_revision"], "first_fix": count + 1, "last_fix": count + data["additional_fixes"]}
    store["plans"].append(record)
    _event(store, at, "correction_plan_authorized", data["task"], {"plan": data["id"], "first_fix": record["first_fix"], "last_fix": record["last_fix"]})
    return record


def active_plans(store):
    replaced = {row["supersedes"] for row in store["plans"] if row.get("supersedes")}
    return [row for row in store["plans"] if row["id"] not in replaced]


def validate_work(store, assignments, task, fix_round, plan_id=None, work=None, *, implementation=True):
    """The same allowance is checked by planning, dispatch and state readers."""
    if fix_round is None:
        if plan_id:
            raise UsageError("A correction approval requires the actual cumulative fix number; do not reset it to initial development.", {})
        return None
    positive(fix_round, "fix_round")
    if implementation:
        from .role_clear import validate_requested
        validate_requested(store, assignments, task, fix_round, plan_id, work)
    if fix_round <= DEFAULT_FIX_LIMIT:
        if plan_id:
            raise UsageError("An extra-correction plan cannot relabel an ordinary fix; preserve the cumulative number.", {})
        return None
    if not task or not plan_id:
        raise UsageError("The five-fix budget is exhausted. Dispatch the judge and record an explicit bounded correction plan; ordinary sixth attempts are refused.", {})
    plan = _item(store["plans"], plan_id, "correction plan")
    if plan not in active_plans(store):
        raise UsageError("That approval was superseded by an explicit operator decision; use the current recorded bounds.", {})
    if plan["task"] != task or not plan["first_fix"] <= fix_round <= plan["last_fix"]:
        raise UsageError("This correction is outside the approved task or budget; pause implementation and request a new bounded decision.", {})
    if not isinstance(work, dict) or set(work) != {"base_revision", "scope", "paths", "findings"}:
        raise UsageError("Extra corrections require --work with base_revision, scope, paths and findings from the blocking review.", {})
    if work["base_revision"] != plan["base_revision"] or work["scope"] != plan["scope"]:
        raise UsageError("Correction base or scope differs from its authorization; preserve the base and ask only for the changed scope decision.", {})
    paths(work["paths"], "correction paths")
    if not isinstance(work["findings"], list) or not work["findings"] or any(not isinstance(value, str) or not value.strip() for value in work["findings"]):
        raise UsageError("Name the concrete blocking findings this correction addresses.", {})
    if any(not any(fnmatchcase(path, allowed) for allowed in plan["allowed_paths"]) for path in work["paths"]):
        raise UsageError("Correction paths exceed the approved scope; pause implementation for the changed decision.", {})
    count = confirmed_fix(assignments, task)
    if implementation and fix_round != count + 1:
        raise UsageError("Use this task's actual next fix number; approval never resets, skips, or reuses a completed attempt.", {})
    # Count identities are unique; receipt append time cannot select an attempt.
    previous = next((row for row in store["dispatches"] if row["task"] == task
                     and row["role"] == "developer" and row["status"] == "applied"
                     and (row.get("fix_round") or 0) == count), None)
    historical = next((row for row in store["historical_attempts"] if row["task"] == task
                       and row["fix_round"] == count), None)
    if implementation and historical and historical["fix_round"] >= plan["first_fix"]:
        report = historical["reviews"][-1] if historical["reviews"] else None
        if not report or report["input"]["verdict"] != "blocking":
            raise UsageError("Record the imported correction's actual blocking review with record-historical-review before spending the next approved attempt.", {})
        evidence, _body = receipt(report["input"]["report"])
        if evidence != report["evidence"]:
            raise UsageError("The imported correction's review artifact changed; record its actual current review before continuing.", {})
    if implementation and not historical and previous and previous.get("fix_round", 0) and previous["fix_round"] >= plan["first_fix"]:
        report = previous.get("report")
        if not report or report.get("verdict") != "blocking":
            raise UsageError("Collect and record the preceding correction's actual blocking review before spending the next approved attempt.", {})
        evidence, _body = receipt(report["evidence"]["path"])
        if evidence != report["evidence"]:
            raise UsageError("The preceding review artifact changed after recording; collect and record the actual current review before continuing.", {})
    return plan


def dispatch_identity(task, role, agent, fix_round, paths_by_role, supplied=None, *, options=None):
    digest = hashlib.sha256()
    for value in (task, role, agent, fix_round, paths_by_role["common"], paths_by_role[role], options):
        digest.update(json.dumps(value, sort_keys=True).encode("utf-8"))
        digest.update(b"\0")
    for path in (paths_by_role["common"], paths_by_role[role]):
        try:
            content = Path(path).read_bytes()
        except OSError as exc:
            raise UsageError("Cannot fingerprint brief {}: {}. Restore it before dispatch.".format(path, exc), {}) from None
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    fingerprint = digest.hexdigest()
    identifier = text(supplied, "dispatch id") + ":" + role if supplied else fingerprint
    return identifier, fingerprint


def prior_dispatch(store, identifier, fingerprint):
    prior = next((row for row in store["dispatches"] if row["id"] == identifier), None)
    if prior is None:
        return None
    if prior["fingerprint"] != fingerprint:
        raise UsageError("Dispatch identity already names different inputs; inspect its outcome before assigning a new identity.", {})
    if prior["status"] in PENDING_STATUSES:
        raise UsageError("Dispatch {!r} has an uncertain outcome. Implementation is paused; inspect the worker and run `teamlead reconcile` with explicit evidence. Do not resend it.".format(identifier), {})
    return prior


def reserve(store, record, at):
    pending = [row for row in store["dispatches"] if row["status"] in PENDING_STATUSES
               and (row["agent"] == record["agent"] or row["task"] == record["task"]
                    and row["role"] in {"developer", "release"} and record["role"] in {"developer", "release"})]
    if pending:
        raise UsageError("Task has an unresolved dispatch {}; reconcile it before another assignment.".format(pending[0]["id"]), {})
    prior = next((row for row in store["dispatches"] if row["id"] == record["id"]), None)
    if prior:
        if prior["status"] != "not_sent" or prior["fingerprint"] != record["fingerprint"]:
            raise UsageError("Dispatch already exists; inspect its recorded result instead of sending again.", {})
        _event(store, at, "dispatch_transport_retry", record["task"], {"dispatch": prior["id"], "previous": dict(prior)})
        prior.update(status="reserved", report=None, result=None)
        prior.pop("reconciliation", None)
        item = prior
    else:
        item = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **record,
                "status": "reserved", "result": None, "report": None}
        store["dispatches"].append(item)
    _event(store, at, "dispatch_reserved", record["task"], {"dispatch": record["id"], "fix_round": record["fix_round"]})
    return item


def mark_sending(store, identifier, at, context):
    record = _item(store["dispatches"], identifier, "dispatch")
    record["status"] = "sending"
    record["context_before_send"] = context
    _event(store, at, "dispatch_sending", record["task"], {"dispatch": identifier, "context": context})


def finish_dispatch(store, identifier, result, assignment_index, at):
    record = _item(store["dispatches"], identifier, "dispatch")
    if "assignment_index" in record and record["assignment_index"] != assignment_index:
        record.setdefault("prior_assignment_indices", []).append(record["assignment_index"])
    record.update(status=result["status"], result={"schema_version": RECOVERY_SCHEMA_VERSION, **result}, assignment_index=assignment_index)
    _event(store, at, "dispatch_recorded", record["task"], {"dispatch": identifier, "status": result["status"], "assignment_index": assignment_index})


def abort_pre_send(store, identifier, at, reason):
    record = _item(store["dispatches"], identifier, "dispatch")
    if record["status"] == "reserved":
        record["status"] = "not_sent"
        _event(store, at, "dispatch_not_sent", record["task"], {"dispatch": identifier, "reason": reason})


def record_report(store, data, at):
    required = {"dispatch", "head_revision", "verdict", "review_mode", "reviewer", "report", "changed_paths"}
    if not isinstance(data, dict) or set(data) != required:
        raise UsageError("Report receipt requires dispatch, head_revision, verdict, review_mode, reviewer, report and changed_paths.", {})
    record = _item(store["dispatches"], data["dispatch"], "dispatch")
    if record["status"] != "applied" or record["role"] != "developer":
        raise UsageError("Record an implementation review only for its confirmed developer dispatch.", {})
    revision(data["head_revision"], "head_revision")
    text(data["reviewer"], "reviewer")
    if data["reviewer"] == record["agent"]:
        raise UsageError("The developer cannot independently review its own correction; collect the reviewer's report.", {})
    if not isinstance(data["verdict"], str) or not isinstance(data["review_mode"], str) or data["verdict"] not in {"blocking", "approved"} or data["review_mode"] not in {"full", "scoped"}:
        raise UsageError("Record the actual blocking/approved verdict and full/scoped review mode.", {})
    if data["verdict"] == "approved" and data["review_mode"] != "full":
        raise UsageError("A scoped recheck cannot approve release; collect full independent verification of the changed tip.", {})
    paths(data["changed_paths"], "changed_paths")
    if record.get("plan"):
        plan = _item(store["plans"], record["plan"], "correction plan")
        if any(not any(fnmatchcase(path, allowed) for allowed in plan["allowed_paths"]) for path in data["changed_paths"]):
            raise UsageError("The resulting diff exceeds the correction plan's scope; pause release for the operator's changed decision.", {})
    evidence, body = receipt(data["report"])
    if data["head_revision"] not in body:
        raise UsageError("The review report does not name the recorded full head SHA; collect the current-tip report before recording it.", {})
    result = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data, "evidence": evidence}
    if record.get("report") and record["report"] != result:
        _event(store, at, "review_superseded", record["task"], {"dispatch": record["id"], "previous": record["report"]})
    record["report"] = result
    _event(store, at, "review_recorded", record["task"], {"dispatch": record["id"], "receipt": result})
    return result


def authorize_context(store, assignments, data, at, observed_session):
    required = {"task", "assignment_index", "reason", "authorization", "evidence"}
    if not isinstance(data, dict) or set(data) != required:
        raise UsageError("Context recovery requires task, assignment_index, reason, authorization and evidence; preserve the original null record.", {})
    task_record(store, text(data["task"], "task"))
    text(data["reason"], "context replacement reason")
    authorization(data["authorization"])
    index = data["assignment_index"]
    if type(index) is not int or not 0 <= index < len(assignments):
        raise UsageError("Use the original assignment_index from `teamlead state`; do not replace history.", {})
    row = assignments[index]
    if row.get("task") != data["task"] or row.get("role") != "developer" or row.get("status") != "applied" or row.get("context_session") is not None:
        raise UsageError("This recovery covers a confirmed developer assignment whose original native-session proof is null.", {})
    latest = latest_assignment(assignments, task=data["task"], role="developer", status="applied")
    if latest is None or latest[0] != index:
        raise UsageError("That assignment is no longer this task's preceding developer attempt; use the current history.", {})
    evidence, _body = receipt(data["evidence"])
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data,
              "next_fix": (row.get("fix_round") or 0) + 1, "evidence_receipt": evidence,
              "observed_session": observed_session, "basis": "operator_authorized_fresh_handoff"}
    store["context_permissions"].append(record)
    _event(store, at, "context_replacement_authorized", data["task"], {"assignment_index": index, "reason": data["reason"], "authorization": data["authorization"]})
    return record


def fresh_transition(store, assignments, task, fix_round):
    """A workflow-cleared release is sufficient cause for the next fresh fix."""
    if task is None or fix_round is None:
        return None
    latest = latest_assignment(assignments, task=task, role="developer", status="applied")
    if latest is None or (latest[1].get("fix_round") or 0) + 1 != fix_round:
        return None
    index, developer = latest
    released = latest_assignment(assignments, task=task, role="release", agent=developer.get("agent"), status="applied")
    if released is not None and released[1].get("cleared") is True and assignment_after(assignments, released[0], index):
        return {"reason": "release_handoff", "previous_developer": index, "release_assignment": released[0]}
    hand = next((row for row in reversed(store["hand_clearances"]) if row["task"] == task
                 and row["previous_developer"] == index), None)
    if hand:
        return {"reason": "verified_hand_release_handoff", "previous_developer": index,
                "release_assignment": hand["assignment_index"], "clearance": hand["id"]}
    historical = next((row for row in reversed(store["historical_attempts"]) if row["task"] == task
                       and row["assignment_index"] == index), None)
    if historical:
        return {"reason": "historical_correction_handoff", "previous_developer": index,
                "historical_attempt": historical["id"], "continuity": "unproven"}
    from .role_clear import transition
    recovered = transition(store, assignments, task, fix_round, index)
    if recovered:
        return recovered
    permission = next((row for row in reversed(store["context_permissions"]) if row["task"] == task
                       and row["assignment_index"] == index and row["next_fix"] == fix_round), None)
    if permission:
        return {"reason": "authorized_context_recovery", "previous_developer": index,
                "permission_at": permission["at"], "authorization": permission["authorization"]}
    return None


def require_recovery_ready(live_info):
    if not isinstance(live_info, dict) or live_info.get("agent_status") not in {"idle", "done"}:
        raise UsageError("Recovery requires a live idle/done worker. Wait for working or blocked workers and restore unknown state; no input was sent.", {})


def recovery_agent(store, assignments, command, data):
    """Resolve the preserved worker identity before collecting live evidence."""
    if command in {"recover-context", "record-release-clear", "recover-role-clear"}:
        index = data.get("assignment_index")
        if type(index) is not int or not 0 <= index < len(assignments):
            raise UsageError("Use the original assignment_index from teamlead state; do not guess a worker identity.", {})
        return assignments[index]["agent"]
    return _item(store["dispatches"], data.get("dispatch"), "dispatch")["agent"]


def reconcile(store, assignments, data, at, live_info):
    required = {"dispatch", "outcome", "reason", "authorization", "evidence"}
    if not isinstance(data, dict) or set(data) != required or not isinstance(data.get("outcome"), str) or data["outcome"] not in {"applied", "not_sent"}:
        raise UsageError("Reconciliation requires dispatch, applied/not_sent outcome, reason, authorization and actual evidence. Idle alone proves neither outcome.", {})
    authorization(data["authorization"])
    text(data["reason"], "reconciliation reason")
    record = _item(store["dispatches"], data["dispatch"], "dispatch")
    if record["status"] not in PENDING_STATUSES:
        if record.get("reconciliation", {}).get("input") == data:
            return record
        raise UsageError("This dispatch already has a terminal outcome; do not change its attempt history.", {})
    require_recovery_ready(live_info)
    evidence, _body = receipt(data["evidence"])
    record["reconciliation"] = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, "input": data,
                                "evidence_receipt": evidence, "observed_state": live_info.get("agent_status"),
                                "observed_session": live_info.get("agent_session")}
    record["status"] = data["outcome"]
    _event(store, at, "dispatch_reconciled", record["task"], {"dispatch": record["id"], "reconciliation": record["reconciliation"]})
    return record


def validate_store(store, assignments):
    """Reject inconsistent recovery ledgers without erasing prior history.

    Reading validates recorded relationships, never fetches historical reports
    or treats an old pane observation as a live readiness check.
    """
    if not isinstance(store, dict) or type(store.get("schema_version")) is not int or store["schema_version"] != RECOVERY_STORE_VERSION:
        raise UsageError("Unsupported recovery schema; update the owner skill before using this ledger.", {})
    try:
        if not isinstance(store["tasks"], dict):
            raise UsageError("Recovery tasks must be an object; restore the owner-written ledger.", {})
        for name in ("checkpoints", "plans", "dispatches", "context_permissions", "events", "hand_clearances", "historical_attempts", "role_clearances", "delivery_recoveries"):
            if not isinstance(store[name], list):
                raise UsageError("Recovery {} must be an array; restore the owner-written ledger.".format(name), {})
            identifiers = []
            for row in store[name]:
                versions = {1, 2} if name == "delivery_recoveries" else {RECOVERY_SCHEMA_VERSION}
                if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] not in versions:
                    raise UsageError("A recovery record has an unsupported schema; update its owner.", {})
                text(row["at"], "record timestamp")
                text(row["task"], "record task")
                if "id" in row:
                    identifiers.append(text(row["id"], "record id"))
            if len(identifiers) != len(set(identifiers)):
                raise UsageError("Recovery identities are duplicated; preserve the original ledger and recover it through its owner.", {})
        for key, row in store["tasks"].items():
            if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] != RECOVERY_SCHEMA_VERSION or key != row.get("task"):
                raise UsageError("Task metadata does not match its recorded identity.", {})
            text(key, "task")
            text(row["at"], "task timestamp")
            revision(row["base_revision"], "task base")
            text(row["scope"], "task scope")
            paths(row["allowed_paths"], "task paths")
            authorization(row["authorization"])
        for row in store["checkpoints"]:
            task = task_record(store, row["task"])
            if row["base_revision"] != task["base_revision"] or positive(row["fix_round"], "checkpoint fix") < DEFAULT_FIX_LIMIT:
                raise UsageError("Checkpoint does not match the original base or exhausted budget.", {})
            for field in ("defect", "previous_attempts", "progress", "change_in_approach", "judge_agent"):
                text(row[field], field)
            validate_receipt(row["judge_evidence"])
        for row in store["plans"]:
            source = _item(store["checkpoints"], row["checkpoint"], "checkpoint")
            authorization(row["authorization"])
            paths(row["allowed_paths"], "plan paths")
            text(row["scope"], "plan scope")
            additional = positive(row["additional_fixes"], "plan budget")
            if row["task"] != source["task"] or row["base_revision"] != source["base_revision"] or row["first_fix"] != source["fix_round"] + 1 or row["last_fix"] != source["fix_round"] + additional:
                raise UsageError("Correction plan bounds do not match the authorized checkpoint.", {})
            if row.get("supersedes"):
                previous = _item(store["plans"][:store["plans"].index(row)], row["supersedes"], "superseded plan")
                if previous["task"] != row["task"] or previous["base_revision"] != row["base_revision"]:
                    raise UsageError("A changed decision cannot supersede another task or original base.", {})
        applied_slots = set()
        pending_workers = set()
        pending_tasks = set()
        for row in store["dispatches"]:
            for key in ("id", "fingerprint", "role", "agent"):
                text(row[key], key)
            if row["status"] not in DISPATCH_STATUSES:
                raise UsageError("Unknown dispatch status; recover through the owner without dropping the attempt.", {})
            if row["status"] in PENDING_STATUSES:
                if row["agent"] in pending_workers or row["role"] in {"developer", "release"} and row["task"] in pending_tasks:
                    raise UsageError("Concurrent pending dispatches overlap a worker or implementation task; preserve and reconcile their outcomes.", {})
                pending_workers.add(row["agent"])
                if row["role"] in {"developer", "release"}:
                    pending_tasks.add(row["task"])
            fix = row["fix_round"]
            if fix is not None:
                positive(fix, "dispatch fix")
            if fix is not None and fix > DEFAULT_FIX_LIMIT:
                plan = _item(store["plans"], row["plan"], "correction plan")
                if plan["task"] != row["task"] or not plan["first_fix"] <= fix <= plan["last_fix"]:
                    raise UsageError("A dispatch exceeds its recorded correction allowance.", {})
                work = row["work"]
                if work["base_revision"] != plan["base_revision"] or work["scope"] != plan["scope"] or any(not any(fnmatchcase(path, allowed) for allowed in plan["allowed_paths"]) for path in paths(work["paths"], "dispatch paths")):
                    raise UsageError("A dispatch exceeds its recorded correction scope.", {})
            if row["status"] == "applied":
                index = row["assignment_index"]
                if type(index) is not int or not 0 <= index < len(assignments):
                    raise UsageError("Dispatch has no matching assignment row; reconcile it without fabricating history.", {})
                assignment = assignments[index]
                if any(assignment.get(key) != row[key] for key in ("task", "role", "agent", "fix_round")) or assignment.get("status") != "applied":
                    raise UsageError("Dispatch outcome disagrees with its assignment row.", {})
                if not isinstance(row["result"], dict) or type(row["result"].get("schema_version")) is not int or row["result"]["schema_version"] != RECOVERY_SCHEMA_VERSION or any(row["result"].get(key) != row[key] for key in ("task", "role", "agent", "fix_round", "status")):
                    raise UsageError("The saved dispatch result does not match its confirmed outcome; recover it before retrying.", {})
                if row["role"] == "developer":
                    slot = (row["task"], fix)
                    if slot in applied_slots:
                        raise UsageError("A correction number was consumed twice; preserve the ledger and reconcile the duplicate.", {})
                    applied_slots.add(slot)
            report = row.get("report")
            if report is not None:
                if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != RECOVERY_SCHEMA_VERSION or report["dispatch"] != row["id"]:
                    raise UsageError("Review receipt has an unsupported schema or different dispatch; preserve it for owner recovery.", {})
                revision(report["head_revision"], "review head")
                validate_receipt(report["evidence"])
                if report["verdict"] not in {"blocking", "approved"} or report["review_mode"] not in {"full", "scoped"} or report["reviewer"] == row["agent"] or report["verdict"] == "approved" and report["review_mode"] != "full":
                    raise UsageError("Review receipt does not preserve independent full approval requirements.", {})
            if row.get("reconciliation") is not None:
                evidence = row["reconciliation"]
                if not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int or evidence["schema_version"] != RECOVERY_SCHEMA_VERSION:
                    raise UsageError("Reconciliation schema is unsupported; update the owner before reading its outcome.", {})
                validate_receipt(evidence["evidence_receipt"])
                authorization(evidence["input"]["authorization"])
        for index, row in enumerate(assignments):
            if (row.get("fix_round") or 0) > DEFAULT_FIX_LIMIT and not any(
                index in [dispatch.get("assignment_index"), *dispatch.get("prior_assignment_indices", [])]
                and all(dispatch[key] == row.get(key) for key in ("task", "role", "agent", "fix_round"))
                for dispatch in store["dispatches"]
            ) and not any(item["assignment_index"] == index for item in store["historical_attempts"]):
                raise UsageError("An extra correction lacks its owner-managed authorization and dispatch record.", {})
        for row in store["context_permissions"]:
            task_record(store, row["task"])
            authorization(row["authorization"])
            validate_receipt(row["evidence_receipt"])
            index = row["assignment_index"]
            if type(index) is not int or not 0 <= index < len(assignments) or assignments[index].get("task") != row["task"] or assignments[index].get("context_session") is not None or assignments[index].get("role") != "developer" or assignments[index].get("status") != "applied":
                raise UsageError("Context recovery does not refer to the preserved null-session assignment.", {})
            if row["next_fix"] != (assignments[index].get("fix_round") or 0) + 1:
                raise UsageError("Context recovery changed the cumulative fix count.", {})
        for sequence, row in enumerate(store["events"], 1):
            if row["sequence"] != sequence or not isinstance(row["details"], dict):
                raise UsageError("Recovery event history is malformed; preserve it for owner recovery.", {})
        # Imported records use these shared validators without a module-level cycle.
        from .historical import validate_history
        validate_history(store, assignments)
        from .role_clear import validate_history as validate_role_clear_history
        validate_role_clear_history(store, assignments)
        from .report_delivery import validate_recoveries
        validate_recoveries(store, assignments)
    except (KeyError, TypeError, ValueError) as exc:
        raise UsageError("Recovery ledger has missing or malformed fields ({}); restore its owner-written state without discarding history.".format(exc), {}) from None
    return store


def task_statuses(store, assignments):
    """Implementation state is separate from any concurrent audit worker."""
    result = {}
    tasks = set(store["tasks"]) | {row["task"] for row in store["dispatches"]} | {
        row["task"] for row in assignments if row.get("task") is not None}
    for task in tasks:
        count = confirmed_fix(assignments, task)
        pending = next((row for row in reversed(store["dispatches"]) if row["task"] == task
                        and row["role"] in {"developer", "release"} and row["status"] in PENDING_STATUSES), None)
        checkpoint_row = next((row for row in reversed(store["checkpoints"]) if row["task"] == task), None)
        plan = next((row for row in reversed(active_plans(store)) if row["task"] == task and row["last_fix"] > count), None)
        status = ("dispatch_outcome_unknown" if pending else "within_authorized_budget" if plan or count < DEFAULT_FIX_LIMIT
                  else "waiting_for_operator" if checkpoint_row and checkpoint_row["fix_round"] == count else "judge_checkpoint_required")
        result[task] = {"status": status, "paused_work": "implementation" if status != "within_authorized_budget" else None,
                        "confirmed_fixes": count, "plan": plan["id"] if plan else None,
                        "remaining_fixes": plan["last_fix"] - count if plan else max(0, DEFAULT_FIX_LIMIT - count)}
    return result
