"""Test doubles shared by the transport, measure, and CLI tests.

`FakeRunner` is a scripted stand-in for `subprocess.run`: it matches a call by
the first few argv tokens, records every invocation, and returns a canned
result. Nothing here starts a process.
"""

import shlex
import json


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

    def set(self, prefix, stdout="", returncode=0, stderr=""):
        self.responses[prefix] = FakeCompleted(returncode, stdout, stderr)
        return self

    def __call__(self, argv):
        self.calls.append(list(argv))
        joined = shlex.join(argv[1:])
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
