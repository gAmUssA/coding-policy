"""Pure specialist requirements and evidence-grounded worker eligibility.

Responsibilities remain the canonical dispatch roles. Specialty and engagement
describe a particular assignment, never a new worker, permission or gate. The
operator's capability declarations establish candidate eligibility; neither
those declarations nor dispatch history certify expertise or completed work.
"""

from .config import CAPABILITY_ID, parse_capabilities
from .errors import UsageError
from .tiers import ROLE_ROUNDS


REQUIREMENTS_SCHEMA_VERSION = 1
CONSULTATION_ROLES = frozenset({"advisor", "investigator", "architect"})
CONTRIBUTOR_ROLES = frozenset({"developer", "architect", "advisor", "investigator"})
CONTRIBUTOR_ROUNDS = frozenset({"architect", "reconciliation", "test_plan"})
POSSIBLE_CONTRIBUTION = frozenset({"applied", "unknown", "sending", "sent_but_not_started"})
REQUIREMENT_FIELDS = frozenset({"specialty", "required_capabilities", "independent", "engagement"})


def normalize_requirement(record, role):
    """Validate one persisted assignment requirement without inventing defaults."""
    if role not in ROLE_ROUNDS:
        raise UsageError("Specialist requirements cannot change the pinned judge or invent a responsibility; choose a documented role.", {"role": role})
    if not isinstance(record, dict) or set(record) != REQUIREMENT_FIELDS:
        raise UsageError("Each specialist requirement needs specialty, required_capabilities, independent and engagement; use the documented requirements shape.", {"role": role})
    specialty = record["specialty"]
    if not isinstance(specialty, str) or not CAPABILITY_ID.fullmatch(specialty):
        raise UsageError("Specialty must be a lowercase name using letters, digits, underscores or hyphens; start it with a letter.", {"role": role})
    capabilities = parse_capabilities(record["required_capabilities"], "{} required_capabilities".format(role), UsageError)
    if not capabilities:
        raise UsageError("A specialist assignment needs at least one required capability; record the skills or tools its worker must have.", {"role": role})
    if type(record["independent"]) is not bool:
        raise UsageError("Specialist independent must be a JSON boolean; state whether this assignment requires an independent assessor.", {"role": role})
    if role in {"reviewer", "tester"} and not record["independent"]:
        raise UsageError("Reviewer and tester assignments require independent:true; use advisor for non-independent consultation.", {"role": role})
    engagement = record["engagement"]
    if (not isinstance(engagement, str) or not engagement.strip() or engagement != engagement.strip()
            or any(ord(char) < 32 for char in engagement)):
        raise UsageError("Engagement must be non-empty text without surrounding whitespace or control characters; preserve the consultation's stable identity.", {"role": role})
    return {"specialty": specialty, "required_capabilities": capabilities,
            "independent": record["independent"], "engagement": engagement}


def parse_requirements(payload, roles, task, *, allow_historical_architect=False):
    """New consultations need requirements; old architect receipts stay readable.

    The historical option is only for reading archived delivery evidence and
    reconstructing completed retry identities. Unsent work uses the default.
    """
    roles = list(roles)
    if payload is None:
        assignments = {}
    else:
        if (not isinstance(payload, dict) or set(payload) != {"schema_version", "assignments"}
                or type(payload.get("schema_version")) is not int
                or payload["schema_version"] != REQUIREMENTS_SCHEMA_VERSION
                or not isinstance(payload.get("assignments"), dict)):
            raise UsageError("Requirements must be a schema_version 1 object with an assignments map; use the documented requirements file.", {})
        assignments = payload["assignments"]
        if not assignments or set(assignments) - set(roles):
            raise UsageError("Requirements must name at least one role and only roles this plan assigns; correct the role keys.", {})
    missing = CONSULTATION_ROLES.intersection(roles) - set(assignments)
    if allow_historical_architect:
        missing -= {"architect"}
    if missing:
        raise UsageError("Roles {} require explicit specialist requirements; supply their specialty, capabilities, independence and engagement with --requirements.".format(", ".join(sorted(missing))), {"roles": sorted(missing)})
    if assignments and (not isinstance(task, str) or not task.strip()):
        raise UsageError("Specialist assignments require --task; preserve their task identity for independence and consultation continuity.", {})
    return {role: normalize_requirement(assignments[role], role) for role in roles if role in assignments}


