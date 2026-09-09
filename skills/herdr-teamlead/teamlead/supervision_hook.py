"""Native Claude/Codex Stop contract for an explicitly bound Herdr lead only.

stdin: native Stop JSON containing cwd and session_id and/or transcript_path.
stdout: {decision:block,reason:...} only for the matching bound lead with
unhandled events, active observation obligations, or unreadable owner state.
Workers, other native sessions, and never-bound sessions produce no output.
No writes, worker contact, process termination, or acknowledgement occurs.

stop_hook_active does not waive supervision: an explicit evidence-backed hold
with dispositions for every active assignment is the supported pause boundary.
"""

import json
import os
import sys

from . import supervision as store
from . import supervision_runtime as runtime
from .errors import TeamLeadError


def block(reason):
    return {"decision": "block", "reason": "Herdr supervision — " + reason}


def check(payload, environ, at, *, root=None, probe=runtime.process_identity):
    if not environ.get("HERDR_ENV") or not environ.get("HERDR_PANE_ID") or not isinstance(payload, dict):
        return None
    if not isinstance(payload.get("cwd"), str) or not payload["cwd"]:
        return None
    for kind, field in (("id", "session_id"), ("path", "transcript_path")):
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            continue
        who = store.identity(value, payload["cwd"], environ["HERDR_ENV"], kind=kind, pane_id=environ["HERDR_PANE_ID"])
        path = store.binding_path(who, root)
        try:
            binding = store.read_json(path)
            if binding is None:
                continue
            if (not isinstance(binding, dict) or binding.get("schema_version") != 1
                    or binding.get("identity") != who or not isinstance(binding.get("state_path"), str)
                    or type(binding.get("generation")) is not int or binding["generation"] < 1):
                return block("The exact lead binding is unreadable. Restore {} and reconcile its saved state before stopping.".format(path))
            data = store.load(binding["state_path"])
            if data["binding"] is None:
                return block("This bound lead's supervision state is missing. Restore {} before stopping; missing state cannot prove its work was resolved.".format(store.store_path(binding["state_path"])))
            if data["binding"]["identity"] != who:
                if binding["generation"] >= data["binding"]["generation"]:
                    return block("This lead's native binding handoff is incomplete. Retry supervision-bind for the same owner state before stopping.")
                continue
            events = store.pending(data)
            active = [row["id"] for row in data["members"] if row["active"]]
            if not events and store.held(data):
                return None
            if not events and not active:
                return None
            health = runtime.health(data, at, probe)
            return block("{} active assignment(s), {} unhandled event(s); watcher is {}. Run supervision-drain, reconcile report/ledger evidence, acknowledge handled outcomes, and keep awaiting the foreground supervision-watch handle. To pause for the user or hand off, save supervision-hold with a disposition and evidence for every active assignment. State: {}".format(len(active), len(events), health["state"], binding["state_path"]))
        except TeamLeadError as exc:
            return block("Cannot verify this bound lead's supervision: {} Resume from its saved state before stopping.".format(exc))
    return None


def main():
    # The CLI remains the package's single wall-clock source.
    from .cli import now_iso
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print("herdr-supervision-stop: cannot identify the native session from invalid hook JSON: {}. Restore native hook payload delivery.".format(exc), file=sys.stderr)
        return 0
    bound = False
    try:
        if isinstance(payload, dict) and payload.get("cwd") and os.environ.get("HERDR_ENV") and os.environ.get("HERDR_PANE_ID"):
            for kind, field in (("id", "session_id"), ("path", "transcript_path")):
                if isinstance(payload.get(field), str) and payload[field]:
                    who = store.identity(payload[field], payload["cwd"], os.environ["HERDR_ENV"], kind=kind, pane_id=os.environ["HERDR_PANE_ID"])
                    bound = bound or store.binding_path(who).exists()
        result = check(payload, os.environ, now_iso())
    # outer-boundary-process-contract: native Stop treats nonzero/invalid stdout
    # as no block; emit a structured block for unexpected evaluation errors so
    # a bound lead's supervision does not silently disappear on a traceback.
    except Exception as exc:
        print("herdr-supervision-stop: evaluation failed ({}); inspect the hook installation.".format(type(exc).__name__), file=sys.stderr)
        if bound:
            print(json.dumps(block("The bound lead's supervision could not be evaluated. Restore the hook/state installation and reconcile active work before stopping.")))
        return 0
    if result is not None:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
