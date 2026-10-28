"""Deterministic role-to-agent assignment.

A pure function of (roles, snapshot, previous role counts, exclusions, cost
weights). Same inputs, same output, every time -- no clock, no filesystem, no
herdr. Planning never touches an agent.

Every role carries a cost weight: what one round in that seat is expected to
burn out of an agent's remaining budget. `developer` is the heaviest seat and
`reviewer` the lightest (DEFAULT_ROLE_COSTS), and an operator re-weighs any of
them with a `role_costs` map in config.json. Seats are filled heaviest first,
whatever order `--roles` arrives in, and each seat goes to the eligible agent
that leaves the round's minimum projected headroom highest -- the minimum
across the agents holding a seat, per `skills/herdr-teamlead/references/team-operation.md`
("Headroom is the minimum remaining window per worker, never the average").
An agent holding no seat burns nothing, so it is not part of that minimum.

`exclude` bars agents from a role, as `{role: [agent, ...]}`. Nobody reviews or
verifies the branch they wrote, so the lead bars the author from those seats. A
role whose eligible field is empty is a PlanError, never a silent drop.

Ranking is only as honest as the field it sorts, so three refusals guard the
field itself before any seat is filled: a snapshot that does not cover every
agent `config.json` declares, an `--exclude` list the snapshot matches no name
of, and a ranked seat whose measured field holds one agent. Each of the three
is a measurement that missed the fleet rather than a fleet with no capacity,
and each used to plan a forced pick that read like a ranked one.

Ordering within one role, in full:

1. Agents excluded from that role are not candidates at all, and neither is
   one whose pick would leave a later role with nobody eligible.
2. Agents with a known headroom sort before agents whose headroom is null
   (busy, unmeasured, or unparseable).
3. Known-headroom agents sort by the round's projected minimum the pick would
   leave, descending -- this candidate's headroom minus this seat's weight,
   floored by what the seats already filled projected.
4. A tie there sorts by the candidate's own projected headroom, descending.
   Once the floor binds, that is the agent with the most headroom left.
5. A remaining tie is broken toward the agent that has held *the role being
   assigned* the fewest times, which stops one agent from owning `developer`
   forever.
6. Any remaining tie is broken by agent name, ascending.
7. Null-headroom agents sort by name alone. They are the fallback pool, and
   the ledger has nothing useful to say about an agent nobody could measure.

`assignments` comes back keyed in the caller's `--roles` order; `rationale`
reads in the order the seats were filled, heaviest first, because the field a
pick chose from only makes sense in that order.

For a requirement-bearing assignment, composition.py supplies capability and
contribution exclusions before this ranking. An affordable candidate with a
confirmed matching task/engagement dispatch ranks before other affordable
candidates, then the existing headroom ordering decides. Legacy assignments
without requirements retain the ordering above.
"""

import math

from .diagnostics import stderr_warn
from .errors import PlanError
from .parsers import headroom_pct

#: Plan document version. 2 adds the optional `judge` object carrying the
#: pinned seat's agent, model and effort. Version 3 adds round-tier data. Additive: a version-1
#: plan simply has no `judge` key, which is indistinguishable from a version-2
#: plan that assigned no judge seat, so both readers take the same path.
#: Version 5 adds normalized specialist requirements when requested. Version 6
#: adds `judge.mode`, the seat's declared adjudication-or-diagnosis choice, so
#: the start and the dispatch read the lead's decision rather than retaking it
#: (#425). Additive: a version-5 plan simply carries no mode, and its readers
#: refuse the start rather than defaulting one.
#: Saved plans are owned by teamlead; its CLI migrates file-backed plans on
#: read and refuses future versions. See skills/herdr-teamlead/state-schema.md.
PLAN_SCHEMA_VERSION = 7


def upgrade_plan(payload):
    """Validate a saved plan and return (current document, migrated).

    Versions 1-6 used the same assignment map. Optional metadata stays absent
    when it was never recorded (notably judge.mode); migration invents no
    dispatch authorization. Version 7 adds the ineligible-agent map.
    """
    if not isinstance(payload, dict) or "assignments" not in payload and "schema_version" not in payload:
        return payload, False  # Legacy, ad-hoc role -> agent instruction.
    version = payload.get("schema_version", 1)
    if type(version) is not int or not 1 <= version <= PLAN_SCHEMA_VERSION:
        raise PlanError("No usable saved plan at schema_version {!r}; re-run teamlead plan with a compatible owner. The file is unchanged.".format(version), {})
    if not isinstance(payload.get("assignments"), dict) or not payload["assignments"]:
        raise PlanError("Saved plan assignments must be a non-empty object; re-run teamlead plan. The file is unchanged.", {})
    if version < 7 and "ineligible_agents" in payload:
        raise PlanError("Older plan contains version-7 ineligible_agents; restore the original plan or re-plan. The file is unchanged.", {})
    result = dict(payload)
    if version < 7:
        result.update(schema_version=PLAN_SCHEMA_VERSION, ineligible_agents={})
    skipped = result.get("ineligible_agents")
    if not isinstance(skipped, dict) or any(not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key] for key in ("reason", "message")) for value in skipped.values()):
        raise PlanError("Saved plan ineligible_agents must map names to reason/message objects; re-run teamlead plan. The file is unchanged.", {})
    return result, version != PLAN_SCHEMA_VERSION or "schema_version" not in payload

