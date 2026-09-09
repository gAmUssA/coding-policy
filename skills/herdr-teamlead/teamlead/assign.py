"""Hand a role and its brief to an agent.

The hard rule this module enforces: teamlead never types into an agent that is
`working` or `blocked`. Statuses are checked for *every* target before the
first keystroke is sent, so a run that is going to be refused sends nothing at
all rather than half the assignments.

`working` from herdr is confirmed against the pane first (see
teamlead/probe.py) -- herdr's title-derived state goes stale and would
otherwise refuse a genuinely idle agent forever. `blocked` is never probed.

The clear command (`/clear`, `/new`) goes out by whichever mechanism the
agent's config names -- see SLASH_DELIVERIES in teamlead/herdr.py, because the
TUIs disagree about what a pasted slash command means. The assignment itself
IS a message, so it always goes through `agent prompt`.

Both are gated on the composer being empty (see teamlead/composer.py). Live,
an unsent `/new` sat in Codex's composer and the assignment was pasted onto
the end of it, so Codex received `/newNew assignment from the team lead...`
and rejected it. The assignment is never sent to an agent whose composer still
holds text.

Sending it is not the end either. A leftover `/` turned an assignment into
`/New assignment ...`, Claude Code answered "Args from unknown skill", and no
turn ever started -- while teamlead reported the round applied. So each
hand-off is confirmed: the transcript must show the message, the runtime must
not have read it as a command, and the agent must leave idle. Anything less is
reported as `sent_but_not_started` rather than as success.

`--dry-run` builds the same argv lists the live path would execute (the
builders live on the transport) and prints them without running anything.
"""

import os
import time
import hashlib
from pathlib import Path
from functools import partial

from .composer import (
    COMPOSER_SETTLE_SEC,
    DEFAULT_START_TIMEOUT_MS,
    LANDING_ATTEMPTS,
    DispatchSession,
    send_command,
    send_message,
)
from .errors import AgentBusyError, HerdrError, UsageError
from .herdr import (
    BUSY_STATES,
    DEFAULT_SETTLE_TIMEOUT_MS,
    SLASH_DELIVERY_TYPE,
    format_argv,
)
from .composer import COMPOSER_READ_LINES, COMPOSER_READ_SOURCE, checkable
from .probe import PROBE_READ_LINES, PROBE_READ_SOURCE, resolve_status, stderr_warn
from .chronology import latest_assignment
from .state import MAX_FIX_ROUNDS
from .recovery import empty_recovery, fresh_transition, task_record, validate_work
from .launch import restart_worker, verify_running, verify_running_permissions
from .tiers import launch_flags, worker_launch_args
from .qualification import require_qualification

# Version 3 adds verified model-tier metadata to context and task/fix evidence.
APPLY_SCHEMA_VERSION = 6

RETAIN_CONTEXT_ROUNDS = frozenset({1, 2, 3})

# Some native clients publish SessionStart only after their first prompt. The
# post-dispatch handshake polls the official integration, never pane labels.
SESSION_CORRELATION_READS = 3

#: States teamlead will type into. Anything else is refused, always.
SETTLE_STATES = ("idle", "done")

#: Stand-in for a pane id in `--dry-run`, which resolves no pane because it
#: makes no herdr calls.
PANE_ID_PLACEHOLDER = "PANE-ID-RESOLVED-AT-RUN-TIME"

#: The opening words looked for in the transcript to confirm the assignment
#: landed as a user message. Short enough to survive the runtime re-wrapping
#: it across rows.
ASSIGNMENT_OPENING = "New assignment from the team lead."

ASSIGNMENT_TEMPLATE = (
    "New assignment from the team lead. Your role for this task is {role}. "
    "Read {common} in full, then read {brief} in full, and execute that brief "
    "exactly. Finish with the REPORT line it specifies."
)


def assignment_text(role, common_path, brief_path):
    """The exact prompt sent to an agent. Pure, so tests pin it byte for byte."""
    return ASSIGNMENT_TEMPLATE.format(
        role=role.upper(), common=common_path, brief=brief_path
    )


