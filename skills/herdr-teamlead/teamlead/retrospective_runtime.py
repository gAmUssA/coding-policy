"""Read-only coverage capture and dispatch-boundary retrospective guards.

Coverage is per worker, never a hash of the whole assignment array. A saved
transition bridges only the exact outgoing work to the new process created
by this utility; it never grants another task, model, or correction attempt.
"""

from pathlib import Path

from . import retrospective as notes
from .chronology import latest_assignment
from .errors import AgentBusyError, HerdrError, StateError, UsageError
from .herdr import READY_STATES
from .launch import foreground_agent
from .probe import resolve_status
from .state import STATE_SCHEMA_VERSION, save_state

TARGET_FIELDS = frozenset({"agent", "role", "model", "effort", "context", "task", "brief", "common", "report", "unavailable", "pane"})


def read_history(path):
    """Read dispatch history without migrating or rewriting its schema."""
    if not Path(path).exists():
        return {"assignments": [], "recovery": {"dispatches": []}}
    value = notes._json(path)
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or not 1 <= value["schema_version"] <= STATE_SCHEMA_VERSION
            or not isinstance(value.get("assignments"), list) or any(not isinstance(row, dict) for row in value["assignments"])):
        raise StateError("Dispatch history cannot establish retrospective sources; preserve it and restore readable assignment records.", {})
    recovery = value.get("recovery", {"dispatches": []})
    if (not isinstance(recovery, dict) or not isinstance(recovery.get("dispatches"), list)
            or any(not isinstance(row, dict) for row in recovery["dispatches"])):
        raise StateError("Dispatch recovery evidence is unreadable; preserve its bytes and restore the original history.", {})
    return value


def request(value):
    if not isinstance(value, dict) or set(value) != {"transitions"} or not isinstance(value["transitions"], list):
        raise UsageError("retro-check --record requires {transitions: [...]} with proposed worker targets.", {})
    result = []
    names = set()
    for raw in value["transitions"]:
        if not isinstance(raw, dict) or set(raw) - TARGET_FIELDS:
            raise UsageError("Retrospective transition has unsupported fields; use the documented agent/role/tier/context/task/brief/report contract.", {})
        item = {key: raw.get(key) for key in TARGET_FIELDS}
        for field in ("agent", "role"):
            notes.text(item[field], "Transition " + field)
        if item["agent"] in names:
            raise UsageError("A retrospective request repeats a worker; batch each outgoing worker only once.", {})
        names.add(item["agent"])
        if item["context"] not in {"clear", "retain", "start"}:
            raise UsageError("Transition context must be clear, retain or start; preserve the actual context change.", {})
        for field in ("model", "effort", "task", "unavailable", "pane"):
            if item[field] is not None:
                notes.text(item[field], "Transition " + field)
        if item["context"] == "start" and not item["pane"]:
            raise UsageError("A start transition needs the actual --pane identifier.", {})
        for field in ("brief", "common", "report"):
            path = item[field]
            if path is not None and (not isinstance(path, str) or not Path(path).is_absolute()):
                raise UsageError("Transition {} must be an absolute file path or null.".format(field), {})
        if bool(item["brief"]) != bool(item["common"]):
            raise UsageError("Transition brief and common must both be supplied, or both null for a worker start.", {})
        result.append(item)
    return {"transitions": result}


def target(item):
    return {"role": item["role"], "model": item["model"], "effort": item["effort"],
            "context": item["context"], "task": item["task"],
            "brief": notes.receipt(item["brief"]) if item["brief"] else None,
            "common": notes.receipt(item["common"]) if item["common"] else None}


def _prior(state, name):
    latest = latest_assignment(state["assignments"], agent=name)
    if latest is None:
        return None, None, None
    index, row = latest
    dispatch = next((entry.get("id") for entry in state.get("recovery", {}).get("dispatches", [])
                     if entry.get("assignment_index") == index and entry.get("agent") == name), None)
    return index, row, dispatch


