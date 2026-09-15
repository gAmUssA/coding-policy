"""Owner-managed task recovery, bounded approval, and dispatch accounting.

This module owns the recovery section of teamlead's state file. Mutations add
audit events; authorization and original assignment rows are never rewritten.
The CLI serializes access and saves before any dispatch side effect. A send
whose outcome is unknown holds its attempt number until explicit recovery.
"""

import hashlib
import json
import re
from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from .errors import UsageError
from .chronology import assignment_after, latest_assignment, timestamp


RECOVERY_SCHEMA_VERSION = 1
#: Store version 6 adds the `brief_identity`, `refusal` and `refusal_move`
#: dispatch fields and the `refusal_authorizations` collection (#399).
#: Version 7 adds the dispatch's send-time `provider`, so a config edit
#: between the send and `record-refusal` cannot re-attribute the refusal
#: (#403). An older store carrying any of them is unowned newer data and is
#: refused; a clean one is stamped and given the empty collection
#: (rules/stateful-artifacts.md). Version 8 adds the `diagnoses` collection
#: (#407). Version 9 adds explicit digest-bound legacy ruling recovery receipts.
RECOVERY_STORE_VERSION = 9
REFUSAL_FIELDS = frozenset({"brief_identity", "refusal", "refusal_move", "provider"})
SPECIALIST_DISPATCH_VERSION = 2
#: Checkpoint record version. 1 carries a mandatory pinned-judge ruling; 2
#: makes it optional. Version-1 rows keep their judge evidence and are never
#: rewritten. The checkpoint now records the exhaustion the diagnosis brief is
#: built from (rules/agent-team-operation.md Judge Seat).
#: The version `checkpoint` writes. A cited ruling at this version carries the
#: `requested_by` receipt for the operator request it answers (#400).
OPERATOR_CHECKPOINT_VERSION = 3
#: Versions the reader still accepts. A version-2 row predates the receipt and
#: is never stamped into a shape whose request nobody recorded
#: (rules/stateful-artifacts.md Migration Policy).
CHECKPOINT_VERSIONS = frozenset({2, OPERATOR_CHECKPOINT_VERSION})
#: What a version-1 row upgrades into: its ruling was mandatory there and
#: optional at 2, so the stamp alone is the whole upgrade.
MIGRATED_CHECKPOINT_VERSION = 2
DISPATCH_METADATA_FIELDS = frozenset({"requirements", "reviewer_scope"})
#: The judge's diagnosis remedies, descending. A task's next diagnosis sits
#: below its last, or repeats that rung once against recorded progress, and
#: `stop` is terminal, so a task takes at most five and cannot loop
#: (rules/agent-team-operation.md Judge Seat).
DIAGNOSIS_LADDER = ("continue", "restructure", "stop")
#: Diagnosis record version. 1 recorded the remedy alone. 2 adds `reissue` --
#: whether this diagnosis repeats its predecessor's rung -- and
#: `investigator_report`, the assessment the judge ruled on (#415).
DIAGNOSIS_RECORD_VERSION = 2
DEFAULT_FIX_LIMIT = 5
#: The most attempts one remedy may buy, in developer attempts. A remedy that
#: needs more than the task's own original allowance is not a bounded
#: correction; the judge takes the next rung instead (#415).
DIAGNOSIS_BOUND_CEILING = DEFAULT_FIX_LIMIT
PENDING_STATUSES = frozenset({"reserved", "sending", "sent_but_not_started"})
DISPATCH_STATUSES = PENDING_STATUSES | {"applied", "not_sent"}
#: The wait-report reason a refusal receipt must carry (see wait-report.sh
#: exit 5). A terminal provider refusal is recorded against the applied
#: dispatch it stopped, keyed by task, role and fix round: the brief that
#: moves to another provider carries a fresh report path, so its bytes never
#: match, while a reworded brief on the same key must still meet the gate.
REFUSAL_REASON = "terminal_provider_refusal"
#: Independent refusals of one (task, role, fix_round) that stop the line.
#: One refusal permits one move of the unchanged brief to another provider;
#: the second refusal is information the operator did not have (#399).
REFUSAL_LIMIT = 2
#: Dispatch fields version 6 already owned; version 7 adds `provider` alone.
ALLOWED_AT_6 = frozenset({"brief_identity", "refusal", "refusal_move"})
SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def empty_recovery():
    return {"schema_version": RECOVERY_STORE_VERSION, "tasks": {}, "checkpoints": [],
            "plans": [], "dispatches": [], "context_permissions": [], "events": [],
            "hand_clearances": [], "historical_attempts": [], "role_clearances": [], "delivery_recoveries": [],
            "refusal_authorizations": [], "diagnoses": [], "legacy_ruling_recoveries": []}


def _migrate_checkpoints(store):
    """Upgrade older checkpoints in place, preserving their evidence.

    Version 1 required the pinned judge's ruling and version 2 makes it
    optional, so every version-1 row is already a valid version-2 row.
    Version 3 adds `requested_by`, the receipt for the operator request a
    cited ruling answers (#400). Neither older version recorded that request,
    and a migration never invents one: an older row keeps its ruling and
    carries no receipt, which the reader accepts for those rows alone
    (rules/stateful-artifacts.md Migration Policy).

    A version-1 row missing the ruling it was required to carry is refused
    rather than stamped into a shape where the pair is optional. A row
    carrying `requested_by` at an older version is unowned newer data and is
    refused rather than stamped.
    """
    rows = store.get("checkpoints")
    if not isinstance(rows, list):
        return False
    migrated = False
    for row in rows:
        if not isinstance(row, dict) or row.get("schema_version") != 1:
            continue
        if "judge_agent" not in row or "judge_evidence" not in row:
            raise UsageError("An older checkpoint is missing the ruling evidence its version required; restore the owner-written ledger.", {})
        if "requested_by" in row:
            raise UsageError("An older checkpoint carries an operator-request receipt its version never wrote; preserve the ledger for owner recovery.", {})
        row["schema_version"] = MIGRATED_CHECKPOINT_VERSION
        migrated = True
    return migrated


def _migrate_diagnoses(store):
    """Stamp version-1 diagnosis rows with the fields version 2 records.

    A version-1 row predates both additions, so its defaults are the facts it
    already carried: it repeated no rung, and it cited no investigator report
    (rules/stateful-artifacts.md Migration Policy). A version-1 row already
    carrying either field is unowned newer data and is refused rather than
    stamped, the way `migrate_store` refuses every other newer record: keeping
    the present value would let a `reissue: true` or an investigator binding
    reach the validator through a migration that never wrote it.
    """
    rows = store.get("diagnoses")
    if not isinstance(rows, list):
        return False
    migrated = False
    for row in rows:
        if not isinstance(row, dict) or row.get("schema_version") != 1:
            continue
        if "reissue" in row or "investigator_report" in row:
            raise UsageError("An older diagnosis carries newer recorded fields; preserve the ledger for owner recovery.", {})
        row["schema_version"] = DIAGNOSIS_RECORD_VERSION
        row["reissue"] = False
        row["investigator_report"] = None
        migrated = True
    return migrated


