"""Append evidence for completed legacy work without replaying its transport.

The owner checks the meaning of archived authorization and transport evidence.
This module binds those attestations to readable bytes, recorded identities,
bounded counts, and local Git objects. It never writes to a worker or repository.
Live session observations remain separate from contemporaneous evidence.
"""

import subprocess
from pathlib import Path
from fnmatch import fnmatchcase

from .errors import UsageError
from .chronology import latest_assignment, timestamp
from . import recovery as ledger


def fields(data, required, label):
    if not isinstance(data, dict) or set(data) != set(required.split()):
        raise UsageError("{} requires exactly: {}. See dispatch-recovery.md; do not infer missing proof.".format(label, required), {})


def prior_developer(assignments, task, stop=None):
    """Use the then-recorded prefix only when validating an import receipt."""
    latest = latest_assignment(assignments[:stop], task=task, role="developer", status="applied")
    return latest[0] if latest is not None else None


def replay(store, collection, data, receipts):
    prior = next((row for row in store[collection] if row["id"] == data["id"]), None)
    if prior:
        if prior["input"] != data or prior["receipts"] != receipts:
            raise UsageError("Historical identity already names different input or evidence bytes; preserve it and resolve the conflict.", {})
        return prior
    return None


def archive_receipts(value, label):
    """Bind split legacy archives without requiring a newly synthesized file."""
    sources = [value] if isinstance(value, str) else value
    if not isinstance(sources, list) or not sources or any(not isinstance(path, str) for path in sources) or len(set(sources)) != len(sources):
        raise UsageError("{} must name an original artifact or a non-empty list of distinct original artifact paths.".format(label), {})
    receipts, bodies = [], []
    for path in sources:
        receipt, body = ledger.receipt(path)
        receipts.append(receipt)
        bodies.append(body)
    return receipts, bodies


def completed_evidence(receipts):
    """Renaming or reordering the same archived bytes cannot create an attempt."""
    return (receipts["report"]["sha256"], tuple(sorted({row["sha256"] for row in receipts["transport_evidence"]})))


def _git(checkout, *args):
    try:
        result = subprocess.run(["git", "-C", checkout, *args], capture_output=True, check=False)
    except OSError as exc:
        raise UsageError("Cannot inspect historical Git evidence: {}. Restore Git and the checkout; no history was imported.".format(exc), {}) from None
    if result.returncode:
        raise UsageError("Historical Git verification failed ({}): {}. Restore the original objects and checkout before importing.".format(
            args[0], result.stderr.decode("utf-8", errors="replace").strip()), {})
    return result.stdout


def _git_evidence(data, task):
    checkout = data["checkout"]
    if not isinstance(checkout, str) or not Path(checkout).is_absolute() or not Path(checkout).is_dir():
        raise UsageError("Historical checkout must be an existing absolute local repository path.", {})
    for name in ("base_revision", "previous_head", "head_revision"):
        ledger.revision(data[name], name)
        _git(checkout, "cat-file", "-e", data[name] + "^{commit}")
    _git(checkout, "merge-base", "--is-ancestor", data["base_revision"], data["previous_head"])
    _git(checkout, "merge-base", "--is-ancestor", data["previous_head"], data["head_revision"])
    _git(checkout, "merge-base", "--is-ancestor", data["head_revision"], "HEAD")
    try:
        changed = [path.decode("utf-8") for path in _git(checkout, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z",
                   data["previous_head"], data["head_revision"], "--").split(b"\0") if path]
    except UnicodeDecodeError:
        raise UsageError("Historical diff contains a non-UTF-8 path; resolve its scope explicitly before importing.", {}) from None
    for path in changed:
        if not all(any(fnmatchcase(path, pattern) for pattern in bounds)
                   for bounds in (task["allowed_paths"], data["allowed_paths"])):
            raise UsageError("Historical diff path {!r} exceeds the recorded task or correction scope; do not import it as authorized.".format(path), {})
    return {"checkout": str(Path(checkout).resolve()), "previous_head": data["previous_head"],
            "head_revision": data["head_revision"], "changed_paths": changed}


