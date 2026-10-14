"""`teamlead preflight`: the round's pre-planning facts in one JSON document.

Before planning, the lead needs five facts: the live roster, repo authority,
whether a retrospective is due, measured headroom, and the fired composition
triggers. Each has a standalone owner that stays usable for recovery; this
module composes them and adds no rule of its own.

Sources, each reused rather than restated:

* roster      -- `roster.sh`, run as a subprocess; its JSON is parsed.
* authority   -- `verify-authority.sh <owner/repo>`, run as a subprocess.
* retrospective -- `retrospective_runtime.check` with no planned transitions,
  the same call `retro-check` makes for a daily check.
* headroom    -- the `measure` command, through the callable the CLI passes in;
  it sends usage commands to idle workers and saves the snapshot.
* triggers    -- `triggers.run_command` without roles, requirements or
  decisions: report-only. Staffing stays with `detect-triggers` and `plan`.

Every blocker found is listed in `stops`; a stop is an answer, not a failure.
A `TeamLeadError` escapes only for bad arguments, an unreadable config, a
planned-surfaces file that cannot be read, or a git failure on the given revs
-- the inputs preflight itself needs.
"""

import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import retrospective_runtime, triggers
from .errors import TeamLeadError, UsageError

PREFLIGHT_SCHEMA_VERSION = 1

#: Directory holding the skill's shell scripts.
SKILL_DIR = Path(__file__).resolve().parent.parent

#: The `<owner>/<repo>` shape verify-authority.sh accepts.
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: Stop code -> the reference that resolves it. `read_next` is the ordered,
#: de-duplicated set of references for the stops and facts of this run.
REFERENCES = {
    "roster_empty": "skills/herdr-teamlead/references/round-setup.md",
    "roster_unavailable": "skills/herdr-teamlead/references/round-setup.md",
    "authority_not_owned": "skills/herdr-teamlead/references/round-setup.md",
    "authority_failed": "skills/herdr-teamlead/references/round-setup.md",
    "retrospective_due": "skills/herdr-teamlead/references/retrospectives.md",
    "retrospective_unavailable": "skills/herdr-teamlead/references/retrospectives.md",
    "headroom_failed": "skills/herdr-teamlead/references/round-setup.md",
    "headroom_unavailable": "skills/herdr-teamlead/references/round-setup.md",
    "trigger_declaration": "skills/herdr-teamlead/references/specialists.md",
    "triggers_unclassified": "skills/herdr-teamlead/references/specialists.md",
}

#: Read when any composition trigger fired; firing is not a stop.
FIRED_REFERENCE = "skills/herdr-teamlead/references/specialists.md"


def _stop(code, message):
    return {"code": code, "message": message, "reference": REFERENCES[code]}


def run_script(name, argv, env):
    """Run one of the skill's shell scripts; return (exit, stdout, stderr)."""
    try:
        completed = subprocess.run(["bash", str(SKILL_DIR / name), *argv], capture_output=True,
                                   text=True, env=env, check=False)
    except OSError as exc:
        return None, "", "could not run bash for {}: {}".format(name, exc.strerror or exc)
    return completed.returncode, completed.stdout, completed.stderr.strip()


def _json_object(text):
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def validate(args):
    """Refuse arguments preflight cannot act on, before any worker contact."""
    if not SLUG_PATTERN.fullmatch(args.slug):
        raise UsageError("--slug {!r} is not an <owner>/<repo> slug; pass the full slug, e.g. gamussa/coding-policy.".format(args.slug), {})
    if not Path(args.repo).is_dir():
        raise UsageError("--repo {} is not a directory; pass the repository checkout holding .herdr/triggers.json.".format(args.repo), {})
    if args.base is None and (args.head is not None or args.planned is not None):
        raise UsageError("--head and --planned classify against a base; pass the task's recorded --base.", {})
    if args.now is not None:
        try:
            parsed = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None:
            raise UsageError("--now {!r} is not an ISO-8601 timestamp with a timezone; pass e.g. 2026-10-14T12:00:00+00:00, or omit it for the current time.".format(args.now), {})