def _refuse_unowned_legacy(store, version):
    """Refuse a legacy document holding records its own version never wrote.

    Read-only, and it runs before any row is stamped: a document rejected here
    used to be rejected with its version-1 checkpoints and diagnoses already
    upgraded in memory (#400). Returns the record names this version adds.
    """
    dispatches = store.get("dispatches")
    if not isinstance(dispatches, list):
        raise UsageError("Older recovery requires a dispatches array; restore the original owner-written store.", {})
    for row in dispatches:
        allowed = ALLOWED_AT_6 if version == 6 else REFUSAL_FIELDS if version >= 7 else frozenset()
        if not isinstance(row, dict) or REFUSAL_FIELDS.intersection(row) - allowed:
            raise UsageError("Older recovery contains unowned newer refusal records; preserve it for owner recovery.", {})
        if version >= 5:
            continue
        if (type(row.get("schema_version")) is not int
                or row["schema_version"] != RECOVERY_SCHEMA_VERSION or DISPATCH_METADATA_FIELDS.intersection(row)):
            raise UsageError("Older recovery contains unowned newer dispatch metadata; preserve it for owner recovery.", {})
        result = row.get("result")
        if result is not None and (not isinstance(result, dict)
                or type(result.get("schema_version")) is not int
                or result["schema_version"] != RECOVERY_SCHEMA_VERSION or DISPATCH_METADATA_FIELDS.intersection(result)):
            raise UsageError("Older recovery contains unowned newer dispatch results; preserve it for owner recovery.", {})
    if version == 3:
        deliveries = store.get("delivery_recoveries")
        if not isinstance(deliveries, list):
            raise UsageError("Older recovery requires a delivery_recoveries array; restore the original owner-written store.", {})
        if any(not isinstance(row, dict) or row.get("schema_version") != 1 for row in deliveries):
            raise UsageError("Older recovery contains unowned newer delivery records; preserve it for owner recovery.", {})
    added = ["legacy_ruling_recoveries"]
    if version < 8:
        added.append("diagnoses")
    if version < 6:
        added.append("refusal_authorizations")
    if version < 3:
        added.extend(["role_clearances", "delivery_recoveries"])
    if version == 1:
        added.extend(["hand_clearances", "historical_attempts"])
    if any(name in store for name in added):
        raise UsageError("Older recovery contains unowned newer records; preserve it for owner recovery.", {})
    return added


def migrate_store(store):
    """Upgrade the enclosing recovery document and its checkpoint records."""
    if not isinstance(store, dict) or type(store.get("schema_version")) is not int:
        return False
    version = store["schema_version"]
    legacy = version in {1, 2, 3, 4, 5, 6, 7, 8}
    added = _refuse_unowned_legacy(store, version) if legacy else None
    # Both run: `or` would skip the second whenever the first reported work,
    # leaving version-1 diagnoses for a validator that accepts only version 2.
    checkpoints = _migrate_checkpoints(store)
    migrated = _migrate_diagnoses(store) or checkpoints
    if not legacy:
        return migrated
    assert added is not None
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


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_legacy_ruling_recoveries(store):
    """Validate digest-bound receipts against the original checkpoint rows."""
    records = store.get("legacy_ruling_recoveries")
    if not isinstance(records, list):
        raise UsageError("Legacy ruling recoveries must be an array; restore owner-written state.", {})
    checkpoints = {row["id"]: row for row in store["checkpoints"]}
    recovered = set()
    identities = set()
    for record in records:
        required = {"schema_version", "id", "task", "authorization", "backup", "checkpoints", "grants_future_attempts", "at"}
        if (not isinstance(record, dict) or set(record) != required
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["grants_future_attempts"] is not False):
            raise UsageError("Invalid legacy ruling recovery; restore its original owner receipt.", {})
        key = text(record["id"], "recovery identity")
        if key in identities:
            raise UsageError("Duplicate legacy recovery identity; restore its original owner receipt.", {})
        identities.add(key)
        text(record["at"], "recovery timestamp")
        authorization(record["authorization"])
        validate_receipt(record["backup"])
        rows = record["checkpoints"]
        if not isinstance(rows, dict) or len(rows) < 2:
            raise UsageError("A legacy recovery must bind multiple original citations; restore its receipt.", {})
        actual = {item["id"] for item in store["checkpoints"] if item["task"] == record["task"] and "judge_evidence" in item}
        if actual != set(rows):
            raise UsageError("Recovered task citations changed; review the original ledger before continuing.", {})
        for identifier, expected in rows.items():
            row = checkpoints.get(identifier)
            if (row is None or row["task"] != record["task"] or row["schema_version"] != 2
                    or "requested_by" in row or "judge_evidence" not in row
                    or expected != canonical_digest(row) or identifier in recovered):
                raise UsageError("Recovered legacy citations changed or overlap; restore the original checkpoints.", {})
            recovered.add(identifier)
    return recovered


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
    """Pause implementation at an exhausted allowance and hand it to the operator.

    The allowance boundary is a budget event, not a dispute: only the operator
    can grant more attempts, so the checkpoint used to buy an expensive ruling
    ahead of a decision the judge cannot make. One task spent 16 judge rulings
    across 19 fix rounds that way, on the same window its developer and every
    reviewer drew from. A ruling is still recordable here -- `judge_report` is
    optional, and a supplied one is held to the same completed-ruling contract
    as before.
    """
    required = {"id", "task", "defect", "previous_attempts", "progress", "change_in_approach"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"judge_report", "requested_by"}:
        raise UsageError("Checkpoint requires id, task, defect, previous_attempts, progress and change_in_approach, and allows an optional judge_report with the requested_by receipt that authorized it; describe the concrete remaining work.", {})
    for key in set(data) - {"requested_by"}:
        text(data[key], key)
    if "requested_by" in data and "judge_report" not in data:
        raise UsageError("requested_by records the operator request a cited ruling answers; record the checkpoint without it when no ruling is cited.", {})
    task = task_record(store, data["task"])
    count = confirmed_fix(assignments, data["task"])
    if count < DEFAULT_FIX_LIMIT:
        raise UsageError("The normal correction budget is not exhausted; continue within it.", {})
    if any(row["task"] == data["task"] and row["status"] in PENDING_STATUSES for row in store["dispatches"]):
        raise UsageError("A dispatch outcome is still unknown; reconcile it before proposing another correction budget.", {})
    ruling = {}
    if "judge_report" in data:
        # One ruling per task, never one per allowance boundary: a re-granted
        # budget exhausts too, and citing a ruling at each boundary is the
        # per-round toll this routing removed
        # (rules/agent-team-operation.md Judge Seat).
        if any(row["task"] == data["task"] and row["id"] != data["id"] and "judge_evidence" in row
               for row in store["checkpoints"]):
            raise UsageError("This task already records an operator-requested ruling; the operator grants at most one per task. Record this checkpoint without judge_report.", {})
        developer = latest_assignment(assignments, task=data["task"], role="developer", status="applied")
        judge = latest_assignment(assignments, task=data["task"], role="judge", agent=judge_agent, status="applied")
        if not judge_agent or developer is None or judge is None or not assignment_after(assignments, judge[0], developer[0]):
            raise UsageError("A cited judge report needs the configured pinned judge's completed assignment after the latest developer attempt; omit judge_report to send the exhausted allowance straight to the operator.", {})
        # The ruling is the operator's to request, and "operator-requested" was
        # the ledger's word for a request nothing in it recorded (#400). The
        # receipt is the same source/quote shape every other operator decision
        # carries, and it is required of NEW records alone: an older row was
        # written before the field existed, so replaying its original payload
        # is an already-processed request, not a missing receipt
        # (rules/file-hygiene.md Idempotency).
        replayed = next((row for row in store["checkpoints"] if row["id"] == data["id"]), None)
        legacy_replay = (replayed is not None
                         and replayed.get("schema_version", OPERATOR_CHECKPOINT_VERSION) < OPERATOR_CHECKPOINT_VERSION)
        if "requested_by" not in data and not legacy_replay:
            raise UsageError("A cited ruling is the operator's to request: record their request as requested_by (source and quote), or record the checkpoint without judge_report.", {})
        if "requested_by" in data:
            authorization(data["requested_by"])
        evidence, body = receipt(data["judge_report"])
        if not re.search(r"^RULING: (?:uphold A|uphold B|amend)(?:\s|$)", body, re.MULTILINE) or not re.search(r"^ACTION: \S", body, re.MULTILINE):
            raise UsageError("The judge report must contain its completed RULING and ACTION; a blocked judge requires the operator's answer first.", {})
        ruling = {"judge_evidence": evidence, "judge_agent": judge_agent}
    record = {"schema_version": OPERATOR_CHECKPOINT_VERSION, "at": at, **data, "fix_round": count,
              "base_revision": task["base_revision"], **ruling}
    prior = next((row for row in store["checkpoints"] if row["id"] == data["id"]), None)
    if prior:
        # Identity and evidence decide a replay, never the writer's version:
        # comparing versions reported an unmigrated version-1 row as different
        # evidence, so re-running an already-recorded checkpoint failed (#396).
        compared = (set(prior) | set(record)) - {"at", "schema_version"}
        # A row written before version 3 carries no request receipt, so the
        # field this version adds cannot make a replay read as changed
        # evidence (rules/stateful-artifacts.md Migration Policy).
        if prior.get("schema_version", OPERATOR_CHECKPOINT_VERSION) < OPERATOR_CHECKPOINT_VERSION:
            compared -= {"requested_by"}
            record.pop("requested_by", None)
        if any(prior.get(key) != record.get(key) for key in compared):
            raise UsageError("Checkpoint identity already describes different evidence; record a new checkpoint without rewriting the old one.", {})
        return prior
    store["checkpoints"].append(record)
    _event(store, at, "awaiting_diagnosis", data["task"], {"checkpoint": data["id"], "paused_work": "implementation", "defect": data["defect"]})
    return record