def _attempt_input(data, task):
    fields(data, "id task agent base_revision scope allowed_paths fix_round authorization authorized_first_fix authorized_last_fix occurred_at checkout previous_head head_revision authorization_evidence transport_evidence report transport", "Historical correction")
    for key in ("id", "task", "agent", "scope"):
        ledger.text(data[key], key)
    for key in ("base_revision", "previous_head", "head_revision"):
        ledger.revision(data[key], key)
    if data["base_revision"] != task["base_revision"]:
        raise UsageError("Historical correction must retain the registered original task base.", {})
    ledger.paths(data["allowed_paths"], "historical correction paths")
    ledger.authorization(data["authorization"])
    for key in ("fix_round", "authorized_first_fix", "authorized_last_fix"):
        ledger.positive(data[key], key)
    if not data["authorized_first_fix"] <= data["fix_round"] <= data["authorized_last_fix"]:
        raise UsageError("The completed attempt is outside its original explicit allowance; import cannot grant a new allowance.", {})
    fields(data["transport"], "sent started clear sent_quote started_quote clear_quote", "Historical transport attestation")
    if data["transport"]["sent"] is not True or data["transport"]["started"] is not True or data["transport"]["clear"] not in ("fresh", "retained"):
        raise UsageError("Import requires verified completed transport with its actual fresh/retained context; uncertain outcomes need evidence first.", {})
    for key in ("sent_quote", "started_quote", "clear_quote"):
        ledger.text(data["transport"][key], key)


def import_attempt(store, assignments, data, at):
    """Return an import receipt; the CLI appends its one assignment atomically."""
    if not isinstance(data, dict):
        raise UsageError("Historical correction must be an object; follow dispatch-recovery.md.", {})
    task = ledger.task_record(store, ledger.text(data.get("task"), "task"))
    _attempt_input(data, task)
    if timestamp(data["occurred_at"], "occurred_at") > timestamp(at, "import time"):
        raise UsageError("A historical correction cannot occur after its import time.", {})
    receipts, bodies = {}, {}
    for key in ("authorization_evidence", "transport_evidence"):
        receipts[key], bodies[key] = archive_receipts(data[key], key)
    receipts["report"], bodies["report"] = ledger.receipt(data["report"])
    if any(not any(value in body for body in bodies["authorization_evidence"])
           for value in (data["authorization"]["quote"], data["task"], data["scope"])):
        raise UsageError("Authorization evidence must contain the actual operator quote, task and approved scope; preserve the decision and its plan together.", {})
    if data["head_revision"] not in bodies["report"]:
        raise UsageError("The completed report must name its actual full resulting head SHA.", {})
    for key in ("sent_quote", "started_quote", "clear_quote"):
        if not any(data["transport"][key] in body for body in bodies["transport_evidence"]):
            raise UsageError("Transport {} must quote the original archived evidence; a newly asserted outcome alone is insufficient.".format(key), {})
    prior = replay(store, "historical_attempts", data, receipts)
    if prior:
        return prior
    if any(row["task"] == data["task"] and row["fix_round"] == data["fix_round"] for row in store["historical_attempts"]):
        raise UsageError("That historical task/attempt is already imported; reuse its original identity, never duplicate it.", {})
    if any(row["task"] == data["task"] and row["vcs"]["head_revision"] == data["head_revision"]
           and completed_evidence(row["receipts"]) == completed_evidence(receipts) for row in store["historical_attempts"]):
        raise UsageError("That completed report and transport evidence already consumed an attempt; do not relabel it with another fix number.", {})
    previous = prior_developer(assignments, data["task"])
    if previous is None or data["fix_round"] != ledger.confirmed_fix(assignments, data["task"]) + 1:
        raise UsageError("Import only the actual next missing completed correction after its canonical developer history; do not skip or reuse a count.", {})
    if timestamp(data["occurred_at"], "occurred_at") <= timestamp(assignments[previous]["at"], "preceding developer time"):
        raise UsageError("The imported correction does not provably follow its preceding developer attempt; resolve the chronology.", {})
    if any(row["status"] in ledger.PENDING_STATUSES and (row["task"] == data["task"] or row["agent"] == data["agent"])
           for row in store["dispatches"]):
        raise UsageError("Reconcile the pending dispatch before importing overlapping historical work; do not count it twice.", {})
    vcs = _git_evidence(data, task)
    previous_import = next((row for row in store["historical_attempts"] if row["assignment_index"] == previous), None)
    if previous_import and previous_import["vcs"]["head_revision"] != data["previous_head"]:
        raise UsageError("Historical corrections must chain from the preceding imported head; preserve the original sequence.", {})
    record = {"schema_version": ledger.RECOVERY_SCHEMA_VERSION, "at": at, "id": data["id"], "task": data["task"],
              "fix_round": data["fix_round"], "input": data, "receipts": receipts, "vcs": vcs,
              "assignment_index": len(assignments), "previous_developer": previous,
              "basis": "completed_authorized_manual_correction", "native_session_proof": None,
              "grants_future_attempts": False, "reviews": []}
    store["historical_attempts"].append(record)
    ledger._event(store, at, "historical_correction_imported", data["task"],
                  {"historical_attempt": data["id"], "assignment_index": len(assignments), "fix_round": data["fix_round"]})
    return record