def _dispatch_evidence(state, identifier, unavailable=None):
    if identifier is None:
        return None
    row = next((entry for entry in state.get("recovery", {}).get("dispatches", []) if entry.get("id") == identifier), None)
    if row is None:
        raise StateError("Original dispatch evidence disappeared; restore the recorded history before transitioning its worker.", {})
    report = row.get("report")
    if report is not None and (not isinstance(report, dict) or not isinstance(report.get("report"), str)):
        raise StateError("Original dispatch review evidence is malformed; restore its recorded receipt before transitioning.", {})
    observed = None
    if report:
        try:
            observed = notes.receipt(report["report"])
        except StateError:
            if not unavailable:
                raise
    return {"sha256": notes.digest(row), "report": observed}


def _observation(client, name, kind, pane=None, *, starting=False, agent=None):
    if starting:
        info = client.pane_process_info(pane)
        processes = info.get("foreground_processes", [])
        if not isinstance(processes, list):
            raise HerdrError("Retrospective start needs readable foreground processes; inspect the shell pane before starting.", {})
        shell = info.get("shell_pid")
        if (isinstance(shell, int) and not isinstance(shell, bool) and shell > 0 and len(processes) == 1
                and isinstance(processes[0], dict) and processes[0].get("pid") == shell):
            argv = processes[0].get("argv")
            if argv is None:
                argv = client.process_args(shell)
            result = {"pane_id": pane, "native": None, "process": {"pid": shell, "argv": argv},
                      "readiness": "shell", "shell": True}
            notes.validate_observation(result)
            return result
    live = client.agent_get(name)
    actual_pane = live.get("pane_id")
    if not isinstance(actual_pane, str) or not actual_pane or (pane and pane != actual_pane):
        raise HerdrError("Retrospective worker changed panes or has no pane; inspect the roster and refresh its coverage.", {})
    process = foreground_agent(client, actual_pane, kind)
    readiness = live.get("agent_status", "unknown")
    if agent is not None:
        readiness, _origin = resolve_status(client, agent, readiness)
    if readiness in READY_STATES:
        readiness = "idle"
    result = {"pane_id": actual_pane, "native": live.get("agent_session"),
              "process": {"pid": process.get("pid"), "argv": process.get("argv")},
              "readiness": readiness, "shell": False}
    notes.validate_observation(result)
    return result


def describe(state, client, agents, item, index=None):
    name = item["agent"]
    if name not in agents:
        raise UsageError("Retrospective worker {} is absent from config; restore its configured identity.".format(name), {})
    offset, row, dispatch = _prior(state, name)
    observed = _observation(client, name, agents[name].kind, item["pane"], starting=item["context"] == "start", agent=agents[name])
    source = {"assignment_index": offset, "assignment_digest": notes.digest(row) if row is not None else None,
              "dispatch_id": dispatch, "dispatch_evidence": _dispatch_evidence(state, dispatch, item["unavailable"]), "task": row.get("task") if row else None,
              "role": row.get("role") if row else None, "tier": row.get("tier") if row else None,
              "observation": observed, "report": notes.receipt(item["report"]) if item["report"] else None,
              "unavailable": item["unavailable"]}
    known = index or {"records": [], "transitions": []}
    seen = any(entry["agent"] == name for entry in known["transitions"]) or any(
        entry["agent"] == name and not entry["first_start"] for record in known["records"] for entry in record["coverage"])
    fresh = row is None and observed["shell"] and not seen
    desired = target(item)
    old_tier = (row or {}).get("tier") or {}
    transition = not fresh and (row is None or item["context"] != "retain" or row.get("role") != desired["role"]
                               or row.get("task") != desired["task"] or old_tier.get("model") != desired["model"]
                               or old_tier.get("effort") != desired["effort"])
    result = {"schema_version": notes.SCHEMA_VERSION, "agent": name, "source": source, "target": desired,
              "first_start": fresh, "transition_required": transition}
    notes.validate_coverage([result])
    return result


def _usable_coverage(index, descriptor):
    if descriptor["source"]["observation"]["readiness"] not in READY_STATES | {"shell"}:
        return None
    for record in reversed(index["records"]):
        for saved in record["coverage"]:
            if saved == descriptor:
                return record["id"]
    return None


