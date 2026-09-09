"""Lead-owned obligations: explicit lifecycle events, independent of Herdr status."""

import copy
import json
import re
from datetime import timezone
from pathlib import Path
from typing import NoReturn

from .chronology import timestamp
from .errors import StateError, UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
KINDS = {"question", "decision", "review", "blocker", "failure", "followup", "update"}
CLOSED = {"resolved", "superseded"}
COMMANDS = {"attention-record", "attention-update", "attention-progress", "attention-list", "attention-show", "catch-up"}


def canonical_state(path):
    return Path(path).expanduser().resolve()


def storage_path(path):
    return Path(str(canonical_state(path)) + ".attention.json")


def _fail(message) -> NoReturn:
    raise UsageError(message, {})


def _text(value, label, *, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail("{} must contain 1–{} characters; record the actual obligation and evidence.".format(label, limit))
    return value


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        _fail("Attention ids need 1–128 letters, digits, dots, underscores, colons or hyphens; reuse the original id when retrying.")
    return value


def _utc(value):
    return timestamp(value, "Attention timestamp").astimezone(timezone.utc).isoformat()


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        _fail("Attention input requires {} and accepts only optional {}; inspect references/attention.md and retry without changing saved state.".format(
            ", ".join(sorted(required)), ", ".join(sorted(optional)) or "none"))


def _sources(value):
    if not isinstance(value, list) or not value or len(value) > 30:
        _fail("Attention sources must contain 1–30 explicit source references; include the user message, ledger or artifact supporting the entry.")
    for row in value:
        _fields(row, {"schema_version", "kind", "ref"})
        if type(row["schema_version"]) is not int or row["schema_version"] != SCHEMA_VERSION:
            _fail("Attention source schema_version must be 1; preserve unsupported records and update the owner.")
        if not isinstance(row["kind"], str) or row["kind"] not in {"user_message", "task_ledger", "retrospective", "artifact", "other"}:
            _fail("Attention source kind must be user_message, task_ledger, retrospective, artifact or other.")
        _text(row["ref"], "Source reference", limit=2000)
    return copy.deepcopy(value)


def _details(data):
    for key in ("title", "context", "consequence", "resolution_condition"):
        _text(data[key], key, limit=300 if key == "title" else 8000)
    if type(data["priority"]) is not int or not 0 <= data["priority"] <= 100:
        _fail("Attention priority must be a lead-assigned integer from 0 to 100, with 100 most consequential.")
    if not isinstance(data["options"], list) or len(data["options"]) > 10:
        _fail("Attention options must list at most 10 choices; use [] when choices do not apply.")
    for option in data["options"]:
        _text(option, "Option", limit=2000)
    if data["recommendation"] is not None:
        _text(data["recommendation"], "Recommendation")
    _sources(data["sources"])


def _creation(data):
    _fields(data, {"id", "kind", "title", "context", "consequence", "resolution_condition", "sources"},
            {"task", "priority", "options", "recommendation"})
    result = {"task": None, "priority": 50, "options": [], "recommendation": None, **copy.deepcopy(data)}
    _id(result["id"])
    _text(result["id"], "Obligation id", limit=110)
    if not isinstance(result["kind"], str) or result["kind"] not in KINDS:
        _fail("Attention kind must be question, decision, review, blocker, failure, followup or update.")
    if result["task"] is not None:
        _text(result["task"], "Task", limit=500)
    _details(result)
    return result


def _evidence(value):
    _fields(value, {"schema_version", "kind", "ref", "summary"})
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        _fail("Resolution evidence schema_version must be 1; preserve unsupported evidence.")
    if not isinstance(value["kind"], str) or value["kind"] not in {"user_answer", "review_outcome", "delivery", "acknowledgement", "verified_outcome", "source"}:
        _fail("Evidence kind must be user_answer, review_outcome, delivery, acknowledgement, verified_outcome or source.")
    _text(value["ref"], "Evidence reference", limit=2000)
    _text(value["summary"], "Evidence summary")


def _update(data):
    _fields(data, {"event_id", "id", "expected_revision", "action", "reason"}, {"evidence", "until", "replacement", "changes"})
    for key in ("id", "event_id"):
        _id(data[key])
    if type(data["expected_revision"]) is not int or data["expected_revision"] < 1:
        _fail("expected_revision must match the current entry revision; read attention-show before updating stale context.")
    _text(data["reason"], "Lifecycle reason")
    extras = {"present": {"evidence"}, "resolve": {"evidence"}, "defer": {"until", "evidence"},
              "reopen": {"evidence"}, "supersede": {"replacement", "evidence"}, "amend": {"changes", "evidence"}}
    action = data["action"]
    if not isinstance(action, str) or action not in extras:
        _fail("Attention action must be present, resolve, defer, reopen, supersede or amend; unrelated messages have no lifecycle action.")
    if set(data) - {"event_id", "id", "expected_revision", "action", "reason"} != extras[action]:
        _fail("Attention {} requires only its additional fields: {}.".format(action, ", ".join(sorted(extras[action]))))
    _evidence(data["evidence"])
    result = copy.deepcopy(data)
    if action == "defer":
        result["until"] = _utc(data["until"])
    if action == "supersede":
        _id(data["replacement"])
    return result


def _progress(data):
    _fields(data, {"id", "task", "summary", "assessment", "sources"})
    _id(data["id"])
    _text(data["id"], "Progress id", limit=110)
    _text(data["task"], "Progress task", limit=500)
    _text(data["summary"], "Progress summary")
    if not isinstance(data["assessment"], str) or data["assessment"] not in {"verified", "reported", "unknown"}:
        _fail("Progress assessment must be verified, reported or unknown; Herdr completion labels never prove acceptance.")
    _sources(data["sources"])
    if not any(row["kind"] == "task_ledger" for row in data["sources"]):
        _fail("Progress requires its task_ledger source reference; a cached summary never replaces the acceptance ledger.")
    return copy.deepcopy(data)


def _apply(event, entries, progress):
    """Replay owner events; no wall clock, external reads, or inferred decisions."""
    action, data, at = event["action"], event["data"], event["at"]
    if action == "record":
        value = _creation(data)
        if value["id"] in entries:
            _fail("Duplicate obligation id in history; restore the original attention file.")
        entries[value["id"]] = {"schema_version": SCHEMA_VERSION, **value, "revision": 1, "status": "open",
                                "created_at": at, "updated_at": at, "last_presented_at": None,
                                "deferred_until": None, "resolution": None, "superseded_by": None}
        return
    if action == "progress":
        value = _progress(data)
        if any(row["id"] == value["id"] for row in progress):
            _fail("Duplicate progress id in history; preserve the source and restore the original file.")
        progress.append({"schema_version": SCHEMA_VERSION, **value, "at": at})
        return
    data = _update(data)
    entry = entries.get(data["id"])
    if entry is None or data["expected_revision"] != entry["revision"]:
        _fail("Attention entry is missing or its revision changed; read attention-show and reconcile before submitting a new event id.")
    action = data["action"]
    if action != "reopen" and entry["status"] in CLOSED:
        _fail("This attention entry is closed; use an evidenced reopen before changing it.")
    if action == "present":
        if data["evidence"]["kind"] != "delivery":
            _fail("Presentation needs delivery evidence identifying the actual user-facing message; displaying a local draft is not delivery.")
        entry["last_presented_at"] = at
    elif action == "resolve":
        allowed = {"question": {"user_answer"}, "decision": {"user_answer"}, "review": {"review_outcome"},
                   "blocker": {"user_answer", "acknowledgement", "verified_outcome"}, "failure": {"user_answer", "acknowledgement", "delivery", "verified_outcome"},
                   "followup": {"delivery"}, "update": {"delivery"}}
        if data["evidence"]["kind"] not in allowed[entry["kind"]]:
            _fail("{} requires {} evidence to resolve; presentation or an unrelated user message leaves it open.".format(entry["kind"], " or ".join(sorted(allowed[entry["kind"]]))))
        entry.update(status="resolved", resolution=copy.deepcopy(data["evidence"]), deferred_until=None)
    elif action == "defer":
        if timestamp(data["until"], "Resurface time") <= timestamp(at, "Deferral time"):
            _fail("Deferral must name a future resurface time; keep already-due obligations open.")
        entry.update(status="deferred", deferred_until=data["until"])
    elif action == "reopen":
        if entry["status"] == "open":
            _fail("Attention entry is already open; record new context with amend instead.")
        entry.update(status="open", deferred_until=None, resolution=None, superseded_by=None)
    elif action == "supersede":
        replacement = entries.get(data["replacement"])
        if replacement is None or replacement["status"] in CLOSED or replacement["id"] == entry["id"]:
            _fail("Record a distinct open replacement obligation before superseding this entry; never discard the question.")
        entry.update(status="superseded", superseded_by=replacement["id"], deferred_until=None)
    else:
        changes = data["changes"]
        fields = {"title", "context", "consequence", "resolution_condition", "sources", "options", "recommendation", "priority"}
        if not isinstance(changes, dict) or not changes or not set(changes) <= fields:
            _fail("Amend needs nonempty changes to title, context, consequence, resolution_condition, sources, options, recommendation or priority; supersede a different obligation.")
        _details({**entry, **changes})
        entry.update(copy.deepcopy(changes))
    entry["updated_at"] = at
    entry["revision"] += 1


def load(path):
    """Read and validate only. Missing means first use; malformed never means empty."""
    target = storage_path(path)
    document = {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)), "events": []}
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if target.is_symlink():
            raise StateError("Attention sidecar is a dangling link; restore its saved target before proceeding.", {}) from None
        return document, {}, []
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise StateError("Cannot read attention history {}: {}. Preserve its bytes and restore access or its verified backup.".format(target, exc), {}) from None
    entries, progress, ids = {}, [], set()
    try:
        _fields(document, {"schema_version", "state_path", "events"})
        if (type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION
                or document["state_path"] != str(canonical_state(path)) or not isinstance(document["events"], list)):
            _fail("Unsupported attention schema or state identity; preserve the file and update its owner or recover the correct state path.")
        previous = None
        for event in document["events"]:
            _fields(event, {"schema_version", "event_id", "at", "action", "data"})
            if type(event["schema_version"]) is not int or event["schema_version"] != SCHEMA_VERSION:
                _fail("Unsupported attention event schema; update the owner without rewriting history.")
            _id(event["event_id"])
            at = timestamp(event["at"], "Saved attention event")
            if event["event_id"] in ids or (previous is not None and at < previous):
                _fail("Attention events contain duplicates or reversed chronology; restore the original history.")
            if not isinstance(event["action"], str) or event["action"] not in {"record", "update", "progress"}:
                _fail("Unknown attention event action; update the owner before writing.")
            expected_id = (("record:" if event["action"] == "record" else "progress:") + str(event["data"].get("id"))) if isinstance(event["data"], dict) and event["action"] != "update" else None
            if expected_id is not None and event["event_id"] != expected_id:
                _fail("Attention event id disagrees with its obligation; restore the original history.")
            if event["action"] == "update" and (not isinstance(event["data"], dict) or event["data"].get("event_id") != event["event_id"]):
                _fail("Attention lifecycle event identity changed; restore the original history.")
            _apply(event, entries, progress)
            previous = at
            ids.add(event["event_id"])
    except UsageError as exc:
        raise StateError("Attention history is unusable: {} Preserve it; do not replace it with an empty queue.".format(exc.message), {}) from None
    return document, entries, progress


