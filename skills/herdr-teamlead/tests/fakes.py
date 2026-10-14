"""Test doubles shared by the transport, measure, and CLI tests.

`FakeRunner` is a scripted stand-in for `subprocess.run`: it matches a call by
the first few argv tokens, records every invocation, and returns a canned
result. Nothing here starts a process.

The context managers at the bottom fake the other process boundaries the
suites exercise -- the filesystem, process spawning, process locks and the
network -- so a test induces a failure, or forbids an effect, at the syscall
wrapper instead of replacing a teamlead function.
"""

import contextlib
import fcntl
import os
import shlex
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch


class FakeCompleted:
    """The subset of subprocess.CompletedProcess the transport reads."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """A scripted runner. Responses are keyed by an argv prefix.

    Keys are shell-quoted argv prefixes with the binary dropped, e.g.
    ``"agent get claude"``. The longest matching prefix wins, so a specific
    ``agent read claude --source visible`` beats a general ``agent read``.
    """

    def __init__(self, responses=None, raises=None):
        self.responses = dict(responses or {})
        self.raises = raises or {}
        self.calls = []
        self.observed_agents = {}
        #: True between a terminate and the next start, when Herdr has released
        #: the stopped worker's name.
        self.killed = False

    def set(self, prefix, stdout="", returncode=0, stderr=""):
        self.responses[prefix] = FakeCompleted(returncode, stdout, stderr)
        return self

    def __call__(self, argv):
        self.calls.append(list(argv))
        joined = shlex.join(argv[1:])
        # Herdr releases a stopped worker's name: once its process is killed,
        # `agent get` answers agent_not_found until the seat is started again.
        # A scripted record that outlives the kill would keep the name reserved
        # forever, which no real relaunch sees (#379).
        if joined.startswith("kill -TERM") or joined.startswith("-TERM"):
            self.killed = True
        elif joined.startswith("agent start"):
            self.killed = False
        elif self.killed and joined.startswith("agent get"):
            return FakeCompleted(1, "", json.dumps(
                {"error": {"code": "agent_not_found", "message": "agent target not found"}}))
        for prefix in sorted(self.raises, key=len, reverse=True):
            if joined.startswith(prefix):
                raise self.raises[prefix]
        best = None
        for prefix in self.responses:
            if joined.startswith(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            # Existing dispatch fixtures describe native workers via agent get.
            # Supply their normal YOLO foreground argv unless a test scripts
            # process-info explicitly to exercise missing or restrictive proof.
            if argv[1:4] == ["pane", "process-info", "--pane"] and argv[4] in self.observed_agents:
                agent = self.observed_agents[argv[4]]
                flags = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox",
                         "grok": "--always-approve"}
                kind = agent["agent"]
                if kind in flags:
                    return FakeCompleted(stdout=json.dumps({"result": {"process_info": {"pane_id": argv[4],
                        "foreground_processes": [{"name": kind, "pid": 200, "argv": [kind, flags[kind]]}]}}}))
            raise AssertionError(
                "FakeRunner has no scripted response for: {}\nscripted: {}".format(
                    joined, sorted(self.responses)
                )
            )
        response = self.responses[best]
        if isinstance(response, PhasedReads):
            response = response.next_completed(self)
        if isinstance(response, ScriptedReads):
            response = response.next_completed()
        if argv[1:3] == ["agent", "get"] and response.returncode == 0:
            try:
                agent = json.loads(response.stdout).get("result", {}).get("agent", {})
            except json.JSONDecodeError:
                agent = {}
            if isinstance(agent, dict) and agent.get("pane_id"):
                self.observed_agents[agent["pane_id"]] = agent
        return response

    def commands(self):
        """Every call as a shell string, binary dropped."""
        return [shlex.join(argv[1:]) for argv in self.calls]

    #: Every argv prefix that puts bytes into somebody's terminal.
    WRITE_PREFIXES = (
        "agent prompt ",
        "agent send-keys ",
        "pane send-text ",
        "pane send-keys ",
        "pane run ",
    )

    def writes(self):
        """Only the calls that write to an agent's terminal."""
        return [
            command
            for command in self.commands()
            if command.startswith(self.WRITE_PREFIXES)
        ]

    def pasted_prompts(self):
        """Text delivered through `agent prompt`, which pastes it.

        A slash command must never appear here: pasted into a TUI with
        bracketed paste on, it lands as a chat message rather than a command.
        """
        return [
            command[len("agent prompt ") :]
            for command in self.commands()
            if command.startswith("agent prompt ")
        ]


class ScriptedReads:
    """A response that yields a different stdout on each successive call.

    Stands in for a pane that takes a few reads to finish painting. The last
    entry repeats once the script runs out, so an over-long poll is stable.
    The runner materializes one FakeCompleted per call, so reading `.stdout`
    twice within a call (tracing does) never advances the script.
    """

    def __init__(self, texts):
        self._texts = list(texts)
        self._index = 0

    def next_completed(self):
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        return FakeCompleted(0, text, "")


