"""Prove native display decoration from the completed assistant source.

The display allowlist is deliberately tiny: Codex's bullet, Grok's five spaces
and Claude Code's record glyph. None is accepted without the same session's
latest completed final message ending in a bare marker. Rows are never joined.
Source formats were verified on Codex 0.153.2, Grok 1.0.13 (Grok 4.6) and
Claude Code 2.1.263, with Herdr 0.8.2. Each kind's source contract lives with
its own reader; Claude's is teamlead/claude_native.py. Unknown integrations,
source formats and incomplete turns stay unconfirmed.

Live probes read only the session reported by Herdr. Owner recovery can prove
one Grok /new turn against the original dispatch fingerprint while retaining
Herdr's contradictory observation. It reads archived evidence, adds a separate receipt, and changes no assignment, dispatch,
negative wait receipt, correction allowance or review verdict.
"""

import hashlib
import json
import os
import re
from fnmatch import fnmatchcase
from pathlib import Path

from . import claude_native
from . import recovery as ledger
from . import supervision
from .errors import UsageError


DISPLAY_PREFIXES = {"codex": ("• ",), "grok": ("     ",), "claude": ("\u23fa ",)}
SESSION_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CONTAINER = re.compile(r"^ {0,3}(?:>|[-+*•][ \t]|[0-9]{1,9}[.)][ \t])")
RECOVERY_INPUTS = {"id", "dispatch", "report", "wait_receipt", "pane", "visible", "source"}
STALE_RECOVERY_INPUTS = RECOVERY_INPUTS | {"plan"}
RECOVERY_ARTIFACTS = {"report", "wait_receipt", "pane", "visible", "source", "brief", "common"}


def bare_final(text, report):
    """The final source row is unindented and outside authored fenced code."""
    if not isinstance(text, str):
        return False
    rows = text.splitlines()
    if not rows or rows[-1] != "REPORT: " + report:
        return False
    fence, length, container = "", 0, False
    for row in rows[:-1]:
        match = FENCE.match(row)
        if match:
            run, tail = match.groups()
            if not fence:
                fence, length, container = run[0], len(run), False
            elif run[0] == fence and len(run) >= length and not tail.strip():
                fence = ""
            continue
        if fence:
            continue
        if not row.strip():
            container = False
        elif CONTAINER.match(row):
            container = True
    # Unmarked rows can be lazy paragraph continuations inside a list/quote.
    # A blank row ends that ambiguity for an unindented final marker.
    return not fence and not container


def decorated_row(visible, kind, report):
    """Return the complete observed row; arbitrary bullets/indentation fail."""
    expected = {prefix + "REPORT: " + report for prefix in DISPLAY_PREFIXES.get(kind, ())}
    for row in visible.splitlines():
        if row in expected:
            return row
        # Grok can omit the right-aligned clock when only the scrollbar fits.
        # The source proof still requires the bare path, with no decoration.
        if kind == "grok" and re.fullmatch(re.escape("     REPORT: " + report) + r" {2,}(?:(?:1[0-2]|[1-9]):[0-5][0-9] [AP]M(?: {2,}█)?|█)", row):
            return row
    return None


def _rows(body):
    try:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return None
    return rows if rows and all(isinstance(row, dict) for row in rows) else None


def codex_final(rows, session):
    """A final_answer plus matching task_complete in the latest started turn."""
    identities = [row.get("payload") for row in rows if row.get("type") == "session_meta"]
    if not identities or any(not isinstance(meta, dict) or meta.get("id") != session for meta in identities):
        return None
    turn, final, complete = None, None, False
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        kind = payload.get("type")
        if row.get("type") == "event_msg":
            if kind == "task_started":
                turn, final, complete = payload.get("turn_id"), None, False
            elif kind == "task_complete":
                complete = bool(turn and payload.get("turn_id") == turn and final
                                and payload.get("last_agent_message") == final)
            elif kind in ("turn_aborted", "error", "user_message"):
                final, complete = None, False
        elif row.get("type") == "response_item":
            if payload.get("role") == "user" or kind in ("function_call", "function_call_output"):
                final, complete = None, False
            elif payload.get("role") == "assistant":
                final, complete = None, False
                content = payload.get("content")
                if (payload.get("phase") == "final_answer" and isinstance(content, list)
                        and content and all(isinstance(item, dict) and item.get("type") == "output_text"
                                            and isinstance(item.get("text"), str) for item in content)):
                    final = "".join(item["text"] for item in content)
    return final if complete else None