#: What one round in each seat is expected to burn, in points of the agent's
#: remaining headroom percentage. The ORDER is what the planner acts on:
#: heaviest seat first, to the agent that can best afford it. The magnitudes
#: are the fleet's working calibration, re-derived by measuring rather than by
#: argument; an operator overrides any of them per role with a `role_costs`
#: map in config.json.
DEFAULT_ROLE_COSTS = {
    "developer": 12.0,
    "tester": 10.0,
    "reviewer": 5.0,
    # The judge runs the top model at its highest effort against a window it
    # shares with another worker, so one ruling costs more than one build.
    # Weighed explicitly: an unweighed role inherits DEFAULT_ROLE_COST below,
    # which would price the most expensive seat under `developer` by accident.
    "judge": 15.0,
    "architect": 10.0,
    "critic": 5.0,
    "release": 8.0,
    "lead": 12.0,
    "advisor": 8.0,
    "investigator": 10.0,
}

#: Weight for a role nobody has weighed -- a folded seat, or a role a later
#: round invents. Between reviewer and developer: free would let an unweighed
#: seat outrank every measured one.
DEFAULT_ROLE_COST = 8.0


def _sort_key(name, headroom, cost, role, counts, floor, familiarity=None, same_family=None):
    """Rank one candidate for one role. Lower sorts first.

    `same_family` is `{role: {agent: 1 when it shares a model family with the
    task's contributors}}`: a tie-break after headroom, before rotation.
    """
    if familiarity is not None:
        affordable = headroom is not None and float(headroom) - cost >= 0
        # Continuity is useful only inside an affordable, eligible field. It
        # never elevates an exhausted or unmeasured worker over usable capacity.
        affinity = familiarity.get(role, {}).get(name, 0) if affordable else 0
        return (0 if affordable else 1, -affinity,
                *_sort_key(name, headroom, cost, role, counts, floor, same_family=same_family))
    family = (same_family or {}).get(role, {}).get(name, 0)
    if headroom is None:
        return (1, 0.0, 0.0, family, 0, name)
    projected = float(headroom) - cost
    team_min = projected if floor is None else min(floor, projected)
    return (0, -team_min, -projected, family, counts.get(role, {}).get(name, 0), name)


def _headroom_of(name, record, warn):
    """This agent's headroom as a float, or None when it is not a usable number.

    A snapshot is a file on disk: hand-edited, written by an older build, or
    truncated mid-write. `float()` on whatever it happens to hold crashes the
    plan, and a crash here loses the whole round over one bad field. Anything
    that is not a finite number reads as unknown instead, which already has a
    defined place in the ordering -- last, and named in the rationale.
    """
    if not isinstance(record, dict):
        warn(
            "snapshot entry for {!r} is {}, not an object; treating its "
            "headroom as unknown.".format(name, type(record).__name__)
        )
        return None

    value = record.get("headroom_pct")
    if value is None:
        return None

    # bool is a subclass of int, and `true` is not 100% headroom.
    if isinstance(value, bool):
        warn(
            "headroom_pct for {!r} is {!r}, not a number; treating it as "
            "unknown.".format(name, value)
        )
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        warn(
            "headroom_pct for {!r} is {!r}, which is not a number; treating it "
            "as unknown.".format(name, value)
        )
        return None

    # NaN compares false against everything, which would make the sort order
    # depend on input order -- and this planner is documented deterministic.
    if math.isnan(number) or math.isinf(number):
        warn(
            "headroom_pct for {!r} is {!r}, which cannot be ordered; treating "
            "it as unknown.".format(name, value)
        )
        return None
    return number


def _window_groups(agents):
    """`{agent: window_group}` for every agent that declares one.

    Two workers can authenticate as one subscription -- the judge seat runs a
    dedicated worker on the same Claude account as `claude`, and Fable draws
    on that account's weekly pool rather than a pool of its own. `measure`
    copies each agent's declared `window_group` onto its record; agents
    sharing a value share one window, and an agent that declares none has a
    window to itself.
    """
    groups = {}
    for name, record in agents.items():
        if not isinstance(record, dict):
            continue
        group = record.get("window_group")
        if isinstance(group, str) and group:
            groups[name] = group
    return groups


