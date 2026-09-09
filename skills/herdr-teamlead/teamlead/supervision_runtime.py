"""Read-only bounded fleet watching and executable supervision commands.

A watch owns no agent input capability. It observes every active enrollment on
one sweep, records changes durably, and returns the unacknowledged batch. The
caller must retain/await its real execution handle; this is not a daemon or an
asynchronous wake service. Resume replays saved events before more polling.

Process health combines PID, ps start/argv identity, injected heartbeat time,
and bounded deadline. A stale file or os.kill(pid, 0) alone never proves life.
Clock and sleeper are injected by cli.py. Constants here own polling limits.
"""

import hashlib
import os
import subprocess
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import supervision as store
from .errors import HerdrError, StateError, TeamLeadError, UsageError
from .herdr import HerdrClient, scrub_for_trace

DEFAULT_INTERVAL = 2.0
DEFAULT_DURATION = 45.0
MAX_DURATION = 60.0
HEARTBEAT_GRACE = 15.0
READ_LINES = 200
OBSERVATION_TIMEOUT = 3.0


def observation_runner(argv):
    """Bound each read-only Herdr call so one broken worker cannot stall a sweep."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=OBSERVATION_TIMEOUT)
    except FileNotFoundError:
        # HerdrClient supplies the install/binary-path recovery instruction.
        raise
    except subprocess.TimeoutExpired:
        raise HerdrError("Herdr observation exceeded its {}s read timeout. Inspect the named worker and Herdr connection, then retry this bounded read without relaunching the worker.".format(OBSERVATION_TIMEOUT), {}) from None
    except OSError as exc:
        raise HerdrError("Cannot start the Herdr observation: {}. Restore executable access and the Herdr installation, then retry this read.".format(scrub_for_trace(str(exc))), {}) from None


class ObservationClient(HerdrClient):
    """Reject malformed control shapes before native record access can abort a fleet sweep."""

    def _run_json(self, argv):
        result = super()._run_json(argv)
        if not isinstance(result, dict):
            raise HerdrError("Herdr returned a control response whose result is not an object. Check the installed Herdr version and restore its JSON control response before retrying this read.", {})
        return result


def read_client(binary=None):
    return ObservationClient(binary=binary, runner=observation_runner)


def process_identity(pid):
    """Return a live PID's start time and argv digest; absent is None."""
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                                capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StateError("Cannot verify supervision process {}: {}. Restore ps access before replacing a watcher.".format(pid, scrub_for_trace(str(exc))), {}) from None
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return None
    if result.returncode != 0 or not result.stdout.strip():
        diagnostic = scrub_for_trace(result.stderr.strip()) or "no process identity returned"
        raise StateError("ps could not verify watcher {} (exit {}): {}. Restore process visibility and inspect the existing execution handle before restarting supervision.".format(pid, result.returncode, diagnostic), {})
    return {"pid": pid, "identity": store.digest(result.stdout.strip())}


def health(data, at, probe=process_identity):
    if not data["watchers"]:
        return {"state": "absent", "watcher": None}
    row = data["watchers"][-1]
    if row["status"] != "running":
        return {"state": "stopped", "watcher": row["id"], "reason": row.get("reason")}
    actual = probe(row["process"]["pid"])
    if actual != row["process"]:
        return {"state": "dead", "watcher": row["id"]}
    now = store.timestamp(at)
    age = (now - store.timestamp(row["heartbeat"])).total_seconds()
    if age < 0 or age > HEARTBEAT_GRACE or now > store.timestamp(row["deadline"]):
        return {"state": "unresponsive", "watcher": row["id"]}
    return {"state": "live", "watcher": row["id"], "heartbeat": row["heartbeat"], "process": actual}


def status(state_path, at, probe=process_identity):
    data = store.load(state_path)
    return {"schema_version": 1, "state_path": data["state_path"], "binding": data["binding"],
            "active": [row for row in data["members"] if row["active"]], "events": store.pending(data),
            "through": len(data["events"]), "hold": store.held(data), "watcher": health(data, at, probe)}


def observation_error(exc, operation, agent):
    """Persist a bounded actionable message, never arbitrary exception details."""
    message = exc.message
    if message.startswith("herdr returned non-JSON output"):
        # HerdrClient includes a stdout excerpt in this exception. Its contents
        # are not needed to identify a malformed control response.
        message = "Herdr returned a non-JSON control response. Inspect the Herdr connection and installed version before retrying this read."
    elif message.startswith("herdr returned JSON without a `result` field"):
        message = "Herdr's JSON control response omitted the result field. Check the installed Herdr version before retrying this read."
    return {"code": exc.code, "operation": operation, "agent": scrub_for_trace(agent),
            "message": scrub_for_trace(message),
            "recovery": "Inspect the named worker and restore the failing read operation before retrying observation. This error does not prove completion or permit a worker relaunch."}