def investigated_after(assignments, row, task, developer_index):
    """True when `row` assesses an investigator consultation of `task` that
    followed the developer attempt at `developer_index`."""
    return (row["task"] == task and row["role"] == "investigator"
            and assignment_after(assignments, row["assignment_index"], developer_index))


#: The judge's two modes (`rules/agent-team-operation.md` Judge Seat). A
#: dispatch declares which it is for, so a gate can tell a diagnosis -- which
#: rules on the investigator's assessment and follows the remedy ladder -- from
#: an adjudication of a contested verdict, which does neither (#425).
JUDGE_MODES = ("adjudication", "diagnosis")


def require_judge_mode(mode):
    """A seated judge declares which mode it is for, never a default.

    Defaulting would pick one of the two gates for the lead: an undeclared
    dispatch would either skip the stop refusal a diagnosis owes, or apply it
    to an adjudication the judge still owes (#425).
    """
    if mode is None:
        raise UsageError("A judge seat declares what it is for: pass --judge-mode {}. Adjudication rules on a contested verdict; diagnosis rules on the investigator's assessment at an exhausted allowance.".format(" | ".join(JUDGE_MODES)), {})
    if mode not in JUDGE_MODES:
        raise UsageError("A judge dispatch declares its mode as one of {}; an undeclared mode is refused rather than defaulted.".format(" | ".join(JUDGE_MODES)), {"mode": mode})
    return mode


def require_investigation_before_judge(store, assignments, task, investigations, mode=None):
    """Refuse a judge dispatch the seat cannot answer.

    The judge rules on the investigator's causal assessment (#408), so the
    expensive seat is never spent before that assessment exists. A judge
    dispatched for an ordinary dispute is untouched: the assessment gate
    applies only to a DIAGNOSIS at an exhausted allowance with no unspent
    bound.

    `mode` is the dispatch's declared judge mode. With it, a diagnosis for a
    task whose ladder reached `stop` is refused before the round runs: `stop`
    ends implementation and the ladder, and the one operator-requested ruling a
    checkpoint may cite is bounded per task, so there is nothing such a round
    could record. An adjudication is untouched on the same task -- the judge
    still owes a contested reviewer or tester verdict, a lead override, or a
    disputed bot finding during the release of the clean scope. An operator
    plan authorized over the `stop` lifts the refusal, since the correction it
    authorizes is ordinary work.

    Without a declared mode the stop refusal is not read: the gate cannot tell
    the two apart, and refusing both would refuse an adjudication the judge
    owes (#400).
    """
    if mode is not None and mode not in JUDGE_MODES:
        raise UsageError("A judge dispatch declares its mode as one of {}; an undeclared mode is refused rather than defaulted.".format(" | ".join(JUDGE_MODES)), {"mode": mode})
    if not task or task not in store["tasks"]:
        return None
    if mode == "diagnosis" and any(row["remedy"] == "stop" for row in diagnoses_for(store, task)):
        if not any(row["task"] == task for row in active_plans(store)):
            raise UsageError("Task {} is diagnosed `stop` with no plan authorized over it: implementation has ended and the ladder is spent, so a diagnosis round records nothing. Ship what is clean and track the remainder, or record the operator's plan over this remedy first.".format(task), {})
    if mode == "adjudication":
        return None
    count = confirmed_fix(assignments, task)
    if count < DEFAULT_FIX_LIMIT:
        return None
    if any(row["task"] == task and row["last_fix"] > count for row in active_plans(store)):
        return None
    developer = latest_assignment(assignments, task=task, role="developer", status="applied")
    if developer is None:
        return None
    if not any(investigated_after(assignments, row, task, developer[0]) for row in investigations):
        raise UsageError("Task {} sits at an exhausted allowance: consult the investigator and assess its report before dispatching the judge, which rules on that assessment.".format(task), {})
    return None


def diagnoses_for(store, task):
    return [row for row in store["diagnoses"] if row["task"] == task]


def _remedy_options(store, task):
    """The rungs this task's next diagnosis may take: `(floor, repeat)`.

    `floor` is the lowest rung still available, or None once `stop` is spent.
    `repeat` is the rung this task may reissue once, or None. A monotonic
    ladder guarantees termination but conflates "this remedy was wrong" with
    "this remedy needed another increment": a restructure routinely surfaces
    work the first pass could not see, and consuming the rung stranded that
    correct diagnosis at `stop` (#415). One reissue per rung keeps termination
    -- at most five diagnoses, `stop` still terminal -- and it costs the judge
    the recorded progress its `PROGRESS` line carries.
    """
    prior = diagnoses_for(store, task)
    if not prior:
        return 0, None
    last = prior[-1]
    index = DIAGNOSIS_LADDER.index(last["remedy"])
    floor = index + 1 if index + 1 < len(DIAGNOSIS_LADDER) else None
    repeat = None if last["remedy"] == "stop" or last.get("reissue") else index
    return floor, repeat


def applied_judge_dispatch(store, assignments, task, judge_agent):
    """The applied judge dispatch whose report a diagnosis for `task` cites.

    `diagnose` rules on the pinned judge's completed assignment after the
    latest developer attempt, so the enrollment binding its report is the one
    for THAT dispatch. Resolving by newest-for-task-and-agent instead let an
    older enrollment stand in when the dispatch persisted and its enrollment or
    refinement then failed (#412). None when no such dispatch is on record --
    the caller has no enrollment to offer, and `diagnose` refuses.
    """
    developer = latest_assignment(assignments, task=task, role="developer", status="applied")
    judge = latest_assignment(assignments, task=task, role="judge", agent=judge_agent, status="applied")
    if developer is None or judge is None or not assignment_after(assignments, judge[0], developer[0]):
        return None
    return next((row for row in store["dispatches"]
                 if row.get("assignment_index") == judge[0] and row.get("role") == "judge"
                 and row.get("task") == task and row.get("agent") == judge_agent
                 and row.get("status") == "applied"), None)