def _model_offset(record, recorded, model):
    """The adjustment that re-reads one record's windows under `model`.

    `measure` scopes each record's `headroom_pct` to the seat's declared
    `model_label`, but a seat runs the model its tier row or the judge pin
    names for this round. A worker labelled Opus that a round seats on a
    Fable tier is bound by the `Current week (Fable)` window, and a Fable
    pool-mate's spent Fable window never binds an Opus judge. The re-read is
    kept as an offset from the recorded headroom: the fill loop charges
    seats against the recorded values, and the offset carries that spend
    through unchanged.

    0.0 -- the recorded reading -- when there is no model to scope to, no
    recorded headroom, or no usable `windows` (missing, empty, malformed, or
    non-finite). The stored windows mean the same at every snapshot version,
    so a migrated record is re-read like a fresh one.
    """
    if not model or recorded is None or not isinstance(record, dict):
        return 0.0
    windows = record.get("windows")
    if not isinstance(windows, dict) or not windows:
        return 0.0
    if not all(_usable_window(window) for window in windows.values()):
        return 0.0
    scoped = headroom_pct(windows, model)
    return 0.0 if scoped is None else float(scoped) - recorded


def _usable_window(window):
    """Whether a snapshot window carries a finite numeric `remaining_pct`."""
    if not isinstance(window, dict):
        return False
    value = window.get("remaining_pct")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _costs_for(roles, role_costs):
    """Merge the operator's overrides over the defaults, one entry per role.

    `role_costs` arrives already validated by `config.parse_role_costs`, the
    only path a config file reaches this module by: each loader validates the
    file it reads.
    """
    merged = {}
    overrides = role_costs or {}
    for role in roles:
        if role in overrides:
            merged[role] = float(overrides[role])
        else:
            merged[role] = DEFAULT_ROLE_COSTS.get(role, DEFAULT_ROLE_COST)
    return merged


def _normalize_exclusions(exclude, roles):
    """`{role: [agent, ...]}` for every role, de-duplicated and name-ordered.

    An exclusion naming a role nobody is assigning is refused: `--exclude
    reviewr=grok` would otherwise read as no exclusion at all and seat the
    author it was typed to keep out.
    """
    normalized = {role: [] for role in roles}
    unknown = []
    for role, names in (exclude or {}).items():
        if role not in normalized:
            unknown.append(role)
            continue
        for name in names:
            if name not in normalized[role]:
                normalized[role].append(name)
    if unknown:
        raise PlanError(
            "--exclude names role {} that this plan is not assigning - the "
            "roles being assigned are {}. Fix the role name, or add it to "
            "--roles.".format(", ".join(sorted(unknown)), ", ".join(roles)),
            {"unknown_roles": sorted(unknown), "roles": list(roles)},
        )
    for names in normalized.values():
        names.sort()
    return normalized


def _augment(role, agents, excluded, match, seen):
    """One augmenting-path step of Kuhn's bipartite matching."""
    for agent in agents:
        if agent in excluded[role] or agent in seen:
            continue
        seen.add(agent)
        holder = match.get(agent)
        if holder is None or _augment(holder, agents, excluded, match, seen):
            match[agent] = role
            return True
    return False


def _fillable(roles, agents, excluded):
    """Can every role here still get a distinct eligible agent?

    A pick that ignored this strands a later role: bar the branch's author
    from reviewer AND tester and the author has exactly one seat left, so
    handing `developer` to whoever has the most headroom leaves the author
    nowhere and the round unplannable. Roles and agents number a handful, so
    the matching is Kuhn's, run per candidate.
    """
    match = {}
    for role in roles:
        if not _augment(role, agents, excluded, match, set()):
            return False
    return True


def _refuse_uncovered_roster(roster, agents):
    """Halt when the snapshot leaves out an agent `config.json` declares.

    A declared worker absent from the snapshot is a measurement that missed
    the fleet, not a worker with nothing left: `measure` pointed at one
    freshly spawned pane writes a snapshot of one, and every seat then ranks
    against that pane while an idle worker at 97% headroom stays invisible to
    the sort. Measuring the roster is what keeps that worker a candidate, so
    the plan refuses here rather than filling seats from whatever happened to
    be measured.

    An agent present but unmeasured is a different state: it carries a null
    headroom, sorts last, and is named in the rationale.
    """
    if not roster:
        return
    uncovered = sorted(set(roster) - set(agents))
    if not uncovered:
        return
    raise PlanError(
        "Snapshot does not cover {} - config.json declares {} and this "
        "snapshot measured {}. Run `teamlead measure` over the roster before "
        "planning, or drop the worker from config.json.".format(
            ", ".join(uncovered), ", ".join(sorted(roster)), ", ".join(sorted(agents))
        ),
        {"uncovered": uncovered, "roster": sorted(roster), "agents": sorted(agents)},
    )