def _contributor(row, assessment=None):
    if row.get("status", "unknown") not in POSSIBLE_CONTRIBUTION:
        return False
    if row.get("role") == "developer":
        return True
    if assessment is not None:
        return assessment["contribution"] != "none"
    tier = row.get("tier")
    if tier is None and isinstance(row.get("result"), dict):
        tier = row["result"].get("tier")
    return (row.get("role") in CONTRIBUTOR_ROLES
            or isinstance(tier, dict) and tier.get("round") in CONTRIBUTOR_ROUNDS
            or row.get("role") == "reviewer" and row.get("reviewer_scope") != "verification")


def selection_constraints(roles, agents, requirements, history, task, dispatches=(), assessments=(), candidate_names=None):
    """Return hard exclusions and a dispatch-familiarity hint, without I/O.

    Callers merge exclusions with explicit author exclusions and use the same
    result before planning and before an unsent apply. Same-task possible
    contributors remain barred after a clear or model change. External work
    and old rows without task/proposal provenance require the lead's explicit
    exclusions; an empty history never proves independence.
    """
    roles = list(roles)
    if set(requirements) - set(roles):
        raise UsageError("Requirements name a responsibility outside this assignment; replan with matching role keys.", {})
    normalized = parse_requirements(
        {"schema_version": REQUIREMENTS_SCHEMA_VERSION, "assignments": requirements} if requirements else None,
        roles, task,
    )
    by_assignment = {row["assignment_index"]: row for row in assessments}
    contributors = {row["agent"] for row in assessments
                    if task and row.get("task") == task and row["contribution"] in {"design", "implementation"}}
    for index, row in enumerate(history):
        if task and row.get("task") == task and _contributor(row, by_assignment.get(index)):
            contributors.add(row.get("agent"))
    for row in dispatches:
        if task and row.get("task") == task and _contributor(row, by_assignment.get(row.get("assignment_index"))):
            contributors.add(row.get("agent"))
    by_name = {agent.name: agent for agent in agents}
    names = sorted(by_name if candidate_names is None else set(candidate_names))
    excluded = {role: [] for role in roles}
    familiarity = {role: {} for role in roles}
    rationale = []
    for role in roles:
        requirement = normalized.get(role)
        independent = role in {"reviewer", "tester"} or requirement is not None and requirement["independent"]
        for name in names:
            agent = by_name.get(name)
            unconfigured = requirement is not None and agent is None
            missing = sorted(set(requirement["required_capabilities"]) - set(agent.capabilities if agent else ())) if requirement else []
            conflict = independent and name in contributors
            if missing or conflict or unconfigured:
                excluded[role].append(name)
                reasons = []
                if unconfigured:
                    reasons.append("worker absent from current config")
                if missing:
                    reasons.append("missing declared capabilities " + ", ".join(missing))
                if conflict:
                    reasons.append("same-task possible contribution recorded in the owner ledger")
                rationale.append("{} excludes {}: {}.".format(role, name, "; ".join(reasons)))
                continue
            if requirement is not None:
                familiar = any(row.get("task") == task and row.get("role") == role
                               and row.get("agent") == name and row.get("status") == "applied"
                               and row.get("requirements") == requirement for row in history)
                familiarity[role][name] = int(familiar)
        excluded[role].sort()
        if requirement is not None:
            rationale.append("{} requests {} for engagement {!r}; capability declarations establish eligibility and prior matching dispatch is a familiarity hint, never expertise or completion evidence.".format(
                role, requirement["specialty"], requirement["engagement"]))
        if independent and task:
            rationale.append("{} independence also requires the lead's exclusions for external authors and contributions missing task/proposal provenance.".format(role))
    return {"exclude": excluded, "familiarity": familiarity, "rationale": rationale}
