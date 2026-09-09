"""`teamlead` command line: argparse subcommands, JSON on stdout.

This is the only module allowed to read the clock, and the only one that
decides an exit code. Everything below it is either pure or takes an injected
herdr client, which is what keeps the tests off the real binary.

I/O contract:

* success -> the command's JSON document on stdout, exit 0
* failure -> a JSON error object on stderr, exit 1 (2 for an argparse error)
"""

import argparse
import json
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import __version__
from .assign import apply as apply_assignments
from .assign import APPLY_SCHEMA_VERSION, dry_run, native_context_session, normalize_assignments, resolve_paths, validate_fix_history
from . import attention, historical, memory, recovery, report_delivery, restoration, role_clear, retrospective, retrospective_runtime, supervision, supervision_runtime
from .config import default_config_path, load_config, load_judge, load_role_costs, select_agents
from .errors import PlanError, StateError, TeamLeadError, UsageError
from .herdr import (
    DEFAULT_MARKER_TIMEOUT_MS,
    DEFAULT_SETTLE_TIMEOUT_MS,
    HerdrClient,
    trace_enabled_in_env,
)
from .composer import COMPOSER_SETTLE_SEC, DEFAULT_START_TIMEOUT_MS
from .diagnostics import PREFIX as DIAGNOSTIC_PREFIX
from .measure import (
    DEFAULT_MARKER_POLL_ATTEMPTS,
    DEFAULT_MARKER_POLL_INTERVAL_SEC,
    DEFAULT_READ_LINES,
    measure,
)
from .planner import plan as build_plan
from .tiers import parse_launch_args, parse_tiers, select_tier
from .qualification import require_qualification
from .launch import start_worker, verify_running
from .state import (
    add_assignment,
    add_snapshot,
    default_state_path,
    latest_snapshot,
    load_state,
    load_state_checked,
    role_counts,
    save_state,
    state_lock,
)

EPILOG = (
    "Config defaults to $XDG_CONFIG_HOME/teamlead/config.json (~/.config/...); "
    "copy config.example.json there to get started. State defaults to "
    "$XDG_STATE_HOME/teamlead/state.json (~/.local/state/...)."
)


def now_iso():
    """The wall clock, read here and nowhere else in the package."""
    return datetime.now(timezone.utc).isoformat()