def _refuse_inert_exclusions(excluded, agents):
    """Halt when the snapshot matches no name `--exclude` bars.

    An exclusion the snapshot cannot match bars nobody, so the author the lead
    meant to keep out of review is eligible again and the round reads as if
    independence had been enforced. One stray name among several is a typo and
    stays a note (`_notes`); every name stray means the caller is excluding
    against a fleet this snapshot never measured.
    """
    named = sorted({name for names in excluded.values() for name in names})
    if not named or any(name in agents for name in named):
        return
    raise PlanError(
        "--exclude names {}, and the snapshot contains none of them ({}) - "
        "every exclusion is inert, so nothing was barred. Re-measure the "
        "roster, or correct the names against `herdr agent list`.".format(
            ", ".join(named), ", ".join(sorted(agents))
        ),
        {"inert_exclusions": named, "agents": sorted(agents)},
    )


def _refuse_degenerate_field(roles, agents, judge_agent, roster):
    """Halt when a ranked seat has no field to rank.

    One candidate is a forced pick, and reporting it as a headroom-ranked one
    hides that nothing was compared: five consecutive rounds filled from a
    one-pane snapshot drained a single window to 77% used while another sat at
    1%. The measured field, not the field after `--exclude`, is what this
    reads: an operator barring the author down to one candidate chose that
    narrowing deliberately, whereas a one-agent snapshot chose nothing.

    The pinned judge is not in that field. Its seat is assigned rather than
    ranked, and every other seat bars it structurally, so a `{judge, worker}`
    snapshot offers a ranked seat exactly one candidate.

    A config whose own rankable roster holds one agent is exempt: it has no
    rotation for the snapshot to have missed.
    """
    if roster and len({name for name in roster if name != judge_agent}) <= 1:
        return
    field = sorted(name for name in agents if name != judge_agent)
    if len(field) > 1:
        return
    ranked = [role for role in roles if role != "judge" or not judge_agent]
    if not ranked:
        return
    measured = "one agent ({})".format(field[0]) if field else "no agent at all"
    raise PlanError(
        "Role {} would be filled from a measured field of {} - one candidate "
        "is a forced pick, not a ranking. Run `teamlead measure` over the "
        "roster so every idle worker is a candidate, then plan "
        "again.".format(", ".join(repr(role) for role in ranked), measured),
        {"roles": ranked, "agents": field},
    )


def _unfillable_message(role, barred, remaining, later_roles, constrained=False):
    """Why one seat could not be filled, and what to change."""
    parts = [
        "Cannot fill role {!r} without leaving a later role with no eligible "
        "agent.".format(role)
        if later_roles
        else "Cannot fill role {!r}.".format(role),
        "--exclude bars {} from it.".format(", ".join(barred)) if barred else "",
        "Still unassigned: {}.".format(", ".join(sorted(remaining))) if remaining else "",
        "Roles left to fill after it: {}.".format(", ".join(later_roles))
        if later_roles
        else "",
        "Measure another eligible worker or assign fewer simultaneous roles; preserve capability and independence requirements."
        if constrained else "Drop an exclusion, measure another agent, or assign fewer roles.",
    ]
    return " ".join(part for part in parts if part)


def _refuse_unaffordable_judge(judge_agent, headrooms, cost, groups, offsets=None):
    """Halt the round when the pinned judge's window cannot afford it.

    The judge seat has no substitute: it is pinned, and its worker shares a
    window with `claude`. When that window cannot cover one ruling there is no
    second-best seat to fall back to, so the planner refuses rather than
    dispatching a round it cannot finish.

    One window reports through every worker on it, and those workers are
    measured one after another rather than at one instant. Two records for the
    same window can therefore disagree, and the lower reading is the later
    truth about a pool that only drains. Affordability is the MINIMUM known
    headroom across the window, never the judge's own record alone.

    An unknown headroom is not exhaustion and never refuses on its own -- it
    is unmeasured, and the existing unknown-headroom handling already names it
    in the rationale.

    `offsets` re-reads each record under the pinned judge model (see
    `_model_offset`), so a pool-mate's per-model window for a model
    the judge does not run never halts the judge.

    Called from inside the fill loop, at the judge's own turn, so `headrooms`
    already reflects what the seats filled before it spent from the same
    window. Checked before the loop instead, a round seating a heavier
    developer on the shared pool would pass the check and leave the judge
    projected below zero.
    """
    group = groups.get(judge_agent)
    if group is None:
        window = [judge_agent] if judge_agent in headrooms else []
    else:
        window = [name for name in headrooms if groups.get(name) == group]

    offsets = offsets or {}
    readings = {
        name: headrooms[name] + offsets.get(name, 0.0)
        for name in window
        if headrooms.get(name) is not None
    }
    if not readings:
        return

    lowest = min(readings, key=lambda name: (readings[name], name))
    headroom = readings[lowest]
    if headroom - cost < 0:
        through = (
            "" if lowest == judge_agent
            else " (read through {!r}, which shares its window)".format(lowest)
        )
        raise PlanError(
            "Judge worker {!r} has {:g}% headroom{} and one ruling costs {:g}% "
            "- the round halts. There is no substitute judge: wait for the "
            "window to reset, or have the operator rule.".format(
                judge_agent, headroom, through, cost
            ),
            {
                "role": "judge",
                "judge_agent": judge_agent,
                "headroom_pct": headroom,
                "measured_through": lowest,
                "window_group": group,
                "cost": cost,
            },
        )