def diagnose(store, assignments, data, at, judge_agent, enrolled_report, supervised, investigations=()):
    """Record the judge's diagnosis of a fix loop that did not converge.

    An exhausted allowance is a diagnostic question, not a budget prompt: more
    attempts at a failing approach reproduce it, and the operator holds none of
    the inputs the number needs (#407). The pinned judge reads the rounds and
    returns a remedy. `continue` and `restructure` carry a bound, recorded here
    as the correction plan the ordinary allowance machinery already enforces,
    with the ruling as its authorization. `stop` is terminal: it ships what is
    clean and the remainder is tracked.

    A dispatch marked `applied` proves the send, never the delivery. Every
    team round is supervised (`rules/agent-team-operation.md` Fleet
    Supervision), so when the lead is bound the cited report must be the one
    supervision enrolled for the pinned judge, and a bound lead with no such
    enrollment has no diagnosis to record. `supervised` and `enrolled_report`
    are the caller's reading of that binding.

    `investigations` are the lead's assessed specialist consultations. The
    judge rules on a prepared causal assessment rather than investigating from
    scratch: the investigator's profile is written for "unclear causality or
    repeated unsuccessful fixes", and it is the cheaper seat (#408). One for
    this task after the latest developer attempt is required.

    Re-entry moves down `DIAGNOSIS_LADDER`, or repeats one rung once against a
    recorded `PROGRESS` line, so a remedy that produced nothing is never
    reissued and a task takes at most five diagnoses. That holds for a
    supersession too: a changed scope or an operator override re-enters before
    the bound is spent, naming the plan it replaces.

    The report's `ASSESSMENT` line names the investigator report the diagnosis
    ruled on, and that report is bound into the record the way supervision's
    enrollment binds the judge's own.
    """
    required = {"id", "task", "checkpoint", "judge_report", "scope", "allowed_paths"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"supersedes", "authorization"}:
        raise UsageError("Diagnosis requires id, task, checkpoint, judge_report, scope and allowed_paths, and allows an optional supersedes with authorization.", {})
    if "authorization" in data and "supersedes" not in data:
        raise UsageError("An operator override accompanies the supersedes it authorizes; record the plan it replaces.", {})
    if "supersedes" in data:
        text(data["supersedes"], "supersedes")
    for key in ("id", "task", "checkpoint", "judge_report", "scope"):
        text(data[key], key)
    paths(data["allowed_paths"], "allowed_paths")
    prior = next((row for row in store["diagnoses"] if row["id"] == data["id"]), None)
    if prior is not None:
        # Replay on identical inputs, decided before the state gates: the
        # bound this diagnosis recorded is spent by the time it is re-run.
        same = (prior["task"] == data["task"] and prior["checkpoint"] == data["checkpoint"]
                and prior["scope"] == data["scope"] and prior["allowed_paths"] == data["allowed_paths"]
                and prior.get("supersedes") == data.get("supersedes")
                and prior.get("authorization") == data.get("authorization")
                and prior["judge_evidence"] == receipt(data["judge_report"])[0])
        if not same:
            raise UsageError("Diagnosis identity already describes a different remedy; record a new diagnosis without rewriting the old one.", {})
        return prior
    task = task_record(store, data["task"])
    source = _item(store["checkpoints"], data["checkpoint"], "checkpoint")
    if source["task"] != data["task"]:
        raise UsageError("The cited checkpoint belongs to another task; record this task's own exhausted-allowance checkpoint first.", {})
    count = confirmed_fix(assignments, data["task"])
    if source["fix_round"] != count:
        raise UsageError("Checkpoint {} records fix round {}, and this task stands at {}; record the exhaustion this diagnosis answers.".format(
            data["checkpoint"], source["fix_round"], count), {})
    floor, repeat = _remedy_options(store, data["task"])
    if floor is None and repeat is None:
        raise UsageError("This task's diagnosis reached `stop`, which is terminal; ship what is clean and track the remainder rather than diagnosing again.", {})
    if count < DEFAULT_FIX_LIMIT:
        raise UsageError("The normal correction budget is not exhausted; continue within it.", {})
    # An unspent bound normally holds: the remedy has not had its attempts. A
    # changed scope or an operator override is the exception Fix Loops names,
    # and it supersedes that plan explicitly rather than shadowing it.
    active = next((row for row in active_plans(store) if row["task"] == data["task"] and row["last_fix"] > count), None)
    if data.get("supersedes"):
        if active is None or data["supersedes"] != active["id"]:
            raise UsageError("Supersedes must name this task's current unexhausted plan; inspect teamlead status before recording the changed decision.", {})
        # Re-entry before the bound is spent is for a changed scope or an
        # operator override; neither is proved by naming the plan alone.
        changed = data["scope"] != active["scope"] or data["allowed_paths"] != active["allowed_paths"]
        if not changed and "authorization" not in data:
            raise UsageError("An early re-entry needs the change it claims: a scope or path change against plan {}, or the operator's recorded override in authorization.".format(active["id"]), {})
        if "authorization" in data:
            authorization(data["authorization"])
    elif active is not None:
        raise UsageError("This task still has unspent attempts under plan {}; diagnose again once that bound is exhausted, or name it in supersedes for a changed scope or an operator override.".format(active["id"]), {})
    if any(row["task"] == data["task"] and row["status"] in PENDING_STATUSES for row in store["dispatches"]):
        raise UsageError("A dispatch outcome is still unknown; reconcile it before diagnosing the loop.", {})
    developer = latest_assignment(assignments, task=data["task"], role="developer", status="applied")
    judge = latest_assignment(assignments, task=data["task"], role="judge", agent=judge_agent, status="applied")
    if not judge_agent or developer is None or judge is None or not assignment_after(assignments, judge[0], developer[0]):
        raise UsageError("A diagnosis needs the configured pinned judge's completed assignment after the latest developer attempt.", {})
    # After the attempt it explains and assessed before the judge that rules on
    # it. The assessment time is what matters: an investigator dispatched early
    # and assessed after the judge finished is not what the judge read (#408).
    judge_at = timestamp(assignments[judge[0]].get("at"), "Judge assignment chronology")
    consulted = [row for row in investigations
                 if investigated_after(assignments, row, data["task"], developer[0])
                 and timestamp(row["at"], "Investigator assessment chronology") < judge_at]
    if not consulted:
        raise UsageError("A diagnosis rules on a prepared causal assessment: record an assessed investigator consultation for task {} after its latest developer attempt, assessed before the judge dispatch you cite.".format(data["task"]), {})
    if supervised:
        if not isinstance(enrolled_report, str) or not enrolled_report.strip():
            raise UsageError("This lead is bound, and no supervision enrollment binds a report to the pinned judge on task {}; dispatch the diagnosis through the bound round before recording it.".format(data["task"]), {})
        if str(Path(data["judge_report"]).resolve()) != str(Path(enrolled_report).resolve()):
            raise UsageError("The cited report is not the one supervision enrolled for this judge dispatch ({}); cite the delivered report.".format(enrolled_report), {})
    evidence, body = receipt(data["judge_report"])
    remedy_line = re.search(r"^REMEDY:[ \t]*(\S+)(.*)$", body, re.MULTILINE)
    bound_line = re.search(r"^BOUND:[ \t]*(\S+)(.*)$", body, re.MULTILINE)
    assessment_line = re.search(r"^ASSESSMENT:[ \t]*(\S.*)$", body, re.MULTILINE)
    if (not remedy_line or not bound_line or not assessment_line or remedy_line.group(1) not in DIAGNOSIS_LADDER
            or not re.search(r"^DIAGNOSIS: \S", body, re.MULTILINE)
            or not re.search(r"^EVIDENCE: \S", body, re.MULTILINE)
            or not re.search(r"^UNVERIFIED: \S", body, re.MULTILINE)):
        raise UsageError("The judge report must carry DIAGNOSIS, REMEDY ({}), BOUND, ASSESSMENT, EVIDENCE and UNVERIFIED.".format(" | ".join(DIAGNOSIS_LADDER)), {})
    # The gate above proves an assessed consultation exists and is ordered
    # before the judge; this one proves the diagnosis consumed it. The judge's
    # own report is bound to the enrollment supervision made, and the report it
    # ruled on gets the same binding rather than none (#415).
    cited = assessment_line.group(1).strip()
    assessment = next((row for row in consulted
                       if str(Path(row["report"]).resolve()) == str(Path(cited).resolve())), None)
    if assessment is None:
        raise UsageError("ASSESSMENT names {}, which is not an assessed investigator report for task {} after its latest developer attempt; cite the report the diagnosis ruled on.".format(cited, data["task"]), {})
    # The saved receipt is a last-seen snapshot, never authority
    # (rules/stateful-artifacts.md Hints, Not Authority): a report deleted or
    # rewritten since its assessment would otherwise authorize a correction
    # plan on evidence nobody holds any more.
    current, _assessed = receipt(assessment["report"])
    if current != assessment["report_evidence"]:
        raise UsageError("The investigator report {} changed since its assessment; restore the assessed bytes or record a fresh assessed consultation before diagnosing.".format(assessment["report"]), {})
    if re.search(r"^(?:RULING|ACTION):", body, re.MULTILINE):
        raise UsageError("This report carries an adjudication's RULING or ACTION; a diagnosis carries neither. Dispatch the diagnosis brief and cite its report.", {})
    remedy = remedy_line.group(1)
    if not remedy_line.group(2).strip(" \t-—"):
        raise UsageError("REMEDY names its remedy and what it means: the rounds for continue, the structural change for restructure, what ships and what is tracked for stop.", {})
    index = DIAGNOSIS_LADDER.index(remedy)
    reissue = index == repeat
    if not reissue and (floor is None or index < floor):
        raise UsageError("This task's next diagnosis may not sit above {}; the ladder never runs backwards, and a rung already reissued is spent.".format(
            DIAGNOSIS_LADDER[floor] if floor is not None else DIAGNOSIS_LADDER[-1]), {})
    if reissue and not re.search(r"^PROGRESS: \S", body, re.MULTILINE):
        raise UsageError("Reissuing {} needs its PROGRESS line: a remedy that produced no progress is never reissued, and the ladder's next rung answers it instead.".format(remedy), {})
    bound = None
    if remedy == "stop":
        if bound_line.group(1).lower() != "none":
            raise UsageError("A stop remedy allows no attempts; its BOUND is `none`.", {})
    else:
        raw = bound_line.group(1)
        if not raw.isdigit() or int(raw) < 1:
            raise UsageError("A {} remedy needs a positive BOUND naming the developer attempts it allows.".format(remedy), {})
        bound = int(raw)
        if bound > DIAGNOSIS_BOUND_CEILING:
            raise UsageError("BOUND {} exceeds the {}-attempt ceiling one remedy may buy; a correction needing more than the task's own allowance takes the next rung instead.".format(bound, DIAGNOSIS_BOUND_CEILING), {})
        if not bound_line.group(2).strip(" \t-\u2014"):
            raise UsageError("BOUND states the developer attempts and justifies the number against the evidence the diagnosis cites.", {})
    record = {"schema_version": DIAGNOSIS_RECORD_VERSION, "at": at, "id": data["id"], "task": data["task"],
              "checkpoint": data["checkpoint"], "fix_round": count, "base_revision": task["base_revision"],
              "remedy": remedy, "bound": bound, "reissue": reissue,
              "judge_agent": judge_agent, "judge_evidence": evidence,
              "investigator_report": {"schema_version": DIAGNOSIS_RECORD_VERSION,
                                      "report": assessment["report"], "evidence": assessment["report_evidence"]},
              "scope": data["scope"], "allowed_paths": data["allowed_paths"],
              "supersedes": data.get("supersedes"), "authorization": data.get("authorization"),
              "plan": None if remedy == "stop" else data["id"] + ":plan"}
    if record["plan"] is not None:
        assert bound is not None
        ruling = {"source": data["judge_report"],
                  "quote": ("REMEDY: " + remedy + remedy_line.group(2)).strip()}
        plan = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, "id": record["plan"],
                "task": data["task"], "checkpoint": data["checkpoint"], "scope": data["scope"],
                "allowed_paths": data["allowed_paths"], "additional_fixes": bound,
                "authorization": ruling, "base_revision": task["base_revision"],
                "first_fix": count + 1, "last_fix": count + bound}
        if data.get("supersedes"):
            plan["supersedes"] = data["supersedes"]
        store["plans"].append(plan)
    store["diagnoses"].append(record)
    _event(store, at, "loop_diagnosed", data["task"],
           {"diagnosis": data["id"], "remedy": remedy, "bound": bound, "plan": record["plan"]})
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
        raise UsageError("Approval must match this task's current exhausted checkpoint; record a fresh operator checkpoint and correction proposal.", {})
    # The operator overrides a remedy; they do not stand in for one. Without a
    # recorded diagnosis this path would reopen the budget prompt the judge
    # replaced (rules/agent-team-operation.md Judge Seat).
    if not any(row["fix_round"] == count for row in diagnoses_for(store, data["task"])):
        raise UsageError("This task has no diagnosis at fix round {}; an older remedy cannot authorize these attempts. Take the judge's diagnosis for this exhaustion with `teamlead diagnose` first.".format(count), {})
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
    # A diagnosis supersedes a plan too, and a `stop` remedy records no
    # replacement, so a plan retired that way is retired here or nowhere.
    replaced = {row["supersedes"] for row in store["plans"] if row.get("supersedes")}
    replaced |= {row["supersedes"] for row in store.get("diagnoses", []) if row.get("supersedes")}
    return [row for row in store["plans"] if row["id"] not in replaced]


