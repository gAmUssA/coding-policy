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
across the agents holding a seat, per `rules/agent-team-operation.md`
("Headroom is the minimum remaining window per worker, never the average").
An agent holding no seat burns nothing, so it is not part of that minimum.

`exclude` bars agents from a role, as `{role: [agent, ...]}`. Nobody reviews or
verifies the branch they wrote, so the lead bars the author from those seats. A
role whose eligible field is empty is a PlanError, never a silent drop.

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
"""

import math

from .diagnostics import stderr_warn
from .errors import PlanError

#: Plan document version. 2 adds the optional `judge` object carrying the
#: pinned seat's agent, model and effort. Version 3 adds round-tier data. Additive: a version-1
#: plan simply has no `judge` key, which is indistinguishable from a version-2
#: plan that assigned no judge seat, so both readers take the same path.
#: A plan is a round's instruction, not stored state -- it is produced and
#: consumed inside one round and never migrated (rules/stateful-artifacts.md).
PLAN_SCHEMA_VERSION = 4

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
}

#: Weight for a role nobody has weighed -- a folded seat, or a role a later
#: round invents. Between reviewer and developer: free would let an unweighed
#: seat outrank every measured one.
DEFAULT_ROLE_COST = 8.0


def _sort_key(name, headroom, cost, role, counts, floor):
    """Rank one candidate for one role. Lower sorts first."""
    if headroom is None:
        return (1, 0.0, 0.0, 0, name)
    projected = float(headroom) - cost
    team_min = projected if floor is None else min(floor, projected)
    return (0, -team_min, -projected, counts.get(role, {}).get(name, 0), name)


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


def _unfillable_message(role, barred, remaining, later_roles):
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
        "Drop an exclusion, measure another agent, or assign fewer roles.",
    ]
    return " ".join(part for part in parts if part)


def _refuse_unaffordable_judge(judge_agent, headrooms, cost, groups):
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

    readings = {
        name: headrooms[name] for name in window if headrooms.get(name) is not None
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


def plan(roles, snapshot, counts=None, exclude=None, role_costs=None, snapshot_ref=None, warn=None, judge_agent=None, judge_tier=None, tier_candidates=None, rounds=None):
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

    Raises PlanError when there are no roles, no agents, fewer agents than
    roles, an exclusion naming a role nobody is assigning, or a role whose
    eligible field is empty -- silently dropping a role would hide work nobody
    is doing.
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
            raise PlanError(
                "No agent is eligible for role {!r} - --exclude bars {}, and "
                "those are every agent in the snapshot ({}). Drop an exclusion, "
                "or measure another agent.".format(
                    role, ", ".join(excluded[role]), ", ".join(sorted(agents))
                ),
                {"role": role, "excluded": list(excluded[role]), "agents": sorted(agents)},
            )
    costs = _costs_for(roles, role_costs)
    if tier_candidates is not None:
        for role in roles:
            if role not in DEFAULT_ROLE_COSTS and role not in (role_costs or {}):
                raise PlanError("Tiered role {!r} needs an explicit cost; no fallback weight is used.".format(role), {})
            for name in agents:
                if name not in tier_candidates.get(role, {}):
                    excluded[role] = sorted(set(excluded[role]) | {name})
            if all(name in excluded[role] for name in agents):
                raise PlanError("No qualified tier is eligible for {!r}. Record its paired validation and current canary, check exclusions, or use --preview-tiers to inspect launches without dispatch.".format(role), {})

    def candidate_cost(role, name):
        tier = (tier_candidates or {}).get(role, {}).get(name) or {}
        return costs[role] * tier.get("effective_multiplier", 1.0)

    headrooms = {
        name: _headroom_of(name, record, warn) for name, record in agents.items()
    }
    groups = _window_groups(agents)
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

    for index, role in enumerate(fill_order):
        cost = candidate_cost(role, judge_agent) if role == "judge" else costs[role]
        barred = excluded[role]
        if role == "judge" and judge_agent:
            _refuse_unaffordable_judge(judge_agent, headrooms, cost, groups)
        later_roles = fill_order[index + 1:]
        ranked = sorted(
            (name for name in remaining if name not in barred),
            key=lambda name: _sort_key(name, headrooms[name], candidate_cost(role, name), role, counts, floor),
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
                _unfillable_message(role, barred, remaining, later_roles),
                {
                    "role": role,
                    "excluded": list(barred),
                    "assigned": dict(picks),
                    "unassigned": sorted(remaining),
                },
            )
        remaining.discard(chosen)
        picks[role] = chosen
        cost = candidate_cost(role, chosen)
        rationale.append(
            _explain(role, chosen, ranked, headrooms, counts, cost, floor, barred)
        )
        headroom = headrooms[chosen]
        if headroom is not None:
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
                if groups.get(name) == group and headrooms[name] is not None:
                    headrooms[name] -= cost

    rationale.extend(_notes(excluded, agents, warn))

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

    if tier_candidates is not None:
        document["tiers"] = {
            role: tier_candidates[role][picks[role]] for role in roles
        }
        document["rounds"] = rounds or {}

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
        if isinstance(record, dict) and record.get("skipped")
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