def _review_input(data, record):
    fields(data, "id historical_attempt head_revision verdict review_mode reviewer report", "Historical review")
    for key in ("id", "historical_attempt", "reviewer"):
        ledger.text(data[key], key)
    if data["historical_attempt"] != record["id"] or data["head_revision"] != record["vcs"]["head_revision"]:
        raise UsageError("Historical review must name the imported attempt and its actual resulting head.", {})
    if (data["reviewer"] == record["input"]["agent"] or data["verdict"] not in ("blocking", "approved")
            or data["review_mode"] not in ("full", "scoped") or data["verdict"] == "approved" and data["review_mode"] != "full"):
        raise UsageError("Historical review must remain independent, with full verification for an approval.", {})


def record_review(store, data, at):
    if not isinstance(data, dict):
        raise UsageError("Historical review must be an object; follow dispatch-recovery.md.", {})
    record = ledger._item(store["historical_attempts"], data.get("historical_attempt"), "historical attempt")
    _review_input(data, record)
    evidence, body = ledger.receipt(data["report"])
    if data["head_revision"] not in body:
        raise UsageError("Historical review report must name the full imported head SHA.", {})
    prior = next((row for row in record["reviews"] if row["id"] == data["id"]), None)
    if prior:
        if prior["input"] != data or prior["evidence"] != evidence:
            raise UsageError("Historical review identity already names different input or bytes; record a new review without replacing the original.", {})
        return prior
    review = {"schema_version": ledger.RECOVERY_SCHEMA_VERSION, "at": at, "id": data["id"],
              "task": record["task"], "input": data, "evidence": evidence}
    record["reviews"].append(review)
    ledger._event(store, at, "historical_review_recorded", record["task"], {"historical_attempt": record["id"], "review": data["id"]})
    return review


def _release_input(data, assignments):
    fields(data, "id task assignment_index cleared_at fresh_session verified_empty_composer verified_fresh_conversation fresh_quote composer_quote evidence reason", "Verified release hand-clear")
    for key in ("id", "task", "reason", "fresh_quote", "composer_quote"):
        ledger.text(data[key], key)
    index = data["assignment_index"]
    if type(index) is not int or not 0 <= index < len(assignments):
        raise UsageError("Use the preserved successful hand-cleared release assignment index.", {})
    row = assignments[index]
    if (row.get("task") != data["task"] or row.get("role") != "release" or row.get("status") != "applied"
            or row.get("cleared") is not False or row.get("clear_reason") != "hand"):
        raise UsageError("Clear evidence must refer to this task's applied release --no-clear row; never relabel a different handoff.", {})
    preceding = latest_assignment(assignments, task=data["task"], role="developer", status="applied", before=index)
    previous = preceding[0] if preceding is not None else None
    if previous is None or assignments[previous]["agent"] != row["agent"]:
        raise UsageError("Release clear must follow the same worker's confirmed developer attempt.", {})
    if data["verified_empty_composer"] is not True or data["verified_fresh_conversation"] is not True:
        raise UsageError("Record the verified fresh conversation and empty composer before release; --no-clear alone is not proof.", {})
    fresh = data["fresh_session"]
    if fresh is not None:
        fields(fresh, "kind value", "Cleared native session")
        if fresh["kind"] not in ("id", "path"):
            raise UsageError("Fresh session kind must be id or path from native evidence.", {})
        ledger.text(fresh["value"], "fresh native session")
    if not timestamp(assignments[previous]["at"], "developer time") <= timestamp(data["cleared_at"], "cleared_at") <= timestamp(row["at"], "release time"):
        raise UsageError("The verified clear must fall between the preceding developer and release assignments.", {})
    original = assignments[previous].get("context_session")
    if original and fresh and all(original.get(key) == fresh[key] for key in ("kind", "value")):
        raise UsageError("The reported release session is the old developer session; collect actual fresh-conversation evidence.", {})
    return previous


