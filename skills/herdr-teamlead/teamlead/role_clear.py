"""Fresh corrections after an owner-recorded automatic change of role.

The owner preserves both assignments and verifies archived apply output against
its successful clearing dispatch. Recovery grants one handoff, never an attempt
or new scope. Existing registered authorization covers each task; only missing
clear authority needs an explicit decision and its original artifact. The owner
checks the meaning of that decision before recording it. Live readiness remains
mandatory at recovery and again at normal apply; this module sends no input.
"""

import json
from fnmatch import fnmatchcase

from . import recovery as ledger
from .chronology import assignment_after, latest_assignment
from .errors import UsageError
from .historical import fields, timestamp


def _assignment(assignments, index):
    if type(index) is not int or not 0 <= index < len(assignments):
        raise UsageError("Use the original developer and clearing assignment indices from teamlead state; preserve history.", {})
    return assignments[index]


def _bounds(store, task, fix_round, plan_id, work):
    original = ledger.task_record(store, task)
    bounds = original
    if fix_round > ledger.DEFAULT_FIX_LIMIT:
        bounds = ledger._item(store["plans"], plan_id, "correction plan")
        if (bounds["task"] != task or bounds["base_revision"] != original["base_revision"]
                or not bounds["first_fix"] <= fix_round <= bounds["last_fix"]):
            raise UsageError("Role-clear recovery cannot expand the original task or correction allowance; record the missing bounded decision.", {})
    elif plan_id is not None:
        raise UsageError("An ordinary correction cannot use an extra-correction plan; preserve its actual count.", {})
    fields(work, "base_revision scope paths findings", "Role-clear correction work")
    if work["base_revision"] != original["base_revision"] or work["scope"] != bounds["scope"]:
        raise UsageError("Role-clear correction base or scope differs from its authorization; preserve the original base and authorized scope.", {})
    ledger.paths(work["paths"], "role-clear correction paths")
    if any(not any(fnmatchcase(path, pattern) for pattern in bounds["allowed_paths"]) for path in work["paths"]):
        raise UsageError("Role-clear correction paths exceed the authorized scope; obtain the missing scope decision.", {})
    if (not isinstance(work["findings"], list) or not work["findings"]
            or any(not isinstance(value, str) or not value.strip() for value in work["findings"])):
        raise UsageError("Name the actual blocking findings for this role-clear correction before continuing.", {})
    return bounds


def _clear_authority(store, clear, source, *, read_evidence):
    if isinstance(source, dict) and set(source) == {"task"}:
        ledger.text(source["task"], "clearing authorization task")
        if source["task"] != clear["task"]:
            raise UsageError("The existing clear authorization must name the clearing assignment's task; do not borrow another task's authority.", {})
        record = ledger.task_record(store, source["task"])
        return {"task": source["task"], "authorization": record["authorization"]}, None
    fields(source, "authorization evidence", "Missing clear authorization decision")
    ledger.authorization(source["authorization"])
    if not read_evidence:
        return {"authorization": source["authorization"]}, None
    evidence, body = ledger.receipt(source["evidence"])
    if any(value not in body for value in (source["authorization"]["quote"], clear["task"], clear["role"])):
        raise UsageError("Clear authorization evidence must contain the actual operator decision, clearing task and role; collect the missing explicit decision.", {})
    return {"authorization": source["authorization"]}, evidence