def gather_triggers(args, runner=None):
    """Report-only trigger facts and any declaration or classification stop."""
    plan_path = args.planned
    if plan_path is not None:
        triggers.load_plan(plan_path)
    try:
        triggers.load_declaration(args.repo)
    except triggers.DeclarationError as exc:
        return None, "trigger declaration refused", [_stop("trigger_declaration", exc.message)]
    if args.base is None:
        return None, "no --base or --planned given; pass them to classify this round", []
    detection = SimpleNamespace(repo=args.repo, base=args.base, head=args.head, planned=plan_path,
                                roles=None, requirements=None, decisions=None)
    try:
        payload, _staffing = triggers.run_command(detection, runner=runner)
    except triggers.UnclassifiedRoundError as exc:
        return None, "round classifies nothing", [_stop("triggers_unclassified", exc.message)]
    facts = {"declaration": payload["declaration"], "base": payload["base"], "head": payload["head"],
             "fired": payload["fired"],
             "triggers": [{"trigger": row["trigger"], "fired": row["fired"], "signals": row["signals"]}
                          for row in payload["triggers"]]}
    return facts, None, []


def gather_roster(env):
    code, out, err = run_script("roster.sh", [], env)
    roster = _json_object(out) if code == 0 else None
    if code == 0 and roster is not None and isinstance(roster.get("agents"), list):
        empty = not roster["agents"]
        fact = {"exit": 0, "caller": roster.get("caller"), "agents": roster["agents"], "empty": empty, "diagnostic": None}
        if empty:
            return fact, [_stop("roster_empty", "No named worker is live. Read unnamed panes from `herdr agent list` and name each with `herdr agent rename <pane-id> <name>`, then rerun preflight.")]
        return fact, []
    diagnostic = err or "roster.sh emitted no readable roster"
    fact = {"exit": code, "caller": None, "agents": [], "empty": True, "diagnostic": diagnostic}
    return fact, [_stop("roster_unavailable", "roster.sh exit {}: {} -- resolve it under round-setup Step 2 Roster, then rerun preflight.".format(code, diagnostic))]


def gather_authority(slug, env):
    code, out, err = run_script("verify-authority.sh", [slug], env)
    verdict = _json_object(out) if code == 0 else None
    if code == 0 and verdict is not None and isinstance(verdict.get("authorized"), bool):
        fact = {"exit": 0, "result": verdict, "diagnostic": None}
        if verdict["authorized"]:
            return fact, []
        return fact, [_stop("authority_not_owned", "The operator does not own {}. Record existing explicit per-action permission, ask for only the missing one, or keep the round read-only (round-setup Step 2 Authority).".format(slug))]
    diagnostic = err or "verify-authority.sh emitted no readable verdict"
    fact = {"exit": code, "result": None, "diagnostic": diagnostic}
    return fact, [_stop("authority_failed", "verify-authority.sh exit {}: {} -- an unanswerable check is not permission; resolve it, then rerun preflight.".format(code, diagnostic))]


def gather_retrospective(state_path, at):
    try:
        result = retrospective_runtime.check(state_path, retrospective_runtime.read_history(state_path),
                                             None, {}, {"transitions": []}, at)
    except TeamLeadError as exc:
        fact = {"due": None, "cadence": None, "error": exc.to_dict()}
        return fact, [_stop("retrospective_unavailable", "Retrospective check failed: {} -- reconcile it before planning.".format(exc.message))]
    fact = {"due": result["due"], "cadence": result["cadence"], "error": None}
    if result["due"]:
        return fact, [_stop("retrospective_due", "A retrospective is due ({}). Run retro-check, write the synthesis and retro-record it, then rerun preflight.".format(result["cadence"]["reason"]))]
    return fact, []


def gather_headroom(measure_fn):
    try:
        snapshot, _failure = measure_fn()
    except TeamLeadError as exc:
        fact = {"saved": False, "snapshot": None, "failed_agents": [], "skipped_busy": [], "error": exc.to_dict()}
        return fact, [_stop("headroom_unavailable", "measure failed: {} -- fix it and rerun preflight or `teamlead measure`.".format(exc.message))]
    fact = {"saved": True, "snapshot": snapshot, "failed_agents": list(snapshot["failed_agents"]),
            "skipped_busy": sorted(name for name, record in snapshot["agents"].items() if record.get("skipped")),
            "error": None}
    if fact["failed_agents"]:
        return fact, [_stop("headroom_failed", "Could not measure {}; see each agent's `error` and obtain its reading before relying on that seat.".format(", ".join(fact["failed_agents"])))]
    return fact, []