def tiered_prompt(text, tier, common, brief):
    """Hash length-framed dispatch inputs, excluding this metadata footer."""
    digest = hashlib.sha256()
    parts = [text.encode("utf-8")]
    for path in (common, brief):
        try:
            parts.append(Path(path).read_bytes())
        except OSError as exc:
            raise UsageError("Cannot read briefing file {}: {}. Restore readability or correct its --common/--brief path before dispatch.".format(
                path, exc.strerror or str(exc)), {"path": str(path)}) from None
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    prompt_hash = digest.hexdigest()
    return text + " Launch metadata: model={}, effort={}, prompt_hash={}. Include the tier metadata required by COMMON.md in your report.".format(
        tier["model"], tier.get("effort") or "none", prompt_hash), prompt_hash


def normalize_assignments(payload):
    """Accept either `plan` output or a bare `{role: agent}` mapping.

    Version-agnostic on purpose: this reads `assignments` alone, which every
    plan version carries in the same shape. A version-1 plan has no `judge`
    object; so does a version-2 plan whose round assigned no judge seat, and
    both take the same path here (rules/stateful-artifacts.md Cross-Pipeline
    Schema Bumps -- an additive bump a reader absorbs through absence).
    """
    if isinstance(payload, dict) and isinstance(payload.get("assignments"), dict):
        payload = payload["assignments"]
    if not isinstance(payload, dict) or not payload:
        raise UsageError(
            "--assignments needs a JSON object mapping roles to agent names, or "
            "the output of `teamlead plan` (which nests one under "
            "\"assignments\"). Got: {}.".format(type(payload).__name__),
            {},
        )
    for role, agent in payload.items():
        if not isinstance(agent, str) or not agent:
            raise UsageError(
                "--assignments maps role {!r} to {!r}; each role must map to an "
                "agent name string.".format(role, agent),
                {"role": role},
            )

    reject_duplicate_agents(payload)
    return dict(payload)


def reject_duplicate_agents(assignments):
    """Refuse an agent that appears in more than one role.

    Briefing the same pane twice in a round means the second brief overwrites
    the first, so the earlier role is simply not being done -- silently, since
    both hand-offs report success. Checked before any herdr call, and checked
    again inside `apply` in case the mapping arrived some other way.
    """
    roles_by_agent = {}
    for role, agent in assignments.items():
        roles_by_agent.setdefault(agent, []).append(role)
    doubled = {
        agent: roles for agent, roles in roles_by_agent.items() if len(roles) > 1
    }
    if not doubled:
        return
    raise UsageError(
        "One agent is assigned several roles: {}. Each brief would overwrite "
        "the last in the same pane, so the earlier roles would go undone. "
        "Assign one agent per role.".format(
            "; ".join(
                "{} -> {}".format(agent, ", ".join(roles))
                for agent, roles in sorted(doubled.items())
            )
        ),
        {"doubled": {agent: sorted(roles) for agent, roles in doubled.items()}},
    )


def resolve_paths(assignments, briefs, common):
    """Turn the brief/common inputs into absolute paths, or explain what is missing."""
    missing_briefs = [role for role in assignments if role not in briefs]
    if missing_briefs:
        raise UsageError(
            "No --brief given for role {} - pass --brief {}=/path/to/brief.md "
            "for every role in --assignments.".format(
                ", ".join(sorted(missing_briefs)), sorted(missing_briefs)[0]
            ),
            {"roles": sorted(missing_briefs)},
        )

    resolved = {"common": os.path.abspath(common)}
    unreadable = [] if os.path.isfile(resolved["common"]) else [resolved["common"]]
    for role in assignments:
        path = os.path.abspath(briefs[role])
        resolved[role] = path
        if not os.path.isfile(path):
            unreadable.append(path)
    if unreadable:
        raise UsageError(
            "These brief files do not exist: {} - create them, or fix the "
            "--common / --brief paths. The agents are told to read these "
            "paths, so a missing file wastes a whole assignment round.".format(
                ", ".join(unreadable)
            ),
            {"missing": unreadable},
        )
    return resolved


def pane_label(role, task=None, model=""):
    """`<role> #<task> · <model>`, dropping whichever parts are absent.

    Role first, and the agent's name is deliberately NOT in it: the workspace
    row already carries the name, so repeating it in the pane row spends the
    sidebar's width saying the same thing twice. The separator is a middle dot
    because roles and model names both carry hyphens.

    Pure, so the shape is testable without a herdr session.
    """
    label = str(role)
    if task:
        marker = str(task) if str(task).startswith("#") else "#{}".format(task)
        label = "{} {}".format(label, marker)
    if model:
        label = "{} · {}".format(label, model)
    return label