def _empty_field_message(role, agents, judge_agent, operator_excluded, role_refusals, tier_candidates=None):
    """Compose independent bars within the workers that can own this seat."""
    domain = set(agents) & {judge_agent} if role == "judge" else set(agents) - {judge_agent}
    operator_names = sorted(set(operator_excluded.get(role, [])) & domain)
    refusals = {name: message for name, message in sorted((role_refusals or {}).get(role, {}).items())
                if name in domain}
    clauses = [("No agent is eligible for role {!r}." if tier_candidates is None
                else "No tier is eligible for {!r}.").format(role)]
    if operator_names:
        clauses.append("--exclude bars {}. Drop an exclusion if appropriate.".format(
            ", ".join(operator_names)))
    clauses.extend(refusals.values())
    if role == "judge":
        clauses.append("Role 'judge' is pinned to worker {!r}; check that worker's judge configuration and eligibility.".format(judge_agent))
    else:
        if tier_candidates is not None and any(
                name not in operator_names and name not in refusals
                and name not in tier_candidates.get(role, {}) for name in domain):
            # Candidate absence also covers a refused local round; do not
            # claim it proves a missing row, or suggest unlocking pi's roles.
            clauses.append("Configure a tier row for this round on an eligible worker, or select an eligible metered worker for the round.")
        clauses.append("Measure another eligible worker.")
    return " ".join(clauses), refusals