class PhasedReads:
    """A response that changes when the worker's terminal receives input.

    `phases` is a list of `(command_prefix, texts)`. The first phase's prefix
    is None; a later phase starts once the runner has recorded a command
    starting with its prefix (a clear, a prompt, a kill, a start), after the
    previous phase's command. Within a phase, reads walk `texts` and the last
    one repeats. However many read-only probes run between two actions
    (readiness, retrospective evidence), each sees the same worker.
    """

    def __init__(self, phases):
        self._phases = [(prefix, list(texts)) for prefix, texts in phases]
        self._reads = [0] * len(self._phases)

    def _phase(self, runner):
        commands, current, cursor = runner.commands(), 0, 0
        for index, (prefix, _texts) in enumerate(self._phases[1:], start=1):
            match = next((position for position in range(cursor, len(commands))
                          if commands[position].startswith(prefix)), None)
            if match is None:
                break
            current, cursor = index, match + 1
        return current

    def next_completed(self, runner):
        phase = self._phase(runner)
        texts = self._phases[phase][1]
        text = texts[min(self._reads[phase], len(texts) - 1)]
        self._reads[phase] += 1
        return FakeCompleted(0, text, "")


#: The composer row each agent draws, keyed by the glyph in its config.
COMPOSER_GLYPHS = {"claude": "❯ ", "codex": "› ", "grok": "│ ❯"}


def composer_row(name, held=""):
    """One rendered composer row for `name`, holding `held` (empty by default)."""
    glyph = COMPOSER_GLYPHS[name]
    if name == "grok":
        return "  {}{:<40}│".format(glyph, held)
    return "  {}{}".format(glyph, held)


def composer_screen(name, screen="idle transcript", held=""):
    """A viewport: some content, then the composer row last."""
    return "{}\n{}\n".format(screen, composer_row(name, held))


#: A transcript showing the assignment as a user message, which is what
#: `send_message` looks for before calling a hand-off started.
LANDED_SCREEN = "> New assignment from the team lead. Your role for this task is DEVELOPER."

#: The default read sequence for a full apply: the screen before the clear,
#: the screen after it, the composer check, then the landed assignment.
DEFAULT_COMPOSER_SCREENS = ("before", "after", "after", LANDED_SCREEN)


def composer_reads(name, screens=DEFAULT_COMPOSER_SCREENS, held=""):
    """A ScriptedReads walking `screens`, composer empty unless `held` given.

    The last screen repeats, so an over-long sequence of reads is stable.
    """
    return ScriptedReads([composer_screen(name, screen, held) for screen in screens])


def agent_json(name, status, pane_id, session_id=None):
    """A minimal `herdr agent get` response body."""
    payload = (
        '{"id":"cli:agent:get","result":{"type":"agent_info","agent":'
        '{"agent":"%s","name":"%s","agent_status":"%s","pane_id":"%s",'
        '"workspace_id":"w1","tab_id":"w1:t1"}}}' % (name, name, status, pane_id)
    )
    if session_id is not None:
        result = json.loads(payload)
        result["result"]["agent"]["agent_session"] = {
            "source": "herdr:" + name, "agent": name, "kind": "id", "value": session_id,
        }
        return json.dumps(result)
    return payload


def ok_json(kind="ok"):
    """A generic successful control response."""
    return '{"id":"cli:test","result":{"type":"%s"}}' % kind


@contextlib.contextmanager
def failing_replace(predicate, message="injected replace failure"):
    """Fail `os.replace` -- the commit step of every atomic state write.

    `predicate(source, target)` receives both paths; the temporary `source`
    is still readable, so a predicate can inspect the bytes about to land.
    Every other replace runs for real.
    """
    real = os.replace

    def replace(source, target, *args, **kwargs):
        if predicate(Path(source), Path(target)):
            raise OSError(message)
        return real(source, target, *args, **kwargs)

    with patch.object(os, "replace", replace):
        yield


def replaces_into(path):
    """A `failing_replace` predicate matching writes that land on `path`."""
    path = Path(path)
    return lambda _source, target: target == path


@contextlib.contextmanager
def forbid_writes(reason="unexpected filesystem write"):
    """Raise AssertionError at every atomic-write syscall (mkstemp, replace, link)."""
    with patch.object(tempfile, "mkstemp", side_effect=AssertionError(reason)), \
            patch.object(os, "replace", side_effect=AssertionError(reason)), \
            patch.object(os, "link", side_effect=AssertionError(reason)):
        yield


@contextlib.contextmanager
def forbid_reads(*paths, reason="unexpected read"):
    """Raise AssertionError when any of `paths` is read through pathlib."""
    forbidden = {Path(path).expanduser().resolve() for path in paths}
    originals = {name: getattr(Path, name) for name in ("read_text", "read_bytes", "open")}

    def guarded(name):
        def method(self, *args, **kwargs):
            if self.expanduser().resolve() in forbidden:
                raise AssertionError("{}: {}".format(reason, self))
            return originals[name](self, *args, **kwargs)
        return method

    with contextlib.ExitStack() as stack:
        for name in originals:
            stack.enter_context(patch.object(Path, name, guarded(name)))
        yield


@contextlib.contextmanager
def forbid_processes(reason="unexpected child process"):
    """Raise AssertionError when anything spawns a process (Herdr, git, ps)."""
    with patch.object(subprocess, "Popen", side_effect=AssertionError(reason)):
        yield


@contextlib.contextmanager
def forbid_locks(reason="unexpected process lock"):
    """Raise AssertionError when anything takes an OS file lock."""
    with patch.object(fcntl, "flock", side_effect=AssertionError(reason)):
        yield


@contextlib.contextmanager
def forbid_network(reason="unexpected network access"):
    """Raise AssertionError when anything opens a socket."""
    with patch.object(socket, "socket", side_effect=AssertionError(reason)):
        yield