def validate_agents(assignments, agents_by_name):
    """Refuse an assignment naming an unknown agent, or one used twice."""
    reject_duplicate_agents(assignments)
    for role, name in assignments.items():
        if name not in agents_by_name:
            raise UsageError(
                "Assignment for role {!r} names agent {!r}, which is not in the "
                "config - configured agents are {}.".format(
                    role, name, ", ".join(sorted(agents_by_name))
                ),
                {"role": role, "agent": name},
            )


def validate_context_mode(assignments, no_clear, retain_context, task, fix_round, *, recovery=None, history=None, plan_id=None, work=None):
    """Validate the explicit context choice before any herdr operation."""
    if no_clear and retain_context:
        raise UsageError("Choose --no-clear or --retain-context, never both.", {})
    if task is not None and (not isinstance(task, str) or not task.strip()):
        raise UsageError("Pass a non-empty --task label, or omit it.", {})
    if isinstance(task, str) and task != task.strip():
        raise UsageError(
            "New --task labels must have no leading or trailing whitespace. "
            "A padded legacy identity requires an explicit operator recovery decision; "
            "do not retry the padded label, trim or merge history, or reset its fix count.", {}
        )
    if fix_round is not None and (
        isinstance(fix_round, bool) or not isinstance(fix_round, int)
        or fix_round < 1
    ):
        raise UsageError(
            "Fix rounds must be 1–{}; after the cap, dispatch the judge.".format(MAX_FIX_ROUNDS), {}
        )
    if fix_round is not None and (not isinstance(task, str) or not task.strip()):
        raise UsageError("Pass --task with --fix-round to identify the task.", {})
    store = recovery if recovery is not None else empty_recovery()
    validate_work(store, history or [], task, fix_round, plan_id, work, implementation="developer" in assignments)
    transition = fresh_transition(store, history or [], task, fix_round)
    if retain_context and (
        set(assignments) != {"developer"} or fix_round not in RETAIN_CONTEXT_ROUNDS
    ):
        raise UsageError(
            "--retain-context requires one developer assignment and --fix-round 1, 2 or 3.", {}
        )
    if "developer" in assignments and fix_round in RETAIN_CONTEXT_ROUNDS and not retain_context:
        if transition is None:
            raise UsageError("Early developer fixes require --retain-context or a recorded fresh handoff. Use recover-role-clear for a verified automatic role clear, or follow dispatch-recovery.md for other causes; never reset the task.", {})
        task_record(store, task)
        if no_clear:
            raise UsageError("A replacement developer session requires an automatic clear; omit --no-clear.", {})
    if no_clear and fix_round is not None and fix_round not in RETAIN_CONTEXT_ROUNDS:
        raise UsageError("Fresh fix rounds require an automatic clear; omit --no-clear.", {})
    return transition if not retain_context and "developer" in assignments else None


def validate_fix_history(assignments, history, task, fix_round):
    """A worker change cannot reset or skip the task's confirmed fix count."""
    if "developer" not in assignments or task is None:
        return
    prior = [row for row in (history or []) if row.get("task") == task
             and row.get("role") == "developer" and row.get("status") == "applied"]
    completed = max((row.get("fix_round") or 0 for row in prior), default=0)
    if fix_round is None:
        if prior:
            raise UsageError("Task {!r} already has a developer assignment; do not reset its counter. Use its next fix or replay the original dispatch identity.".format(task), {})
        return
    if not prior or fix_round != completed + 1:
        raise UsageError(
            "Task {!r} requires its preceding confirmed developer assignment and next "
            "fix number {}; do not skip or reset the counter.".format(task, completed + 1), {}
        )


def validate_retained_history(assignments, history, task, fix_round):
    """Only the same agent's last confirmed task/role can retain context.

    The ledger is a necessary history check, never proof of a live pane:
    check_all_ready and composer checks still run before sending the brief.
    """
    name = assignments["developer"]
    latest = latest_assignment(history or [], agent=name)
    prior = latest[1] if latest is not None else None
    if prior is None or (
        prior.get("role") != "developer" or prior.get("task") != task
        or prior.get("status") != "applied"
        or (prior.get("fix_round") or 0) + 1 != fix_round
    ):
        raise UsageError(
            "Cannot retain {}'s context: the ledger must show its preceding confirmed "
            "developer round for task {!r}. Report lost context; do not reset the "
            "task's fix counter.".format(name, task), {}
        )
    return prior