def validate_work(store, assignments, task, fix_round, plan_id=None, work=None, *, implementation=True):
    """The same allowance is checked by planning, dispatch and state readers."""
    stopped = next((row for row in store["diagnoses"] if row["task"] == task and row["remedy"] == "stop"), None)
    if stopped is not None and implementation:
        # Implementation only: `stop` ships what is clean, so the reviewer,
        # tester and release roles it depends on still run.
        # Only the operator overrides a ruling, and they do it by authorizing
        # a plan over the stop, never by resuming silently.
        override = next((row for row in active_plans(store)
                         if row["task"] == task and row["first_fix"] > stopped["fix_round"]), None)
        if override is None:
            raise UsageError("This task's diagnosis is `stop`, which is terminal: ship what is clean and track the remainder. Only the operator overrides it, by authorizing a plan over that remedy.", {})
        if plan_id != override["id"]:
            raise UsageError("This task's `stop` remedy is overridden by plan {}; name it to spend its attempts.".format(override["id"]), {})
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
        raise UsageError("The five-fix budget is exhausted. Record the checkpoint and take the judge's diagnosis with `teamlead diagnose`; its bounded remedy authorizes the next attempts and ordinary sixth attempts are refused.", {})
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


def _dispatch_version(record):
    """Composition metadata is explicit v2 evidence, never a legacy default."""
    if not DISPATCH_METADATA_FIELDS.intersection(record):
        return RECOVERY_SCHEMA_VERSION
    if "requirements" in record:
        from .composition import normalize_requirement
        requirement = record["requirements"]
        if normalize_requirement(requirement, record.get("role")) != requirement:
            raise UsageError("Dispatch requirements must be canonical owner-normalized values; replan without editing saved engagement metadata.", {})
    if "reviewer_scope" in record and (record.get("role") != "reviewer"
            or not isinstance(record["reviewer_scope"], str) or record["reviewer_scope"] not in {"verification", "design"}):
        raise UsageError("New reviewer_scope must name verification or design on a reviewer dispatch; preserve unknown scope only in legacy assignment history.", {})
    return SPECIALIST_DISPATCH_VERSION


def _dispatch_metadata(record):
    return {key: deepcopy(record[key]) for key in DISPATCH_METADATA_FIELDS if key in record}


def _validate_dispatch_metadata(record):
    version = _dispatch_version(record)
    if type(record.get("schema_version")) is not int or record["schema_version"] != version:
        raise UsageError("Dispatch schema does not match its composition metadata; preserve the original record for owner recovery.", {})
    result = record.get("result")
    if result is not None:
        if (not isinstance(result, dict) or type(result.get("schema_version")) is not int
                or result["schema_version"] != version or _dispatch_version(result) != version
                or _dispatch_metadata(result) != _dispatch_metadata(record)):
            raise UsageError("Saved dispatch result has different composition metadata or schema; restore the original dispatch evidence before retrying.", {})


def reserve(store, record, at):
    version = _dispatch_version(record)
    if version == SPECIALIST_DISPATCH_VERSION and store.get("schema_version") != RECOVERY_STORE_VERSION:
        raise UsageError("Composition dispatch metadata needs the owner-migrated recovery store; load the current state before reserving this assignment.", {})
    pending = [row for row in store["dispatches"] if row["status"] in PENDING_STATUSES
               and (row["agent"] == record["agent"] or row["task"] == record["task"]
                    and row["role"] in {"developer", "release"} and record["role"] in {"developer", "release"})]
    if pending:
        raise UsageError("Task has an unresolved dispatch {}; reconcile it before another assignment.".format(pending[0]["id"]), {})
    prior = next((row for row in store["dispatches"] if row["id"] == record["id"]), None)
    if prior:
        if prior["status"] != "not_sent" or prior["fingerprint"] != record["fingerprint"]:
            raise UsageError("Dispatch already exists; inspect its recorded result instead of sending again.", {})
        if prior["schema_version"] != version or _dispatch_metadata(prior) != _dispatch_metadata(record):
            raise UsageError("Retry changes the original composition metadata; restore the recorded dispatch inputs instead of reusing its identity.", {})
        _event(store, at, "dispatch_transport_retry", record["task"], {"dispatch": prior["id"], "previous": dict(prior)})
        prior.update(status="reserved", report=None, result=None)
        # The fingerprint does not cover these, so a retry after a config
        # change would otherwise keep the original row's provider, and a move
        # naming the old one fails validation on the next refusal (#403).
        for key in ("provider", "brief_identity", "refusal_move"):
            if key in record:
                prior[key] = record[key]
            else:
                prior.pop(key, None)
        prior.pop("reconciliation", None)
        item = prior
    else:
        item = {"at": at, **record, "schema_version": version,
                "status": "reserved", "result": None, "report": None}
        if version == SPECIALIST_DISPATCH_VERSION:
            item.update(_dispatch_metadata(record))
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
    version = _dispatch_version(result)
    if record["schema_version"] != version or _dispatch_metadata(record) != _dispatch_metadata(result):
        raise UsageError("Dispatch result changes its reserved composition metadata; preserve the send outcome and recover the original engagement and reviewer scope.", {})
    if "assignment_index" in record and record["assignment_index"] != assignment_index:
        record.setdefault("prior_assignment_indices", []).append(record["assignment_index"])
    saved_result = {**result, "schema_version": version}
    if version == SPECIALIST_DISPATCH_VERSION:
        saved_result.update(_dispatch_metadata(result))
    record.update(status=result["status"], result=saved_result, assignment_index=assignment_index)
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


