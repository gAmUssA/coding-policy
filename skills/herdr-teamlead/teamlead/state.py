"""The teamlead state file: usage snapshots and the assignment ledger.

State is a hint, not authority. A snapshot records what an agent's budget
looked like when it was measured; it never substitutes for reading the agent's
live status before writing to it. `plan` may run off a stale snapshot on
purpose (planning has no side effects); `apply` always re-checks live status.

Schema (schema_version 6)::

    {
      "schema_version": 6,
      "snapshots":  [ <measure output>, ... ],   # newest last, capped at 20
      "assignments":[ {"schema_version": 6, "at": <ISO-8601>,
                       "role": <str>, "agent": <str>,
                       "status": "applied" | "sent_but_not_started"
                                 | "unknown",
                       "cleared": <bool> | null,
                       "clear_reason": "automatic" | "hand" | "retained" | "unknown",
                       "task": <str> | null, "fix_round": <int> | null,
                       "context_session": <object> | null,
                       "tier": <object> | null,
                       "requirements": <object> | null,
                       "reviewer_scope": <str> | null}, ... ],
      "specialist_assessments": [ <immutable lead assessment>, ... ],
      "recovery": <owner-managed task, approval, dispatch and evidence ledger>
    }

`status` records whether the hand-off was confirmed: `applied` counts toward
an agent's role history, `sent_but_not_started` does not (see
UNCOUNTED_STATUSES), and `unknown` marks a version-1 row migrated without the
information. Version 1 documents and rows carry no `status`; the 1 -> 2
migration below stamps them `unknown`.

Version 6 adds specialist requirements and assessed contribution receipts.
Version 5 adds recovery history without inventing original authorization or
session proof. Version 4 adds verified model-tier evidence.
Version 3 records context handling, the task and the fix-round number.
Version-2 history cannot prove those facts: migration stamps null values and
an unknown reason. Snapshot versions evolve independently of ledger versions.

Every RECORD carries its own `schema_version`, not just the document: a ledger
row outlives the document it arrived in, and a version on the row is what makes
a later migration auditable row by row.

teamlead owns this file and is the only writer. Writes are atomic: a temp file
in the same directory followed by `os.replace`, so an interrupted run leaves
the previous state intact rather than a truncated file.

Reading follows one rule per direction:

* **Older** document or record: the owner migrates it through MIGRATIONS and
  rewrites the upgraded file. Nobody else migrates.
* **Newer** document or record: this build is the lagging reader, not the
  migrator. It takes the no-usable-prior-state path -- an empty document in
  memory, the file left exactly as found, a warning on stderr.
* **Corrupt** file: no usable prior state, same treatment. Discarding a
  snapshot ring costs one re-measure; overwriting an unread file costs the
  ledger.
"""

import json
import os
import tempfile
import fcntl
from contextlib import contextmanager
from pathlib import Path

from .diagnostics import stderr_warn as _warn
from .errors import ConfigError, HerdrError, StateError, UsageError
from .tiers import parse_launch_args, parse_tiers, verify_argv
from .recovery import DEFAULT_FIX_LIMIT, empty_recovery, migrate_store, validate_store

#: The version this build writes for the document and assignment rows.
#: Snapshots have their own version and migration chain below.
STATE_SCHEMA_VERSION = 6

#: Default checkpoint, not a global permission to exceed an approved budget.
MAX_FIX_ROUNDS = DEFAULT_FIX_LIMIT
CLEAR_REASONS = frozenset({"automatic", "hand", "retained", "unknown"})

#: An assignment row records what teamlead did, including what did not work.
#: `applied` -- the hand-off was CONFIRMED: the brief appeared in the agent's
#:     transcript, or the agent left idle. Either is enough, and neither
#:     proves the work finished -- see `landed` and `started` on the apply
#:     record for which one it was.
#: `sent_but_not_started` -- the paste went out and NEITHER was observed (see
#:     teamlead/composer.py send_message).
#: `unknown` -- written before rows carried a status.
STATUS_APPLIED = "applied"
STATUS_NOT_STARTED = "sent_but_not_started"
STATUS_UNKNOWN = "unknown"