def grok_final(rows, session):
    """Join native chunks within one message stream, never rendered rows."""
    final, prompt, stream, complete = "", None, None, False
    for row in rows:
        params = row.get("params")
        if not isinstance(params, dict) or params.get("sessionId") != session:
            return None
        update, meta = params.get("update"), params.get("_meta", {})
        if not isinstance(update, dict) or not isinstance(meta, dict):
            return None
        kind = update.get("sessionUpdate")
        if kind in ("user_message_chunk", "tool_call", "tool_call_update"):
            final, stream, complete = "", None, False
        elif kind == "hook_execution" and update.get("event_name") == "user_prompt_submit":
            final, stream, complete = "", None, False
        elif kind == "agent_message_chunk":
            content = update.get("content")
            if (not isinstance(content, dict) or content.get("type") != "text"
                    or not isinstance(content.get("text"), str) or not meta.get("promptId")
                    or not isinstance(meta.get("streamStartMs"), int)):
                return None
            current = (meta["promptId"], meta["streamStartMs"])
            if stream != current:
                final, stream = "", current
            prompt, complete = meta["promptId"], False
            final += content["text"]
        elif kind == "turn_completed":
            complete = bool(final and update.get("prompt_id") == prompt and update.get("stop_reason") == "end_turn")
        elif kind in ("turn_cancelled", "turn_failed", "error"):
            final, complete = "", False
    return final if complete else None


def source_final(body, kind, session):
    rows = _rows(body)
    if rows is None:
        return None
    parser = {"codex": codex_final, "grok": grok_final, "claude": claude_native.final_message}.get(kind)
    return parser(rows, session) if parser else None


def source_prompt(body, kind, session=None):
    """Read the actual latest user message; quoted assistant instructions fail.

    `session` binds the read to one session and only Claude needs it; Codex and
    Grok prove their own identity inside the parser. A Claude read without it
    matches no session and stays unconfirmed.
    """
    rows = _rows(body)
    if rows is None:
        return None
    if kind == "claude":
        return claude_native.prompt_text(rows, session)
    prompt, in_chunks = None, False
    for row in rows:
        if kind == "codex":
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                return None
            if row.get("type") == "response_item" and payload.get("role") == "user":
                content = payload.get("content")
                if not isinstance(content, list) or not all(isinstance(item, dict) and item.get("type") == "input_text"
                        and isinstance(item.get("text"), str) for item in content):
                    return None
                prompt = "".join(item["text"] for item in content)
        else:
            params = row.get("params", {})
            update = params.get("update", {}) if isinstance(params, dict) else {}
            if not isinstance(update, dict):
                return None
            if update.get("sessionUpdate") == "user_message_chunk":
                content = update.get("content", {})
                if not isinstance(content, dict) or content.get("type") != "text" or not isinstance(content.get("text"), str):
                    return None
                prompt = (prompt or "") + content["text"] if in_chunks else content["text"]
                in_chunks = True
            else:
                in_chunks = False
    return prompt


def native_identity(info):
    ref = info.get("agent_session")
    if (not isinstance(ref, dict) or not isinstance(ref.get("agent"), str) or ref["agent"] not in DISPLAY_PREFIXES
            or ref.get("source") != "herdr:" + ref["agent"] or ref.get("kind") != "id"
            or not isinstance(ref.get("value"), str) or not SESSION_ID.fullmatch(ref["value"])):
        return None
    return {key: ref[key] for key in ("source", "agent", "kind", "value")}


def source_root(kind):
    if kind == "codex":
        return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    if kind == "claude":
        return claude_native.sessions_root()
    return Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))) / "sessions"