#: The exact object wait-report.sh emits on exit 5 (see its `emit`).
REFUSAL_RECEIPT_FIELDS = frozenset({"agent", "state", "report_path", "found", "elapsed_seconds", "reason"})
REFUSAL_STATES = frozenset({"idle", "done"})
#: What an operator authorization says about the brief it approves.
AUTHORIZED_BRIEFS = frozenset({"unchanged", "revised"})


def brief_identity(paths_by_role, role, report):
    """Digest the common and role brief bytes with the report path masked.

    A replacement brief carries a fresh report path and nothing else; masking
    every occurrence of `report` lets two dispatches of the unchanged brief
    share one identity while a reworded brief gets another. Without a bound
    report path the raw bytes are digested, so only a byte-identical brief
    matches.
    """
    digest = hashlib.sha256()
    for path in (paths_by_role["common"], paths_by_role[role]):
        try:
            content = Path(path).read_bytes()
        except OSError as exc:
            raise UsageError("Cannot read brief {}: {}. Restore it before dispatch.".format(path, exc), {}) from None
        if report:
            content = content.replace(report.encode("utf-8"), b"<REPORT>")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def record_refusal(store, data, at, provider, report, aliases=()):
    """Bind a wait-report exit-5 receipt to the applied dispatch it stopped.

    `provider` is the refused agent's config `kind` and `report` the absolute
    report path the dispatch's supervision enrollment bound, both resolved by
    the caller; an unbound dispatch has no verifiable binding and is refused.
    A dispatch that recorded its own send-time `provider` uses that instead,
    so a config edit since the send cannot re-attribute the refusal (#403).
    `aliases` are other names wait-report may have been given for this worker
    (its enrolled pane id), accepted in the receipt's `agent` field.
    The receipt's JSON must be the complete exit-5 object for this agent and
    report. Recording the same receipt twice replays; a different receipt for
    an already-refused dispatch is refused.
    """
    required = {"dispatch", "receipt"}
    if not isinstance(data, dict) or set(data) != required:
        raise UsageError("Refusal record requires dispatch and receipt.", {})
    record = _item(store["dispatches"], data["dispatch"], "dispatch")
    if record["status"] != "applied":
        raise UsageError("Record a provider refusal only against its confirmed applied dispatch; reconcile an uncertain send first.", {})
    provider = record.get("provider") or provider
    text(provider, "provider")
    if not isinstance(report, str) or not Path(report).is_absolute():
        raise UsageError("Dispatch {} has no supervision enrollment binding its report path, so no receipt can be verified against it; record the operator's decision for this attempt instead.".format(record["id"]), {})
    evidence, body = receipt(data["receipt"])
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise UsageError("Refusal receipt {} is not wait-report JSON: {}. Save the exact exit-5 output.".format(data["receipt"], exc), {}) from None
    # Every field is typed before any membership test: a receipt holding a
    # list or object would raise TypeError on an unhashable value, and saved
    # evidence must fail as a usage error (#403).
    if (not isinstance(payload, dict) or set(payload) != REFUSAL_RECEIPT_FIELDS
            or any(not isinstance(payload[key], str) for key in ("agent", "state", "reason", "report_path"))
            or payload["reason"] != REFUSAL_REASON or payload["found"] is not False
            or payload["agent"] not in {record["agent"], *[alias for alias in aliases if isinstance(alias, str) and alias]}
            or payload["state"] not in REFUSAL_STATES
            or type(payload["elapsed_seconds"]) is not int or payload["elapsed_seconds"] < 0):
        raise UsageError("Refusal receipt must be wait-report's complete exit-5 output for this dispatch's agent: reason {}, found false, an idle or done state. A missing report without that reason is not a refusal.".format(REFUSAL_REASON), {})
    if payload["report_path"] != report:
        raise UsageError("Refusal receipt names report {} but dispatch {} is enrolled for {}; a receipt from another attempt does not refuse this one.".format(payload["report_path"], record["id"], report), {})
    result = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, "provider": provider, "reason": REFUSAL_REASON,
              "receipt": data["receipt"], "report_path": report, "evidence": evidence}
    prior = record.get("refusal")
    if prior is not None:
        if prior["evidence"] != evidence:
            raise UsageError("Dispatch already records a different refusal receipt; preserve it and inspect both before recording again.", {})
        return prior
    record["refusal"] = result
    _event(store, at, "provider_refusal_recorded", record["task"], {"dispatch": record["id"], "provider": provider, "evidence": evidence})
    return result


def authorize_refused_dispatch(store, data, at):
    """Record the operator's decision to dispatch a twice-refused brief again.

    Requires a recorded refusal on the task, role and fix round. One
    authorization permits one further dispatch on that key to the named
    `provider` (a config `kind`), with the refused brief `unchanged` or, when
    the operator approved a rewrite, `revised`; it is carried on that
    dispatch's `refusal_move.authorization`, and a later dispatch needs
    another. Same id with the same input replays.
    """
    required = {"id", "task", "role", "fix_round", "provider", "brief", "decision", "authorization"}
    if not isinstance(data, dict) or set(data) != required:
        raise UsageError("Refused-dispatch authorization requires id, task, role, fix_round, the approved provider, brief (unchanged or revised), decision and explicit authorization.", {})
    for key in ("id", "task", "role", "provider", "decision"):
        text(data[key], key)
    if data["brief"] not in AUTHORIZED_BRIEFS:
        raise UsageError("Authorization brief must be unchanged or revised; record which the operator approved.", {})
    if data["fix_round"] is not None:
        positive(data["fix_round"], "fix_round")
    authorization(data["authorization"])
    prior = next((row for row in store["refusal_authorizations"] if row["id"] == data["id"]), None)
    if prior:
        if any(prior.get(key) != value for key, value in data.items()):
            raise UsageError("This authorization identity already records a different decision; use a new id for a new decision.", {})
        return prior
    providers = _refusal_providers(store, data["task"], data["role"], data["fix_round"])
    if len(providers) < REFUSAL_LIMIT:
        raise UsageError("Task {} {} round {} has refusals from {} provider(s); an operator decision follows {} independent refusals. Move the unchanged brief to another provider first, or record that refusal.".format(
            data["task"], data["role"], data["fix_round"], len(providers), REFUSAL_LIMIT), {})
    record = {"schema_version": RECOVERY_SCHEMA_VERSION, "at": at, **data}
    store["refusal_authorizations"].append(record)
    _event(store, at, "refused_dispatch_authorized", data["task"], {"authorization": data["id"], "role": data["role"], "fix_round": data["fix_round"]})
    return record


def _refusal_providers(store, task, role, fix_round):
    providers = []
    for row in refusals(store, task, role, fix_round):
        if row["refusal"]["provider"] not in providers:
            providers.append(row["refusal"]["provider"])
    return providers


def _authorization_uses(store, identifier):
    return [row for row in store["dispatches"] if row["status"] != "not_sent"
            and (row.get("refusal_move") or {}).get("authorization") == identifier]


def _unused_authorization(store, task, role, fix_round):
    for row in store["refusal_authorizations"]:
        if (row["task"], row["role"], row["fix_round"]) == (task, role, fix_round) and not _authorization_uses(store, row["id"]):
            return row
    return None


def refusals(store, task, role, fix_round):
    return [row for row in store["dispatches"] if row.get("refusal") is not None
            and row["task"] == task and row["role"] == role and row["fix_round"] == fix_round]