#: Statuses that do NOT count toward "held this role N times". A hand-off
#: nobody started is not experience, and letting it count would push the next
#: round's tie-break away from an agent that never did the work.
#: Deny-list, not an allow-list: rows migrated from before the field are
#: `unknown`, and those were real hand-offs whose history should not vanish.
UNCOUNTED_STATUSES = frozenset({STATUS_NOT_STARTED})

#: The version a document or record carries when it has no `schema_version` at
#: all -- the pre-versioning shape. Reading an absent key as 0 is what lets the
#: migration chain start below the first stamped version.
UNVERSIONED = 0

MAX_SNAPSHOTS = 20


def default_state_path():
    """`$XDG_STATE_HOME/teamlead/state.json`, falling back to `~/.local/state`."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "teamlead" / "state.json"


def empty_state():
    """A fresh, valid state document."""
    return {"schema_version": STATE_SCHEMA_VERSION, "snapshots": [], "assignments": [], "recovery": empty_recovery(), "specialist_assessments": []}


@contextmanager
def state_lock(path):
    """Serialize owner transactions using a live OS lock, never a stale file flag."""
    lock_path = Path(str(path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a", encoding="utf-8")
    except OSError as exc:
        raise StateError("Cannot open state lock {}: {}. Restore directory access before changing task history.".format(lock_path, exc), {}) from None
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateError("Another teamlead command owns this state transaction. Wait for that process; do not delete the lock or start a second dispatch.", {}) from None
        except OSError as exc:
            raise StateError("Cannot lock state {}: {}. Use a filesystem supporting process locks before dispatch.".format(path, exc), {}) from None
        yield
    finally:
        handle.close()


class _NoUsableState(Exception):
    """Internal signal: this file cannot be read, and must not be written."""


def _migrate_record_0_to_1(record):
    """Pre-versioning assignment row -> version 1: stamp it."""
    record["schema_version"] = 1
    return record


def _migrate_record_1_to_2(record):
    """Assignment row version 1 -> 2: stamp a status.

    Version 1 rows were appended whether or not the agent started, so their
    real outcome is not recoverable -- `unknown` says so rather than claiming
    `applied`. Unknown still counts toward role history: these were genuine
    hand-offs, and the failure the status was added for is the rare case.
    """
    record["schema_version"] = 2
    record.setdefault("status", STATUS_UNKNOWN)
    return record


def _migrate_document_1_to_2(payload):
    """Document version 1 -> 2: carry every assignment row up with it."""
    payload["schema_version"] = 2
    payload["assignments"] = [
        _migrate_record_1_to_2(record) if isinstance(record, dict) else record
        for record in payload.get("assignments", [])
    ]
    return payload


def _migrate_document_0_to_1(payload):
    """Pre-versioning document -> version 1.

    The pre-versioning shape is this one without the stamps, so the upgrade is
    the stamps: the document gains `schema_version`, and every assignment row
    gains its own. The entry earns its place as much for its shape as for its
    work -- adding a real 1->2 step later is one more row in MIGRATIONS, not a
    rewrite of the reader.
    """
    payload["schema_version"] = 1
    payload["assignments"] = [
        _migrate_record_0_to_1(record) if isinstance(record, dict) else record
        for record in payload.get("assignments", [])
    ]
    return payload


def _migrate_record_2_to_3(record):
    """Preserve old history without inventing context or task evidence."""
    record.update(
        schema_version=3, cleared=None, clear_reason="unknown",
        task=None, fix_round=None, context_session=None,
    )
    return record


def _migrate_document_2_to_3(payload):
    """Rows are migrated independently by the owner during validation."""
    payload["schema_version"] = 3
    return payload


def _migrate_record_3_to_4(record):
    """Preserve retained-context history; old rows prove no model tier."""
    record["schema_version"] = 4
    record["tier"] = None
    return record


def _migrate_document_3_to_4(payload):
    payload["schema_version"] = 4
    return payload


def _migrate_record_4_to_5(record):
    """Original session, role and attempt evidence is never retroactively repaired."""
    record["schema_version"] = 5
    return record


def _migrate_document_4_to_5(payload):
    """Old history authorizes no extra attempts and invents no base or scope."""
    payload["schema_version"] = 5
    payload["recovery"] = empty_recovery()
    return payload


def _migrate_record_5_to_6(record):
    """Old assignments prove no specialty or engagement identity."""
    if "requirements" in record or "reviewer_scope" in record:
        raise _NoUsableState("older assignment contains unowned specialist requirements")
    record.update(schema_version=6, requirements=None,
                  reviewer_scope="unknown" if record.get("role") == "reviewer" else None)
    return record


def _migrate_document_5_to_6(payload):
    if "specialist_assessments" in payload:
        raise _NoUsableState("older state contains unowned specialist assessments")
    payload.update(schema_version=6, specialist_assessments=[])
    return payload


def _migrate_snapshot_2_to_3(snapshot):
    """An older snapshot has no measured per-tier billing attribution."""
    snapshot["schema_version"] = 3
    for record in snapshot.get("agents", {}).values():
        if isinstance(record, dict):
            record["tier_billing"] = {}
    return snapshot


#: Document migrations, keyed by the version being upgraded FROM. Each value is
#: (version_produced, upgrade_callable). `_apply_migrations` walks the chain
#: until it reaches STATE_SCHEMA_VERSION, so a future 1->2 is one entry.
def _migrate_snapshot_1_to_2(snapshot):
    """Snapshot version 1 -> 2: stamp every agent record's `window_group`.

    Version 1 predates shared usage windows, so no agent in it declared one.
    The safe value is the empty string -- "this worker has a window of its
    own" -- which is what a v1 roster meant: the planner charges a seat's cost
    to nobody else, exactly as it did when the snapshot was written. Guessing
    a group here would invent a link the measurement never observed and could
    halt a judge round on another worker's headroom.
    """
    snapshot["schema_version"] = 2
    agents = snapshot.get("agents")
    if isinstance(agents, dict):
        for record in agents.values():
            if isinstance(record, dict):
                record.setdefault("window_group", "")
    return snapshot


#: Snapshot documents carry their own version, like assignment rows: a
#: snapshot outlives the state document it arrived in, and `measure` bumps it
#: on its own release train.
SNAPSHOT_MIGRATIONS = {
    1: (2, _migrate_snapshot_1_to_2),
    2: (3, _migrate_snapshot_2_to_3),
}

#: The snapshot version this build owns. Kept beside the migration table so
#: the two move together; `measure.MEASURE_SCHEMA_VERSION` writes it.
SNAPSHOT_SCHEMA_VERSION = 3


MIGRATIONS = {
    UNVERSIONED: (1, _migrate_document_0_to_1),
    1: (2, _migrate_document_1_to_2),
    2: (3, _migrate_document_2_to_3),
    3: (4, _migrate_document_3_to_4),
    4: (5, _migrate_document_4_to_5),
    5: (6, _migrate_document_5_to_6),
}

#: The same table for one assignment record, walked the same way.
RECORD_MIGRATIONS = {
    UNVERSIONED: (1, _migrate_record_0_to_1),
    1: (2, _migrate_record_1_to_2),
    2: (3, _migrate_record_2_to_3),
    3: (4, _migrate_record_3_to_4),
    4: (5, _migrate_record_4_to_5),
    5: (6, _migrate_record_5_to_6),
}


def _version_of(payload):
    """The declared version, with an absent key reading as UNVERSIONED."""
    version = payload.get("schema_version", UNVERSIONED)
    if isinstance(version, bool) or not isinstance(version, int):
        raise _NoUsableState("schema_version {!r} is not an integer".format(version))
    return version


def _apply_migrations(payload, table, label, target_version=STATE_SCHEMA_VERSION):
    """Walk `payload` up the migration chain. Returns (payload, migrated?)."""
    version = _version_of(payload)
    migrated = False
    seen = set()
    while version != target_version:
        if version > target_version:
            raise _NoUsableState(
                "{} is at schema_version {}; this build owns {}".format(
                    label, version, target_version
                )
            )
        if version in seen:
            raise _NoUsableState(
                "{} migration chain loops at version {}".format(label, version)
            )
        seen.add(version)
        step = table.get(version)
        if step is None:
            raise _NoUsableState(
                "{} is at schema_version {}, which no migration upgrades".format(
                    label, version
                )
            )
        produced, upgrade = step
        payload = upgrade(payload)
        version = produced
        migrated = True
    return payload, migrated


def _validate(payload, path):
    """Return (state, migrated?) or raise _NoUsableState.

    Every rejection here is a no-usable-prior-state signal, never an
    instruction to the operator to delete their ledger.
    """
    from .composition import normalize_requirement
    from .engagement import validate_assessments

    if not isinstance(payload, dict):
        raise _NoUsableState("the document is not a JSON object")

    payload, migrated = _apply_migrations(payload, MIGRATIONS, "state file")

    for key in ("snapshots", "assignments"):
        if not isinstance(payload.get(key), list):
            raise _NoUsableState("{!r} is not an array".format(key))

    rows = []
    for record in payload["assignments"]:
        if not isinstance(record, dict):
            raise _NoUsableState("an assignment row is not a JSON object")
        record, row_migrated = _apply_migrations(record, RECORD_MIGRATIONS, "an assignment row")
        if "requirements" not in record:
            raise _NoUsableState("an assignment row is missing specialist requirements provenance")
        scope = record.get("reviewer_scope")
        reviewer = record.get("role") == "reviewer"
        if ("reviewer_scope" not in record
                or reviewer and (not isinstance(scope, str) or scope not in {"verification", "design", "unknown"})
                or not reviewer and scope is not None):
            raise _NoUsableState("an assignment row has invalid reviewer responsibility provenance")
        if record["requirements"] is not None:
            try:
                if normalize_requirement(record["requirements"], record.get("role")) != record["requirements"]:
                    raise _NoUsableState("an assignment has non-canonical specialist requirements")
            except UsageError as exc:
                raise _NoUsableState(str(exc)) from None
        cleared = record.get("cleared")
        reason = record.get("clear_reason")
        if not isinstance(reason, str) or reason not in CLEAR_REASONS or (
            (reason == "automatic" and cleared is not True)
            or (reason in {"hand", "retained"} and cleared is not False)
            or (reason == "unknown" and cleared is not None)
        ):
            raise _NoUsableState("an assignment row has invalid context evidence")
        if record.get("task") is not None and (
            not isinstance(record["task"], str) or not record["task"].strip()
        ):
            raise _NoUsableState("an assignment row has an invalid task label")
        fix_round = record.get("fix_round")
        if fix_round is not None and (
            isinstance(fix_round, bool) or not isinstance(fix_round, int)
            or fix_round < 1
        ):
            raise _NoUsableState("an assignment row has an invalid fix-round number")
        session = record.get("context_session")
        tier = record.get("tier")
        if tier is not None:
            proof = tier.get("verified") if isinstance(tier, dict) else None
            if (not isinstance(proof, dict) or not isinstance(tier.get("kind"), str)
                    or not isinstance(tier.get("model"), str)
                    or not isinstance(proof.get("source"), str)
                    or proof.get("source") not in {"launch_argv", "process_argv"}
                    or proof.get("model") != tier["model"] or proof.get("effort") != tier.get("effort")
                    or not isinstance(proof.get("pane_id"), str) or not proof["pane_id"]):
                raise _NoUsableState("an assignment row has invalid tier evidence")
            try:
                parse_tiers({"build": {"model": tier["model"], "effort": tier.get("effort")}}, tier["kind"])
                launch_args = parse_launch_args(tier.get("launch_args", []), tier["kind"])
                verify_argv(tier["kind"], tier, proof.get("argv"), launch_args)
            except (ConfigError, HerdrError, UsageError):
                raise _NoUsableState("an assignment row's launch arguments do not prove its tier") from None
        if session is not None and (
            not isinstance(session, dict)
            or any(not isinstance(session.get(key), str) or not session[key].strip()
                   for key in ("pane_id", "source", "agent", "kind", "value"))
            or session.get("kind") not in ("id", "path")
        ):
            raise _NoUsableState("an assignment row has invalid native session evidence")
        migrated = migrated or row_migrated
        rows.append(record)
    payload["assignments"] = rows

    # A snapshot is a whole `measure` document and arrives already stamped. An
    # older one is migrated and rewritten here -- this module owns the file it
    # sits in (rules/stateful-artifacts.md Migration Policy) -- while one
    # stamped ahead of this build is the same lagging-reader case as the
    # document itself.
    snapshots = []
    for snapshot in payload["snapshots"]:
        if not isinstance(snapshot, dict):
            raise _NoUsableState("a snapshot entry is not a JSON object")
        found = _version_of(snapshot)
        if found > SNAPSHOT_SCHEMA_VERSION:
            raise _NoUsableState(
                "a snapshot is at schema_version {}; this build owns {}".format(
                    found, SNAPSHOT_SCHEMA_VERSION
                )
            )
        snapshot, snap_migrated = _apply_migrations(
            snapshot, SNAPSHOT_MIGRATIONS, "a snapshot", SNAPSHOT_SCHEMA_VERSION
        )
        migrated = migrated or snap_migrated
        snapshots.append(snapshot)
    payload["snapshots"] = snapshots
    try:
        store = payload.setdefault("recovery", empty_recovery())
        migrated = migrate_store(store) or migrated
        validate_store(store, rows)
        validate_assessments(payload)
    except UsageError as exc:
        raise _NoUsableState(str(exc)) from None
    return payload, migrated


def load_state(path, warn=None):
    """Read the state file. See `load_state_checked` for the full contract."""
    state, _usable = load_state_checked(path, warn=warn)
    return state


def load_state_checked(path, warn=None, *, persist_migration=True):
    """Read the state file, migrating an older one and rewriting it.

    Returns `(state, usable)`. `usable` is False when a file EXISTS and could
    not be read as prior state -- corrupt, or written by a newer build. A
    caller that intends to WRITE must refuse on False: this function promised
    to leave that file exactly as found, and saving over it would destroy the
    ledger it just preserved. A missing file is usable: there is nothing to
    lose, and an empty document is the honest starting point.

    A missing file and a never-written file are the same thing: no prior state.
    So is a corrupt one, and so is one written by a newer build -- in both of
    those the file is left exactly as found, and the caller gets an empty
    document plus a warning on stderr. A tool failure (unreadable permissions,
    a directory in the way) still raises: that is not "no state", it is "this
    environment is wrong", and it carries a fix.
    """
    warn = warn or _warn
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state(), True
    except PermissionError as exc:
        raise StateError(
            "State file {} is not readable: {} - fix its permissions with "
            "`chmod u+rw {}`.".format(path, exc.strerror, path),
            {"path": str(path)},
        ) from None
    except IsADirectoryError:
        raise StateError(
            "State path {} is a directory - point --state at the state.json "
            "file itself.".format(path),
            {"path": str(path)},
        ) from None
    except UnicodeDecodeError:
        warn("state file {} is not UTF-8 JSON; the file is left untouched. Restore a readable UTF-8 backup or use a separate --state file.".format(path))
        return empty_state(), False
    except OSError as exc:
        raise StateError("Cannot read state {}: {}. Restore readability or use a separate --state file; preserve the original ledger.".format(path, exc),
                         {"path": str(path)}) from None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        warn(
            "state file {} is not valid JSON ({} at line {} column {}) - starting "
            "from an empty ledger; the file is left untouched.".format(
                path, exc.msg, exc.lineno, exc.colno
            )
        )
        return empty_state(), False

    try:
        state, migrated = _validate(payload, path)
    except _NoUsableState as exc:
        warn(
            "state file {}: {} - starting from an empty ledger; the file is left "
            "untouched.".format(path, exc)
        )
        return empty_state(), False

    if migrated and not persist_migration:
        raise StateError("This read-only preview needs an owner migration. Run `teamlead state` with the same --state path, then retry the preview; the original file is unchanged.", {"path": str(path)})
    if migrated:
        try:
            save_state(path, state)
        except StateError as exc:
            # The upgrade holds in memory even when the rewrite cannot land.
            warn(
                "state file {} migrated to schema_version {} in memory, but the "
                "rewrite failed ({}) - the next run migrates it again.".format(
                    path, STATE_SCHEMA_VERSION, exc
                )
            )
    return state, True


def save_state(path, state):
    """Write `state` atomically, creating the parent directory when needed."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, FileExistsError, NotADirectoryError) as exc:
        raise StateError(
            "Cannot create the state directory {}: {} - pass --state to point "
            "at a writable location.".format(path.parent, exc),
            {"path": str(path)},
        ) from None

    handle = None
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(state, handle, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(temp_name, str(path))
        temp_name = None
    except OSError as exc:
        raise StateError(
            "Cannot write the state file {}: {} - pass --state to point at a "
            "writable location.".format(path, exc),
            {"path": str(path)},
        ) from None
    finally:
        if handle is not None:
            handle.close()
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


def add_snapshot(state, snapshot):
    """Append `snapshot` and keep only the newest MAX_SNAPSHOTS entries."""
    snapshots = list(state.get("snapshots", []))
    snapshots.append(snapshot)
    state["snapshots"] = snapshots[-MAX_SNAPSHOTS:]
    return state


def add_assignment(state, at, role, agent, status=STATUS_APPLIED, *,
                   cleared=None, clear_reason="unknown", task=None, fix_round=None, context_session=None, tier=None, requirements=None, reviewer_scope=None):
    """Append one role-to-agent assignment to the ledger.

    Every hand-off is recorded, including one that never started -- the ledger
    is what teamlead did, and a round that went out and died is exactly the
    thing worth being able to look up afterwards. `status` is what keeps that
    honesty from corrupting the role history: see UNCOUNTED_STATUSES.

    The row carries its own `schema_version`, so a later migration can walk the
    ledger row by row rather than inferring a row's shape from the document.
    """
    state.setdefault("assignments", []).append(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "at": at,
            "role": role,
            "agent": agent,
            "status": status,
            "cleared": cleared,
            "clear_reason": clear_reason,
            "task": task,
            "fix_round": fix_round,
            "context_session": context_session,
            "tier": tier,
            "requirements": requirements,
            "reviewer_scope": (reviewer_scope or "unknown") if role == "reviewer" else None,
        }
    )
    return state


def latest_snapshot(state):
    """The most recent snapshot, or None when nothing has been measured yet."""
    snapshots = state.get("snapshots", [])
    return snapshots[-1] if snapshots else None


def role_counts(state):
    """How many times each agent has previously held each role.

    Returns ``{role: {agent: count}}``. The planner uses it to break headroom
    ties toward the agent that has held the role least often, which spreads
    roles around instead of pinning one agent to `developer` forever.

    Rows whose status is in UNCOUNTED_STATUSES are skipped: an assignment that
    was sent but never started is not experience of the role, and counting it
    would steer the next round away from the agent that never did the work.
    """
    counts = {}
    for record in state.get("assignments", []):
        if not isinstance(record, dict):
            continue
        if record.get("status") in UNCOUNTED_STATUSES:
            continue
        role = record.get("role")
        agent = record.get("agent")
        if role is None or agent is None:
            continue
        per_role = counts.setdefault(role, {})
        per_role[agent] = per_role.get(agent, 0) + 1
    return counts
