"""Durable, lead-authored retrospective notes, separate from dispatch state.

The index is the commit point: immutable note bytes are installed before its
atomic replacement. A pending transaction allows an identical interrupted
record to finish without treating an orphan note as completed coverage.
Readers never create files or migrate dispatch state. All times are injected.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import timedelta, timezone
from pathlib import Path
from typing import NoReturn

from .chronology import timestamp
from .errors import StateError, UsageError
from .state import save_state, state_lock

SCHEMA_VERSION = 1
INTERVAL = timedelta(hours=24)
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
TRIGGERS = frozenset({"daily", "transition"})
SECTIONS = ("Outcomes", "Quality", "Coordination", "Seats and models", "Improvements")


def canonical_state(path):
    return Path(path).expanduser().resolve()


def directory(path):
    return Path(str(canonical_state(path)) + ".retrospectives")


def lock(path):
    """Acquire after the dispatch-state lock; offline readers take neither."""
    return state_lock(directory(path) / "index.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc(value):
    return timestamp(value, "Retrospective time").astimezone(timezone.utc).isoformat()


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise UsageError("{} must be a non-empty string; supply the actual retrospective metadata.".format(label), {})
    return value


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise UsageError("Retrospective id must use 1-64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit.", {})
    return value


def file_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise StateError("Cannot read retrospective evidence {}: {}. Restore the file or explicitly record the evidence as unavailable.".format(path, exc), {}) from None


def receipt(path):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise UsageError("Retrospective evidence paths must be absolute; supply the original file path.", {})
    resolved = str(Path(path).resolve())
    data = file_bytes(resolved)
    return {"path": resolved, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def current_receipt(record):
    validate_receipt(record)
    return receipt(record["path"]) == record


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError("Cannot read retrospective JSON {}: {}. Preserve its bytes and restore a valid backup before recording or dispatching.".format(path, exc), {}) from None


def empty(path):
    return {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)),
            "baseline_at": None, "records": [], "transitions": []}


def _malformed(label) -> NoReturn:
    raise StateError("Malformed retrospective {}; preserve its bytes and restore the original artifact.".format(label), {})


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _version(value):
    return type(value) is int and value == SCHEMA_VERSION


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _names(value):
    return isinstance(value, list) and all(_nonempty(item) for item in value) and len(set(value)) == len(value)


def _unavailable(value):
    return isinstance(value, dict) and all(_nonempty(key) and _nonempty(reason) for key, reason in value.items())


def _saved_time(value):
    try:
        return timestamp(utc(value), "Saved retrospective time")
    except UsageError:
        _malformed("timestamp")


def validate_receipt(value):
    if (not isinstance(value, dict) or set(value) != {"path", "sha256", "size"}
            or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or not _hash(value["sha256"])
            or type(value["size"]) is not int or value["size"] < 0):
        _malformed("file receipt")


def validate_coverage(value):
    if not isinstance(value, list):
        _malformed("coverage")
    agents = set()
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"schema_version", "agent", "source", "target", "first_start", "transition_required"}
                or not _version(row.get("schema_version")) or not _nonempty(row.get("agent"))
                or type(row.get("first_start")) is not bool or type(row.get("transition_required")) is not bool
                or not isinstance(row.get("source"), dict) or not isinstance(row.get("target"), dict)):
            _malformed("coverage descriptor")
        if row["agent"] in agents:
            _malformed("duplicate worker coverage")
        agents.add(row["agent"])
        source, target = row["source"], row["target"]
        if set(source) != {"assignment_index", "assignment_digest", "dispatch_id", "dispatch_evidence", "task", "role", "tier", "observation", "report", "unavailable"}:
            _malformed("coverage source")
        validate_observation(source["observation"])
        offset = source["assignment_index"]
        if ((offset is None and source["assignment_digest"] is not None)
                or (offset is not None and (type(offset) is not int or offset < 0 or not _hash(source["assignment_digest"])))
                or any(source[key] is not None and not _nonempty(source[key]) for key in ("dispatch_id", "task", "role", "unavailable"))
                or (source["tier"] is not None and not isinstance(source["tier"], dict))):
            _malformed("coverage assignment identity")
        if source["report"] is not None:
            validate_receipt(source["report"])
        evidence = source["dispatch_evidence"]
        if evidence is not None:
            if (not isinstance(evidence, dict) or set(evidence) != {"sha256", "report"}
                    or not _hash(evidence["sha256"]) or source["dispatch_id"] is None):
                _malformed("dispatch evidence")
            if evidence["report"] is not None:
                validate_receipt(evidence["report"])
        if (set(target) != {"role", "model", "effort", "context", "task", "brief", "common"}
                or not _nonempty(target["role"]) or not isinstance(target["context"], str)
                or target["context"] not in {"start", "clear", "retain"}
                or any(target[key] is not None and not _nonempty(target[key]) for key in ("model", "effort", "task"))
                or (target["brief"] is None) != (target["common"] is None)):
            _malformed("coverage target")
        for key in ("brief", "common"):
            if target[key] is not None:
                validate_receipt(target[key])
        if row["first_start"] and (row["transition_required"] or target["context"] != "start"
                or not source["observation"]["shell"]
                or any(source[key] is not None for key in ("assignment_index", "assignment_digest", "dispatch_id", "dispatch_evidence", "task", "role", "tier", "report"))):
            _malformed("first-start proof")


def validate_observation(value):
    if (not isinstance(value, dict) or set(value) != {"pane_id", "native", "process", "readiness", "shell"}
            or not _nonempty(value["pane_id"]) or not isinstance(value["process"], dict)
            or set(value["process"]) != {"pid", "argv"}
            or type(value["process"]["pid"]) is not int or value["process"]["pid"] <= 0
            or not isinstance(value["process"]["argv"], list)
            or not value["process"]["argv"] or any(not isinstance(arg, str) for arg in value["process"]["argv"])
            or not _nonempty(value["readiness"]) or type(value["shell"]) is not bool
            or (value["native"] is not None and not isinstance(value["native"], dict))):
        _malformed("worker observation")
    if value["shell"] and (value["readiness"] != "shell" or value["native"] is not None):
        _malformed("shell observation")


def validate_record(row):
    required = {"schema_version", "id", "completed_at", "period_start", "period_end", "triggers", "tasks", "participants", "unavailable", "sources", "note", "coverage", "input_digest"}
    if not isinstance(row, dict) or set(row) != required or not _version(row["schema_version"]):
        _malformed("record schema")
    if not isinstance(row["id"], str) or not IDENTIFIER.fullmatch(row["id"]):
        _malformed("record identifier")
    completed, start, end = [_saved_time(row[key]) for key in ("completed_at", "period_start", "period_end")]
    if not start <= end <= completed:
        _malformed("record period")
    for key in ("triggers", "tasks", "participants"):
        if not _names(row[key]):
            _malformed("record " + key)
    if (not row["triggers"] or not set(row["triggers"]) <= TRIGGERS or not _unavailable(row["unavailable"])
            or set(row["participants"]) & set(row["unavailable"])):
        _malformed("record metadata")
    if not isinstance(row["sources"], list):
        _malformed("record sources")
    for source in row["sources"]:
        validate_receipt(source)
    validate_receipt(row["note"])
    validate_coverage(row["coverage"])
    if not row["sources"] and not row["coverage"]:
        _malformed("record evidence")
    if not _hash(row["input_digest"]):
        _malformed("record identity")


def _metadata(row):
    return {key: row[key] for key in ("schema_version", "id", "completed_at", "period_start", "period_end", "triggers", "tasks", "participants", "unavailable", "sources", "coverage")}


def _read_note(row):
    data = file_bytes(row["note"]["path"])
    if len(data) != row["note"]["size"] or hashlib.sha256(data).hexdigest() != row["note"]["sha256"]:
        raise StateError("Saved retrospective note {} changed; restore its immutable bytes before using its coverage.".format(row["id"]), {})
    try:
        markdown = data.decode("utf-8")
        header, _body = markdown.removeprefix("---\n").split("\n---\n", 1)
        if not markdown.startswith("---\n") or json.loads(header) != _metadata(row):
            _malformed("note metadata")
    except (UnicodeDecodeError, ValueError):
        _malformed("note metadata")
    return markdown


def load(path, *, allow_pending=False):
    root = directory(path)
    index = root / "index.json"
    if not index.exists():
        if root.exists():
            try:
                leftovers = [item.name for item in root.iterdir() if item.name != "index.json.lock"]
            except OSError as exc:
                raise StateError("Cannot inspect retrospective directory {}: {}. Restore access without removing saved artifacts.".format(root, exc), {}) from None
            if leftovers and not (allow_pending and (root / "pending.json").is_file()):
                raise StateError("Retrospective index is missing beside saved artifacts; preserve them and resume its pending transaction or restore the index backup.", {})
        return empty(path)
    result = _json(index)
    if (not isinstance(result, dict) or set(result) != {"schema_version", "state_path", "baseline_at", "records", "transitions"}
            or not _version(result.get("schema_version"))
            or result.get("state_path") != str(canonical_state(path))
            or not isinstance(result.get("records"), list) or not isinstance(result.get("transitions"), list)):
        raise StateError("Retrospective index has an unsupported schema or state identity; preserve its bytes and update the owner or restore its backup.", {})
    if result.get("baseline_at") is not None:
        _saved_time(result["baseline_at"])
    ids = set()
    for record in result["records"]:
        validate_record(record)
        name = identifier(record.get("id"))
        if name in ids:
            raise StateError("Retrospective ids are duplicated; restore the original index before writing.", {})
        ids.add(name)
        if record["note"]["path"] != str(root / (name + ".md")):
            _malformed("note location")
        _read_note(record)
    transition_ids = set()
    for row in result["transitions"]:
        if (not isinstance(row, dict) or set(row) != {"schema_version", "id", "at", "agent", "descriptor", "incoming"}
                or not _version(row["schema_version"])):
            _malformed("transition schema")
        _saved_time(row["at"])
        validate_coverage([row["descriptor"]])
        validate_observation(row["incoming"])
        descriptor = row["descriptor"]
        if row["agent"] != descriptor["agent"] or row["id"] != digest({key: value for key, value in row.items() if key not in {"at", "id"}}):
            _malformed("transition identity")
        if row["id"] in transition_ids:
            _malformed("duplicate transition")
        transition_ids.add(row["id"])
        if not descriptor["first_start"] and not any(descriptor in record["coverage"] for record in result["records"]):
            _malformed("transition coverage provenance")
    return result


def require_no_pending(path):
    """Refuse sidecar changes until the recording owner reconciles its journal."""
    if (directory(path) / "pending.json").exists():
        raise StateError("A retrospective recording transaction is pending. Retry its original retro-record command to reconcile it before dispatching or changing retrospective state.", {})


def cadence(index, at, *, existing_work=False):
    now = timestamp(utc(at), "Retrospective checkpoint")
    dates = [timestamp(row["completed_at"], "Completed retrospective") for row in index["records"]]
    if any(value > now for value in dates):
        raise UsageError("Retrospective checkpoint precedes a saved completion; use the current UTC checkpoint without rewriting history.", {})
    last = max(dates) if dates else None
    baseline = timestamp(index["baseline_at"], "Retrospective baseline") if index.get("baseline_at") else None
    if baseline is not None and baseline > now:
        raise UsageError("Retrospective checkpoint precedes the saved baseline; use a current UTC checkpoint.", {})
    reference = last or baseline
    due = (now >= reference + INTERVAL) if reference else existing_work
    return {"due": due, "last_completed_at": last.isoformat() if last else None,
            "next_due_at": (reference + INTERVAL).isoformat() if reference else None,
            "reason": "24_hours" if due and reference else "existing_work_without_history" if due else "not_due"}


def establish_baseline(path, index, at):
    if index["baseline_at"] is None and not index["records"]:
        require_no_pending(path)
        index["baseline_at"] = utc(at)
        save_state(directory(path) / "index.json", index)


def _install_note(path, data):
    if path.exists():
        if file_bytes(path) != data:
            raise StateError("Retrospective note id already has different bytes; preserve it and choose a new id.", {})
        return
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="note-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if file_bytes(path) != data:
                raise StateError("Retrospective note id was claimed with different bytes; preserve it and use a new id.", {}) from None
    except OSError as exc:
        raise StateError("Cannot install retrospective note {}: {}. Restore directory access and retry the same record.".format(path, exc), {}) from None
    finally:
        if temporary is not None:
            _unlink(Path(temporary))


def _unlink(path):
    try:
        path.unlink()
    except OSError as exc:
        raise StateError("Cannot remove retrospective transaction file {}: {}. Restore directory access and retry the same record to reconcile it.".format(path, exc), {}) from None


def _pending(root, index):
    path = root / "pending.json"
    if not path.exists():
        return None
    pending = _json(path)
    if (not isinstance(pending, dict) or set(pending) != {"schema_version", "previous_index", "record"}
            or not _version(pending["schema_version"]) or not _hash(pending["previous_index"])):
        _malformed("pending transaction")
    validate_record(pending["record"])
    row = pending["record"]
    if row["note"]["path"] != str(root / (row["id"] + ".md")):
        _malformed("pending note location")
    committed = next((entry for entry in index["records"] if entry["id"] == row["id"]), None)
    if committed:
        if committed != row:
            _malformed("conflicting committed transaction")
        previous = {**index, "records": [entry for entry in index["records"] if entry["id"] != row["id"]]}
        if pending["previous_index"] != digest(previous):
            _malformed("committed transaction ancestry")
    elif pending["previous_index"] != digest(index):
        _malformed("pending transaction ancestry")
    return pending


def record(path, data, coverage, at):
    """Commit validated coverage and Markdown; the caller holds lock(path)."""
    required = {"id", "note", "period_start", "period_end", "triggers", "tasks", "participants", "unavailable", "sources", "completed", "check"}
    if not isinstance(data, dict) or set(data) != required or data.get("completed") is not True:
        raise UsageError("Retrospective record requires id, note, period_start/end, triggers, tasks, participants, unavailable, sources, completed: true, and check.", {})
    name = identifier(data["id"])
    at = utc(at)
    start, end = utc(data["period_start"]), utc(data["period_end"])
    if not timestamp(start, "Period start") <= timestamp(end, "Period end") <= timestamp(at, "Completion"):
        raise UsageError("Retrospective period must end no later than completion and begin no later than its end.", {})
    for key in ("tasks", "participants"):
        if not _names(data[key]):
            raise UsageError("Retrospective {} must be a list of distinct non-empty names.".format(key), {})
    if (not _names(data["triggers"]) or not data["triggers"]
            or not set(data["triggers"]) <= TRIGGERS):
        raise UsageError("Retrospective triggers must name daily and/or transition.", {})
    if not _unavailable(data["unavailable"]):
        raise UsageError("Retrospective unavailable must map worker names to explicit reasons.", {})
    if set(data["participants"]) & set(data["unavailable"]):
        raise UsageError("A retrospective worker cannot both participate and be unavailable; record what actually happened.", {})
    if not isinstance(data["sources"], list):
        raise UsageError("Retrospective sources must list absolute evidence-file paths.", {})
    for key in ("note", "check"):
        if not isinstance(data[key], str) or not Path(data[key]).is_absolute():
            raise UsageError("Retrospective {} must be an absolute file path.".format(key), {})
    note_data = file_bytes(data["note"])
    try:
        note_text = note_data.decode("utf-8")
    except UnicodeDecodeError:
        raise UsageError("Retrospective note must be UTF-8 Markdown; save the lead's substantive synthesis and retry.", {}) from None
    for section in SECTIONS:
        match = re.search(r"^#{1,6} " + re.escape(section) + r"[ \t]*\r?$\n(.*?)(?=^#{1,6} |\Z)", note_text, re.M | re.S)
        body = re.sub(r"<!--.*?-->", "", match[1], flags=re.S).strip() if match else ""
        if not body or body.lower() in {"todo", "tbd", "n/a", "..."}:
            raise UsageError("Retrospective note needs a nonempty {} section with the lead's synthesis; headings or template placeholders do not complete it.".format(section), {})
    validate_coverage(coverage)
    sources = [receipt(source) for source in data["sources"]]
    if not sources and not coverage:
        raise UsageError("A quiet retrospective needs an actual ledger or prior note source; provide evidence for the interval.", {})
    root = directory(path)
    index = load(path, allow_pending=True)
    cadence(index, at)
    pending = _pending(root, index)
    prior = next((row for row in index["records"] if row["id"] == name), None)
    original = prior or (pending["record"] if pending and pending["record"]["id"] == name else None)
    completed_at = original["completed_at"] if original else at
    if timestamp(completed_at, "Original completion") > timestamp(at, "Record retry"):
        raise UsageError("Retrospective retry precedes its original completion; use the current UTC checkpoint.", {})
    metadata = {"schema_version": SCHEMA_VERSION, "id": name, "completed_at": completed_at,
                "period_start": start, "period_end": end, "triggers": data["triggers"], "tasks": data["tasks"],
                "participants": data["participants"], "unavailable": data["unavailable"], "sources": sources, "coverage": coverage}
    note_data = ("---\n" + json.dumps(metadata, indent=2, sort_keys=True) + "\n---\n\n" + note_text).encode("utf-8")
    note = {"path": str(root / (name + ".md")), "sha256": hashlib.sha256(note_data).hexdigest(), "size": len(note_data)}
    item = {**metadata, "note": note, "input_digest": digest(data)}
    validate_record(item)
    pending_path = root / "pending.json"
    if pending and pending["record"] in index["records"]:
        _unlink(pending_path)
        pending = None
    if prior:
        if prior != item:
            raise UsageError("Retrospective id already records different metadata; preserve it and use a new id.", {})
        return {**prior, "replayed": True}
    transaction = {"schema_version": SCHEMA_VERSION, "previous_index": digest(index), "record": item}
    if pending:
        if pending != transaction:
            raise StateError("A different retrospective transaction is pending; resume its original record before starting another.", {})
    else:
        installed = root / (name + ".md")
        if installed.exists() and file_bytes(installed) != note_data:
            raise StateError("Retrospective note id already has different bytes; preserve it and choose a new id.", {})
        save_state(pending_path, transaction)
    _install_note(root / (name + ".md"), note_data)
    index["records"].append(item)
    save_state(root / "index.json", index)
    _unlink(pending_path)
    return {**item, "replayed": False}


def list_notes(path, *, task=None, since=None):
    index = load(path)
    cutoff = timestamp(utc(since), "Retrospective since") if since else None
    rows = [row for row in index["records"] if (task is None or task in row["tasks"])
            and (cutoff is None or timestamp(row["completed_at"], "Retrospective completion") >= cutoff)]
    return {"schema_version": SCHEMA_VERSION, "state_path": str(canonical_state(path)), "records": rows}


def show(path, name="latest", *, task=None):
    records = list_notes(path, task=task)["records"]
    record = (max(records, key=lambda row: timestamp(row["completed_at"], "Retrospective completion")) if records else None) if name == "latest" else next((row for row in records if row["id"] == name), None)
    if record is None:
        raise UsageError("No saved retrospective matches; use retro-list with the same --state path to inspect available notes.", {})
    return {"schema_version": SCHEMA_VERSION, "record": record, "markdown": _read_note(record)}