def _source(store, assignments, data):
    fields(data, "id task base_revision assignment_index clearing_assignment_index next_fix correction_plan work clearing_authorization evidence reason", "Role-clear recovery")
    for key in ("id", "task", "reason"):
        ledger.text(data[key], key)
    task = ledger.task_record(store, data["task"])
    if data["base_revision"] != task["base_revision"]:
        raise UsageError("Role-clear recovery must retain the original registered task base; do not relabel its history.", {})
    if data["correction_plan"] is not None:
        ledger.text(data["correction_plan"], "correction_plan")
    developer = _assignment(assignments, data["assignment_index"])
    clear = _assignment(assignments, data["clearing_assignment_index"])
    if (developer.get("task") != data["task"] or developer.get("role") != "developer"
            or developer.get("status") != "applied" or developer.get("context_session") is None):
        raise UsageError("Role-clear recovery requires the original confirmed developer assignment with known native-session proof; use recover-context for missing proof.", {})
    if (clear.get("agent") != developer.get("agent") or clear.get("role") == "developer"
            or clear.get("status") != "applied" or clear.get("cleared") is not True
            or clear.get("clear_reason") != "automatic" or not clear.get("task")):
        raise UsageError("Use the same worker's successful automatic clearing assignment in another authorized role; unconfirmed or hand-clear rows do not prove this recovery.", {})
    if not assignment_after(assignments, data["clearing_assignment_index"], data["assignment_index"]):
        raise UsageError("The clearing assignment must occur after the preserved developer attempt; resolve the actual chronology.", {})
    old, fresh = developer["context_session"], clear.get("context_session")
    if fresh and all(old.get(key) == fresh.get(key) for key in ("kind", "value")):
        raise UsageError("The clearing assignment records the old developer session; resolve the conflicting clear evidence without changing original proof.", {})
    fix_round = ledger.positive(data["next_fix"], "next_fix")
    if fix_round != (developer.get("fix_round") or 0) + 1:
        raise UsageError("Role-clear recovery must use the actual next correction after its developer assignment; never skip or reset counts.", {})
    dispatches = [row for row in store["dispatches"] if row.get("assignment_index") == data["clearing_assignment_index"]
                  and row.get("status") == "applied"]
    if len(dispatches) != 1:
        raise UsageError("The clearing assignment lacks one confirmed owner dispatch; recover its transport evidence before requesting a fresh handoff.", {})
    dispatch = dispatches[0]
    result = dispatch.get("result")
    keys = ("task", "role", "agent", "status", "cleared", "clear_reason", "context_session", "fix_round")
    if not isinstance(result, dict) or any(result.get(key) != clear.get(key) for key in keys):
        raise UsageError("The clearing dispatch and preserved assignment disagree; restore the owner-written evidence without editing history.", {})
    _bounds(store, data["task"], fix_round, data["correction_plan"], data["work"])
    return developer, clear, dispatch


def _evidence(data, dispatch):
    evidence, body = ledger.receipt(data["evidence"])
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise UsageError("Clear evidence must be the original JSON apply output; preserve its recorded successful dispatch result.", {}) from None
    results = payload.get("applied", []) if isinstance(payload, dict) and "applied" in payload else [payload]
    # The owner adds a row schema when saving, then labels the pane after
    # saving. Those envelope fields do not describe dispatch/clear evidence.
    cosmetic = {"schema_version", "pane_label", "replayed"}
    original = {key: value for key, value in dispatch["result"].items() if key not in cosmetic}
    if not isinstance(results, list) or not any(
        isinstance(value, dict) and {key: item for key, item in value.items() if key not in cosmetic} == original
        for value in results
    ):
        raise UsageError("Archived clear evidence differs from the owner's confirmed apply result; use the original output, never a fabricated clear assertion.", {})
    return evidence


def record_role_clear(store, assignments, data, at, observed_session):
    developer, clear, dispatch = _source(store, assignments, data)
    evidence = _evidence(data, dispatch)
    authority, authority_evidence = _clear_authority(store, clear, data["clearing_authorization"], read_evidence=True)
    receipts = {"clear": evidence, "authorization": authority_evidence}
    prior = next((row for row in store["role_clearances"] if row["id"] == data["id"]), None)
    if prior:
        if prior["input"] != data or prior["receipts"] != receipts:
            raise UsageError("This role-clear recovery identity already names different input or evidence; preserve it and resolve the conflict.", {})
        return prior
    latest = latest_assignment(assignments, task=data["task"], role="developer", status="applied")
    if latest is None or latest[0] != data["assignment_index"] or data["next_fix"] != ledger.confirmed_fix(assignments, data["task"]) + 1:
        raise UsageError("This developer attempt is no longer the preceding correction; never reuse a consumed attempt or stale recovery.", {})
    if timestamp(at, "recovery time") < timestamp(clear["at"], "clearing time"):
        raise UsageError("Recovery cannot precede the clearing assignment; preserve the actual observation times.", {})
    if any(row["task"] == data["task"] and row["next_fix"] == data["next_fix"] for row in store["role_clearances"]):
        raise UsageError("This correction already has a role-clear recovery; reuse its original identity and bounds.", {})
    if any(row["status"] in ledger.PENDING_STATUSES and (row["task"] == data["task"] or row["agent"] == developer["agent"])
           for row in store["dispatches"]):
        raise UsageError("Reconcile the overlapping pending dispatch before role-clear recovery; do not consume or send another attempt.", {})
    ledger.validate_work(store, assignments, data["task"], data["next_fix"], data["correction_plan"], data["work"])
    record = {"schema_version": ledger.RECOVERY_SCHEMA_VERSION, "at": at, "id": data["id"], "task": data["task"],
              "base_revision": data["base_revision"], "assignment_index": data["assignment_index"],
              "clearing_assignment_index": data["clearing_assignment_index"], "clearing_dispatch": dispatch["id"],
              "next_fix": data["next_fix"], "input": data, "receipts": receipts, "clear_authority": authority,
              "observed_session": observed_session, "basis": "verified_authorized_role_clear", "grants_future_attempts": False}
    store["role_clearances"].append(record)
    ledger._event(store, at, "role_clear_recovery_recorded", data["task"],
                  {"clearance": data["id"], "previous_developer": data["assignment_index"],
                   "clearing_assignment": data["clearing_assignment_index"], "next_fix": data["next_fix"]})
    return record