def native_context_session(info, kind):
    """Read the official integration's native session reference, never a label.

    Herdr's agent.get contract exposes agent_session {source, agent, kind,
    value}; the integration updates it on native SessionStart, including
    clear/new. A worker name or pane alone is not a conversation identity.
    https://herdr.dev/docs/socket-api/#agent-state-reporting
    """
    ref = info.get("agent_session")
    pane = info.get("pane_id")
    if not isinstance(ref, dict) or not isinstance(pane, str) or not pane:
        return None
    if (ref.get("source") != "herdr:" + kind or ref.get("agent") != kind
            or ref.get("kind") not in ("id", "path")
            or not isinstance(ref.get("value"), str) or not ref["value"].strip()):
        return None
    return {"pane_id": pane, **{key: ref[key] for key in ("source", "agent", "kind", "value")}}


def verify_live_retention(prior, current, name):
    """Refuse an absent or changed native session before any terminal write."""
    if current is None or prior.get("context_session") != current:
        raise HerdrError(
            "Cannot retain {}: live native session continuity is unproven or changed. "
            "Check `herdr integration status` and the worker's session; report lost "
            "context without resetting the fix counter. No brief was sent.".format(name),
            {"agent": name},
        )


def correlate_dispatch_session(client, agent, pane_id, previous, before_prompt, *,
                               cleared, warn, sleep, settle_sec, grok_new=False):
    """Bind a confirmed first prompt to the native session it started.

    A pre-clear identity is stale even when it arrives again after dispatch.
    A different identity from the one observed immediately before the prompt
    cannot prove continuity either. Failure here does not undo a sent prompt.
    """
    if grok_new and previous is None:
        warn("{} was dispatched after a Grok clear without a recorded pre-clear native ID. "
             "Continuity remains unproven and stale-ID recovery is unavailable. Wait for its report; "
             "if delivery remains unconfirmed, record the report as unavailable and notify the operator "
             "of the missing pre-clear evidence. Keep review/release gates unsatisfied; "
             "do not resend or reconstruct evidence."
             .format(agent.name))
        return None
    for attempt in range(SESSION_CORRELATION_READS):
        try:
            current = native_context_session(client.agent_get(agent.name), agent.kind)
        except HerdrError as exc:
            warn("{} was dispatched but its native session could not be correlated: {}. "
                 "Wait for its report; do not send the assignment again.".format(agent.name, exc))
            return None
        if current is not None and current["pane_id"] != pane_id:
            warn("{} changed panes after dispatch; session binding is unavailable. "
                 "Wait for its report and recover the recorded assignment before retaining.".format(agent.name))
            return None
        if current is not None and (not cleared or current != previous):
            if before_prompt is not None and current != before_prompt:
                warn("{} changed native sessions across the dispatch; continuity is unproven. "
                     "Wait for its report and use the recorded recovery path; do not resend.".format(agent.name))
                return None
            return current
        if attempt + 1 < SESSION_CORRELATION_READS:
            sleep(settle_sec)
    warn("{} has no verified post-clear native session reference after dispatch. "
         "The sent assignment remains recorded; wait for its report, check `herdr integration status`, "
         "and recover that assignment before retaining context. Do not resend it.".format(agent.name))
    return None