def plan(roles, snapshot, counts=None, exclude=None, role_costs=None, snapshot_ref=None, warn=None, judge_agent=None, judge_tier=None, judge_mode=None, tier_candidates=None, rounds=None, familiarity=None, requirements=None, selection_rationale=None, roster=None, operator_exclude=None, same_family=None, local_agents=None, ineligible_agents=None, role_refusals=None):
    """Assign `roles` to the agents in `snapshot`, heaviest seat first.

    `counts` is `{role: {agent: times_held}}` from the state ledger; omit it
    to plan with no history. `exclude` is `{role: [agent, ...]}`: agents
    barred from that role, the author of the branch under review first among
    them. `role_costs` overrides DEFAULT_ROLE_COSTS per role and arrives
    validated from `config.parse_role_costs`. `snapshot_ref` is echoed back so
    a caller can tell which measurement the plan was built on. `judge_agent`
    is the worker the `judge` block pins the seat to: the planner never ranks
    that seat, and never gives the pinned worker any other one. `judge_tier`
    is that block's `{model, effort}`; when the judge seat is planned the
    document echoes it back as the tier the worker is started on, so a caller
    builds the launch flags from the config rather than typing them by hand.
    `judge_mode` is the seat's declared mode, echoed beside the tier so the
    start and the dispatch read the lead's choice instead of retaking it.

    `roster` is the agent names `config.json` declares, so the plan can tell a
    snapshot that missed the fleet from a fleet with no capacity; omit it to
    plan against whatever the snapshot holds. `operator_exclude` is the
    exclusion map as the operator typed it, before a caller merges its own
    generated bars into `exclude`; omit it when `exclude` carries only what
    the operator asked for. `same_family` is the model-family tie-break from
    `composition.selection_constraints`; omit it to rank without one.
    `role_refusals` maps each role to worker refusal messages from
    `require_role`, preserving the reason for those generated exclusions.

    Raises PlanError when there are no roles, no agents, fewer agents than
    roles, an exclusion naming a role nobody is assigning, or a role whose
    eligible field is empty -- silently dropping a role would hide work nobody
    is doing. Raises it again for a field nothing could have been ranked in: a
    snapshot missing a declared agent, an `--exclude` list naming nobody the
    snapshot holds, or a ranked seat measured against one agent.
    """
    counts = counts or {}
    roles = list(roles)
    if not roles:
        raise PlanError(
            "No roles to assign - pass --roles with a comma-separated list, "
            "e.g. --roles developer,tester,reviewer.",
            {},
        )
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        raise PlanError(
            "Role {} appears more than once in --roles - each role is assigned "
            "to exactly one agent.".format(", ".join(duplicates)),
            {"duplicates": duplicates},
        )

    # A snapshot is `teamlead measure` output: an object whose `agents` is an
    # object keyed by agent name. Anything else is a hand-edited or wrong file,
    # and reading it as if it were the right shape crashes on `.items()` a few
    # lines down instead of naming the problem.
    if snapshot is None:
        snapshot = {}
    if not isinstance(snapshot, dict):
        raise PlanError(
            "Snapshot is a JSON {}, not an object - pass --snapshot pointing at "
            "a file `teamlead measure` wrote.".format(type(snapshot).__name__),
            {"snapshot_type": type(snapshot).__name__},
        )
    agents = snapshot.get("agents") or {}
    if not isinstance(agents, dict):
        raise PlanError(
            "Snapshot `agents` is a JSON {}, not an object keyed by agent name - "
            "re-run `teamlead measure`, or pass --snapshot pointing at its "
            "output.".format(type(agents).__name__),
            {"agents_type": type(agents).__name__},
        )
    if not agents:
        raise PlanError(
            "Snapshot contains no agents - run `teamlead measure` first, or "
            "pass --snapshot pointing at a snapshot that has an `agents` object.",
            {},
        )
    # Before the capacity count: a snapshot that missed declared workers fails
    # "measure more agents or pass fewer roles" first, which invites adding
    # panes until the count fits instead of measuring the roster the config
    # declares (#400). The field is wrong before the arithmetic is.
    _refuse_uncovered_roster(roster, agents)
    if len(agents) < len(roles):
        raise PlanError(
            "Cannot assign {} roles across {} agent(s) - measure more agents or "
            "pass fewer roles.".format(len(roles), len(agents)),
            {"roles": roles, "agents": sorted(agents)},
        )

    warn = warn or stderr_warn
    excluded = _normalize_exclusions(exclude, roles)

    # The judge seat is pinned, never ranked. Without a `judge` block there is
    # nothing to pin it to, and ranking an ordinary worker into the seat would
    # produce a plan carrying no usable tier -- which fails later, at worker
    # startup, a long way from the config that caused it.
    if "judge" in roles and not judge_agent:
        raise PlanError(
            "Role 'judge' needs a `judge` block in config.json naming the "
            "worker, model and effort the seat is pinned to - "
            "this config has none, and the planner never ranks an ordinary "
            "worker into the judge seat. Add the block, or drop 'judge' from "
            "--roles.",
            {"role": "judge"},
        )

    # Only the names the operator typed: a generated bar naming a measured
    # agent would otherwise vouch for an exclusion list that matched nobody.
    operator_excluded = _normalize_exclusions(exclude if operator_exclude is None else operator_exclude, roles)
    _refuse_inert_exclusions(operator_excluded, agents)
    _refuse_degenerate_field(roles, agents, judge_agent, roster)

    # Expressed as exclusions so eligibility, fillability and the rationale all
    # read the same way they do for every other seat.
    if judge_agent:
        if "judge" in roles and judge_agent not in agents:
            raise PlanError(
                "Role 'judge' is pinned to worker {!r}, which is not in the "
                "snapshot ({}) - measure it, or drop 'judge' from --roles.".format(
                    judge_agent, ", ".join(sorted(agents))
                ),
                {"role": "judge", "judge_agent": judge_agent, "agents": sorted(agents)},
            )
        for role in roles:
            if role == "judge":
                excluded[role] = sorted(
                    name for name in agents if name != judge_agent
                )
            elif judge_agent in agents and judge_agent not in excluded[role]:
                excluded[role] = sorted(set(excluded[role]) | {judge_agent})
    for role in roles:
        if all(name in excluded[role] for name in agents):
            if ineligible_agents:
                reasons = ", ".join("{!r} is {}".format(name, value["reason"]) for name, value in sorted(ineligible_agents.items()))
                raise PlanError("Cannot fill role {!r}: {} and no other worker meets the declared requirements. Measure another eligible worker or assign fewer simultaneous roles; preserve capability and independence requirements.".format(role, reasons), {"role": role, "ineligible_agents": ineligible_agents})
            if selection_rationale:
                raise PlanError(
                    "No agent is eligible for role {!r}. Measure another eligible worker or correct verified capability declarations; preserve independence requirements. {}".format(
                        role, " ".join(selection_rationale)),
                    {"role": role, "excluded": list(excluded[role]), "agents": sorted(agents),
                     "eligibility": list(selection_rationale)},
                )
            message, refusals = _empty_field_message(
                role, agents, judge_agent, operator_excluded, role_refusals)
            raise PlanError(
                message,
                {"role": role, "excluded": list(excluded[role]), "agents": sorted(agents),
                 "role_refusals": refusals},
            )
    local_agents = {name for name in (local_agents or []) if snapshot.get("schema_version") == 5 and isinstance(agents.get(name), dict)
        and agents[name].get("kind") == "pi" and agents[name].get("budget_mode") == "local_unmetered"
        and not agents[name].get("skipped") and not agents[name].get("error")
        and agents[name].get("state") in {"idle", "done"} and isinstance(agents[name].get("host_eligibility"), dict) and agents[name]["host_eligibility"].get("eligible") is True}
    for role in roles:
        excluded[role] = sorted(set(excluded[role]) | {name for name, record in agents.items()
            if isinstance(record, dict) and (record.get("kind") == "pi" or record.get("budget_mode") == "local_unmetered") and (name not in local_agents or role != "developer")})
    costs = _costs_for(roles, role_costs)
    if tier_candidates is not None:
        for role in roles:
            if role not in DEFAULT_ROLE_COSTS and role not in (role_costs or {}):
                raise PlanError("Tiered role {!r} needs an explicit cost; no fallback weight is used.".format(role), {})
            for name in agents:
                if name not in tier_candidates.get(role, {}):
                    excluded[role] = sorted(set(excluded[role]) | {name})
            if all(name in excluded[role] for name in agents):
                message, refusals = _empty_field_message(
                    role, agents, judge_agent, operator_excluded, role_refusals, tier_candidates)
                raise PlanError(message, {"role": role, "role_refusals": refusals})

    def candidate_cost(role, name):
        if name in local_agents:
            return 0.0
        tier = (tier_candidates or {}).get(role, {}).get(name) or {}
        return costs[role] * tier.get("effective_multiplier", 1.0)

    headrooms = {
        name: None if name in local_agents else _headroom_of(name, record, warn) for name, record in agents.items()
    }
    groups = _window_groups(agents)
    recorded = dict(headrooms)
    offsets = {}

    def seat_model(role, name):
        """The model `name` runs in `role`: the judge pin, else its tier row."""
        if role == "judge" and name == judge_agent:
            return (judge_tier or {}).get("model")
        return ((tier_candidates or {}).get(role, {}).get(name) or {}).get("model")

    def offset(name, model):
        if (name, model) not in offsets:
            offsets[(name, model)] = _model_offset(agents.get(name), recorded[name], model)
        return offsets[(name, model)]

    def seat_headrooms(role):
        """Every agent's current headroom under the model it runs in `role`."""
        return {
            name: None if value is None else value + offset(name, seat_model(role, name))
            for name, value in headrooms.items()
        }

    judge_model = (judge_tier or {}).get("model")
    judge_offsets = {name: offset(name, judge_model) for name in headrooms}
    remaining = set(headrooms)
    picks = {}
    rationale = []
    floor = None

    # Heaviest seat first. An equal-cost pair keeps the caller's --roles order,
    # so a role set nobody has weighed plans exactly as it did before weights.
    fill_order = sorted(roles, key=lambda role: (
        -max((candidate_cost(role, name) for name in agents if name not in excluded[role]), default=costs[role]),
        roles.index(role),
    ))

    def rank(role, name, scoped):
        affinity = familiarity.get(role, {}).get(name, 0) if role in (requirements or {}) and familiarity else 0
        if name in local_agents:
            return (0, -affinity, 0, counts.get(role, {}).get(name, 0), name)
        affordable = scoped[name] is not None and scoped[name] - candidate_cost(role, name) >= 0
        return (0 if affordable else 1, -affinity if affordable else 0, 1,
                *_sort_key(name, scoped[name], candidate_cost(role, name), role, counts, floor, same_family=same_family))

    for index, role in enumerate(fill_order):
        cost = candidate_cost(role, judge_agent) if role == "judge" else costs[role]
        barred = excluded[role]
        if role == "judge" and judge_agent:
            _refuse_unaffordable_judge(judge_agent, headrooms, cost, groups, judge_offsets)
        later_roles = fill_order[index + 1:]
        scoped = seat_headrooms(role)
        ranked = sorted(
            (name for name in remaining if name not in barred),
            key=lambda name: rank(role, name, scoped) if local_agents else _sort_key(name, scoped[name], candidate_cost(role, name), role, counts, floor,
                                      familiarity if role in (requirements or {}) else None, same_family),
        )
        # The ordering above says who SHOULD hold the seat; the matching says
        # who still can without stranding a later role. First candidate that
        # satisfies both wins, so exclusions never silently reorder by cost.
        chosen = next(
            (
                name
                for name in ranked
                if _fillable(later_roles, sorted(remaining - {name}), excluded)
            ),
            None,
        )
        if chosen is None:
            raise PlanError(
                _unfillable_message(role, barred, remaining, later_roles, bool(selection_rationale)),
                {
                    "role": role,
                    "excluded": list(barred),
                    "assigned": dict(picks),
                    "unassigned": sorted(remaining),
                    **({"eligibility": list(selection_rationale)} if selection_rationale else {}),
                },
            )
        remaining.discard(chosen)
        picks[role] = chosen
        cost = candidate_cost(role, chosen)
        rationale.append(
            "{} -> {} (local_unmetered; no subscription-window charge; lead verification required)".format(role, chosen) if chosen in local_agents else _explain(role, chosen, ranked, scoped, counts, cost, floor, barred)
        )
        if role in (requirements or {}) and (familiarity or {}).get(role, {}).get(chosen):
            rationale.append("{} -> {} has a prior confirmed dispatch for the same task and engagement; familiarity ranks before headroom only when this projected seat is affordable.".format(role, chosen))
        headroom = scoped[chosen]
        if headroom is not None and chosen not in local_agents:
            projected = headroom - cost
            floor = projected if floor is None else min(floor, projected)

        # One window, two workers: a seat's burn reduces what its pool-mates
        # have left, so the next seat ranks against what the pool actually
        # holds. Skipping this double-counts one window and over-commits it.
        #
        # EVERY worker on the window is charged, not just the unassigned ones.
        # Affordability reads the minimum across the whole window, so a worker
        # already holding a seat keeps a stale reading that the minimum then
        # believes -- a window spent to zero by an earlier seat still looked
        # affordable through its pool-mate's untouched number.
        group = groups.get(chosen)
        if group is not None:
            for name in headrooms:
                remaining_headroom = headrooms[name]
                if groups.get(name) == group and remaining_headroom is not None:
                    headrooms[name] = remaining_headroom - cost

    rationale.extend(_notes(excluded, agents, warn))
    rationale.extend(selection_rationale or [])

    # The loop removes each pick from `remaining`, so a repeat is impossible
    # by construction. Asserted anyway: `apply` briefs one pane per role, and
    # a duplicate here would mean one role silently overwriting another.
    chosen_agents = list(picks.values())
    if len(set(chosen_agents)) != len(chosen_agents):
        raise PlanError(
            "Planner produced a duplicate agent in {!r} - this is a bug in "
            "teamlead, not in your snapshot. Please report it.".format(picks),
            {"assignments": dict(picks)},
        )

    document = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "ineligible_agents": ineligible_agents or {},
        # Keyed in the caller's order, not the fill order: --roles still shapes
        # the document even though it no longer decides which seat is heaviest.
        "assignments": {role: picks[role] for role in roles},
        "rationale": rationale,
        "snapshot_ref": snapshot_ref
        if snapshot_ref is not None
        else {"source": None, "measured_at": (snapshot or {}).get("measured_at")},
    }

    # The judge seat is pinned to a tier, not just a worker. Echoing the tier
    # here is what makes the config the single place a model swap happens: the
    # caller builds the worker's launch flags from this, never by hand. A
    # model that takes no effort flag echoes `null` rather than an empty
    # string, so a caller can tell "no effort" from "unset".
    if "judge" in roles and judge_agent:
        tier = judge_tier or {}
        document["judge"] = {
            "agent": judge_agent,
            "model": tier.get("model") or None,
            "effort": tier.get("effort") or None,
        }
        if tier.get("launch_args"):
            document["judge"]["launch_args"] = tier["launch_args"]
        # The seat's declared mode travels with the plan, so `start-judge` and
        # `apply` read the choice the lead already made rather than taking it
        # again -- or, worse, defaulting it (#425).
        if judge_mode:
            document["judge"]["mode"] = judge_mode

    if tier_candidates is not None:
        document["tiers"] = {
            role: tier_candidates[role][picks[role]] for role in roles
        }
        document["rounds"] = rounds or {}

    if requirements:
        document["requirements"] = requirements

    return document