def check(path, state, client, agents, value, at, *, allow_pending=False):
    normalized = request(value)
    index = notes.load(path, allow_pending=allow_pending)
    coverage = [describe(state, client, agents, item, index) for item in normalized["transitions"]]
    existing = bool(state["assignments"]) or any(not item["first_start"] for item in coverage)
    daily = notes.cadence(index, at, existing_work=existing)
    missing = [item["agent"] for item in coverage if item["transition_required"] and not _usable_coverage(index, item)]
    return {"schema_version": notes.SCHEMA_VERSION, "state_path": str(notes.canonical_state(path)),
            "checked_at": notes.utc(at), "request": normalized, "coverage": coverage,
            "cadence": daily, "due": daily["due"] or bool(missing), "missing_coverage": missing}


class Guard:
    """One active dispatch, serialized by its caller's canonical sidecar lock."""

    def __init__(self, path, state, client, agents, at, *, task=None, retain=False, no_clear=False):
        self.path, self.state, self.client, self.agents, self.at = path, state, client, agents, notes.utc(at)
        self.task, self.retain, self.no_clear = task, retain, no_clear
        self.requests = {}
        self.original = {}

    def _item(self, step):
        tier = step.get("tier") or {}
        return {"agent": step["agent"], "role": step["role"], "model": tier.get("model"), "effort": tier.get("effort"),
                "context": "retain" if self.retain or self.no_clear else "clear", "task": self.task,
                "brief": step["brief"], "common": step["common"], "report": None, "unavailable": None, "pane": step["pane_id"]}

    def _known_report(self, item, index):
        _offset, row, _dispatch = _prior(self.state, item["agent"])
        row_digest = notes.digest(row) if row else None
        for record in reversed(index["records"]):
            for descriptor in record["coverage"]:
                if descriptor["agent"] == item["agent"] and descriptor["source"]["assignment_digest"] == row_digest:
                    report = descriptor["source"].get("report")
                    return {**item, "report": report["path"] if report else None,
                            "unavailable": descriptor["source"].get("unavailable")}
        return item

    def _bridge(self, index, current):
        for row in reversed(index["transitions"]):
            original = row["descriptor"]
            if (row["agent"] != current["agent"] or original["source"]["assignment_digest"] != current["source"]["assignment_digest"]
                    or original["source"]["dispatch_evidence"] != current["source"]["dispatch_evidence"]):
                continue
            if {key: value for key, value in row["incoming"].items() if key != "readiness"} != {key: value for key, value in current["source"]["observation"].items() if key != "readiness"}:
                continue
            desired, saved = current["target"], original["target"]
            exact = saved == desired
            judge_handoff = (saved["context"] == "start" and desired["context"] == "retain"
                             and saved["role"] == desired["role"] == "judge"
                             and all(saved[key] == desired[key] for key in ("model", "effort", "task")))
            report = original["source"]["report"]
            if (exact or judge_handoff) and report == current["source"]["report"] and (report is None or notes.current_receipt(report)):
                return row
        return None

    def _require(self, item, *, allow_bridge=True):
        notes.require_no_pending(self.path)
        index = notes.load(self.path)
        item = self._known_report(item, index)
        current = describe(self.state, self.client, self.agents, item, index)
        daily = notes.cadence(index, self.at, existing_work=bool(self.state["assignments"]) or not current["first_start"])
        covered = not current["transition_required"] or _usable_coverage(index, current)
        if allow_bridge and self._bridge(index, current):
            covered = True
        if daily["due"] or not covered:
            raise UsageError("A retrospective is due before this worker transition. Run retro-check with the provided request, write the lead's synthesis, and use retro-record before retrying the same dispatch.",
                             {"daily": daily, "request": {"transitions": [item]}, "coverage": [current]})
        if current["source"]["observation"]["readiness"] not in READY_STATES | {"shell"}:
            raise AgentBusyError("Retrospective cannot authorize input to a busy or blocked worker; wait for readiness without interrupting it.", {})
        bridge = self._bridge(index, current) if allow_bridge else None
        self.original[item["agent"]] = bridge["descriptor"] if bridge else current
        return current

    def preflight(self, steps, _statuses=None):
        for step in steps:
            self.requests[step["agent"]] = self._item(step)
        # Refuse the whole batch before its first reservation or input.
        notes.require_no_pending(self.path)
        index = notes.load(self.path)
        items = [self._known_report(item, index) for item in self.requests.values()]
        current = [describe(self.state, self.client, self.agents, item, index) for item in items]
        daily = notes.cadence(index, self.at, existing_work=bool(self.state["assignments"]) or any(not row["first_start"] for row in current))
        missing = [row["agent"] for row in current if row["transition_required"] and not _usable_coverage(index, row) and not self._bridge(index, row)]
        if daily["due"] or missing:
            raise UsageError("A retrospective is due before dispatch. Save the provided request, run retro-check, record the lead's completed synthesis with retro-record, then retry this dispatch.",
                             {"daily": daily, "missing_coverage": missing, "request": {"transitions": items}, "coverage": current})
        self.original = {row["agent"]: (self._bridge(index, row) or {}).get("descriptor", row) for row in current}

    def before(self, step):
        current = self._require(self.requests[step["agent"]])
        self.original.setdefault(step["agent"], current)

    def after_transition(self, step, *, launch_proof=None):
        """Bind only our submitted clear or verified launch to its resulting identity."""
        item = self.requests[step["agent"]]
        original = self.original[step["agent"]]
        observed = _observation(self.client, item["agent"], self.agents[item["agent"]].kind, item["pane"], agent=self.agents[item["agent"]])
        if launch_proof is None:
            expected = original["source"]["observation"]
            if observed["process"] != expected["process"] or observed["pane_id"] != expected["pane_id"]:
                raise HerdrError("Worker process changed during its clear; refresh retrospective evidence before further input.", {})
        elif observed["process"]["argv"] != launch_proof["argv"] or (launch_proof.get("pid") is not None and observed["process"]["pid"] != launch_proof["pid"]):
            raise HerdrError("Worker changed after its verified launch; refresh retrospective evidence before further input.", {})
        notes.require_no_pending(self.path)
        index = notes.load(self.path)
        row = {"schema_version": notes.SCHEMA_VERSION, "at": self.at, "agent": item["agent"],
               "descriptor": original, "incoming": observed}
        row["id"] = notes.digest({key: value for key, value in row.items() if key != "at"})
        if not any(previous["id"] == row["id"] for previous in index["transitions"]):
            index["transitions"].append(row)
            save_state(notes.directory(self.path) / "index.json", index)

    def before_launch(self, step):
        """After our termination, recheck evidence before starting in its shell."""
        notes.require_no_pending(self.path)
        index = notes.load(self.path)
        original = self.original[step["agent"]]
        item = self._known_report(self.requests[step["agent"]], index)
        _offset, row, _dispatch = _prior(self.state, item["agent"])
        current_report = notes.receipt(item["report"]) if item["report"] else None
        row_digest = notes.digest(row) if row else None
        if row_digest != original["source"]["assignment_digest"] or _dispatch_evidence(self.state, _dispatch, item["unavailable"]) != original["source"]["dispatch_evidence"]:
            raise UsageError("Outgoing assignment changed during relaunch; refresh retrospective coverage before starting.", {})
        if target(item) != original["target"] or current_report != original["source"]["report"] or not _usable_coverage(index, original):
            raise UsageError("Retrospective evidence changed during relaunch; refresh the note and target before starting.", {})
        observed = _observation(self.client, item["agent"], self.agents[item["agent"]].kind, item["pane"], starting=True)
        if not observed["shell"]:
            raise HerdrError("Relaunch pane no longer contains only its shell; no worker was started.", {})

    def before_start(self, item):
        self.requests[item["agent"]] = item
        current = self._require(item)
        if current["source"]["observation"]["readiness"] not in READY_STATES | {"shell"}:
            raise AgentBusyError("Retrospective cannot authorize replacing a busy or blocked worker; wait for its confirmed report and readiness.", {})
        if not current["source"]["observation"]["shell"]:
            raise UsageError("start-judge requires a shell pane; use apply for an existing worker's verified relaunch instead of starting into its TUI.", {})
        self.original[item["agent"]] = current
        if current["first_start"]:
            notes.require_no_pending(self.path)
            index = notes.load(self.path)
            notes.establish_baseline(self.path, index, self.at)