def build_steps(client, assignments, agents_by_name, paths, panes=None, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS, track_context=False, tiers=None):
    """Build the per-role command plan. Pure with respect to herdr: nothing runs.

    This is what `--dry-run` prints, and what the live path walks. `panes` maps
    agent name to pane id; `--dry-run` has resolved no panes, so its rendering
    carries PANE_ID_PLACEHOLDER where the live path substitutes the real id.
    """
    validate_agents(assignments, agents_by_name)
    panes = panes or {}
    steps = []
    for role, name in assignments.items():
        agent = agents_by_name[name]
        launch_args = worker_launch_args(agent.kind, agent.launch_args)
        pane_id = panes.get(name) or PANE_ID_PLACEHOLDER
        text = assignment_text(role, paths["common"], paths[role])
        composer_reads = (
            [
                client.argv_agent_read(
                    name,
                    source=COMPOSER_READ_SOURCE,
                    lines=COMPOSER_READ_LINES,
                    fmt="ansi",
                )
            ]
            if checkable(agent)
            else []
        )
        commands = [client.argv_agent_get(name)]
        # Runs only when the `agent get` above reports `working`; see
        # teamlead/probe.py.
        conditional = [
            (
                client.argv_agent_read(name, source=PROBE_READ_SOURCE, lines=PROBE_READ_LINES),
                "herdr reports the agent as working; confirms it against the "
                "pane footer before refusing",
            )
        ]
        tier = (tiers or {}).get(role)
        prompt_hash = None
        if tier:
            text, prompt_hash = tiered_prompt(text, tier, paths["common"], paths[role])
            commands.extend(composer_reads)
            commands.append(client.argv_pane_process_info(pane_id))
            if not no_clear:
                commands.append(["kill", "-TERM", "VERIFIED-IDLE-FOREGROUND-PID"])
                commands.append(client.argv_pane_process_info(pane_id))
                commands.append(client.argv_agent_start(name, agent.kind, pane_id,
                    launch_args + launch_flags(agent.kind, tier)))
        else:
            # Initial all-target preflight, then this role's own boundary.
            commands.append(client.argv_pane_process_info(pane_id))
            commands.append(client.argv_pane_process_info(pane_id))
            conditional.append((client.argv_pane_process_info(pane_id),
                                "immediately before each recovery keystroke or extra Enter"))
        if not tier and not no_clear:
            commands.extend(composer_reads)
            for command in client.argv_deliver_slash_command(
                agent.slash_delivery, name, pane_id, agent.clear_prompt,
                enter_count=agent.slash_enter_count,
            ):
                commands.append(client.argv_pane_process_info(pane_id))
                commands.append(command)
            commands.extend(composer_reads)
            commands.append(
                client.argv_agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            )
            if agent.recover_keys:
                conditional.append(
                    (
                        client.argv_agent_send_keys(name, agent.recover_keys),
                        "the composer already holds text before dispatch; sent "
                        "exactly once, never twice",
                    )
                )
            conditional.append(
                (
                    client.argv_pane_send_keys(pane_id, ["enter"]),
                    "the clear command is still in the composer after the "
                    "first Enter (Codex's autocomplete popup eats it)",
                )
            )
        if track_context and role == "developer":
            commands.append(client.argv_agent_get(name))
        commands.extend(composer_reads)
        # The assignment is real message text, so pasting it is correct.
        commands.append(client.argv_pane_process_info(pane_id))
        commands.append(client.argv_agent_prompt(name, text))
        # Sending is not starting: confirm it landed as a user message.
        commands.extend(composer_reads)
        commands.append(
            client.argv_agent_wait(name, until=("working",), timeout_ms=start_timeout_ms)
        )
        step = {
                "role": role,
                "agent": name,
                "kind": agent.kind,
                "pane_id": pane_id,
                "brief": paths[role],
                "common": paths["common"],
                "prompt": text,
                "tier": tier,
                "commands": [{"argv": argv, "shell": format_argv(argv)} for argv in commands],
                "conditional_commands": [
                    {"argv": argv, "shell": format_argv(argv), "when": when}
                    for argv, when in conditional
                ],
            }
        if tier:
            step["prompt_hash"] = prompt_hash
        steps.append(step)
    return steps


def check_all_ready(client, assignments, agents_by_name, warn=None):
    """Read every target's live status before anything is sent.

    Returns `{agent: {"state": ..., "herdr_state": ..., "state_source": ...}}`.
    Raises AgentBusyError -- having sent nothing -- when any target is
    `working` or `blocked`. There is no override: rules/agent-team-operation.md
    Dispatch Safety is unconditional, and a keystroke into a working agent
    lands in the middle of somebody's turn.
    """
    validate_agents(assignments, agents_by_name)
    statuses = {}
    for name in assignments.values():
        info = client.agent_get(name)
        herdr_status = info.get("agent_status")
        status, source = resolve_status(client, agents_by_name[name], herdr_status, warn=warn)
        statuses[name] = {
            "state": status,
            "herdr_state": herdr_status,
            "state_source": source,
            "pane_id": info.get("pane_id"),
            "context_session": native_context_session(info, agents_by_name[name].kind),
        }
    busy = {
        name: record["state"]
        for name, record in statuses.items()
        if record["state"] in BUSY_STATES
    }
    if busy:
        raise AgentBusyError(
            "Refusing to interrupt {} - wait for them to reach idle or done, "
            "then run this again.".format(
                ", ".join("{} ({})".format(name, status) for name, status in sorted(busy.items()))
            ),
            {"busy": busy},
        )
    return statuses


