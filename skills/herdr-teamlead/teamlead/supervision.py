"""Durable lead-owned fleet observations, acknowledgements, and stop bindings.

Every document/row is schema 1. The canonical selected state owns an adjacent
`.supervision.json`; exact native lead identities discover it through one
hashed binding under the default state directory. Reads never migrate or write.
Unknown/corrupt files are preserved and mutations refuse them. Transactions use
state_lock/save_state; observations and acknowledgements commit together.

Events describe observations, never task acceptance. Acknowledgement requires
an explicit lead outcome and evidence receipt; reading/draining cannot ack.
Resolving enrollment ends observation only and grants no task completion.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from .errors import StateError, UsageError
from .state import default_state_path, save_state, state_lock

SCHEMA_VERSION = 1
DEFAULT_RECHECK_SECONDS = 30


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise UsageError("Pass an ISO timestamp with an explicit timezone.", {}) from None
    if parsed.tzinfo is None:
        raise UsageError("Pass an ISO timestamp with an explicit timezone.", {})
    return parsed


def text(value, label):
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise UsageError("{} must be nonempty single-line text; preserve the original record.".format(label), {})
    return value


def canonical(path):
    return Path(path).expanduser().resolve()


def store_path(state_path):
    return Path(str(canonical(state_path)) + ".supervision.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def report_bound_fingerprint(fingerprint, report):
    """A bound round's dispatch fingerprint: the legacy digest wrapped with the exact report path.

    `apply` stores this form for every new dispatch of a bound round, and
    owner recovery must reproduce it from the same report path; any other path
    or a second wrapping yields a different value.
    """
    return digest({"dispatch": fingerprint, "report": report})


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if value is None:
            raise StateError("Supervision record {} contains null instead of an owner record. Preserve it and restore a supported backup.".format(path), {})
        return value
    except FileNotFoundError:
        if Path(path).is_symlink():
            raise StateError("Supervision record {} is a dangling link. Restore its original target; do not replace saved history with fresh state.".format(path), {}) from None
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError("Cannot read supervision record {}: {}. Restore its original readable bytes; do not overwrite it.".format(path, exc), {}) from None


def receipt(path):
    source = canonical(text(path, "evidence path"))
    if not Path(path).expanduser().is_absolute():
        raise UsageError("Evidence paths must be absolute; supply the saved lead outcome or task-ledger path.", {})
    try:
        body = source.read_bytes()
    except OSError as exc:
        raise StateError("Cannot read evidence {}: {}. Save the evidence before recording the outcome.".format(source, exc), {}) from None
    return {"schema_version": 1, "path": str(source), "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


def evidence(paths):
    if not isinstance(paths, list) or not paths:
        raise UsageError("Provide at least one absolute saved evidence path for this lead outcome.", {})
    return [receipt(path) for path in paths]


def empty(state_path):
    return {"schema_version": 1, "state_path": str(canonical(state_path)), "binding": None,
            "members": [], "events": [], "acknowledgements": [], "holds": [], "watchers": []}


def load(state_path):
    path = store_path(state_path)
    data = read_json(path)
    if data is None:
        return empty(state_path)
    try:
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("state_path") != str(canonical(state_path))):
            raise ValueError("document identity or schema differs")
        for key in ("members", "events", "acknowledgements", "holds", "watchers"):
            if not isinstance(data.get(key), list) or any(not isinstance(row, dict) or row.get("schema_version") != 1 for row in data[key]):
                raise ValueError("invalid {} rows".format(key))
        if data.get("binding") is not None and (not isinstance(data["binding"], dict) or data["binding"].get("schema_version") != 1):
            raise ValueError("invalid binding")
        if data["binding"] is None and any(data[key] for key in ("members", "events", "acknowledgements", "holds", "watchers")):
            raise ValueError("unbound owner contains active history")
        if data["binding"] is not None:
            saved_binding = data["binding"]
            who = saved_binding["identity"]
            if (not isinstance(who, dict) or set(who) != {"kind", "value", "cwd", "herdr_env", "pane_id"}
                    or identity(who["value"], who["cwd"], who["herdr_env"], kind=who["kind"], pane_id=who["pane_id"]) != who
                    or saved_binding["state_path"] != str(canonical(state_path))
                    or type(saved_binding["generation"]) is not int or saved_binding["generation"] < 1):
                raise ValueError("invalid bound lead identity")
            timestamp(saved_binding["at"])
        ids = set()
        for row in data["members"]:
            validate_member(row["assignment"])
            if row["id"] != row["assignment"]["id"] or row["id"] in ids or type(row["active"]) is not bool:
                raise ValueError("invalid enrollment identity")
            ids.add(row["id"])
            timestamp(row["at"])
            if not isinstance(row["observed"], dict) or not isinstance(row["refinements"], list):
                raise ValueError("invalid observation cursor")
            for refinement in row["refinements"]:
                if not isinstance(refinement, dict) or refinement.get("schema_version") != 1:
                    raise ValueError("invalid identity refinement")
                timestamp(refinement["at"])
                validate_member({**row["assignment"], **{key: refinement[key] for key in ("pane_id", "native_session")}})
            if row["active"] != (row["resolution"] is None):
                raise ValueError("enrollment resolution differs from active flag")
            if row["resolution"] is not None:
                if row["resolution"].get("schema_version") != 1:
                    raise ValueError("invalid resolution schema")
                timestamp(row["resolution"]["at"])
                text(row["resolution"]["outcome"], "resolution outcome")
                validate_receipts(row["resolution"]["evidence"])
        for seq, row in enumerate(data["events"], 1):
            if type(row["seq"]) is not int or row["seq"] != seq or row["id"] != "event-{}".format(seq) or not isinstance(row["data"], dict):
                raise ValueError("invalid event sequence")
            timestamp(row["at"])
            text(row["kind"], "event kind")
            if row["member"] is not None and row["member"] not in ids:
                raise ValueError("event references unknown member")
        acknowledged = set()
        for row in data["acknowledgements"]:
            if row["event"] in acknowledged or row["event"] not in {event["id"] for event in data["events"]}:
                raise ValueError("invalid acknowledgement")
            acknowledged.add(row["event"])
            text(row["outcome"], "acknowledgement outcome")
            if (not isinstance(row["input_digest"], str) or len(row["input_digest"]) != 64
                    or any(char not in "0123456789abcdef" for char in row["input_digest"])):
                raise ValueError("invalid acknowledgement input identity")
            timestamp(row["at"])
            if type(row["pending"]) is not bool or row["pending"] != (row["recheck_at"] is not None):
                raise ValueError("invalid pending recheck")
            if row["recheck_at"] is not None and timestamp(row["recheck_at"]) < timestamp(row["at"]):
                raise ValueError("recheck must follow acknowledgement")
            validate_receipts(row["evidence"])
        for row in data["holds"]:
            if row["kind"] not in ("waiting_for_user", "handoff") or type(row["through"]) is not int or not 0 <= row["through"] <= len(data["events"]):
                raise ValueError("invalid hold")
            validate_receipts(row["evidence"])
            text(row["resume_condition"], "resume condition")
            text(row["id"], "hold id")
            text(row["members"], "hold members digest")
            timestamp(row["at"])
            if row["resumed_at"] is not None:
                timestamp(row["resumed_at"])
            if not isinstance(row["dispositions"], list):
                raise ValueError("invalid hold dispositions")
            for disposition in row["dispositions"]:
                if not isinstance(disposition, dict) or disposition.get("schema_version") != 1 or disposition["member"] not in ids:
                    raise ValueError("invalid hold disposition")
                text(disposition["outcome"], "disposition outcome")
                validate_receipts(disposition["evidence"])
        for row in data["watchers"]:
            if row["status"] not in ("running", "stopped") or not isinstance(row["process"], dict):
                raise ValueError("invalid watcher")
            timestamp(row["heartbeat"])
            timestamp(row["deadline"])
            if type(row["process"]["pid"]) is not int or row["process"]["pid"] <= 0:
                raise ValueError("invalid process")
            text(row["process"]["identity"], "process identity")
        return data
    except (AttributeError, KeyError, TypeError, ValueError, UsageError) as exc:
        raise StateError("Unsupported or corrupt supervision record {} ({}). Preserve it and restore a supported owner-written backup.".format(path, exc), {}) from None


def validate_receipts(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing evidence")
    for row in rows:
        if (not isinstance(row, dict) or row.get("schema_version") != 1 or not isinstance(row.get("path"), str)
                or not Path(row["path"]).is_absolute() or not isinstance(row.get("sha256"), str)
                or len(row["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in row["sha256"]) or type(row.get("size")) is not int or row["size"] < 0):
            raise ValueError("invalid evidence receipt")


def transaction(state_path, mutate):
    path = store_path(state_path)
    with state_lock(path):
        data = load(state_path)
        if data["binding"] is None:
            raise StateError("Supervision owner state is missing or unbound. Run supervision-bind for first use; restore a previously bound owner document before resuming its obligations.", {})
        result = mutate(data)
        save_state(path, data)
        return result


def validate_member(record):
    if not isinstance(record, dict) or set(record) != {"id", "agent", "task", "report", "pane_id", "native_session"}:
        raise UsageError("Enrollment requires id, agent, task, absolute report, pane_id and native_session (nullable identity fields).", {})
    for key in ("id", "agent", "task", "report"):
        text(record[key], key)
    if not Path(record["report"]).is_absolute():
        raise UsageError("Enrollment report must be the absolute path in the worker brief.", {})
    if record["pane_id"] is not None:
        text(record["pane_id"], "pane_id")
    native = record["native_session"]
    if native is not None:
        if not isinstance(native, dict) or not {"source", "agent", "kind", "value"}.issubset(native) or native["kind"] not in ("id", "path"):
            raise UsageError("native_session must preserve a native identity object (source,agent,kind,value; optional pane_id) or null.", {})
        for key in ("source", "agent", "value"):
            text(native[key], "native " + key)


def enroll(state_path, record, at):
    validate_member(record)
    timestamp(at)
    def mutate(data):
        if data["binding"] is None:
            raise UsageError("Bind this lead's native session with supervision-bind before enrolling worker assignments.", {})
        previous = next((row for row in data["members"] if row["id"] == record["id"]), None)
        if previous:
            if previous["assignment"] != record:
                raise UsageError("Enrollment ID already names different evidence; reconcile the original assignment before assigning a new ID.", {})
            return previous
        if any(row["active"] and row["assignment"]["agent"] == record["agent"] for row in data["members"]):
            raise UsageError("This worker still has an active enrollment. Reconcile its outcome with supervision-resolve before replacing it.", {})
        row = {"schema_version": 1, "id": record["id"], "at": at, "assignment": record,
               "active": True, "observed": {}, "resolution": None, "refinements": []}
        data["members"].append(row)
        return row
    return transaction(state_path, mutate)


def append_event(data, at, member, kind, detail):
    seq = len(data["events"]) + 1
    row = {"schema_version": 1, "id": "event-{}".format(seq), "seq": seq, "at": at,
           "member": member, "kind": kind, "data": detail}
    data["events"].append(row)
    return row


def pending(data):
    done = {row["event"] for row in data["acknowledgements"]}
    return [row for row in data["events"] if row["id"] not in done]


def drain(state_path, through=None):
    data = load(state_path)
    high = len(data["events"]) if through is None else through
    if type(high) is not int or not 0 <= high <= len(data["events"]):
        raise UsageError("Drain through must identify an existing event sequence, or omit it for the current snapshot.", {})
    return {"schema_version": 1, "state_path": data["state_path"], "through": high,
            "events": [row for row in pending(data) if row["seq"] <= high]}


def acknowledge(state_path, record, at):
    timestamp(at)
    if not isinstance(record, dict) or set(record) != {"through", "outcomes"} or not isinstance(record["outcomes"], list) or not record["outcomes"]:
        raise UsageError("Acknowledgement requires the drained through sequence and nonempty outcomes [{event,outcome,evidence}].", {})
    prepared = []
    for row in record["outcomes"]:
        if not isinstance(row, dict) or not {"event", "outcome", "evidence"}.issubset(row) or set(row) - {"event", "outcome", "evidence", "pending", "recheck_at"}:
            raise UsageError("Each acknowledgement requires event, outcome, and evidence paths.", {})
        prepared.append({"schema_version": 1, "at": at, "event": text(row["event"], "event"),
                         "input_digest": digest(row), "outcome": text(row["outcome"], "outcome"),
                         "evidence": row["evidence"], "recheck_at": row.get("recheck_at"), "pending": row.get("pending", row.get("recheck_at") is not None)})
        if type(prepared[-1]["pending"]) is not bool or row.get("recheck_at") is not None and not prepared[-1]["pending"]:
            raise UsageError("pending must be a boolean; a scheduled recheck cannot declare pending false.", {})
    if len({row["event"] for row in prepared}) != len(prepared):
        raise UsageError("Acknowledge each event only once in a request.", {})
    def mutate(data):
        high = record["through"]
        if type(high) is not int or not 0 <= high <= len(data["events"]):
            raise UsageError("Use the through sequence returned by supervision-drain.", {})
        for row in prepared:
            event = next((item for item in data["events"] if item["id"] == row["event"]), None)
            if event is None or event["seq"] > high:
                raise UsageError("Acknowledgement includes an event outside its drained snapshot; drain again before handling later events.", {})
            prior = next((item for item in data["acknowledgements"] if item["event"] == row["event"]), None)
            if prior:
                if prior["input_digest"] != row["input_digest"]:
                    raise UsageError("Event already has a different handled outcome; preserve its acknowledgement.", {})
            else:
                if row["recheck_at"] is not None and timestamp(row["recheck_at"]) < timestamp(at):
                    raise UsageError("A pending observation recheck_at must not precede its acknowledgement time.", {})
                row["evidence"] = evidence(row["evidence"])
                if row["pending"] and row["recheck_at"] is None:
                    row["recheck_at"] = (timestamp(at) + timedelta(seconds=DEFAULT_RECHECK_SECONDS)).isoformat()
                data["acknowledgements"].append(row)
        return {"schema_version": 1, "acknowledged": [row["event"] for row in prepared], "remaining": pending(data)}
    return transaction(state_path, mutate)


def resolve(state_path, record, at):
    if not isinstance(record, dict) or set(record) != {"id", "outcome", "evidence"}:
        raise UsageError("Resolution requires enrollment id, lead outcome, and saved evidence paths.", {})
    result = {"schema_version": 1, "at": at, "outcome": text(record["outcome"], "outcome"), "evidence": evidence(record["evidence"])}
    timestamp(at)
    def mutate(data):
        member = next((row for row in data["members"] if row["id"] == record["id"]), None)
        if member is None:
            raise UsageError("Unknown enrollment ID; use supervision-status to inspect active assignments.", {})
        if any(row["member"] == member["id"] for row in pending(data)):
            raise UsageError("Handle and acknowledge this assignment's pending observations before resolving its enrollment.", {})
        if not member["active"]:
            if any(member["resolution"][key] != result[key] for key in ("outcome", "evidence")):
                raise UsageError("Enrollment already has a different resolution; preserve it.", {})
        else:
            member.update(active=False, resolution=result)
        return member
    return transaction(state_path, mutate)


def active_digest(data):
    return digest([expected_assignment(row) for row in data["members"] if row["active"]])


def refine(state_path, member_id, pane_id, native_session, at):
    """Fill missing post-send identity without rewriting the enrolled assignment."""
    timestamp(at)
    def mutate(data):
        row = next((item for item in data["members"] if item["id"] == member_id), None)
        if row is None or not row["active"]:
            raise UsageError("Refine only an active enrollment; reconcile its saved identity before replacing it.", {})
        current = expected_assignment(row)
        updates = {"pane_id": pane_id, "native_session": native_session}
        validate_member({**current, **updates})
        for key, value in updates.items():
            if current[key] is not None and value != current[key]:
                raise UsageError("Refinement cannot replace a known worker identity; reconcile the changed session explicitly.", {})
        if any(current[key] != value for key, value in updates.items()):
            row["refinements"].append({"schema_version": 1, "at": at, **updates})
        return row
    return transaction(state_path, mutate)


def expected_assignment(member):
    assignment = dict(member["assignment"])
    for row in member["refinements"]:
        assignment.update({key: row[key] for key in ("pane_id", "native_session")})
    return assignment


def hold(state_path, record, at):
    if (not isinstance(record, dict) or set(record) != {"id", "kind", "resume_condition", "evidence", "dispositions"}
            or record["kind"] not in ("waiting_for_user", "handoff")):
        raise UsageError("Hold requires id, kind (waiting_for_user or handoff), resume_condition, saved user-decision/handoff evidence paths, and dispositions [{member,outcome,evidence}] for every active assignment.", {})
    row = {"schema_version": 1, "id": text(record["id"], "hold id"), "at": at,
           "kind": record["kind"], "resume_condition": text(record["resume_condition"], "resume condition"),
           "evidence": evidence(record["evidence"]), "resumed_at": None}
    dispositions = record["dispositions"]
    if not isinstance(dispositions, list):
        raise UsageError("Hold dispositions must explicitly cover each active assignment.", {})
    prepared = []
    for item in dispositions:
        if not isinstance(item, dict) or set(item) != {"member", "outcome", "evidence"}:
            raise UsageError("Each hold disposition requires member, outcome, and saved evidence paths.", {})
        prepared.append({"schema_version": 1, "member": text(item["member"], "member"),
                         "outcome": text(item["outcome"], "disposition outcome"), "evidence": evidence(item["evidence"])})
    row["dispositions"] = prepared
    timestamp(at)
    def mutate(data):
        if len({item["member"] for item in prepared}) != len(prepared) or {item["member"] for item in prepared} != {item["id"] for item in data["members"] if item["active"]}:
            raise UsageError("Record a named user-held or handed-off disposition for every active assignment; a global hold cannot hide unrelated work.", {})
        if pending(data):
            raise UsageError("Handle pending fleet events before recording a user-held pause or handoff.", {})
        row.update(through=len(data["events"]), members=active_digest(data))
        previous = next((item for item in data["holds"] if item["id"] == row["id"]), None)
        if previous:
            if any(previous[key] != row[key] for key in ("kind", "resume_condition", "evidence", "dispositions", "through", "members")):
                raise UsageError("Hold ID already records a different boundary; use a new ID after reconciling it.", {})
            return previous
        data["holds"].append(row)
        return row
    return transaction(state_path, mutate)


def resume(state_path, at):
    timestamp(at)
    def mutate(data):
        for row in data["holds"]:
            if row["resumed_at"] is None:
                row["resumed_at"] = at
        return {"schema_version": 1, "resumed": True, "pending": pending(data)}
    return transaction(state_path, mutate)


def held(data):
    return any(row["resumed_at"] is None and row["through"] == len(data["events"])
               and row["members"] == active_digest(data) for row in data["holds"])


def identity(value, cwd, environment, *, kind="id", pane_id):
    if kind not in ("id", "path"):
        raise UsageError("Native binding kind must be id or path.", {})
    value = text(value, "native lead identity")
    if kind == "path":
        if not Path(value).is_absolute():
            raise UsageError("Native transcript binding must use an absolute path.", {})
        value = str(canonical(value))
    return {"kind": kind, "value": value, "cwd": str(canonical(cwd)),
            "herdr_env": text(environment, "HERDR_ENV"), "pane_id": text(pane_id, "HERDR_PANE_ID")}


def binding_path(who, root=None):
    directory = canonical(root) if root is not None else default_state_path().parent / "supervision-bindings"
    return directory / (digest(who) + ".json")


def dispatch_binding(state_path, *, root=None):
    """Read-only dispatch guard: missing historical owner is never legacy mode.

    Scan all saved lead identities, not only the caller's current identity: a
    context/model change cannot make its earlier obligations disappear. An
    existing empty owner marks an interrupted first bind and is not dispatchable.
    Only a missing owner with no prior discovery may use legacy compatibility.
    """
    data = load(state_path)
    if data["binding"] is not None:
        return data["binding"]
    if store_path(state_path).exists():
        raise StateError("Supervision owner initialization is incomplete. Finish supervision-bind for this state before dispatching worker input.", {})
    directory = canonical(root) if root is not None else default_state_path().parent / "supervision-bindings"
    _refuse_lost_owner(state_path, directory)
    return None


def _refuse_lost_owner(state_path, directory):
    """Discovery proves prior ownership even after a lead identity changes."""
    try:
        paths = [path for path in directory.iterdir() if path.suffix == ".json"]
    except FileNotFoundError:
        paths = []
    except OSError as exc:
        raise StateError("Cannot inspect supervision discovery records: {}. Restore directory access before initializing an owner document.".format(exc), {}) from None
    for path in paths:
        prior = read_json(path)
        if isinstance(prior, dict) and prior.get("state_path") == str(canonical(state_path)):
            raise StateError("A saved lead discovery record already names the missing supervision owner {}. Restore its owner document before rebinding; do not discard unresolved assignments.".format(store_path(state_path)), {})


def bind(state_path, who, at, *, root=None):
    if not isinstance(who, dict) or set(who) != {"kind", "value", "cwd", "herdr_env", "pane_id"}:
        raise UsageError("Binding requires the lead's exact native kind/value, cwd, HERDR_ENV, and pane_id.", {})
    who = identity(who["value"], who["cwd"], who["herdr_env"], kind=who["kind"], pane_id=who["pane_id"])
    timestamp(at)
    path = binding_path(who, root)
    # An empty unbound first-use owner exists before discovery. No transaction
    # can enroll work until binding commits, so this state is safe to retry.
    # Discovery precedes bound owner. A higher-generation discovery
    # record makes an interrupted handoff block the new lead, while older
    # sessions can stop once the owner has committed a newer binding.
    with state_lock(store_path(state_path)):
        data = load(state_path)
        old = data["binding"]
        first_use = old is None and not store_path(state_path).exists()
        if first_use:
            _refuse_lost_owner(state_path, path.parent)
        row = old if old is not None and old["identity"] == who else {
            "schema_version": 1, "at": at, "identity": who, "state_path": str(canonical(state_path)),
            "generation": 1 if old is None else old["generation"] + 1}
        with state_lock(path):
            previous = read_json(path)
            if previous is not None and (not isinstance(previous, dict) or previous.get("schema_version") != 1
                    or previous.get("identity") != who or previous.get("state_path") != row["state_path"]):
                raise StateError("Lead identity already has a conflicting or unreadable state binding. Preserve it and explicitly reconcile the original state path.", {})
            if first_use:
                save_state(store_path(state_path), data)
            save_state(path, row)
            data["binding"] = row
            save_state(store_path(state_path), data)
    return row