def build_parser():
    # SUPPRESS keeps a subparser's copy of these flags from clobbering a value
    # given before the subcommand, so `teamlead --state F plan` and
    # `teamlead plan --state F` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="Agent config JSON (default: $XDG_CONFIG_HOME/teamlead/config.json).",
    )
    common.add_argument(
        "--state",
        metavar="FILE",
        default=argparse.SUPPRESS,
        help="State file (default: $XDG_STATE_HOME/teamlead/state.json).",
    )
    common.add_argument(
        "--herdr-bin",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="herdr executable to invoke (default: $TEAMLEAD_HERDR_BIN or `herdr`).",
    )
    common.add_argument(
        "--trace",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print every herdr command and its raw stdout, stderr, and exit "
        "status to stderr. Same as TEAMLEAD_TRACE=1.",
    )

    parser = argparse.ArgumentParser(
        prog="teamlead",
        parents=[common],
        description="Load-balance coding agents running inside Herdr.",
        epilog=EPILOG,
    )
    parser.add_argument("--version", action="version", version="teamlead " + __version__)

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    judge_parser = sub.add_parser("start-judge", parents=[common],
                                  help="Start the plan's judge in a shell pane and verify its launch argv.")
    judge_parser.add_argument("--assignments", required=True, metavar="PLAN")
    judge_parser.add_argument("--pane", required=True)
    judge_parser.add_argument("--kind", choices=("claude", "codex", "grok"), default="claude")
    judge_parser.add_argument("--task")
    judge_parser.add_argument("--now", metavar="ISO")

    for command in ("retro-check", "retro-record"):
        retro_parser = sub.add_parser(command, parents=[common], help="Check or record a lead-authored retrospective.")
        retro_parser.add_argument("--record", required=True, metavar="FILE")
        retro_parser.add_argument("--now", metavar="ISO")
    retro_list = sub.add_parser("retro-list", parents=[common], help="List saved retrospective notes without contacting Herdr.")
    retro_list.add_argument("--task")
    retro_list.add_argument("--since", metavar="ISO")
    retro_show = sub.add_parser("retro-show", parents=[common], help="Read a saved retrospective without contacting Herdr.")
    retro_show.add_argument("--task")
    retro_show.add_argument("--id", default="latest")
    memory.register_commands(sub, common)
    attention.register_commands(sub, common)
    supervision_runtime.register_commands(sub, common)
    restoration.register_commands(sub, common)

    measure_parser = sub.add_parser(
        "measure",
        parents=[common],
        help="Read each idle agent's remaining subscription budget.",
        description=(
            "Send each idle agent its usage command, wait for the report, parse "
            "it, and save the snapshot. Agents that are working or blocked are "
            "reported as skipped, not interrupted."
        ),
    )
    measure_parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        metavar="NAME",
        help="Measure only this agent; repeat for several (default: all configured).",
    )
    measure_parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Timestamp to stamp the snapshot with (default: the current UTC time).",
    )
    measure_parser.add_argument(
        "--marker-timeout",
        type=int,
        default=DEFAULT_MARKER_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for a usage report to appear (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--lines",
        type=int,
        default=DEFAULT_READ_LINES,
        metavar="N",
        help="Pane lines to read for a usage report (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--marker-poll-attempts",
        type=int,
        default=DEFAULT_MARKER_POLL_ATTEMPTS,
        metavar="N",
        help="Re-reads of the pane after `pane wait-output` fails to deliver "
        "the marker (default: %(default)s).",
    )
    measure_parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Let teamlead clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
    )
    measure_parser.add_argument(
        "--composer-settle",
        type=float,
        default=COMPOSER_SETTLE_SEC,
        metavar="SECONDS",
        help="Seconds to let a TUI repaint before re-reading its composer "
        "(default: %(default)s).",
    )
    measure_parser.add_argument(
        "--marker-poll-interval",
        type=float,
        default=DEFAULT_MARKER_POLL_INTERVAL_SEC,
        metavar="SECONDS",
        help="Seconds between those re-reads (default: %(default)s).",
    )

    plan_parser = sub.add_parser(
        "plan",
        parents=[common],
        help="Assign roles to agents by remaining headroom. Touches no agent.",
        description=(
            "Deterministic assignment: each role carries a cost weight, the "
            "heaviest seat is filled first, and every seat goes to the eligible "
            "agent that leaves the round's smallest projected usage window "
            "highest."
        ),
    )
    plan_parser.add_argument(
        "--roles",
        default="developer,tester,reviewer",
        metavar="LIST",
        help="Comma-separated roles to assign (default: %(default)s). The cost "
        "weights decide which seat is filled first, not this order.",
    )
    plan_parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        dest="excludes",
        metavar="ROLE=AGENT[,AGENT...]",
        help="Bar these agents from that role; repeat per role. Bar the branch's "
        "author from reviewer and tester -- nobody verifies their own work.",
    )
    plan_parser.add_argument(
        "--snapshot",
        metavar="FILE",
        help="Snapshot to plan against (default: the newest one in the state file).",
    )
    plan_parser.add_argument("--round", action="append", default=[], metavar="ROLE=ROUND",
                             help="Choose a configured round type for a role; never a model override.")
    plan_parser.add_argument("--round-context", metavar="FILE",
                             help="JSON object keyed by role with mechanical/risk evidence for this round.")
    plan_parser.add_argument("--fix-round", type=int, help="Task fix number; late fixes use the top tier.")
    plan_parser.add_argument("--task", help="Original task identity; preserve it through every correction.")
    plan_parser.add_argument("--preview-tiers", action="store_true",
                             help="Preview unqualified tiers. Live apply still requires complete qualification evidence.")
    plan_parser.add_argument("--now", metavar="ISO-8601", help="Reference time for qualification expiry (default: current UTC time).")

    apply_parser = sub.add_parser(
        "apply",
        parents=[common],
        help="Clear each agent's context and hand it its brief.",
        description=(
            "Refuses to type into an agent that is working or blocked. Statuses "
            "are checked for every target before the first keystroke is sent."
        ),
    )
    apply_parser.add_argument(
        "--assignments",
        required=True,
        metavar="FILE_OR_JSON",
        help="`teamlead plan` output, a {role: agent} object, or a path to either.",
    )
    apply_parser.add_argument(
        "--brief",
        action="append",
        default=[],
        dest="briefs",
        metavar="ROLE=PATH",
        help="Brief for one role; repeat once per role.",
    )
    apply_parser.add_argument(
        "--report", action="append", default=[], dest="reports", metavar="ROLE=ABS_PATH",
        help="Expected report path per assignment; every bound supervision role requires one.",
    )
    apply_parser.add_argument(
        "--common",
        required=True,
        metavar="PATH",
        help="Shared instructions every agent reads before its own brief.",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the herdr commands and send nothing. Makes no herdr calls at all.",
    )
    context_flags = apply_parser.add_mutually_exclusive_group()
    context_flags.add_argument(
        "--no-clear",
        action="store_true",
        help=(
            "Skip the context-clearing prompt and send only the assignment. "
            "For a pane the lead already cleared by hand; the record carries "
            "cleared: false."
        ),
    )
    context_flags.add_argument(
        "--retain-context", action="store_true",
        help="Keep the same developer's context for fix rounds 1–3; requires --task and --fix-round.",
    )
    apply_parser.add_argument(
        "--fix-round", type=int, metavar="N",
        help="Fix-round number for this task; the dispatcher validates the cap.",
    )
    apply_parser.add_argument(
        "--task",
        metavar="LABEL",
        help="Original task identity for dispatch recovery, supervision, and pane titles.",
    )
    apply_parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="Timestamp for the ledger entries (default: the current UTC time).",
    )
    apply_parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Let teamlead clear a composer holding text it did not type. Off by default: the recovery key is ctrl+c on some runtimes, and ctrl+c on an idle Codex exits the process.",
    )
    apply_parser.add_argument(
        "--composer-settle",
        type=float,
        default=COMPOSER_SETTLE_SEC,
        metavar="SECONDS",
        help="Seconds to let a TUI repaint before re-reading its composer "
        "(default: %(default)s).",
    )
    apply_parser.add_argument(
        "--start-timeout",
        type=int,
        default=DEFAULT_START_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for an agent to start its turn after the "
        "assignment lands (default: %(default)s).",
    )
    apply_parser.add_argument(
        "--settle-timeout",
        type=int,
        default=DEFAULT_SETTLE_TIMEOUT_MS,
        metavar="MS",
        help="How long to wait for an agent to settle after clearing (default: %(default)s).",
    )

    for command_parser in (plan_parser, apply_parser):
        command_parser.add_argument("--correction-plan", help="Recorded bounded approval for extra corrections.")
        command_parser.add_argument("--work", metavar="FILE", help="Correction base, scope, paths and blocking findings as JSON.")
    apply_parser.add_argument("--dispatch-id", help="Stable dispatch identity; retries read its recorded outcome.")

    report_parser = sub.add_parser("probe-report", parents=[common], help="Confirm native UI decoration using completed source and visible rows on stdin.")
    report_parser.add_argument("--agent", required=True)
    report_parser.add_argument("--pane", required=True)
    report_parser.add_argument("--report", required=True)
    report_parser.add_argument("--lines", type=int, required=True)

    for command in ("task", "checkpoint", "authorize-corrections", "recover-context", "recover-role-clear", "record-report", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report"):
        record_parser = sub.add_parser(command, parents=[common], help="Record owner-managed {} evidence.".format(command))
        record_parser.add_argument("--record", required=True, metavar="FILE", help="Structured evidence JSON; see dispatch-recovery.md.")
        record_parser.add_argument("--now", metavar="ISO8601")
    sub.add_parser("status", parents=[common], help="Show implementation budgets and paused work separately from active audit workers.")

    sub.add_parser(
        "state",
        parents=[common],
        help="Print the state file.",
        description="Print the state file, or a fresh empty document if none exists.",
    )
    return parser


def _config_path(args):
    value = getattr(args, "config", None)
    return Path(value) if value else default_config_path()


def _state_path(args):
    value = getattr(args, "state", None)
    return retrospective.canonical_state(Path(value) if value else default_state_path())


def _client(args, trace=None):
    tracing = getattr(args, "trace", False) or trace_enabled_in_env()
    return HerdrClient(
        binary=getattr(args, "herdr_bin", None),
        trace=trace if tracing else None,
    )


def _parse_briefs(pairs):
    briefs = {}
    for pair in pairs:
        role, separator, path = pair.partition("=")
        if not separator or not role or not path:
            raise UsageError(
                "--brief expects ROLE=PATH, got {!r} - for example "
                "--brief developer=/path/to/developer-brief.md.".format(pair),
                {"value": pair},
            )
        briefs[role] = path
    return briefs


def _parse_reports(pairs, assignments):
    """Validate the complete report map before any clear, launch, or send."""
    reports = {}
    for pair in pairs:
        role, separator, path = pair.partition("=")
        if (not separator or role not in assignments or role in reports or not path
                or not Path(path).is_absolute() or path.endswith("/")
                or any(ord(char) < 32 for char in path)):
            raise UsageError("--report requires one ROLE=ABS_PATH for each assigned role; no duplicates, unknown roles, relative paths, or directory paths.", {})
        if any(Path(existing).resolve() == Path(path).resolve() for existing in reports.values()):
            raise UsageError("Each dispatched role needs a distinct report file; shared report paths would overwrite worker evidence.", {})
        if Path(path).is_dir():
            raise UsageError("The expected --report path names a directory; use the absolute report file path from the brief.", {})
        reports[role] = path
    return reports


def _supervision_enrollment(state_path, identifier, task, role, name, report, at, *,
                            pane_id=None, native=None, replay=False, persist=False):
    """Preserve enrollment evidence; prepare a new row before worker input."""
    data = supervision.load(state_path)
    expected = {"id": identifier, "agent": name, "task": task, "report": report,
                "pane_id": pane_id, "native_session": None}
    prior = next((row for row in data["members"] if row["id"] == identifier), None)
    if prior:
        if any(prior["assignment"][key] != expected[key] for key in ("id", "agent", "task", "report")):
            raise UsageError("This dispatch enrollment names different task/report evidence; preserve its original report path and reconcile before retrying.", {"role": role})
        if not replay and not prior["active"]:
            raise UsageError("This enrollment was resolved. Use a new explicit --dispatch-id for a new authorized transport attempt; never reactivate accepted work.", {"role": role})
        expected = prior["assignment"]
        current = supervision.expected_assignment(prior)
        if persist and pane_id is not None and current["pane_id"] is not None and current["pane_id"] != pane_id:
            raise UsageError("This dispatch now points at a different pane. Reconcile the original enrollment before a new authorized dispatch.", {"agent": name})
        if (persist and native is not None and current["native_session"] is not None
                and {key: value for key, value in current["native_session"].items() if key != "pane_id"}
                != {key: value for key, value in native.items() if key != "pane_id"}):
            raise UsageError("This dispatch has different native session evidence. Preserve the enrollment and reconcile the context before continuing.", {"agent": name})
    elif any(row["active"] and row["assignment"]["agent"] == name for row in data["members"]):
        raise UsageError("This worker has another active enrollment. Reconcile and resolve its existing assignment before dispatching another one.", {"agent": name})
    if persist:
        member = supervision.enroll(state_path, expected, at)
        if member["active"] and native is not None:
            known = supervision.expected_assignment(member)
            if known["native_session"] is None:
                supervision.refine(state_path, identifier, known["pane_id"] or pane_id, native, at)
    return expected


def _parse_excludes(pairs):
    """`--exclude ROLE=AGENT[,AGENT...]` into `{role: [agent, ...]}`.

    Repeats of the same role merge rather than replace, so a lead can bar the
    author from two seats in two flags or one.
    """
    excludes = {}
    for pair in pairs:
        role, separator, names = pair.partition("=")
        role = role.strip()
        agents = [name.strip() for name in names.split(",") if name.strip()]
        if not separator or not role or not agents:
            raise UsageError(
                "--exclude expects ROLE=AGENT[,AGENT...], got {!r} - for example "
                "--exclude reviewer=grok or --exclude tester=grok,claude.".format(pair),
                {"value": pair},
            )
        for name in agents:
            if name not in excludes.setdefault(role, []):
                excludes[role].append(name)
    return excludes


def _load_assignments(value, document=False):
    """`--assignments` takes inline JSON or a path to a JSON file."""
    text = value.strip()
    if not text.startswith("{"):
        path = Path(text)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise UsageError(
                "--assignments file {} not found - pass the path to `teamlead "
                "plan` output, or the JSON object itself.".format(path),
                {"path": str(path)},
            ) from None
        except IsADirectoryError:
            raise UsageError(
                "--assignments path {} is a directory - point it at a JSON "
                "file.".format(path),
                {"path": str(path)},
            ) from None
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError("Cannot read assignments file {}: {}. Supply a readable UTF-8 JSON file with --assignments.".format(path, exc),
                             {"path": str(path)}) from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            "--assignments is not valid JSON ({} at line {} column {}) - expected "
            "`teamlead plan` output or a {{\"role\": \"agent\"}} object.".format(
                exc.msg, exc.lineno, exc.colno
            ),
            {},
        ) from None
    return payload if document else normalize_assignments(payload)