def apply(client, assignments, agents_by_name, paths, at, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, on_assigned=None, warn=None, sleep=time.sleep, settle_sec=COMPOSER_SETTLE_SEC, landing_attempts=LANDING_ATTEMPTS, start_timeout_ms=DEFAULT_START_TIMEOUT_MS, allow_recovery=False, task=None, retain_context=False, fix_round=None, history=None, tiers=None, qualifications=None, recovery=None, plan_id=None, work=None, on_prepare=None, on_before_send=None, on_result=None, retrospective_guard=None):
    """Hand each agent its brief using the selected context mode.

    `on_assigned(role, agent, at, status, context)` is called after each hand-off so the
    caller records it in the state ledger as it goes -- an interrupted run
    still leaves a truthful record of what was actually sent. The status rides
    along because a round that went out and never started is worth recording
    and must not count as experience of the role.
    """
    validate_agents(assignments, agents_by_name)
    transition = validate_context_mode(assignments, no_clear, retain_context, task, fix_round,
                                       recovery=recovery, history=history, plan_id=plan_id, work=work)
    validate_fix_history(assignments, history, task, fix_round)
    prior = validate_retained_history(assignments, history, task, fix_round) if retain_context else None
    tiers = dict(tiers or {})
    if prior is not None and tiers.get("developer"):
        previous_tier = prior.get("tier")
        wanted = tiers["developer"]
        effort_rank = {None: 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
        if (not isinstance(previous_tier, dict) or not previous_tier.get("verified")
                or previous_tier.get("model") != wanted["model"]
                or effort_rank.get(previous_tier.get("effort"), -1) < effort_rank.get(wanted.get("effort"), 0)):
            raise UsageError("Retained context cannot switch model or raise effort. Recover the task through an explicit fresh-round decision without resetting its counter.", {})
        tiers["developer"] = {**wanted, "effort": previous_tier.get("effort")}
        if previous_tier.get("effort") != wanted.get("effort"):
            for key, default in (("multiplier", 1.0), ("effective_multiplier", 1.0), ("billing_window", "unknown")):
                tiers["developer"][key] = previous_tier.get(key, default)
    for role, tier in tiers.items():
        if role != "judge":
            proof = require_qualification({**tier, "qualification": (qualifications or {}).get(role, [])}, role, at)
            tiers[role] = {**tier, "qualification": proof}
    skip_clear = no_clear or retain_context
    clear_reason = "retained" if retain_context else "hand" if no_clear else "automatic"
    # Resolve the sink once. Every helper below defaults it too, but this
    # function calls it directly on the label path, and a None there would
    # raise instead of warning -- exactly when something already went wrong.
    warn = warn or stderr_warn
    # Status first: it is the refusal gate, and it is also where the pane ids
    # the clear command needs come from.
    statuses = check_all_ready(client, assignments, agents_by_name, warn=warn)
    if prior is not None:
        name = assignments["developer"]
        verify_live_retention(prior, statuses[name]["context_session"], name)
    steps = build_steps(
        client,
        assignments,
        agents_by_name,
        paths,
        panes={name: record.get("pane_id") for name, record in statuses.items()},
        no_clear=skip_clear,
        settle_timeout_ms=settle_timeout_ms,
        start_timeout_ms=start_timeout_ms,
        track_context=task is not None,
        tiers=tiers,
    )
    # Prove every pre-existing legacy worker before any role receives input.
    # The team lead starts these workers manually; apply never guesses a tier
    # or silently restarts a retained session to repair its permission mode.
    for step in steps:
        if step["pane_id"] == PANE_ID_PLACEHOLDER:
            raise UsageError("Herdr reported no pane for {!r}; inspect `herdr agent list` before dispatch so live YOLO arguments can be verified.".format(step["agent"]), {})
        if not step["tier"]:
            verify_running_permissions(client, agents_by_name[step["agent"]], step["pane_id"])

    if retrospective_guard is not None:
        retrospective_guard.preflight(steps, statuses)

    # One session per run. Recovery keys clear somebody's input line, and for
    # Codex the key that does it exits the process when the line is empty, so
    # teamlead only clears text it can account for.
    session = DispatchSession(allow_recovery=allow_recovery)
    applied = []
    for step in steps:
        name = step["agent"]
        agent = agents_by_name[name]
        if retrospective_guard is not None:
            retrospective_guard.before(step)
        if on_prepare is not None:
            on_prepare(step, statuses[name])
        cleared = False
        tier = tiers.get(step["role"])
        tier_record = None
        def before_input():
            if tier:
                verify_running(client, agent, step["pane_id"], tier)
            else:
                verify_running_permissions(client, agent, step["pane_id"])
            if retrospective_guard is not None:
                retrospective_guard.before(step)

        if not tier:
            # Earlier roles and their callbacks may replace a later worker.
            # Composer operations also recheck immediately before each input.
            before_input()
        if tier:
            proof = (verify_running(client, agent, step["pane_id"], tier) if skip_clear
                     else restart_worker(client, agent, step["pane_id"], tier, sleep=sleep,
                                         before_transition=partial(retrospective_guard.before, step) if retrospective_guard else None,
                                         before_start=partial(retrospective_guard.before_launch, step) if retrospective_guard else None))
            tier_record = {**tier, "launch_args": worker_launch_args(agent.kind, agent.launch_args), "verified": proof,
                           "prompt_hash": step["prompt_hash"]}
            # A fresh launch or retained-tier proof can become stale while the
            # composer is read. Verify the selected live tier before input.
            cleared = not skip_clear
            if cleared and retrospective_guard is not None:
                live_proof = verify_running(client, agent, step["pane_id"], tier)
                retrospective_guard.after_transition(step, launch_proof=live_proof)
        elif not skip_clear:
            pane_id = step["pane_id"]
            outcome = send_command(
                client,
                agent,
                pane_id,
                agent.clear_prompt,
                session=session,
                sleep=sleep,
                warn=warn,
                settle_sec=settle_sec,
                before_input=before_input,
                after_submit=partial(retrospective_guard.after_transition, step) if retrospective_guard else None,
            )
            client.agent_wait(name, until=SETTLE_STATES, timeout_ms=settle_timeout_ms)
            # `cleared` means the command was consumed AND the screen changed:
            # a fresh Codex session draws its banner, Claude empties the
            # transcript, Grok redraws session_start. Consumed but unchanged is
            # reported honestly rather than assumed.
            # A clear that changed nothing did not clear anything. Gating,
            # not advisory: briefing an agent that still holds the last task's
            # context is the failure the clear exists to prevent.
            if not outcome["screen_changed"]:
                raise HerdrError(
                    "{} consumed {} but its screen did not change, so the "
                    "context was not cleared -- a fresh session redraws (Codex "
                    "prints its banner, Claude Code empties the transcript). "
                    "Nothing further was sent. Look at pane {}, clear it by "
                    "hand, or pass --no-clear if that is what you want.".format(
                        name, agent.clear_prompt, step["pane_id"] or "(unknown)"
                    ),
                    {"agent": name, "clear_prompt": agent.clear_prompt},
                )
            cleared = True
            # The clear's redraw races the next paste; a leftover `/` is what
            # made Claude Code read the assignment as a slash command.
            sleep(settle_sec)
            if retrospective_guard is not None:
                retrospective_guard.after_transition(step)
        context_session = None
        if task is not None and step["role"] == "developer":
            # Query again after the clear, or immediately before retaining.
            # A pre-clear reference cannot stand in for the new conversation.
            context_session = native_context_session(client.agent_get(name), agent.kind)
            if prior is not None:
                verify_live_retention(prior, context_session, name)
            elif cleared and context_session == statuses[name]["context_session"]:
                context_session = None
        grok_new = agent.kind == "grok" and cleared and not tier
        if grok_new and statuses[name]["context_session"] is None:
            # A /new redraw does not repair Herdr's stale native ID (#365).
            # Preserve null proof even if the old ID was absent before clear.
            context_session = None
        # send_message re-checks the composer, pastes, and confirms the
        # message actually landed as a user message rather than as a command.
        if on_before_send is not None:
            on_before_send(step, {"cleared": cleared, "clear_reason": clear_reason,
                                  "context_session": context_session, "tier": tier_record,
                                  "transition": transition if step["role"] == "developer" else None})
        landing = send_message(
            client,
            agent,
            step["prompt"],
            ASSIGNMENT_OPENING,
            pane_id=step["pane_id"],
            session=session,
            sleep=sleep,
            warn=warn,
            settle_sec=settle_sec,
            attempts=landing_attempts,
            start_timeout_ms=start_timeout_ms,
            before_input=before_input,
        )
        if task is not None and step["role"] == "developer" and prior is None:
            context_session = (correlate_dispatch_session(
                client, agent, step["pane_id"], statuses[name]["context_session"], context_session,
                cleared=cleared, warn=warn, sleep=sleep, settle_sec=settle_sec, grok_new=grok_new,
            ) if landing["landed"] or landing["started"] else None)
        if grok_new and step["role"] != "developer":
            warn("{} received a fresh Grok assignment. Wait for its report; if delivery is unconfirmed, "
                 "stale-ID recovery through recover-report requires the recorded pre-clear native ID, original plan and native updates. "
                 "Without that ID, stale-ID recovery is unavailable: record the report as unavailable "
                 "and notify the operator of the missing pre-clear evidence. Keep review/release gates "
                 "unsatisfied; never rerun completed work.".format(name))
        checked = statuses.get(name, {})
        record = {
            "role": step["role"],
            "agent": name,
            "state_before": checked.get("state"),
            "herdr_state_before": checked.get("herdr_state"),
            "state_source": checked.get("state_source"),
            "pane_id": checked.get("pane_id"),
            "cleared": cleared,
            "clear_reason": clear_reason,
            "task": task,
            "fix_round": fix_round,
            "context_session": context_session,
            "tier": tier_record,
            "landed": landing["landed"],
            "started": landing["started"],
            "status": "applied"
            if (landing["landed"] or landing["started"])
            else "sent_but_not_started",
            "brief": step["brief"],
            "common": step["common"],
            "at": at,
            "context_transition": transition if step["role"] == "developer" else None,
        }
        # Persist the dispatch outcome before optional UI work. A broken pipe
        # during pane relabeling must never erase a confirmed handoff.
        if on_assigned is not None:
            context = {key: record[key] for key in ("cleared", "clear_reason", "task", "fix_round", "context_session", "tier")}
            on_assigned(step["role"], name, at, record["status"], context)
        if on_result is not None:
            on_result(record)
        # A sidebar of w1 w2 w3 tells the operator nothing. Label the pane with
        # who is doing what, but only once the hand-off is CONFIRMED: a label
        # claiming a role nobody started is worse than no label. Cosmetic, so a
        # failure warns and the dispatch stands.
        if record["status"] == "applied" and record.get("pane_id"):
            label = pane_label(
                step["role"], task, getattr(agents_by_name[name], "model_label", "")
            )
            try:
                client.pane_rename(record["pane_id"], label)
                record["pane_label"] = label
            except HerdrError as exc:
                warn(
                    "could not label {}'s pane {} as {!r}: {} - the assignment "
                    "landed, only the sidebar name did not.".format(
                        name, record["pane_id"], label, exc
                    )
                )
                record["pane_label"] = None
        else:
            record["pane_label"] = None

        applied.append(record)

    return {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": False,
        "applied_at": at,
        "applied": applied,
    }


def dry_run(client, assignments, agents_by_name, paths, no_clear=False, settle_timeout_ms=DEFAULT_SETTLE_TIMEOUT_MS, retain_context=False, task=None, fix_round=None, tiers=None, recovery=None, history=None, plan_id=None, work=None):
    """Print the plan without contacting herdr at all.

    Deliberately makes zero herdr calls, including the status check: a dry run
    against busy agents must show the plan rather than refuse it. The live
    `apply` re-checks status for real before sending anything.
    """
    transition = validate_context_mode(assignments, no_clear, retain_context, task, fix_round,
                                       recovery=recovery, history=history, plan_id=plan_id, work=work)
    return {
        "schema_version": APPLY_SCHEMA_VERSION,
        "dry_run": True,
        "sent": False,
        "clear_reason": "retained" if retain_context else "hand" if no_clear else "automatic",
        "task": task,
        "fix_round": fix_round,
        "context_transition": transition,
        "steps": build_steps(
            client,
            assignments,
            agents_by_name,
            paths,
            no_clear=no_clear or retain_context,
            settle_timeout_ms=settle_timeout_ms,
            track_context=task is not None,
            tiers=tiers,
        ),
    }