def source_path(identity):
    """Resolve the official ID without glob's suppression of directory errors."""
    session, kind = identity["value"], identity["agent"]
    root, matches = source_root(kind), []
    depth_limit = {"codex": 3, "claude": claude_native.SESSION_DEPTH}.get(kind, 2)

    def fail(error):
        raise error

    for directory, directories, files in os.walk(root, onerror=fail):
        path = Path(directory)
        depth = len(path.relative_to(root).parts)
        if depth == depth_limit:
            directories[:] = []
            if kind == "codex":
                matches.extend(path / name for name in files if fnmatchcase(name, "rollout-*-" + session + ".jsonl"))
            elif kind == "claude":
                name = claude_native.transcript_name(session)
                if name in files:
                    matches.append(path / name)
            elif path.name == session and "updates.jsonl" in files:
                matches.append(path / "updates.jsonl")
        elif kind == "grok" and depth == 1:
            directories[:] = [name for name in directories if name == session]
    return matches[0] if len(matches) == 1 else None


def read_native_source(path):
    """Missing files are expected; unreadable or invalid source is a failure."""
    try:
        content = path.read_bytes()
        return content, content.decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError("Cannot read native transcript {}: {}. Restore readable UTF-8 transcript bytes and file permissions before retrying report verification.".format(path, exc), {}) from None


def pane_identity(pane, expected, identity):
    return (pane.get("pane_id") == expected and pane.get("agent_session") == identity
            and isinstance(pane.get("terminal_id"), str) and bool(pane["terminal_id"])
            and type(pane.get("revision")) is int and pane["revision"] >= 0
            and isinstance(pane.get("scroll"), dict) and pane["scroll"].get("offset_from_bottom") == 0
            and pane.get("agent_status") in ("idle", "done"))


def probe(client, agent, pane_id, report, visible, lines):
    """Read-only fallback for a candidate native row; no usable proof is false."""
    result = {"found": False, "reason": "native_marker_unconfirmed"}
    info = client.agent_get(agent)
    identity = native_identity(info)
    if (identity is None or info.get("pane_id") != pane_id
            or info.get("agent_status") not in ("idle", "done")
            or not decorated_row(visible, identity["agent"], report)):
        return result
    before = client.pane_get(pane_id)
    if not pane_identity(before, pane_id, identity):
        return result
    try:
        path = source_path(identity)
    except FileNotFoundError:
        return {**result, "reason": "native_source_unavailable"}
    except OSError as exc:
        raise UsageError("Cannot locate native transcript under {}: {}. Restore readable session directories and search permissions before retrying report verification.".format(source_root(identity["agent"]), exc), {}) from None
    if path is None:
        return {**result, "reason": "native_source_unavailable"}
    source = read_native_source(path)
    if source is None:
        return {**result, "reason": "native_source_unavailable"}
    source_bytes, body = source
    final = source_final(body, identity["agent"], identity["value"])
    if not bare_final(final, report) or not Path(report).is_file():
        return result
    after_visible = client.pane_read(pane_id, lines=lines).rstrip("\n")
    after = client.pane_get(pane_id)
    after_info = client.agent_get(agent)
    if (after_visible != visible or after != before or native_identity(after_info) != identity
            or after_info.get("pane_id") != pane_id or after_info.get("agent_status") not in ("idle", "done")):
        return result
    verified_source = read_native_source(path)
    if verified_source is None:
        return {**result, "reason": "native_source_unavailable"}
    if verified_source[0] != source_bytes or not Path(report).is_file():
        return result
    return {"found": True, "basis": "native_final_source", "native_session": identity,
            "source": {"path": str(path), "sha256": hashlib.sha256(source_bytes).hexdigest()}}


def _json(body, label):
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        raise UsageError("{} must preserve the original JSON evidence; restore its bytes.".format(label), {}) from None
    if not isinstance(value, dict):
        raise UsageError("{} must contain a JSON object.".format(label), {})
    return value


def validate_stale_binding(dispatch, assignment, observed, source):
    """Only the known automatic-clear contradiction may use a separate ID."""
    before = dispatch.get("observed_before", {})
    context = dispatch.get("context_before_send", {})
    result = dispatch.get("result", {})
    if not all(isinstance(row, dict) for row in (before, context, result, assignment)):
        raise UsageError("grok_clear_identity_unproven: restore the owner-written clear and dispatch observations.", {})
    original = native_identity({"agent_session": before.get("context_session")})
    if (not isinstance(observed, dict) or native_identity({"agent_session": observed}) != observed
            or observed.get("agent") != "grok"
            or original != observed or before.get("pane_id") != result.get("pane_id")
            or before.get("context_session", {}).get("pane_id") != result.get("pane_id")
            or not isinstance(source, dict) or set(source) != {"agent", "kind", "value"}
            or source.get("agent") != "grok" or source.get("kind") != "id"
            or not isinstance(source.get("value"), str) or not SESSION_ID.fullmatch(source["value"])
            or source["value"] == observed["value"]
            or any(row.get("cleared") is not True or row.get("clear_reason") != "automatic"
                   or row.get("context_session") is not None for row in (assignment, context, result))):
        raise UsageError("grok_clear_identity_unproven: preserve the original automatic clear, null continuity and stale Herdr observation; do not replace known session proof.", {})