def read_next(stops, trigger_facts):
    """The references this round needs: one per stop, plus specialists on a fired trigger."""
    wanted = [stop["reference"] for stop in stops]
    if trigger_facts is not None and trigger_facts["fired"]:
        wanted.append(FIRED_REFERENCE)
    return sorted(set(wanted))


def run(args, at, measure_fn, state_path, herdr_bin=None, runner=None):
    """Gather every fact; return the preflight document."""
    validate(args)
    trigger_facts, trigger_reason, stops = gather_triggers(args, runner=runner)
    env = dict(os.environ)
    if herdr_bin:
        env["HERDR_BIN"] = herdr_bin
    roster, found = gather_roster(env)
    stops += found
    authority, found = gather_authority(args.slug, env)
    stops += found
    retrospective, found = gather_retrospective(state_path, at)
    stops += found
    if roster["empty"]:
        # Measuring sends usage commands to workers; with no usable roster the
        # old Step 2 finished before Step 4 ever ran.
        headroom = {"saved": False, "snapshot": None, "failed_agents": [], "skipped_busy": [],
                    "error": None, "skipped": "roster not usable"}
    else:
        headroom, found = gather_headroom(measure_fn)
        stops += found
    return {"schema_version": PREFLIGHT_SCHEMA_VERSION, "checked_at": at, "repo": str(args.repo),
            "slug": args.slug, "roster": roster, "authority": authority, "retrospective": retrospective,
            "headroom": headroom, "triggers": trigger_facts, "triggers_reason": trigger_reason,
            "verdict": "stop" if stops else "ready", "stops": stops,
            "read_next": read_next(stops, trigger_facts)}


HELP = """\
Gather the round's pre-planning facts in one call: roster (roster.sh),
authority (verify-authority.sh), retrospective cadence (retro-check's daily
check), headroom (measure), and fired composition triggers (detect-triggers
without staffing). Side effect: the headroom step IS `measure` -- it sends each
idle configured worker its usage command and saves the snapshot to --state. It
is skipped when the roster is empty or unavailable.

stdout: {schema_version, checked_at, repo, slug, roster, authority,
retrospective, headroom, triggers, triggers_reason, verdict, stops, read_next}.
verdict is "ready" or "stop"; stops lists every blocker as {code, message,
reference}. triggers is null with triggers_reason when no --base is given or a
stop prevented classification. Fired triggers are facts, never stops; staffing
stays with detect-triggers and plan.

read_next table: roster_*, authority_*, headroom_* -> round-setup.md;
retrospective_* -> retrospectives.md; trigger_declaration,
triggers_unclassified, or any fired trigger -> specialists.md. An all-clear
run reads nothing extra.

exit: 0 when the facts were gathered (ready or stop); 1 with a JSON error on
stderr for bad arguments, an unreadable config, an unreadable --planned file,
or a git failure on the given revisions; 2 for an argparse error."""


def register_command(sub, common, add_measure_tuning):
    parser = sub.add_parser("preflight", parents=[common], help="Gather roster, authority, retrospective, headroom and trigger facts in one JSON.",
                            description=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, metavar="DIR", help="Repository checkout holding " + triggers.DECLARATION_FILE + ".")
    parser.add_argument("--slug", required=True, metavar="OWNER/REPO", help="GitHub slug verify-authority.sh checks.")
    parser.add_argument("--base", metavar="REF", help="The task's recorded base; omit to skip trigger classification.")
    parser.add_argument("--head", metavar="REF", help="Pushed head; omit to read the working tree.")
    parser.add_argument("--planned", metavar="FILE", help="Surfaces a pre-implementation round will touch (requires --base).")
    parser.add_argument("--now", metavar="ISO8601", help="Timestamp for the retrospective check and the snapshot (default: now).")
    add_measure_tuning(parser)
    return parser