def write(path, action, data, at):
    """Commit one event under the sidecar lock; identical retries never duplicate."""
    parsers = {"record": _creation, "update": _update, "progress": _progress}
    if action not in parsers:
        _fail("Unknown attention write; choose record, update or progress.")
    value = parsers[action](data)
    name = value["event_id"] if action == "update" else action + ":" + value["id"]
    _id(name)
    at = _utc(at)
    with state_lock(storage_path(path)):
        document, entries, progress = load(path)
        prior = next((event for event in document["events"] if event["event_id"] == name), None)
        if prior:
            if prior["action"] != action or prior["data"] != value:
                _fail("Attention event id already records different input; preserve it and use a new id for an intentional change.")
            return {"schema_version": SCHEMA_VERSION, "event": copy.deepcopy(prior), "replayed": True}
        if document["events"] and timestamp(at, "New event") < timestamp(document["events"][-1]["at"], "Latest event"):
            _fail("Attention event predates saved history; use the current checkpoint without rewriting earlier events.")
        event = {"schema_version": SCHEMA_VERSION, "event_id": name, "at": at, "action": action, "data": value}
        _apply(event, entries, progress)
        document["events"].append(event)
        save_state(storage_path(path), document)
    return {"schema_version": SCHEMA_VERSION, "event": event, "replayed": False}