def observe(client, member):
    """Collect hints only; wait-report/report_delivery retain delivery authority."""
    assignment = store.expected_assignment(member)
    try:
        live = client.agent_get(assignment["agent"])
    except TeamLeadError as exc:
        return {"observation_error": observation_error(exc, "herdr agent get", assignment["agent"])}
    pane = live.get("pane_id")
    native = live.get("agent_session")
    expected_native = assignment["native_session"]
    if isinstance(expected_native, dict):
        expected_native = {key: value for key, value in expected_native.items() if key != "pane_id"}
    observations = {"lifecycle": {"status": live.get("agent_status", "unknown")},
                    "identity": {"pane_id": pane, "native_session": native}}
    if not pane:
        observations["unavailable"] = {"reason": "worker_has_no_pane"}
    elif (assignment["pane_id"] is not None and pane != assignment["pane_id"]
            or expected_native is not None and native != expected_native):
        observations["identity_changed"] = {"pane_id": pane, "native_session": native}
    if not pane or native is None:
        observations["identity_unverified"] = {"pane_id": pane, "native_session": native}
    report = Path(assignment["report"])
    try:
        body = report.read_bytes()
    except FileNotFoundError:
        observations["report"] = {"present": False}
    except OSError as exc:
        observations["report_error"] = {"reason": "report_unreadable", "error": type(exc).__name__,
                                        "operation": "read report file", "path": scrub_for_trace(str(report)),
                                        "message": scrub_for_trace(str(exc)),
                                        "recovery": "Restore access to the enrolled report file, then repeat the report-delivery checkpoint. Keep acceptance unverified while its evidence is unreadable."}
    else:
        observations["report"] = {"present": True, "path": str(report), "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    # Save only a digest of visible output, never raw potentially sensitive pane
    # contents. It lets the lead notice a blocked/report candidate changed.
    if pane and (live.get("agent_status") == "blocked" or observations.get("report", {}).get("present")):
        try:
            visible = client.agent_read(assignment["agent"], source="visible", lines=READ_LINES)
        except TeamLeadError as exc:
            observations["observation_error"] = observation_error(exc, "herdr agent read --source visible", assignment["agent"])
        else:
            observations["visible"] = {"sha256": store.digest(visible)}
    return observations


def sweep(state_path, client, at, watcher_id):
    data = store.load(state_path)
    members = [row for row in data["members"] if row["active"]]
    def sample(member):
        observed = observe(client, member)
        return member["id"], store.digest(store.expected_assignment(member)), observed
    # Independent read-only observations run concurrently. No branch receives a
    # prompt/keys operation, and event order remains enrollment order.
    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        samples = list(pool.map(sample, members))
    def mutate(current):
        watcher = current["watchers"][-1]
        if watcher["id"] != watcher_id or watcher["status"] != "running":
            raise StateError("This watcher no longer owns the fleet observation lease; retain its output and inspect supervision-status.", {})
        watcher["heartbeat"] = at
        for member_id, assignment_digest, sample in samples:
            member = next(row for row in current["members"] if row["id"] == member_id)
            if not member["active"] or store.digest(store.expected_assignment(member)) != assignment_digest:
                continue
            previous = member["observed"]
            for key, value in sample.items():
                if previous.get(key) == value:
                    continue
                first = not previous
                quiet = (key == "identity" and (first or value == {"pane_id": member["assignment"]["pane_id"],
                                                                  "native_session": member["assignment"]["native_session"]})
                         or key == "report" and not value.get("present") and first
                         or key == "lifecycle" and first and value.get("status") == "working")
                if not quiet:
                    store.append_event(current, at, member_id, key + "_observed", value)
            for key in previous.keys() - sample.keys():
                if key in ("observation_error", "report_error", "unavailable", "identity_changed", "identity_unverified"):
                    store.append_event(current, at, member_id, key + "_cleared", {})
            member["observed"] = sample
        rechecked = {row["data"].get("event") for row in current["events"] if row["kind"] == "recheck_due"}
        for acknowledgement in current["acknowledgements"]:
            due = acknowledgement["recheck_at"]
            if due is None or acknowledgement["event"] in rechecked or store.timestamp(at) < store.timestamp(due):
                continue
            original = next(row for row in current["events"] if row["id"] == acknowledgement["event"])
            if original["member"] is not None and not any(row["id"] == original["member"] and row["active"] for row in current["members"]):
                continue
            store.append_event(current, at, original["member"], "recheck_due", {"event": original["id"], "recheck_at": due})
        return store.pending(current)
    return store.transaction(state_path, mutate)


def watch(state_path, client, at, *, clock, sleeper, probe=process_identity,
          pid=None, duration=DEFAULT_DURATION, interval=DEFAULT_INTERVAL):
    if not 0 < interval <= HEARTBEAT_GRACE / 2 or not 0 < duration <= MAX_DURATION:
        raise UsageError("Watch interval must be positive and within the heartbeat allowance; duration must be positive and at most {} seconds.".format(MAX_DURATION), {})
    store.timestamp(at)
    process = probe(os.getpid() if pid is None else pid)
    if process is None:
        raise StateError("Cannot prove the foreground watch process is live; restore process visibility before watching.", {})
    def start(data):
        prior = health(data, at, probe)
        if prior["state"] in ("live", "unresponsive"):
            raise UsageError("A watcher process still exists. Poll its real execution handle or inspect it; stale heartbeat alone does not permit replacement.", {"watcher": prior})
        if prior["state"] == "dead":
            outgoing = data["watchers"][-1]
            outgoing.update(status="stopped", reason="process_lost", ended_at=at)
            store.append_event(data, at, None, "watcher_lost", {"watcher": outgoing["id"]})
        if store.pending(data):
            return None
        row = {"schema_version": 1, "id": "watch-{}".format(len(data["watchers"]) + 1),
               "at": at, "heartbeat": at, "deadline": (store.timestamp(at) + timedelta(seconds=duration)).isoformat(),
               "process": process, "status": "running", "reason": None, "ended_at": None}
        data["watchers"].append(row)
        return row
    watcher = store.transaction(state_path, start)
    if watcher is None:
        return {**store.drain(state_path), "reason": "pending_events", "watcher": None}
    reason = "interrupted"
    checked = at
    try:
        while True:
            events = sweep(state_path, client, checked, watcher["id"])
            if events:
                reason = "events"
                break
            current = store.load(state_path)
            if not any(row["active"] for row in current["members"]):
                reason = "no_active_members"
                break
            remaining = (store.timestamp(watcher["deadline"]) - store.timestamp(clock())).total_seconds()
            if remaining <= 0:
                reason = "deadline"
                break
            sleeper(min(interval, remaining))
            checked = clock()
    finally:
        def finish(data):
            row = next(row for row in data["watchers"] if row["id"] == watcher["id"])
            row.update(status="stopped", reason=reason, ended_at=checked)
        store.transaction(state_path, finish)
    return {**store.drain(state_path), "reason": reason, "watcher": watcher["id"]}


def bind_current(state_path, client, at, *, environ=None, cwd=None, root=None):
    environ = os.environ if environ is None else environ
    pane_id = store.text(environ.get("HERDR_PANE_ID"), "HERDR_PANE_ID")
    pane = client.pane_get(pane_id)
    native = pane.get("agent_session")
    if (pane.get("pane_id") != pane_id or not isinstance(native, dict)
            or native.get("kind") not in ("id", "path") or native.get("agent") not in ("claude", "codex")
            or native.get("source") != "herdr:" + native["agent"]):
        raise UsageError("This lead pane lacks supported Claude/Codex native session proof. Restore Herdr's session identity before binding the native Stop backstop.", {})
    who = store.identity(native["value"], cwd or str(Path.cwd()), environ.get("HERDR_ENV"),
                         kind=native["kind"], pane_id=pane_id)
    return store.bind(state_path, who, at, root=root)


def register_commands(sub, common):
    for name in ("bind", "enroll", "ack", "resolve", "hold", "resume", "drain", "status", "watch"):
        parser = sub.add_parser("supervision-" + name, parents=[common], help="Persist or inspect lead fleet supervision: " + name)
        parser.add_argument("--now", metavar="ISO")
        if name in ("enroll", "ack", "resolve", "hold"):
            parser.add_argument("--record", required=True, metavar="FILE")
        if name == "bind":
            parser.add_argument("--record", metavar="FILE", help="Explicit verified native binding; otherwise discover the current lead pane.")
        if name == "drain":
            parser.add_argument("--through", type=int)
        if name == "watch":
            parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
            parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)