def refusal_move(store, task, role, fix_round, provider, identity, report=None):
    """Return the move record a new dispatch carries, or None without refusals.

    `identity` is the new dispatch's `brief_identity`. Refuses a brief whose
    identity differs from the refused brief's (a rewording is not a move),
    a resend to a provider that already refused this task, role and fix
    round, a second move while one is reserved, uncertain or applied without
    a recorded refusal, and any dispatch once `REFUSAL_LIMIT` independent
    refusals are recorded: that line stops and goes to the operator. A
    `not_sent` move consumed nothing. `report` is the new dispatch's report
    path; reusing one a refused attempt already claimed is refused, so two
    attempts never enroll against one file. An unused operator authorization on the
    key (`authorize_refused_dispatch`) replaces every check for one dispatch
    with its own scope: the approved provider, and the brief unchanged unless
    the operator approved a revision.
    """
    refused = refusals(store, task, role, fix_round)
    if not refused:
        return None
    # Normalized on both sides: `_parse_reports` already rejects two roles
    # whose report paths resolve to one file, and an alias must not slip a
    # second attempt onto a refused attempt's evidence.
    target = str(Path(report).resolve()) if report is not None else None
    burned = [row for row in refused if target is not None and str(Path(row["refusal"]["report_path"]).resolve()) == target]
    if burned:
        raise UsageError("Report path {} already carries the refusal of dispatch {}; give this attempt a fresh report path so their evidence cannot collide.".format(
            report, burned[-1]["id"]), {"refusals": [row["id"] for row in burned]})
    authorized = _unused_authorization(store, task, role, fix_round)
    if authorized is not None:
        if authorized["provider"] != provider:
            raise UsageError("Authorization {} approves provider {} for task {} {} round {}, not {}; dispatch what the operator approved or record a new decision.".format(
                authorized["id"], authorized["provider"], task, role, fix_round, provider), {"authorization": authorized["id"]})
        if authorized["brief"] == "unchanged" and any(row.get("brief_identity") != identity for row in refused):
            raise UsageError("Authorization {} approves the refused brief unchanged, and this brief differs; send it unchanged or record a decision approving the revision.".format(authorized["id"]), {"authorization": authorized["id"]})
        return {"schema_version": RECOVERY_SCHEMA_VERSION, "from": refused[-1]["id"], "from_provider": refused[-1]["refusal"]["provider"],
                "provider": provider, "authorization": authorized["id"]}
    providers = _refusal_providers(store, task, role, fix_round)
    if len(providers) >= REFUSAL_LIMIT:
        raise UsageError("Task {} {} round {} was refused by {} providers ({}); the line stops here. Record the operator's decision with authorize-refused-dispatch before any further dispatch of this brief.".format(
            task, role, fix_round, len(providers), ", ".join(providers)), {"refusals": [row["id"] for row in refused]})
    for row in refused:
        if row.get("brief_identity") is None:
            raise UsageError("Refused dispatch {} predates brief identity, so an unchanged move cannot be verified; record the operator's decision before any further dispatch of this brief.".format(row["id"]), {"refusals": [item["id"] for item in refused]})
        if row["brief_identity"] != identity:
            raise UsageError("The brief differs from the one provider {} refused (dispatch {}); a reworded brief is not a move. Send the refused brief unchanged, fresh report path aside, or record the operator's decision.".format(row["refusal"]["provider"], row["id"]), {"refusals": [item["id"] for item in refused]})
    moves = [row for row in store["dispatches"] if row.get("refusal_move") is not None and row["status"] != "not_sent"
             and row["task"] == task and row["role"] == role and row["fix_round"] == fix_round and row.get("refusal") is None]
    if moves:
        raise UsageError("Task {} {} round {} already moved to provider {} (dispatch {}, {}); one move per refusal. Wait for its report, record its refusal, or record the operator's decision before another dispatch of this brief.".format(
            task, role, fix_round, moves[-1]["refusal_move"]["provider"], moves[-1]["id"], moves[-1]["status"]), {"moves": [row["id"] for row in moves]})
    if provider in providers:
        raise UsageError("Provider {} already refused task {} {} round {} (dispatch {}); a resend to the same provider is refused. Move the unchanged brief to another provider once, or record the operator's decision.".format(
            provider, task, role, fix_round, refused[-1]["id"]), {"refusals": [row["id"] for row in refused]})
    return {"schema_version": RECOVERY_SCHEMA_VERSION, "from": refused[-1]["id"], "from_provider": refused[-1]["refusal"]["provider"], "provider": provider}