def record_release_clear(store, assignments, data, at, observed_session):
    previous = _release_input(data, assignments)
    ledger.task_record(store, data["task"])
    if timestamp(at, "record time") < timestamp(assignments[data["assignment_index"]]["at"], "release time"):
        raise UsageError("A release clear cannot be recorded before its successful release assignment.", {})
    evidence, body = ledger.receipt(data["evidence"])
    if data["fresh_session"] is not None and data["fresh_session"]["value"] not in body:
        raise UsageError("The archived clear evidence must name the verified fresh native session.", {})
    if any(data[key] not in body for key in ("fresh_quote", "composer_quote")):
        raise UsageError("Quote the original archived fresh-conversation and empty-composer observations before recording this clear.", {})
    receipts = {"clear": evidence}
    prior = replay(store, "hand_clearances", data, receipts)
    if prior:
        return prior
    if previous != prior_developer(assignments, data["task"]):
        raise UsageError("That release no longer follows this task's latest developer; use the current history.", {})
    if any(row["assignment_index"] == data["assignment_index"] for row in store["hand_clearances"]):
        raise UsageError("This release clear is already recorded; reuse its original identity.", {})
    # This observation occurs after release; it cannot prove or disprove the
    # archived clear. The next developer dispatch verifies its own live clear.
    record = {"schema_version": ledger.RECOVERY_SCHEMA_VERSION, "at": at, "id": data["id"], "task": data["task"],
              "assignment_index": data["assignment_index"], "previous_developer": previous,
              "input": data, "receipts": receipts, "observed_session": observed_session,
              "basis": "verified_required_release_clear"}
    store["hand_clearances"].append(record)
    ledger._event(store, at, "release_hand_clear_recorded", data["task"], {"clearance": data["id"], "assignment_index": data["assignment_index"]})
    return record