def _round_inputs(args, roles):
    """Read only round choices and evidence; models remain config-owned."""
    contexts = {}
    if args.round_context:
        try:
            contexts = json.loads(Path(args.round_context).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UsageError("Cannot read round context {}: {}. Supply a readable UTF-8 JSON file with --round-context.".format(
                args.round_context, exc), {"path": str(args.round_context)}) from None
    if not isinstance(contexts, dict) or set(contexts) - set(roles):
        raise UsageError("Round context must map only roles this plan assigns to evidence objects.", {})
    rounds = {role: {"context": context} for role, context in contexts.items()}
    for pair in args.round:
        if "=" not in pair:
            raise UsageError("Use --round ROLE=ROUND.", {})
        role, round_type = pair.split("=", 1)
        if role not in roles or "type" in rounds.get(role, {}):
            raise UsageError("--round names an unknown or duplicate role; choose it once.", {})
        rounds.setdefault(role, {})["type"] = round_type
    return rounds


def _candidate_tiers(roles, agents, rounds, fix_round=None, judge=None, qualified_at=None):
    tiered = any(agent.tiers for agent in agents)
    if not tiered and not (judge and "judge" in roles):
        if rounds:
            raise UsageError("Round selection requires configured tier tables.", {})
        return None
    candidates = {role: {} for role in roles}
    for role in roles:
        inputs = rounds.get(role, {})
        for agent in agents:
            if judge and agent.name == judge.agent:
                if role == "judge":
                    candidates[role][agent.name] = {"round": "judge", "tier_row": "judge", "kind": agent.kind,
                        "model": judge.model, "effort": judge.effort or None,
                        "billing_window": "unknown", "multiplier": 1.0, "effective_multiplier": 1.0}
                continue
            if role == "judge":
                continue
            if not agent.tiers:
                if not tiered:
                    candidates[role][agent.name] = None
                continue
            tier = select_tier(agent, role, inputs.get("type"), inputs.get("context"), fix_round)
            if tier is None:
                continue
            if qualified_at is not None:
                evidence = [record for entry in agent.tiers.values() for record in entry.get("qualification", [])]
                try:
                    require_qualification({**tier, "qualification": evidence}, role, qualified_at)
                except UsageError:
                    # This candidate is ineligible; another qualified worker may fill the seat.
                    continue
            candidates[role][agent.name] = {key: tier[key] for key in (
                "round", "tier_row", "kind", "model", "effort", "multiplier", "billing_window", "effective_multiplier"
            )}
    return candidates


def _load_state_for_write(path, warn, *, persist_migration=True):
    """Read state a caller intends to write back, or refuse.

    `load_state_checked` leaves an unreadable file exactly as found and hands
    back an empty document. Writing that document over the file would undo
    precisely the preservation it just performed, taking the operator's whole
    ledger with it -- so a write path refuses instead, and says how to keep
    both.
    """
    state, usable = load_state_checked(path, warn=warn, persist_migration=persist_migration)
    if not usable:
        raise StateError(
            "State file {} could not be read (see the warning above), and "
            "writing an empty ledger over it would destroy its contents. "
            "Move it aside with `mv {} {}.bak` to start fresh, or pass "
            "--state at another path to keep it.".format(path, path, path),
            {"path": str(path)},
        )
    return state


def _read_record(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError("Cannot read record {}: {}. Supply readable UTF-8 JSON with the documented fields.".format(path, exc), {}) from None
    if not isinstance(data, dict):
        raise UsageError("A record must be a JSON object; use the documented field names.", {})
    return data


def cmd_measure(args, client=None, warn=None, trace=None):
    agents = select_agents(load_config(_config_path(args)), args.agents)
    client = client if client is not None else _client(args, trace=trace)
    snapshot = measure(
        client,
        agents,
        args.now or now_iso(),
        marker_timeout_ms=args.marker_timeout,
        read_lines=args.lines,
        warn=warn,
        poll_attempts=args.marker_poll_attempts,
        poll_interval_sec=args.marker_poll_interval,
        settle_sec=args.composer_settle,
        allow_recovery=args.allow_recovery,
    )
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)
    add_snapshot(state, snapshot)
    save_state(state_path, state)
    if not snapshot["failed_agents"]:
        return snapshot, None
    return snapshot, {
        "error": "measure_incomplete",
        "message": "Could not measure {} - see the `error` field on each agent "
        "in the snapshot on stdout.".format(", ".join(snapshot["failed_agents"])),
        "details": {"failed_agents": snapshot["failed_agents"]},
    }


def cmd_plan(args, client=None, warn=None, trace=None):
    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    excludes = _parse_excludes(args.excludes)
    role_costs = load_role_costs(_config_path(args))
    judge = load_judge(_config_path(args))
    rounds = _round_inputs(args, roles)
    agents = load_config(_config_path(args)) if _config_path(args).exists() else []
    state_path = _state_path(args)
    state = load_state(state_path, warn=warn)
    work = _read_record(args.work) if args.work else None
    recovery.validate_work(state["recovery"], state["assignments"], args.task, args.fix_round,
                           args.correction_plan, work, implementation="developer" in roles)
    if args.task:
        validate_fix_history({role: None for role in roles}, state["assignments"], args.task, args.fix_round)
    tier_candidates = _candidate_tiers(roles, agents, rounds, args.fix_round, judge,
                                      None if args.preview_tiers else (args.now or now_iso()))

    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PlanError(
                "Snapshot file {} not found - run `teamlead measure` or point "
                "--snapshot at a saved snapshot.".format(snapshot_path),
                {"path": str(snapshot_path)},
            ) from None
        except json.JSONDecodeError as exc:
            raise PlanError(
                "Snapshot file {} is not valid JSON ({} at line {} column {}).".format(
                    snapshot_path, exc.msg, exc.lineno, exc.colno
                ),
                {"path": str(snapshot_path)},
            ) from None
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanError("Cannot read snapshot {}: {}. Supply a readable UTF-8 JSON file with --snapshot.".format(snapshot_path, exc),
                            {"path": str(snapshot_path)}) from None
        source = str(snapshot_path)
    else:
        snapshot = latest_snapshot(state)
        if snapshot is None:
            raise PlanError(
                "No snapshot in {} - run `teamlead measure` first, or pass "
                "--snapshot FILE.".format(state_path),
                {"state": str(state_path)},
            )
        source = str(state_path)

    if not isinstance(snapshot, dict):
        raise PlanError(
            "Snapshot from {} is not a JSON object with an `agents` field.".format(source),
            {"source": source},
        )

    result = build_plan(
            roles,
            snapshot,
            role_counts(state),
            exclude=excludes,
            role_costs=role_costs,
            judge_agent=judge.agent if judge else None,
            judge_tier={
                "model": judge.model,
                "effort": judge.effort,
                "launch_args": next((list(agent.launch_args) for agent in agents if agent.name == judge.agent), []),
            }
            if judge
            else None,
            snapshot_ref={"source": source, "measured_at": snapshot.get("measured_at")},
            warn=warn,
            tier_candidates=tier_candidates,
            rounds=rounds,
        )
    result["task_context"] = ({"task": args.task, "fix_round": args.fix_round,
                               "plan": args.correction_plan, "work": work} if args.task else None)
    return result, None


def cmd_apply(args, client=None, warn=None, trace=None):
    agents = load_config(_config_path(args))
    agents_by_name = {agent.name: agent for agent in agents}
    document = _load_assignments(args.assignments, document=True)
    assignments = normalize_assignments(document)
    rounds = document.get("rounds", {}) if "assignments" in document else {}
    if not isinstance(rounds, dict) or set(rounds) - set(assignments):
        raise UsageError("Plan rounds must map only assigned roles to round inputs.", {})
    for value in rounds.values():
        if not isinstance(value, dict) or set(value) - {"type", "context"}:
            raise UsageError("Plan round inputs allow only type and context; model overrides are forbidden.", {})
    judge = load_judge(_config_path(args))
    if "judge" in assignments and (judge is None or assignments["judge"] != judge.agent):
        raise UsageError("Judge assignment must match the pinned judge in config.json.", {})
    if judge and any(name == judge.agent and role != "judge" for role, name in assignments.items()):
        raise UsageError("The pinned judge worker cannot hold another role.", {})
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn, persist_migration=not args.dry_run)
    store = state["recovery"]
    at = args.now or now_iso()
    work = _read_record(args.work) if args.work else None
    task_context = {"task": args.task, "fix_round": args.fix_round, "plan": args.correction_plan, "work": work}
    if document.get("task_context") is not None and document["task_context"] != task_context:
        raise UsageError("Saved plan and apply name different task, count or correction bounds; replan from the current ledger.", {})
    paths = resolve_paths(assignments, _parse_briefs(args.briefs), args.common)
    reports = _parse_reports(args.reports, assignments)
    supervised = supervision.dispatch_binding(state_path) is not None
    if supervised and (not args.task or set(reports) != set(assignments)):
        raise UsageError("Bound team rounds require --task and one --report ROLE=ABS_PATH for every assigned role before any worker input.", {})
    replayed = []
    dispatches = {}
    # Check retry identities before next-attempt validation: a completed retry
    # returns its original outcome and never consumes a second attempt.
    if args.task and not args.dry_run:
        for role, name in assignments.items():
            identifier, fingerprint = recovery.dispatch_identity(
                args.task, role, name, args.fix_round, paths, args.dispatch_id,
                options={**task_context, "rounds": rounds, "retain_context": args.retain_context, "no_clear": args.no_clear})
            if supervised:
                # Keep legacy retry IDs, while new bound dispatch fingerprints
                # also bind the explicit report path. Existing legacy receipts
                # cannot retroactively prove a report input they never stored.
                old = next((row for row in store["dispatches"] if row["id"] == identifier), None)
                if old is None or old["fingerprint"] != fingerprint:
                    fingerprint = supervision.report_bound_fingerprint(fingerprint, reports[role])
            prior = recovery.prior_dispatch(store, identifier, fingerprint)
            if supervised:
                saved_result = prior["result"] if prior and prior["status"] == "applied" else {}
                _supervision_enrollment(state_path, identifier, args.task, role, name, reports[role], at,
                    pane_id=saved_result.get("pane_id"), native=saved_result.get("context_session"),
                    replay=bool(saved_result), persist=bool(saved_result))
            if prior and prior["status"] == "applied":
                replayed.append({**prior["result"], "dispatch_id": identifier, "replayed": True})
            else:
                dispatches[role] = {"id": identifier, "fingerprint": fingerprint, "role": role, "agent": name,
                                    "task": args.task, "fix_round": args.fix_round,
                                    "plan": args.correction_plan, "work": work}
        if len(replayed) == len(assignments):
            return {"schema_version": APPLY_SCHEMA_VERSION, "dry_run": False, "applied_at": at, "applied": replayed}, None
        assignments = {role: name for role, name in assignments.items() if role in dispatches}
    elif args.dispatch_id and not args.task:
        raise UsageError("--dispatch-id requires --task; preserve the task's identity for retry accounting.", {})
    recovery.validate_work(store, state["assignments"], args.task, args.fix_round,
                           args.correction_plan, work, implementation="developer" in assignments)
    candidates = _candidate_tiers(list(assignments), agents, rounds, args.fix_round, judge)
    tiers = {}
    if candidates is not None:
        for role, name in assignments.items():
            if name not in candidates.get(role, {}):
                raise UsageError("Assigned agent {} has no eligible tier for {}; replan from current config.".format(name, role), {})
            if candidates[role][name] is not None:
                tiers[role] = candidates[role][name]
        saved_tiers = {role: tier for role, tier in document.get("tiers", {}).items() if tier is not None and role in assignments} if isinstance(document.get("tiers", {}), dict) else None
        if "tiers" in document and saved_tiers != tiers:
            raise UsageError("Plan tiers differ from current config or fix context; re-run plan before dispatch.", {})
    client = client if client is not None else _client(args, trace=trace)

    if args.dry_run:
        return (
            dry_run(
                client,
                assignments,
                agents_by_name,
                paths,
                no_clear=args.no_clear,
                retain_context=args.retain_context,
                task=args.task,
                fix_round=args.fix_round,
                settle_timeout_ms=args.settle_timeout,
                tiers=tiers,
                recovery=store, history=state["assignments"], plan_id=args.correction_plan, work=work,
            ),
            None,
        )

    prepared = []

    def prepare(step, observed):
        if not args.task:
            return
        record = {**dispatches[step["role"]], "observed_before": observed,
                  "brief": step["brief"], "common": step["common"]}
        if supervised:
            _supervision_enrollment(state_path, record["id"], args.task, step["role"], step["agent"], reports[step["role"]], at,
                                    pane_id=step["pane_id"], persist=True)
        recovery.reserve(store, record, at)
        save_state(state_path, state)
        prepared.append(record["id"])

    def observed_native(role, name, context):
        native = context.get("context_session")
        if native is None and role != "developer":
            native = native_context_session(client.agent_get(name), agents_by_name[name].kind)
            prior = next(row for row in store["dispatches"] if row["id"] == dispatches[role]["id"])
            if context.get("cleared") and native == prior["observed_before"].get("context_session"):
                native = None
        return native

    def before_send(step, context):
        if args.task:
            if supervised:
                native = observed_native(step["role"], step["agent"], context)
                _supervision_enrollment(state_path, dispatches[step["role"]]["id"], args.task, step["role"], step["agent"], reports[step["role"]], at,
                                        pane_id=step["pane_id"], native=native, persist=True)
            recovery.mark_sending(store, dispatches[step["role"]]["id"], at, context)
            save_state(state_path, state)

    def record(result):
        context = {key: result[key] for key in ("cleared", "clear_reason", "task", "fix_round", "context_session", "tier")}
        add_assignment(state, at, result["role"], result["agent"], status=result["status"], **context)
        if args.task:
            result["dispatch_id"] = dispatches[result["role"]]["id"]
            recovery.finish_dispatch(store, result["dispatch_id"], dict(result), len(state["assignments"]) - 1, at)
        save_state(state_path, state)
        # Commit the real transport outcome before optional identity refinement;
        # a failed sidecar write must never make a confirmed send replayable.
        if supervised and result["status"] == "applied":
            native = observed_native(result["role"], result["agent"], result)
            _supervision_enrollment(state_path, result["dispatch_id"], args.task, result["role"], result["agent"], reports[result["role"]], at,
                                    pane_id=result["pane_id"], native=native, persist=True)

    try:
        result = apply_assignments(
            client,
            assignments,
            agents_by_name,
            paths,
            at,
            no_clear=args.no_clear,
            retain_context=args.retain_context,
            fix_round=args.fix_round,
            history=state["assignments"],
            settle_timeout_ms=args.settle_timeout,
            on_prepare=prepare, on_before_send=before_send, on_result=record,
            recovery=store, plan_id=args.correction_plan, work=work,
            warn=warn,
            task=args.task,
            settle_sec=args.composer_settle,
            start_timeout_ms=args.start_timeout,
            allow_recovery=args.allow_recovery,
            tiers=tiers,
            retrospective_guard=retrospective_runtime.Guard(state_path, state, client, agents_by_name, at,
                                                          task=args.task, retain=args.retain_context, no_clear=args.no_clear),
            qualifications={role: [record for entry in agents_by_name[name].tiers.values()
                                   for record in entry.get("qualification", [])]
                            for role, name in assignments.items()},
        )
    except TeamLeadError as exc:
        for identifier in prepared:
            recovery.abort_pre_send(store, identifier, at, str(exc))
        if prepared:
            save_state(state_path, state)
        raise
    result["applied"] = replayed + result["applied"]
    not_started = [
        record["agent"]
        for record in result["applied"]
        if record.get("status") == "sent_but_not_started"
    ]
    if not not_started:
        return result, None
    return result, {
        "error": "sent_but_not_started",
        "message": "Sent the assignment to {} but neither saw it in the "
        "transcript nor saw the agent start a turn. Check the pane before "
        "assuming it is working.".format(", ".join(not_started)),
        "details": {"sent_but_not_started": not_started},
    }