def transition(store, assignments, task, fix_round, previous_developer):
    record = next((row for row in store["role_clearances"] if row["task"] == task
                   and row["assignment_index"] == previous_developer and row["next_fix"] == fix_round), None)
    if record is None:
        return None
    return {"reason": "verified_role_clear_handoff", "previous_developer": previous_developer,
            "clearing_assignment": record["clearing_assignment_index"], "clearance": record["id"]}


def validate_requested(store, assignments, task, fix_round, plan_id, work):
    """Revalidate the one recorded handoff at plan/apply without spending it."""
    record = next((row for row in store["role_clearances"] if row["task"] == task and row["next_fix"] == fix_round), None)
    if record is None:
        return
    if record["input"]["work"] != work or record["input"]["correction_plan"] != plan_id:
        raise UsageError("Use the role-clear recovery's same --work and correction plan for plan/apply; the recovery grants no changed scope.", {})
    _developer, clear, dispatch = _source(store, assignments, record["input"])
    authority, authority_evidence = _clear_authority(store, clear, record["input"]["clearing_authorization"], read_evidence=True)
    if ({"clear": _evidence(record["input"], dispatch), "authorization": authority_evidence} != record["receipts"]
            or authority != record["clear_authority"]):
        raise UsageError("Role-clear evidence changed after recovery; restore the original artifacts before dispatch.", {})


def validate_history(store, assignments):
    slots = set()
    for row in store["role_clearances"]:
        _developer, clear, dispatch = _source(store, assignments, row["input"])
        data = row["input"]
        keys = ("id", "task", "base_revision", "assignment_index", "clearing_assignment_index", "next_fix")
        if (any(row[key] != data[key] for key in keys) or row["clearing_dispatch"] != dispatch["id"]
                or row["basis"] != "verified_authorized_role_clear" or row["grants_future_attempts"] is not False):
            raise UsageError("Role-clear recovery disagrees with its preserved assignments or bounds; restore the owner record.", {})
        authority, _evidence_receipt = _clear_authority(store, clear, data["clearing_authorization"], read_evidence=False)
        if row["clear_authority"] != authority:
            raise UsageError("Role-clear authorization disagrees with its original decision; preserve the recorded authority.", {})
        fields(row["receipts"], "clear authorization", "Role-clear evidence receipts")
        if timestamp(row["at"], "role-clear receipt time") < timestamp(clear["at"], "clearing time"):
            raise UsageError("Role-clear receipt predates the clearing assignment; restore the recorded chronology.", {})
        ledger.validate_receipt(row["receipts"]["clear"])
        if "task" not in data["clearing_authorization"]:
            ledger.validate_receipt(row["receipts"]["authorization"])
            if row["receipts"]["authorization"]["path"] != data["clearing_authorization"]["evidence"]:
                raise UsageError("Role-clear authority receipt names another artifact; restore its original evidence path.", {})
        elif row["receipts"]["authorization"] is not None:
            raise UsageError("Existing task authority cannot carry an invented decision receipt; restore the owner record.", {})
        if row["receipts"]["clear"]["path"] != data["evidence"]:
            raise UsageError("Role-clear receipt names another artifact; restore its original evidence path.", {})
        latest = latest_assignment(assignments, task=data["task"], role="developer", status="applied", before=data["clearing_assignment_index"])
        if latest is None or latest[0] != data["assignment_index"]:
            raise UsageError("Role-clear history does not preserve its preceding developer; resolve the recorded chronology.", {})
        slot = (row["task"], row["next_fix"])
        if slot in slots:
            raise UsageError("A role-clear handoff was duplicated; preserve its single original recovery record.", {})
        slots.add(slot)