def _validate_refusals(store):
    rows = store["dispatches"]
    for index, row in enumerate(rows):
        if "brief_identity" in row:
            text(row["brief_identity"], "brief identity")
        if "provider" in row:
            text(row["provider"], "dispatch provider")
        refusal = row.get("refusal")
        if refusal is not None:
            if (not isinstance(refusal, dict) or set(refusal) != {"schema_version", "at", "provider", "reason", "receipt", "report_path", "evidence"}
                    or type(refusal["schema_version"]) is not int or refusal["schema_version"] != RECOVERY_SCHEMA_VERSION
                    or refusal["reason"] != REFUSAL_REASON or row["status"] != "applied"):
                raise UsageError("Refusal record has an unsupported schema or sits on an unconfirmed dispatch; preserve it for owner recovery.", {})
            text(refusal["at"], "refusal timestamp")
            text(refusal["provider"], "refusal provider")
            validate_receipt(refusal["evidence"])
            if text(refusal["receipt"], "refusal receipt") != refusal["evidence"]["path"] or not Path(text(refusal["report_path"], "refusal report")).is_absolute():
                raise UsageError("Refusal record's receipt path disagrees with its bound evidence; preserve it for owner recovery.", {})
        move = row.get("refusal_move")
        if move is not None:
            if (not isinstance(move, dict) or set(move) - {"authorization"} != {"schema_version", "from", "from_provider", "provider"}
                    or type(move["schema_version"]) is not int or move["schema_version"] != RECOVERY_SCHEMA_VERSION):
                raise UsageError("Refusal move has an unsupported schema; preserve it for owner recovery.", {})
            source_index = next((position for position, item in enumerate(rows[:index]) if item["id"] == text(move["from"], "move source")), None)
            if source_index is None:
                raise UsageError("Refusal move names no earlier dispatch; preserve the ledger for owner recovery.", {})
            source = rows[source_index]
            if (source.get("refusal") is None or any(source[key] != row[key] for key in ("task", "role", "fix_round"))
                    or source["refusal"]["provider"] != text(move["from_provider"], "move source provider")):
                raise UsageError("Refusal move does not name an earlier refused dispatch of the same task, role and fix round; preserve the ledger for owner recovery.", {})
            text(move["provider"], "move provider")
            if refusal is not None and refusal["provider"] != move["provider"]:
                raise UsageError("A moved dispatch records a refusal from a provider other than the one it moved to; preserve the ledger for owner recovery.", {})
            if "authorization" in move:
                grant = _item(store["refusal_authorizations"], move["authorization"], "refusal authorization")
                if ((grant["task"], grant["role"], grant["fix_round"], grant["provider"]) != (row["task"], row["role"], row["fix_round"], move["provider"])
                        or len(_authorization_uses(store, grant["id"])) > 1
                        or grant["brief"] == "unchanged" and (source.get("brief_identity") is None or source["brief_identity"] != row.get("brief_identity"))):
                    raise UsageError("An authorized refused dispatch exceeds its authorization's provider or brief scope, names another key, or reuses a consumed authorization; preserve the ledger for owner recovery.", {})
            elif (move["provider"] == move["from_provider"]
                    or source.get("brief_identity") is None or source["brief_identity"] != row.get("brief_identity")):
                raise UsageError("Refusal move does not carry the refused brief unchanged to another provider; preserve the ledger for owner recovery.", {})
    for row in store["refusal_authorizations"]:
        for key in ("id", "task", "role", "provider", "decision"):
            text(row[key], key)
        if row["fix_round"] is not None:
            positive(row["fix_round"], "authorization fix_round")
        if row["brief"] not in AUTHORIZED_BRIEFS:
            raise UsageError("Refusal authorization names an unknown brief scope; preserve the ledger for owner recovery.", {})
        if len(_refusal_providers(store, row["task"], row["role"], row["fix_round"])) < REFUSAL_LIMIT:
            raise UsageError("Refusal authorization precedes the independent refusals it answers; preserve the ledger for owner recovery.", {})
        authorization(row["authorization"])
    seen_diagnoses = {}
    for row in store["diagnoses"]:
        task = task_record(store, row["task"])
        source = _item(store["checkpoints"], row["checkpoint"], "checkpoint")
        if (source["task"] != row["task"] or source["fix_round"] != row["fix_round"]
                or row["base_revision"] != task["base_revision"]):
            raise UsageError("A diagnosis cites a checkpoint from another task, base or fix round; preserve the ledger for owner recovery.", {})
        for key in ("id", "scope"):
            text(row[key], key)
        paths(row["allowed_paths"], "diagnosis paths")
        if row["remedy"] not in DIAGNOSIS_LADDER:
            raise UsageError("A diagnosis names an unknown remedy; preserve the ledger for owner recovery.", {})
        text(row["judge_agent"], "diagnosis judge")
        validate_receipt(row["judge_evidence"])
        rung = DIAGNOSIS_LADDER.index(row["remedy"])
        reissue = row.get("reissue") is True
        last, repeated = seen_diagnoses.get(row["task"], (None, False))
        if last is not None and (rung < last or rung == last and not reissue):
            raise UsageError("A task's diagnoses must move down the remedy ladder; preserve the ledger for owner recovery.", {})
        if reissue and (last is None or rung != last or repeated or row["remedy"] == "stop"):
            raise UsageError("A diagnosis records a reissue of a rung its predecessor did not take, or of one already reissued; preserve the ledger for owner recovery.", {})
        seen_diagnoses[row["task"]] = (rung, reissue)
        cited = row.get("investigator_report")
        if cited is not None:
            if not isinstance(cited, dict) or set(cited) != {"schema_version", "report", "evidence"}:
                raise UsageError("A diagnosis cites an investigator report in an unsupported shape; preserve the ledger for owner recovery.", {})
            validate_receipt(cited["evidence"])
            if text(cited["report"], "diagnosis investigator report") != cited["evidence"]["path"]:
                raise UsageError("A diagnosis cites an investigator report whose receipt names another artifact; preserve the ledger for owner recovery.", {})
        if row.get("supersedes") is not None:
            text(row["supersedes"], "diagnosis supersedes")
        if row.get("authorization") is not None:
            if row.get("supersedes") is None:
                raise UsageError("A diagnosis records an operator override with no plan it superseded; preserve the ledger for owner recovery.", {})
            authorization(row["authorization"])
        if row["remedy"] == "stop":
            if row["bound"] is not None or row["plan"] is not None:
                raise UsageError("A stop remedy carries no bound and no plan; preserve the ledger for owner recovery.", {})
            continue
        if positive(row["bound"], "diagnosis bound") and row["plan"] is None:
            raise UsageError("A bounded remedy records the plan its bound authorizes; preserve the ledger for owner recovery.", {})
        plan = _item(store["plans"], row["plan"], "diagnosis plan")
        # Every field `diagnose` derives for the plan, not three of them: a
        # same-task plan carrying another checkpoint, base, scope, path set,
        # fix range or supersession used to pass here, after which
        # `validate_work` enforced that unrelated plan's budget and scope
        # against this diagnosis (#412).
        if (plan["task"] != row["task"] or plan["additional_fixes"] != row["bound"]
                or plan["authorization"]["source"] != row["judge_evidence"]["path"]
                or plan["checkpoint"] != row["checkpoint"]
                or plan["base_revision"] != row["base_revision"]
                or plan["scope"] != row["scope"]
                or plan["allowed_paths"] != row["allowed_paths"]
                or plan["first_fix"] != row["fix_round"] + 1
                or plan["last_fix"] != row["fix_round"] + row["bound"]
                or plan.get("supersedes") != row.get("supersedes")):
            raise UsageError("A diagnosis plan does not match the remedy that authorized it; preserve the ledger for owner recovery.", {})


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
        for name in ("checkpoints", "plans", "dispatches", "context_permissions", "events", "hand_clearances", "historical_attempts", "role_clearances", "delivery_recoveries", "refusal_authorizations", "diagnoses", "legacy_ruling_recoveries"):
            if not isinstance(store[name], list):
                raise UsageError("Recovery {} must be an array; restore the owner-written ledger.".format(name), {})
            identifiers = []
            for row in store[name]:
                versions = ({1, 2} if name in {"delivery_recoveries", "dispatches"}
                            else CHECKPOINT_VERSIONS if name == "checkpoints"
                            else {DIAGNOSIS_RECORD_VERSION} if name == "diagnoses"
                            else {RECOVERY_SCHEMA_VERSION})
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
        validate_legacy_ruling_recoveries(store)
        ruled_tasks = set()
        for row in store["checkpoints"]:
            task = task_record(store, row["task"])
            if row["base_revision"] != task["base_revision"] or positive(row["fix_round"], "checkpoint fix") < DEFAULT_FIX_LIMIT:
                raise UsageError("Checkpoint does not match the original base or exhausted budget.", {})
            for field in ("defect", "previous_attempts", "progress", "change_in_approach"):
                text(row[field], field)
            # A checkpoint carries the trio only when a ruling was cited, and
            # a partial trio is a broken record, never an uncited checkpoint;
            # a migrated version-1 row keeps the ruling it recorded.
            if any(field in row for field in ("judge_agent", "judge_report", "judge_evidence")):
                text(row["judge_agent"], "judge_agent")
                text(row["judge_report"], "judge_report")
                validate_receipt(row["judge_evidence"])
                # The receipt rides with the version that records it: a
                # version-2 row predates it and never has one invented (#400).
                if row["schema_version"] == OPERATOR_CHECKPOINT_VERSION:
                    authorization(row["requested_by"])
                    # The one-ruling-per-task bound is read only over the rows
                    # written under it. A version-2 row predates the bound as
                    # well as the receipt, and enforcing it over history turned
                    # legitimately-written ledgers into unreadable ones --
                    # blocking new work on a rule that did not exist when they
                    # were written (#436).
                    if row["task"] in ruled_tasks:
                        raise UsageError("A task cites more than one operator-requested ruling; the operator grants at most one. Preserve the ledger for owner recovery.", {})
                    ruled_tasks.add(row["task"])
                elif "requested_by" in row:
                    raise UsageError("An older checkpoint carries an operator-request receipt its version never wrote; preserve the ledger for owner recovery.", {})
            elif "requested_by" in row:
                raise UsageError("A checkpoint records an operator request with no ruling it authorized; preserve the ledger for owner recovery.", {})
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
            _validate_dispatch_metadata(row)
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
                if row["schema_version"] == SPECIALIST_DISPATCH_VERSION and any(assignment.get(key) != row.get(key) for key in DISPATCH_METADATA_FIELDS):
                    raise UsageError("Dispatch and assignment composition metadata disagree; restore their original shared engagement and reviewer scope before continuing.", {})
                if not isinstance(row["result"], dict) or any(row["result"].get(key) != row[key] for key in ("task", "role", "agent", "fix_round", "status")):
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
        _validate_refusals(store)
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
        # A `stop` the operator overrode is no longer terminal, the same way
        # validate_work reads it (#407).
        terminal = next((item for item in store["diagnoses"] if item["task"] == task and item["remedy"] == "stop"), None)
        stopped = terminal is not None and not any(
            row["task"] == task and row["first_fix"] > terminal["fix_round"] for row in active_plans(store))
        status = ("dispatch_outcome_unknown" if pending else "diagnosed_stop" if stopped
                  else "within_authorized_budget" if plan or count < DEFAULT_FIX_LIMIT
                  else "awaiting_diagnosis" if checkpoint_row and checkpoint_row["fix_round"] == count else "checkpoint_required")
        result[task] = {"status": status, "paused_work": "implementation" if status != "within_authorized_budget" else None,
                        "confirmed_fixes": count, "plan": plan["id"] if plan else None,
                        "remaining_fixes": plan["last_fix"] - count if plan else max(0, DEFAULT_FIX_LIMIT - count)}
    return result