def grok_clear_identity(body, prompt):
    """Prove one fresh native session and one dispatched turn from its contents.

    An explicit archived updates file is the input, never newest-file selection.
    Repeated prompts, multiple identities, failed turns or later turns make the
    source ambiguous even when one assistant message names the requested file.
    """
    rows = _rows(body)
    if rows is None:
        return None
    identity, submitted, user_groups, previous, completions = None, None, 0, None, 0
    for index, row in enumerate(rows):
        params = row.get("params")
        if not isinstance(params, dict) or row.get("method") not in ("session/update", "_x.ai/session/update"):
            return None
        session, update, meta = params.get("sessionId"), params.get("update"), params.get("_meta", {})
        if (not isinstance(session, str) or not SESSION_ID.fullmatch(session)
                or not isinstance(update, dict) or not isinstance(meta, dict)):
            return None
        if identity is None:
            identity = session
        if identity != session:
            return None
        kind = update.get("sessionUpdate")
        event = update.get("event_name") if kind == "hook_execution" else None
        if index == 0 and event != "session_start":
            return None
        if event == "session_start" and index != 0:
            return None
        if event == "user_prompt_submit":
            if submitted is not None or user_groups:
                return None
            submitted = update.get("prompt_id")
            if not isinstance(submitted, str) or not submitted:
                return None
        if kind == "user_message_chunk":
            if not submitted:
                return None
            user_groups += previous != kind
            if user_groups != 1:
                return None
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            if user_groups != 1 or meta.get("promptId") != submitted:
                return None
        if kind in ("turn_failed", "turn_cancelled", "error"):
            return None
        if kind == "turn_completed":
            completions += 1
            if index != len(rows) - 1 or update.get("prompt_id") != submitted or update.get("stop_reason") != "end_turn":
                return None
        previous = kind
    if completions != 1 or user_groups != 1 or source_prompt(body, "grok") != prompt:
        return None
    return {"agent": "grok", "kind": "id", "value": identity}


def stale_grok_source(dispatch, assignment, observed, body, prompt, plan_body, *, report):
    source = grok_clear_identity(body, prompt)
    if source is None:
        raise UsageError("grok_source_ambiguous: require one original fresh native session and its single completed dispatched turn; preserve the negative receipt.", {})
    validate_stale_binding(dispatch, assignment, observed, source)
    plan = _json(plan_body, "original plan")
    assignments = plan.get("assignments", plan)
    rounds = plan.get("rounds", {}) if "assignments" in plan else {}
    if (not isinstance(assignments, dict) or assignments.get(dispatch["role"]) != dispatch["agent"]
            or not isinstance(rounds, dict)):
        raise UsageError("grok_dispatch_unbound: restore the original plan assigning this worker and role.", {})
    options = {"task": dispatch["task"], "fix_round": dispatch["fix_round"],
               "plan": dispatch.get("plan"), "work": dispatch.get("work"), "rounds": rounds,
               "retain_context": False, "no_clear": False}
    task_context = {key: options[key] for key in ("task", "fix_round", "plan", "work")}
    if plan.get("task_context") is not None and plan["task_context"] != task_context:
        raise UsageError("grok_dispatch_unbound: original plan names different task or correction bounds; restore its dispatch inputs.", {})
    _, fingerprint = ledger.dispatch_identity(dispatch["task"], dispatch["role"], dispatch["agent"],
        dispatch["fix_round"], {"common": dispatch["common"], dispatch["role"]: dispatch["brief"]}, options=options)
    # A legacy dispatch preserved the bare fingerprint; a bound round's dispatch
    # preserved it wrapped with the exact report path it was dispatched with.
    # The requested report must reproduce that binding; no other path can.
    if dispatch["fingerprint"] not in (fingerprint, supervision.report_bound_fingerprint(fingerprint, report)):
        raise UsageError("grok_dispatch_unbound: original plan, dispatch options, report path or briefing bytes differ from the preserved fingerprint; restore the originals.", {})
    return source