def _notes(excluded, agents, warn):
    """Trailing rationale lines about the snapshot this plan ran on."""
    notes = []
    named = sorted({name for names in excluded.values() for name in names})
    stray = [name for name in named if name not in agents]
    if stray:
        # A misspelt exclusion is an input mistake, so it goes to stderr too:
        # the operator meant to bar somebody and barred nobody.
        warn(
            "--exclude names {}, which the snapshot does not contain; check "
            "the spelling against `herdr agent list`.".format(", ".join(stray))
        )
        notes.append(
            "note: --exclude named {}, which this snapshot does not contain, so "
            "that exclusion changed nothing. Check the spelling against "
            "`herdr agent list`.".format(", ".join(stray))
        )
    skipped = sorted(
        name
        for name, record in agents.items()
        if isinstance(record, dict) and record.get("skipped") and record.get("kind") != "pi"
    )
    if skipped:
        notes.append(
            "note: stale headroom for {} - skipped in this snapshot (measured "
            "as working), so the reading predates this round. Re-measure once "
            "the worker is idle before trusting the seat.".format(", ".join(skipped))
        )
    return notes


def _explain(role, chosen, ranked, headrooms, counts, cost, floor, barred):
    """One human-readable sentence per assignment, in ranking order."""
    held = counts.get(role, {}).get(chosen, 0)
    headroom = headrooms[chosen]
    if headroom is None:
        reason = (
            "no headroom reading (busy, skipped, or unparseable), "
            "weight {:g}, no projection".format(cost)
        )
    else:
        projected = headroom - cost
        team_min = projected if floor is None else min(floor, projected)
        reason = "{:g}% headroom - weight {:g} = {:g}% projected, round floor {:g}%".format(
            headroom, cost, projected, team_min
        )
    field = ", ".join(
        "{}={}".format(name, "null" if headrooms[name] is None else "{:g}%".format(headrooms[name]))
        for name in ranked
    )
    return "{} -> {} ({}; excluded: {}; held this role {}x before; field was {})".format(
        role, chosen, reason, ", ".join(barred) if barred else "none", held, field
    )