def run_command(args, state_path, now, *, client=None, clock=None, sleeper=None, process_probe=process_identity):
    action = args.command.removeprefix("supervision-")
    at = args.now or now
    record = store.read_json(args.record) if getattr(args, "record", None) else None
    if getattr(args, "record", None) and record is None:
        raise UsageError("The requested supervision record file is missing; save the JSON input before retrying.", {})
    if action == "bind":
        return store.bind(state_path, record, at) if record is not None else bind_current(state_path, client or read_client(binary=getattr(args, "herdr_bin", None)), at)
    functions = {"enroll": store.enroll, "ack": store.acknowledge, "resolve": store.resolve, "hold": store.hold}
    if action in functions:
        return functions[action](state_path, record, at)
    if action == "resume":
        return store.resume(state_path, at)
    if action == "drain":
        return store.drain(state_path, args.through)
    if action == "status":
        return status(state_path, at, process_probe)
    if action == "watch":
        if clock is None or sleeper is None:
            raise UsageError("CLI must supply a clock and sleeper for bounded fleet watching.", {})
        return watch(state_path, client or read_client(binary=getattr(args, "herdr_bin", None)), at,
                     clock=clock, sleeper=sleeper, probe=process_probe, duration=args.duration, interval=args.interval)
    raise UsageError("Unknown supervision command; run teamlead --help.", {})
