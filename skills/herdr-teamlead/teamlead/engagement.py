"""Lead assessments of delivered specialist work, owned by teamlead state.

Report delivery proves an artifact arrived. The lead supplies its assessment
and contribution classification; these receipts never accept the whole task.
Reading history validates stored relationships without reopening old sources.
Warm follow-up revalidates those sources and the retired fleet enrollment.
"""

import json

from .chronology import latest_assignment
from .errors import UsageError
from .recovery import receipt, text, validate_receipt
from . import supervision


ASSESSMENT_SCHEMA_VERSION = 1
CONSULTATION_ROLES = frozenset({"advisor", "investigator", "architect"})
ASSESSABLE_ROLES = CONSULTATION_ROLES | {"reviewer", "tester"}
CONTRIBUTIONS = frozenset({"none", "design", "implementation"})
INPUT_FIELDS = frozenset({"id", "dispatch", "report", "delivery", "outcome", "contribution", "summary"})


def _input(data):
    if not isinstance(data, dict) or set(data) != INPUT_FIELDS:
        raise UsageError("Specialist assessment requires id, dispatch, report, delivery, outcome, contribution and summary; read the delivered report before classifying it.", {})
    for key in INPUT_FIELDS:
        text(data[key], key)
    if data["contribution"] not in CONTRIBUTIONS:
        raise UsageError("Contribution must be none, design or implementation; record the worker's actual contribution, not its current seat label.", {})


def _dispatch(state, identifier):
    found = next((row for row in state["recovery"]["dispatches"] if row["id"] == identifier), None)
    if found is None or found["status"] != "applied" or found["role"] not in ASSESSABLE_ROLES:
        raise UsageError("Assess a confirmed consultation, reviewer or tester dispatch; reconcile an unknown send before recording its outcome.", {})
    index = found.get("assignment_index")
    if type(index) is not int or not 0 <= index < len(state["assignments"]):
        raise UsageError("Specialist dispatch has no owned assignment; restore its original ledger before assessing it.", {})
    return found, index


def validate_assessments(state):
    """Validate immutable receipts and their original assignment relationships."""
    records = state.get("specialist_assessments")
    if not isinstance(records, list):
        raise UsageError("State requires a specialist_assessments array; restore the owner-written history.", {})
    ids = set()
    expected = INPUT_FIELDS | {"schema_version", "at", "assignment_index", "task", "role", "agent", "report_evidence", "delivery_evidence"}
    for record in records:
        if not isinstance(record, dict) or set(record) != expected or type(record.get("schema_version")) is not int or record["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
            raise UsageError("Unsupported or corrupt specialist assessment; preserve history and update the owner.", {})
        _input({key: record[key] for key in INPUT_FIELDS})
        text(record["at"], "assessment time")
        supervision.timestamp(record["at"])
        if record["id"] in ids:
            raise UsageError("Duplicate specialist assessment identity; restore the append-only owner history.", {})
        ids.add(record["id"])
        dispatch, index = _dispatch(state, record["dispatch"])
        if supervision.timestamp(record["at"]) < supervision.timestamp(state["assignments"][index]["at"]):
            raise UsageError("Specialist assessment predates its assignment; restore the original dated evidence.", {})
        if type(record["assignment_index"]) is not int or record["assignment_index"] != index or any(record[key] != dispatch[key] for key in ("task", "role", "agent")):
            raise UsageError("Specialist assessment no longer matches its original dispatch; restore the owned assignment relationship.", {})
        for key, source in (("report_evidence", "report"), ("delivery_evidence", "delivery")):
            validate_receipt(record[key])
            if record[key]["path"] != record[source]:
                raise UsageError("Specialist assessment receipt names a different artifact; preserve its original evidence.", {})


def record_assessment(state, state_path, data, at):
    """Append a lead assessment bound to a delivered enrollment's report bytes."""
    _input(data)
    supervision.timestamp(at)
    prior = next((row for row in state["specialist_assessments"] if row["id"] == data["id"]), None)
    if prior is not None:
        if any(prior[key] != data[key] for key in INPUT_FIELDS):
            raise UsageError("Assessment identity already names different input; record a new assessment without rewriting prior evidence.", {})
        return prior
    dispatch, index = _dispatch(state, data["dispatch"])
    if supervision.timestamp(at) < supervision.timestamp(state["assignments"][index]["at"]):
        raise UsageError("Assessment cannot predate dispatch; use the actual assessment time.", {})
    fleet = supervision.load(state_path)
    member = next((row for row in fleet["members"] if row["id"] == data["dispatch"]), None)
    if member is None or member["assignment"]["report"] != data["report"] or member["assignment"]["agent"] != dispatch["agent"] or member["assignment"]["task"] != dispatch["task"]:
        raise UsageError("Assessment must name the report enrolled before this dispatch; inspect supervision-status and preserve that report path.", {})
    report_evidence, _body = receipt(data["report"])
    delivery_evidence, delivered = receipt(data["delivery"])
    try:
        proof = json.loads(delivered)
    except json.JSONDecodeError as exc:
        raise UsageError("Delivery receipt is invalid JSON ({}); save the successful wait-report output before assessing the specialist.".format(exc.msg), {}) from None
    recovered = next((row for row in state["recovery"]["delivery_recoveries"]
                      if row["dispatch"] == dispatch["id"] and row["input"]["report"] == data["report"]
                      and row.get("found") is True and row["receipts"]["report"] == report_evidence
                      and row == proof), None)
    waited = (isinstance(proof, dict) and proof.get("found") is True
              and proof.get("agent") == dispatch["agent"] and proof.get("report_path") == data["report"])
    if not waited and recovered is None:
        raise UsageError("Delivery receipt must prove this worker's exact report arrived; a lifecycle status or pending checkpoint is insufficient.", {})
    result = {"schema_version": ASSESSMENT_SCHEMA_VERSION, "at": at, **data,
              "assignment_index": index, "task": dispatch["task"], "role": dispatch["role"], "agent": dispatch["agent"],
              "report_evidence": report_evidence, "delivery_evidence": delivery_evidence}
    state["specialist_assessments"].append(result)
    return result


def require_followup(state, state_path, assignments):
    """Require assessed prior work and retired observation ownership, not idle."""
    for role, name in assignments.items():
        prior = latest_assignment(state["assignments"], agent=name)
        if prior is None:
            raise UsageError("No previous specialist assignment exists; dispatch a fresh consultation first.", {})
        index, row = prior
        assessment = next((item for item in reversed(state["specialist_assessments"]) if item["assignment_index"] == index), None)
        if assessment is None or row["role"] != role:
            raise UsageError("Retained specialist needs the preceding assignment's saved lead assessment; run assess-specialist before following up.", {})
        for key in ("report_evidence", "delivery_evidence"):
            current, _body = receipt(assessment[key]["path"])
            if current != assessment[key]:
                raise UsageError("Specialist follow-up evidence changed; restore the original report and delivery receipt or start a fresh assessed handoff.", {})
        fleet = supervision.load(state_path)
        member = next((item for item in fleet["members"] if item["id"] == assessment["dispatch"]), None)
        if member is None or member["active"] or not member.get("resolution") or any(item["member"] == member["id"] for item in supervision.pending(fleet)):
            raise UsageError("Handle the preceding specialist's observations and resolve its enrollment before following up; a report assessment does not retire supervision.", {})