def show(path, name):
    document, entries, _progress_rows = load(path)
    if name not in entries:
        _fail("No attention entry matches {}; use attention-list with the same --state path.".format(name))
    return {"schema_version": SCHEMA_VERSION, "entry": entries[name],
            "history": [event for event in document["events"] if event["action"] != "progress" and event["data"]["id"] == name]}


def register_commands(sub, common):
    for command in ("attention-record", "attention-update", "attention-progress"):
        parser = sub.add_parser(command, parents=[common], help="Persist a lead-owned attention or progress event.")
        parser.add_argument("--record", required=True, help="JSON input file; see references/attention.md.")
        parser.add_argument("--now", help="ISO-8601 checkpoint with timezone.")
    for command in ("attention-list", "catch-up"):
        parser = sub.add_parser(command, parents=[common], help="Read a bounded offline view of saved user-facing obligations.")
        parser.add_argument("--task")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--since", help="Filter progress and closed history; never suppress open obligations.")
        parser.add_argument("--include-closed", action="store_true")
        parser.add_argument("--now", help="ISO-8601 checkpoint with timezone.")
    parser = sub.add_parser("attention-show", parents=[common], help="Read one obligation and its complete lifecycle history offline.")
    parser.add_argument("--id", required=True)
    parser.add_argument("--now", help="ISO-8601 checkpoint; history readback itself has no clock-dependent mutation.")


def run_command(args, state_path, now):
    if args.command in {"attention-record", "attention-update", "attention-progress"}:
        try:
            data = json.loads(Path(args.record).expanduser().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise UsageError("Cannot read attention input {}: {}. Save valid UTF-8 JSON and retry the same event id.".format(args.record, exc), {}) from None
        return write(state_path, args.command.removeprefix("attention-"), data, getattr(args, "now", None) or now)
    if args.command == "attention-show":
        return show(state_path, args.id)
    from .attention_view import catch_up
    return catch_up(state_path, getattr(args, "now", None) or now, task=args.task, limit=args.limit,
                    offset=args.offset, since=args.since, include_closed=args.include_closed)