def validate_history(store, assignments):
    """Validate stored relationships without re-reading historical artifacts."""
    slots = set()
    completed = set()
    for row in store["historical_attempts"]:
        data = row["input"]
        _attempt_input(data, ledger.task_record(store, row["task"]))
        index = row["assignment_index"]
        if type(index) is not int or not 0 <= index < len(assignments):
            raise UsageError("Historical import has no matching assignment row; preserve the ledger for recovery.", {})
        assignment = assignments[index]
        if (row["id"] != data["id"] or row["task"] != data["task"] or row["fix_round"] != data["fix_round"]
                or any(assignment.get(key) != data[key] for key in ("task", "agent", "fix_round"))
                or assignment.get("at") != data["occurred_at"] or assignment.get("role") != "developer"
                or assignment.get("status") != "applied" or assignment.get("context_session") is not None
                or assignment.get("cleared") is not None or assignment.get("clear_reason") != "unknown"
                or row["native_session_proof"] is not None or row["grants_future_attempts"] is not False
                or row["previous_developer"] != prior_developer(assignments, row["task"], index)
                or row["fix_round"] != ledger.confirmed_fix(assignments[:index], row["task"]) + 1):
            raise UsageError("Historical correction disagrees with its preserved count, identity or unknown native proof.", {})
        slot = (row["task"], row["fix_round"])
        if slot in slots:
            raise UsageError("Historical correction was imported more than once.", {})
        slots.add(slot)
        if not isinstance(row["receipts"], dict) or set(row["receipts"]) != {"authorization_evidence", "transport_evidence", "report"}:
            raise UsageError("Historical correction is missing original evidence receipts.", {})
        for key, value in row["receipts"].items():
            receipts = [value] if key == "report" else value
            sources = [data[key]] if isinstance(data[key], str) else data[key]
            if not isinstance(receipts, list) or not receipts or not isinstance(sources, list) or len(receipts) != len(sources):
                raise UsageError("Historical evidence receipts no longer match the original source list.", {})
            for receipt, source in zip(receipts, sources):
                ledger.validate_receipt(receipt)
                if receipt["path"] != source:
                    raise UsageError("Historical evidence receipt names a different original artifact.", {})
        if not isinstance(row["vcs"], dict) or not isinstance(row["vcs"].get("changed_paths"), list):
            raise UsageError("Historical correction lacks its inspected Git evidence.", {})
        if row["vcs"]["changed_paths"]:
            ledger.paths(row["vcs"]["changed_paths"], "historical changed paths")
        for path in row["vcs"]["changed_paths"]:
            if not all(any(fnmatchcase(path, pattern) for pattern in bounds) for bounds in
                       (data["allowed_paths"], ledger.task_record(store, row["task"])["allowed_paths"])):
                raise UsageError("Historical diff no longer matches its preserved authorization bounds.", {})
        if row["basis"] != "completed_authorized_manual_correction":
            raise UsageError("Historical import must retain its distinct recovery provenance.", {})
        for key in ("previous_head", "head_revision"):
            if row["vcs"][key] != data[key]:
                raise UsageError("Historical Git evidence disagrees with its original import.", {})
            ledger.revision(data[key], key)
        identity = (row["task"], data["head_revision"], completed_evidence(row["receipts"]))
        if identity in completed:
            raise UsageError("Historical evidence was relabeled as another attempt; preserve the ledger and reconcile the duplicate.", {})
        completed.add(identity)
        if not isinstance(row["reviews"], list):
            raise UsageError("Historical reviews must preserve their original sequence.", {})
        review_ids = set()
        for review in row["reviews"]:
            if not isinstance(review, dict) or type(review.get("schema_version")) is not int or review["schema_version"] != ledger.RECOVERY_SCHEMA_VERSION:
                raise UsageError("Historical review schema is unsupported; update the owner.", {})
            _review_input(review["input"], row)
            ledger.text(review["at"], "historical review timestamp")
            ledger.validate_receipt(review["evidence"])
            if review["id"] in review_ids or review["id"] != review["input"]["id"] or review["task"] != row["task"] or review["evidence"]["path"] != review["input"]["report"]:
                raise UsageError("Historical review identity or evidence is inconsistent.", {})
            review_ids.add(review["id"])
    releases = set()
    for row in store["hand_clearances"]:
        previous = _release_input(row["input"], assignments)
        ledger.task_record(store, row["task"])
        if (row["id"] != row["input"]["id"] or row["task"] != row["input"]["task"]
                or row["assignment_index"] != row["input"]["assignment_index"] or row["previous_developer"] != previous
                or row["assignment_index"] in releases):
            raise UsageError("Recorded release-clear evidence is inconsistent; preserve the original hand-clear row.", {})
        observed = row["observed_session"]
        if observed is not None and (not isinstance(observed, dict)
                or any(not isinstance(observed.get(key), str) or not observed[key].strip()
                       for key in ("pane_id", "source", "agent", "kind", "value"))
                or observed["kind"] not in ("id", "path")):
            raise UsageError("The later native-session observation is malformed; restore the owner-written record without changing the archived clear proof.", {})
        releases.add(row["assignment_index"])
        if not isinstance(row["receipts"], dict) or set(row["receipts"]) != {"clear"} or row["basis"] != "verified_required_release_clear":
            raise UsageError("Release clear lacks its preserved evidence and provenance.", {})
        ledger.validate_receipt(row["receipts"]["clear"])
        if row["receipts"]["clear"]["path"] != row["input"]["evidence"]:
            raise UsageError("Release clear receipt names a different original artifact.", {})