def cmd_state(args, client=None, warn=None, trace=None):
    return load_state(_state_path(args), warn=warn), None


def cmd_status(args, client=None, warn=None, trace=None):
    state = load_state(_state_path(args), warn=warn)
    return {"schema_version": recovery.RECOVERY_SCHEMA_VERSION,
            "tasks": recovery.task_statuses(state["recovery"], state["assignments"])}, None


def cmd_recovery(args, client=None, warn=None, trace=None):
    state_path = _state_path(args)
    state = _load_state_for_write(state_path, warn)
    store, history = state["recovery"], state["assignments"]
    data, at = _read_record(args.record), args.now or now_iso()
    if args.command == "task":
        result = recovery.register_task(store, data, at)
    elif args.command == "checkpoint":
        judge = load_judge(_config_path(args))
        result = recovery.checkpoint(store, history, data, at, judge.agent if judge else None)
    elif args.command == "authorize-corrections":
        result = recovery.authorize_plan(store, history, data, at)
    elif args.command == "record-report":
        result = recovery.record_report(store, data, at)
    elif args.command == "recover-report":
        result = report_delivery.recover(store, history, data, at)
    elif args.command == "import-correction":
        result = historical.import_attempt(store, history, data, at)
        if result["assignment_index"] == len(history):
            add_assignment(state, data["occurred_at"], "developer", data["agent"], task=data["task"], fix_round=data["fix_round"])
    elif args.command == "record-historical-review":
        result = historical.record_review(store, data, at)
    else:
        agents = {agent.name: agent for agent in load_config(_config_path(args))}
        name = recovery.recovery_agent(store, history, args.command, data)
        if name not in agents:
            raise UsageError("The recorded worker is absent from config; restore its original identity before recovering.", {})
        client = client if client is not None else _client(args, trace=trace)
        live = client.agent_get(name)
        recovery.require_recovery_ready(live)
        if args.command == "recover-context":
            result = recovery.authorize_context(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "recover-role-clear":
            result = role_clear.record_role_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        elif args.command == "record-release-clear":
            result = historical.record_release_clear(store, history, data, at, native_context_session(live, agents[name].kind))
        else:
            result = recovery.reconcile(store, history, data, at, live)
            if result["status"] == "applied" and (result.get("result") or {}).get("status") != "applied":
                context = result.get("context_before_send", {})
                recovered = {"role": result["role"], "agent": name, "task": result["task"],
                             "fix_round": result["fix_round"], "status": "applied", "at": at,
                             "cleared": context.get("cleared"), "clear_reason": context.get("clear_reason", "unknown"),
                             "context_session": None, "tier": context.get("tier"),
                             "recovered": True, "dispatch_id": result["id"]}
                add_assignment(state, at, recovered["role"], name, status="applied", **{
                    key: recovered[key] for key in ("cleared", "clear_reason", "task", "fix_round", "context_session", "tier")})
                recovery.finish_dispatch(store, result["id"], recovered, len(history) - 1, at)
    recovery.validate_store(store, history)
    save_state(state_path, state)
    return result, None


def cmd_start_judge(args, client=None, warn=None, trace=None):
    document = _load_assignments(args.assignments, document=True)
    tier = document.get("judge") if isinstance(document, dict) else None
    if not isinstance(tier, dict) or not isinstance(tier.get("agent"), str) or not tier["agent"].strip():
        raise UsageError("Plan has no usable judge tier; run plan --roles judge.", {})
    if normalize_assignments(document).get("judge") != tier["agent"]:
        raise UsageError("Plan judge tier and assignment name different workers; replan.", {})
    parsed = parse_tiers({"build": {"model": tier.get("model"), "effort": tier.get("effort")}}, args.kind)["build"]
    agent = SimpleNamespace(name=tier["agent"], kind=args.kind, idle_markers=(), working_markers=(),
                            launch_args=parse_launch_args(tier.get("launch_args", []), args.kind))
    client = client if client is not None else _client(args, trace=trace)
    state_path = _state_path(args)
    state = retrospective_runtime.read_history(state_path)
    planned_task = (document.get("task_context") or {}).get("task")
    if args.task is not None and planned_task is not None and args.task != planned_task:
        raise UsageError("Judge --task differs from its plan; use the original task identity.", {})
    item = retrospective_runtime.request({"transitions": [{"agent": agent.name, "role": "judge",
        "model": parsed["model"], "effort": parsed["effort"], "context": "start", "task": args.task or planned_task,
        "pane": args.pane}]})["transitions"][0]
    guard = retrospective_runtime.Guard(state_path, state, client, {agent.name: agent}, args.now or now_iso())
    proof = start_worker(client, agent, args.pane, parsed, before_start=lambda: guard.before_start(item))
    guard.after_transition({"agent": agent.name}, launch_proof=verify_running(client, agent, args.pane, parsed))
    return {"agent": agent.name, "model": parsed["model"], "effort": parsed["effort"],
            "pane": args.pane, "argv_verified": True, "verified": proof}, None


def cmd_retrospective(args, client=None, warn=None, trace=None):
    path = _state_path(args)
    if args.command == "retro-list":
        return retrospective.list_notes(path, task=args.task, since=args.since), None
    if args.command == "retro-show":
        return retrospective.show(path, args.id, task=args.task), None
    at = args.now or now_iso()
    data = _read_record(args.record)
    saved = None
    if args.command == "retro-record":
        if not isinstance(data, dict) or not isinstance(data.get("check"), str) or not Path(data["check"]).is_absolute():
            raise UsageError("retro-record requires an absolute check receipt path in its metadata.", {})
        saved = _read_record(data["check"])
        if (not isinstance(saved, dict) or saved.get("schema_version") != retrospective.SCHEMA_VERSION
                or saved.get("state_path") != str(retrospective.canonical_state(path))):
            raise UsageError("Retrospective check receipt belongs to another state or schema; rerun retro-check for this --state.", {})
        retrospective.validate_coverage(saved.get("coverage"))
        value = saved.get("request")
    else:
        value = data
    normalized = retrospective_runtime.request(value)
    state = retrospective_runtime.read_history(path)
    agents = {}
    if normalized["transitions"]:
        agents = {agent.name: agent for agent in load_config(_config_path(args))}
        client = client if client is not None else _client(args, trace=trace)
    result = retrospective_runtime.check(path, state, client, agents, normalized, at, allow_pending=saved is not None)
    if saved is None:
        return result, None
    if saved["coverage"] != result["coverage"]:
        raise UsageError("Retrospective worker, report, assignment or target changed since its check; refresh only the affected coverage and the lead's synthesis before recording.", {})
    checked_at = retrospective.utc(saved.get("checked_at"))
    if checked_at > retrospective.utc(at) or retrospective.utc(data.get("period_end")) < checked_at:
        raise UsageError("Retrospective period must include its evidence checkpoint and cannot come from the future; refresh the note's actual interval.", {})
    covered_names = {row["agent"] for row in result["coverage"]}
    participants, unavailable = data.get("participants"), data.get("unavailable")
    if (not isinstance(participants, list) or any(not isinstance(name, str) for name in participants)
            or not isinstance(unavailable, dict) or not covered_names <= set(participants) | set(unavailable)):
        raise UsageError("Retrospective metadata must account for each checked worker as participating or explicitly unavailable.", {})
    for row in result["coverage"]:
        if not row["first_start"] and row["source"]["report"] is None and not row["source"]["unavailable"]:
            raise UsageError("Outgoing worker {} needs a report path or explicit unavailable reason in the check request; status alone is not retrospective evidence.".format(row["agent"]), {})
    tasks = data.get("tasks")
    actual_tasks = {task for row in result["coverage"] for task in (row["source"]["task"], row["target"]["task"]) if task is not None}
    if not isinstance(tasks, list) or any(not isinstance(task, str) for task in tasks) or not actual_tasks <= set(tasks):
        raise UsageError("Retrospective tasks must include its outgoing and proposed task identities.", {})
    triggers = data.get("triggers", [])
    if not isinstance(triggers, list):
        raise UsageError("Retrospective triggers must list daily and/or transition.", {})
    if (result["cadence"]["due"] and "daily" not in triggers) or (any(row["transition_required"] for row in result["coverage"]) and "transition" not in triggers):
        raise UsageError("Retrospective triggers must include every due daily/transition checkpoint represented by this note.", {})
    return retrospective.record(path, data, result["coverage"], at), None


def cmd_probe_report(args, client=None, warn=None, trace=None):
    if not Path(args.report).is_absolute() or any(ord(char) < 32 for char in args.report) or args.lines < 1:
        raise UsageError("Report probing needs an absolute one-row report path and positive --lines.", {})
    client = client if client is not None else _client(args, trace=trace)
    return report_delivery.probe(client, args.agent, args.pane, args.report, sys.stdin.read().rstrip("\n"), args.lines), None


def cmd_memory(args, client=None, warn=None, trace=None):
    return memory.run_command(args, _state_path(args), args.now or now_iso()), None


def cmd_attention(args, client=None, warn=None, trace=None):
    return attention.run_command(args, _state_path(args), args.now or now_iso()), None


SUPERVISION_COMMANDS = frozenset("supervision-" + action for action in ("bind", "enroll", "ack", "resolve", "hold", "resume", "drain", "status", "watch"))


def cmd_supervision(args, client=None, warn=None, trace=None):
    return supervision_runtime.run_command(args, _state_path(args), args.now or now_iso(),
                                           client=client, clock=now_iso, sleeper=time.sleep), None


def cmd_restoration(args, client=None, warn=None, trace=None):
    client = client if client is not None else _client(args, trace=trace)
    return restoration.run_command(args, client, sleep=time.sleep), None


COMMANDS = {
    "measure": cmd_measure,
    "plan": cmd_plan,
    "apply": cmd_apply,
    "state": cmd_state,
    "status": cmd_status,
    **{command: cmd_recovery for command in ("task", "checkpoint", "authorize-corrections", "recover-context", "recover-role-clear", "record-report", "reconcile", "record-release-clear", "import-correction", "record-historical-review", "recover-report")},
    "start-judge": cmd_start_judge,
    "probe-report": cmd_probe_report,
    **{command: cmd_retrospective for command in ("retro-check", "retro-record", "retro-list", "retro-show")},
    **{command: cmd_memory for command in memory.COMMANDS},
    **{command: cmd_attention for command in attention.COMMANDS},
    **{command: cmd_supervision for command in SUPERVISION_COMMANDS},
    **{command: cmd_restoration for command in restoration.COMMANDS},
}


def main(argv=None, stdout=None, stderr=None, client=None):
    """Entry point. Returns the process exit code rather than calling sys.exit."""
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    args = build_parser().parse_args(argv)

    def trace(message):
        # Trace lines identify themselves ("herdr> ..."), so they go out bare.
        stderr.write(message + "\n")

    def warn(message):
        stderr.write(DIAGNOSTIC_PREFIX + message + "\n")

    try:
        # Commands that may migrate or write state share its canonical lock.
        # Dry runs, probes, and retrospective reads remain read-only.
        readonly = args.command in {"probe-report", "retro-check", "retro-list", "retro-show"} or getattr(args, "dry_run", False)
        separate_owner = args.command in memory.COMMANDS | attention.COMMANDS | SUPERVISION_COMMANDS | restoration.COMMANDS
        lock = nullcontext() if readonly or separate_owner else state_lock(retrospective.canonical_state(_state_path(args)))
        with lock:
            retro_lock = retrospective.lock(_state_path(args)) if not readonly and args.command in {"apply", "start-judge", "retro-record"} else nullcontext()
            with retro_lock:
                payload, failure = COMMANDS[args.command](args, client=client, warn=warn, trace=trace)
    except TeamLeadError as exc:
        json.dump(exc.to_dict(), stderr, indent=2)
        stderr.write("\n")
        return 1

    json.dump(payload, stdout, indent=2)
    stdout.write("\n")
    if failure:
        # stdout keeps the full document either way; stderr says why the exit
        # code is non-zero.
        json.dump(failure, stderr, indent=2)
        stderr.write("\n")
        return 1
    return 0