def recover(store, assignments, data, at):
    """Append delivery proof for an applied dispatch whose old wait was negative."""
    if not isinstance(data, dict) or set(data) not in (RECOVERY_INPUTS, STALE_RECOVERY_INPUTS):
        raise UsageError("recover-report requires id, dispatch, report, wait_receipt, pane, visible and source, with optional plan for stale Grok identity recovery; preserve the original evidence.", {})
    ledger.text(data["id"], "recovery id")
    dispatch = ledger._item(store["dispatches"], data["dispatch"], "dispatch")
    if dispatch["status"] != "applied":
        raise UsageError("Recover delivery only for a confirmed applied dispatch; reconcile uncertain transport first.", {})
    index = dispatch["assignment_index"]
    assignment = assignments[index]
    receipts, bodies = {}, {}
    for key in ("report", "wait_receipt", "pane", "visible", "source"):
        receipts[key], bodies[key] = ledger.receipt(data[key])
    receipts["brief"], brief = ledger.receipt(dispatch.get("brief"))
    receipts["common"], _common = ledger.receipt(dispatch.get("common"))
    if "REPORT: " + data["report"] not in brief.splitlines():
        raise UsageError("The original dispatch brief does not assign this report path; restore the matching brief and report.", {})
    negative = _json(bodies["wait_receipt"], "negative wait receipt")
    envelope = _json(bodies["pane"], "archived pane").get("result")
    if not isinstance(envelope, dict):
        raise UsageError("Archived pane JSON needs the original result object; restore the pane get evidence.", {})
    pane = envelope.get("pane")
    identity = native_identity(pane) if isinstance(pane, dict) else None
    original_identity = native_identity({"agent_session": assignment.get("context_session")})
    if assignment.get("context_session") is not None and original_identity is None:
        raise UsageError("The original native-session proof uses an unsupported shape; preserve it and update the evidence adapter before recovery.", {})
    # Old nondeveloper dispatches deliberately recorded null continuity. Bind
    # their actual native user prompt to the saved dispatch instead of filling
    # that historical null with a later observation.
    from .assign import assignment_text, tiered_prompt
    prompt = assignment_text(dispatch["role"], dispatch["common"], dispatch["brief"])
    tier = dispatch["result"].get("tier")
    if tier is not None:
        prompt, prompt_hash = tiered_prompt(prompt, tier, dispatch["common"], dispatch["brief"])
        if tier.get("prompt_hash") != prompt_hash:
            raise UsageError("Original briefing bytes differ from the dispatch's recorded prompt hash; restore them before recovery.", {})
    if (negative.get("found") is not False or negative.get("agent") != dispatch["agent"]
            or negative.get("report_path") != data["report"] or negative.get("state") not in ("idle", "done")
            or "marker unconfirmed" not in str(negative.get("reason", ""))):
        raise UsageError("Recovery requires this completed dispatch's original negative marker-unconfirmed wait receipt; refusals are not delivery.", {})
    source_session = None
    if identity is not None and identity["agent"] == "grok" and "plan" in data:
        competing = [row for row in store["dispatches"] if row["id"] != dispatch["id"]
                     and row["status"] != "not_sent" and all(row.get(key) == dispatch.get(key)
                         for key in ("role", "common", "brief"))]
        if competing:
            raise UsageError("grok_dispatch_ambiguous: another dispatch used the same original prompt paths; preserve both outcomes instead of choosing a transcript.", {})
        receipts["plan"], plan_body = ledger.receipt(data["plan"])
        source_session = stale_grok_source(dispatch, assignment, identity, bodies["source"], prompt, plan_body,
                                           report=data["report"])
    elif "plan" in data:
        if identity is None:
            raise UsageError("Archived pane JSON has no supported native session identity; restore the original pane get evidence.", {})
        raise UsageError("Stale-ID recovery requires an original Grok automatic clear; preserve the original evidence.", {})
    final_session = source_session["value"] if source_session else identity["value"] if identity else None
    if (identity is None or original_identity is not None and identity != original_identity
            or not isinstance(pane, dict) or not pane_identity(pane, dispatch["result"].get("pane_id"), identity)
            or not decorated_row(bodies["visible"], identity["agent"], data["report"])
            or source_prompt(bodies["source"], identity["agent"], final_session) != prompt
            or not bare_final(source_final(bodies["source"], identity["agent"], final_session), data["report"])):
        raise UsageError("Archived pane and completed native source do not prove this report's bare final marker; preserve the negative receipt.", {})
    prior = next((row for row in store["delivery_recoveries"] if row["id"] == data["id"]), None)
    if prior:
        if (prior["input"] != data or prior["receipts"] != receipts or prior["native_session"] != identity
                or source_session is not None and prior.get("source_session") != source_session):
            raise UsageError("Recovery identity names different evidence; preserve the original receipt and resolve the conflict.", {})
        return prior
    if any(row["dispatch"] == data["dispatch"] for row in store["delivery_recoveries"]):
        raise UsageError("This dispatch already has delivery recovery; reuse its original recovery identity.", {})
    record = {"schema_version": 1, "id": data["id"], "at": at, "task": dispatch["task"],
              "dispatch": data["dispatch"], "assignment_index": index, "input": data,
              "receipts": receipts, "native_session": identity, "found": True,
              "basis": "archived_native_final_source", "native_session_proof": None,
              "grants_review_approval": False}
    if source_session is not None:
        for saved in receipts.values():
            current, _body = ledger.receipt(saved["path"])
            if current != saved:
                raise UsageError("grok_evidence_changed: archive stable original evidence before retrying recovery.", {})
        record.update(schema_version=2, source_session=source_session, basis="archived_grok_clear_source")
    store["delivery_recoveries"].append(record)
    ledger._event(store, at, "report_delivery_recovered", dispatch["task"], {"recovery": data["id"], "dispatch": data["dispatch"]})
    return record


