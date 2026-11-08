"""The `rex` CLI as the team's execution surface.

Every call goes through an injected `runner` (one argv in, a
`CompletedProcess`-shaped object out) and every event read through an
injected `events` opener, so tests never start Rex. Blocks are addressed by
their full `block:<uuid>` ids inside one session; nothing here renames,
focuses or switches a session the operator did not hand over.
"""

import json
import os
import select
import subprocess
import time

TERMINAL = "com.superlogical.terminal"
CALL_TIMEOUT_SEC = 30
#: Program status states (OSC 7501) an agent reports.
READY = ("idle", "done")
FINISHED = ("done", "error")


class RexError(Exception):
    """A Rex call failed; `message` says what to do next."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class Timeout(RexError):
    """A wait ran out its budget."""


def subprocess_runner(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=CALL_TIMEOUT_SEC, check=False)


def events_opener(argv):
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def lua_json(value):
    """A Lua expression that decodes `value` from JSON in a long bracket."""
    text = json.dumps(value)
    level = 0
    while "]" + "=" * level + "]" in text:
        level += 1
    eq = "=" * level
    return "rex.json.decode([{0}[{1}]{0}])".format(eq, text)


class Rex:
    def __init__(self, binary="rex", runner=subprocess_runner, events=events_opener, clock=time.monotonic):
        self.binary = binary
        self.runner = runner
        self.events = events
        self.clock = clock

    def run(self, args, quiet_payload=False):
        argv = [self.binary] + args
        try:
            done = self.runner(argv)
        except FileNotFoundError:
            raise RexError("`{}` not found — install Rex or pass --rex with its path".format(self.binary)) from None
        except subprocess.TimeoutExpired:
            raise RexError("`rex {}` did not answer within {}s — check the server with `rex whoami`".format(args[0], CALL_TIMEOUT_SEC)) from None
        if done.returncode != 0:
            detail = "(Rex's message withheld: the command carried text)" if quiet_payload else (done.stderr or "").strip()[:300]
            raise RexError("`rex {}` exited {}: {} — check the server with `rex whoami` and the session with `rex ls`".format(args[0], done.returncode, detail))
        return done.stdout

    def json(self, args, quiet_payload=False):
        out = self.run(args, quiet_payload=quiet_payload)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            raise RexError("`rex {}` returned non-JSON output — update Rex and retry".format(args[0])) from None

    # -- sessions ----------------------------------------------------------

    def sessions(self):
        value = self.json(["ls", "--json"])
        return value if isinstance(value, list) else value.get("sessions", [])

    def new_session(self, label, cwd):
        value = self.json(["new", label, "--cwd", cwd, "--json"])
        return value["session_id"], value["initial_windows"][0]["block_ids"][0]

    def launch(self, session, label, cwd, argv):
        """A new window in `session` running `argv` through the login shell."""
        value = self.json(["run", "-s", session, "-c", cwd, "--label", label, "--focus=false", "--json", "--"] + argv)
        return value["block_ids"][0]

    def close(self, session, block):
        self.run(["block", "close", "-s", session, block])

    # -- one block -----------------------------------------------------------

    def status(self, session, block):
        """The block's root program status record, or None."""
        script = "return rex.block.call('{}', 'program_status', {{block_id = {}}})".format(TERMINAL, lua_json(block))
        value = self.json(["do", "-s", session, "-e", script])
        records = value.get("records") if isinstance(value, dict) else None
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict) and not record.get("id"):
                return record
        return None

    def paste(self, session, block, text):
        payload = {"session_id": session, "block_id": block, "event": {"type": "paste", "payload": {"text": text}}}
        self.run(["api", "call", "block.event", json.dumps(payload)], quiet_payload=True)

    def keys(self, session, block, *keys):
        self.run(["send-key", "-s", session, "-b", block] + list(keys))

    def capture(self, session, block):
        return self.run(["capture", "-s", session, "-b", block, "--trim"])

    # -- waiting -------------------------------------------------------------

    def wait_status(self, session, block, accept, timeout_sec):
        """Block until `accept(record)` holds for the block's program status.

        The event stream opens before the current status is read, so a change
        between the two is never missed. Returns the accepted record.
        """
        stream = self.events([self.binary, "events", "-s", session, "--json"])
        try:
            record = self.status(session, block)
            if accept(record):
                return record
            deadline = self.clock() + timeout_sec
            for line in self._lines(stream, deadline):
                record = self._status_event(line, block)
                if record is not None and accept(record):
                    return record
            raise Timeout("block {} did not reach the awaited state within {}s".format(block, timeout_sec))
        finally:
            stream.terminate()

    def _lines(self, stream, deadline):
        """Lines from the stream until `deadline`.

        Reads the raw descriptor: a buffered `readline` would pull several
        events into Python's buffer, and `select` on the emptied descriptor
        would then sleep past them.
        """
        fd, pending = stream.stdout.fileno(), b""
        while True:
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line.decode("utf-8", "replace")
            left = deadline - self.clock()
            if left <= 0:
                return
            ready, _, _ = select.select([fd], [], [], min(left, 1.0))
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise RexError("the Rex event stream closed — check the server with `rex whoami`, then wait again")
                pending += chunk

    @staticmethod
    def _status_event(line, block):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        inner = ((event.get("payload") or {}).get("event") or {})
        payload = inner.get("payload") or {}
        if inner.get("type") != "block_event" or payload.get("block_id") != block or payload.get("name") != "program_status_changed":
            return None
        status = payload.get("payload") or {}
        if status.get("id"):
            return None
        return status.get("record") or {"state": "cleared"}