def validate_recoveries(store, assignments):
    dispatches = set()
    for row in store["delivery_recoveries"]:
        dispatch = ledger._item(store["dispatches"], row["dispatch"], "dispatch")
        index = row["assignment_index"]
        if type(index) is not int or not 0 <= index < len(assignments) or index != dispatch["assignment_index"]:
            raise UsageError("Delivery recovery has no matching assignment row; restore the owner-written ledger without changing history.", {})
        stale = row["schema_version"] == 2
        expected_inputs = STALE_RECOVERY_INPUTS if stale else RECOVERY_INPUTS
        expected_artifacts = RECOVERY_ARTIFACTS | {"plan"} if stale else RECOVERY_ARTIFACTS
        if stale:
            validate_stale_binding(dispatch, assignments[index], row["native_session"], row["source_session"])
        if (dispatch["status"] != "applied" or row["task"] != dispatch["task"] or row["found"] is not True
                or row["grants_review_approval"] is not False or row["basis"] != ("archived_grok_clear_source" if stale else "archived_native_final_source")
                or row["native_session_proof"] is not None
                or native_identity({"agent_session": row["native_session"]}) is None
                or assignments[index].get("context_session") is not None and
                row["native_session"] != native_identity({"agent_session": assignments[index]["context_session"]})):
            raise UsageError("Delivery recovery differs from its preserved dispatch; restore the owner-written ledger.", {})
        if (not isinstance(row["input"], dict) or set(row["input"]) != expected_inputs
                or row["input"]["id"] != row["id"] or row["input"]["dispatch"] != row["dispatch"]
                or not isinstance(row["receipts"], dict) or set(row["receipts"]) != expected_artifacts
                or row["dispatch"] in dispatches):
            raise UsageError("Delivery recovery must preserve its unique dispatch and complete evidence receipts.", {})
        dispatches.add(row["dispatch"])
        for receipt in row["receipts"].values():
            ledger.validate_receipt(receipt)
        for key in expected_artifacts:
            path = dispatch.get(key) if key in ("common", "brief") else row["input"][key]
            if row["receipts"][key]["path"] != path:
                raise UsageError("Delivery recovery artifact paths differ from their original inputs; restore its owner-written record.", {})
