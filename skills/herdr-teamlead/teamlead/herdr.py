"""Transport for the `herdr` CLI.

Two halves, deliberately split:

* **argv builders** are pure functions from arguments to an argv list. They are
  what `apply --dry-run` prints, so the commands shown are the same lists the
  live path would execute -- there is no second, drifting copy of the command
  shapes.
* **executions** run one of those argv lists through an injected `runner`.
  Every caller in this package receives a client rather than importing one, so
  tests hand in a fake runner and never touch the real binary.

Herdr's I/O contract, from `herdr --skill`:

* most control commands return JSON on stdout
* `agent read` returns raw pane text on stdout, not JSON
* `pane send-text` and `pane send-keys` exit 0 with EMPTY stdout on success
* server errors are JSON on stderr with exit status 1
* CLI syntax errors exit with status 2

Commands whose result teamlead actually consumes are parsed strictly. Commands
teamlead only fires and forgets go through `_run_optional_json`, which treats
exit 0 with no output as success -- the exit code is the contract there, and
demanding a JSON body it never reads only invents failures.

Pass a `trace` sink to record every invocation -- argv, exit status, and its
stdout and stderr -- so a live run that goes wrong is diagnosable without
guessing at what teamlead sent. `--trace` or `TEAMLEAD_TRACE=1` turns it on.

Traced text is never raw. Pane output is arbitrary text an agent drew on a
terminal, and an agent that just ran `gh auth status` or echoed an env var has
credentials on screen; argv can carry them too. Every traced field goes through
`scrub_for_trace` first, which masks the credential shapes in SECRET_PATTERNS
and then caps the field at TRACE_FIELD_CAP_BYTES with an explicit truncation
marker. Redaction runs BEFORE the cap so a truncation cannot slice a token in
half and leave the front of it standing.
"""

import json
import os
import re
import shlex
import subprocess

from .errors import HerdrError

#: Default per-call wall-clock ceiling for the subprocess itself. Herdr's own
#: --timeout governs how long *it* waits; this guards against a wedged binary.
SUBPROCESS_TIMEOUT_SEC = 300

#: How long to wait for a usage report's marker to appear after sending the
#: slash command. Bounded on purpose: a missing marker fails loudly.
DEFAULT_MARKER_TIMEOUT_MS = 20000

#: How long to wait for an agent to settle back to idle after `/clear`.
DEFAULT_SETTLE_TIMEOUT_MS = 60000

#: Agent lifecycle states that mean "ready for input".
READY_STATES = frozenset({"idle", "done"})

#: Agent lifecycle states teamlead refuses to write to. No override.
BUSY_STATES = frozenset({"working", "blocked"})

#: The key that submits a line. Named because `send_slash_command` sends it as
#: a keystroke rather than as part of the text.
SUBMIT_KEY = "enter"

#: How an agent's slash commands reach it. Neither is universally right:
#:
#: * `paste` -- `agent prompt`, which delivers through bracketed paste. Grok
#:   reads a pasted `/usage` as a chat message and answers it in prose.
#: * `type` -- `pane send-text` then Enter as a keystroke. Codex opens a
#:   slash-command autocomplete popup on `/`, where the first Enter accepts
#:   the completion and only a second Enter submits, so a typed command sits
#:   in the composer unsent.
#:
#: Per-agent, from config. See `slash_delivery` in config.example.json.
SLASH_DELIVERY_PASTE = "paste"
SLASH_DELIVERY_TYPE = "type"
SLASH_DELIVERIES = (SLASH_DELIVERY_PASTE, SLASH_DELIVERY_TYPE)


#: Environment switch for tracing, equivalent to the `--trace` flag.
TRACE_ENV_VAR = "TEAMLEAD_TRACE"


def default_binary():
    """The herdr executable to invoke, overridable for an unusual install."""
    return os.environ.get("TEAMLEAD_HERDR_BIN", "herdr")


def trace_enabled_in_env(environ=None):
    """True when TEAMLEAD_TRACE is set to something other than 0 or empty."""
    value = (environ if environ is not None else os.environ).get(TRACE_ENV_VAR, "")
    return value.strip() not in ("", "0")


def subprocess_runner(argv):
    """Run `argv` and return the CompletedProcess. The only impure code here."""
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=SUBPROCESS_TIMEOUT_SEC,
        check=False,
        text=True,
    )


#: What a masked value is replaced with. One token, easy to grep for.
SECRET_MASK = "[redacted]"

#: Per-field ceiling for traced text, in BYTES of UTF-8. A pane read can carry
#: a whole viewport, and a trace is a diagnostic, not a transcript.
TRACE_FIELD_CAP_BYTES = 2048

#: Marker appended when the cap bites, naming what was dropped.
TRUNCATION_MARKER = " [truncated {} bytes]"

#: Credential shapes masked before any text reaches the trace sink. Each entry
#: is (pattern, replacement); a replacement keeping a group preserves the
#: non-secret half (the `Bearer` keyword, the assignment's key name) so a trace
#: still shows WHAT was sent without showing the value.
#:
#: This is signature matching, not proof: a shape absent from this list reaches
#: the sink. Add one here rather than at a call site, so every traced field
#: gains it at once.
SECRET_PATTERNS = (
    # GitHub tokens: ghp_ (classic PAT), gho_/ghu_/ghs_/ghr_ (OAuth, user,
    # server, refresh), and the fine-grained github_pat_ form. No leading
    # \b on the high-entropy prefixes: pane text glues a token to whatever
    # printed before it, and a boundary there would let it through. `sk-`
    # keeps its boundary, where task-/disk-/risk- would false-positive.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), SECRET_MASK),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), SECRET_MASK),
    # OpenAI-style API keys, including the sk-proj-/sk-ant- prefixed variants.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), SECRET_MASK),
    # AWS access key ids.
    (re.compile(r"AKIA[0-9A-Z]{12,}"), SECRET_MASK),
    # JWTs (header.payload.signature), which carry claims even unsigned.
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]+"), SECRET_MASK),
    # `Authorization: <value>` headers. The whole value goes, scheme keyword
    # included, so `Authorization: Bearer <token>` cannot leave the token
    # standing behind a masked keyword. Stops at a quote or a JSON delimiter
    # so a header inside a JSON body does not swallow the rest of the object.
    (
        re.compile(r"(?i)\b(authorization\"?\s*[:=]\s*\"?)[^\r\n\"',}]+"),
        r"\1" + SECRET_MASK,
    ),
    # `Bearer <token>` outside a header, keyword kept.
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + SECRET_MASK),
    # key= / token= / password= / secret= assignments, with or without a
    # prefix (api_key, access_token), quoted or bare, in `k=v` and JSON `k: v`
    # form. The key name survives; the value does not. The value class
    # deliberately excludes `[` and `{`, so a config-shaped `"close_keys":
    # ["esc"]` reads through unmangled while `token=abc123` does not.
    (
        re.compile(
            r"(?i)([\w.-]*(?:key|token|password|passwd|secret)[\w.-]*\"?\s*[=:]\s*)"
            r"(\"|')?([A-Za-z0-9_./+=~@-]+)",
        ),
        lambda m: m.group(1) + (m.group(2) or "") + SECRET_MASK,
    ),
)


def redact_secrets(text):
    """Mask every SECRET_PATTERNS match in `text`.

    Signature matching over arbitrary terminal text, so it is a floor rather
    than a guarantee: it removes the shapes that show up in practice and never
    claims the remainder is secret-free.
    """
    if not text:
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def bound_field(text, cap=TRACE_FIELD_CAP_BYTES):
    """Cap `text` at `cap` UTF-8 bytes, naming how many bytes were dropped."""
    if not text:
        return text
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text
    # Decode with errors="ignore" so a cut through a multi-byte character
    # drops that character rather than raising or emitting a replacement.
    kept = raw[:cap].decode("utf-8", errors="ignore")
    return kept + TRUNCATION_MARKER.format(len(raw) - cap)


def scrub_for_trace(text, cap=TRACE_FIELD_CAP_BYTES):
    """Make one field safe to trace: redact first, then bound.

    The order is load-bearing. Bounding first could cut a credential in half
    and emit the front of it, which no later redaction would recognize.
    """
    return bound_field(redact_secrets(text), cap)


def format_argv(argv):
    """Render an argv list as a copy-pasteable shell command."""
    return shlex.join(argv)


def error_code(exc):
    """Herdr's own error code from a failed command, or None.

    `_run` keeps the failed command's stderr in the HerdrError details, and a
    server error is JSON there (`{"error": {"code": ..., "message": ...}}`).
    A caller that must branch on one code -- `agent_not_found` proving a name
    was released -- reads it here instead of matching on prose. Anything that
    is not that shape is None: a syntax error, a wedged binary, or a non-JSON
    stderr carries no code to branch on.
    """
    details = getattr(exc, "details", None)
    stderr = details.get("stderr") if isinstance(details, dict) else None
    if not isinstance(stderr, str) or not stderr.startswith("{"):
        return None
    try:
        payload = json.loads(stderr)
    except json.JSONDecodeError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else None


class HerdrClient:
    """A thin, injectable wrapper over the `herdr` CLI."""

    def __init__(self, binary=None, runner=None, trace=None):
        self.binary = binary or default_binary()
        self._runner = runner if runner is not None else subprocess_runner
        # A callable taking one string, or None for no tracing. Diagnostics go
        # to stderr so stdout stays machine-readable JSON.
        self._trace = trace

    # -- argv builders (pure) ------------------------------------------------

    def argv_agent_get(self, name):
        return [self.binary, "agent", "get", name]

    def argv_agent_list(self):
        return [self.binary, "agent", "list"]

    def argv_agent_read(self, name, source=None, lines=None, fmt=None):
        argv = [self.binary, "agent", "read", name]
        if source:
            argv += ["--source", source]
        if lines is not None:
            argv += ["--lines", str(lines)]
        if fmt:
            # `ansi` keeps the SGR sequences, which is the only way to tell a
            # dim ghost-text suggestion from text somebody actually typed.
            argv += ["--format", fmt]
        return argv

    def argv_agent_prompt(self, name, text, wait=False, until=(), timeout_ms=None):
        argv = [self.binary, "agent", "prompt", name, text]
        if wait:
            argv.append("--wait")
        for state in until:
            argv += ["--until", state]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        return argv

    def argv_agent_send_keys(self, name, keys):
        return [self.binary, "agent", "send-keys", name] + list(keys)

    def argv_agent_wait(self, name, until=(), timeout_ms=None):
        argv = [self.binary, "agent", "wait", name]
        for state in until:
            argv += ["--until", state]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        return argv

    def argv_pane_send_text(self, pane_id, text):
        return [self.binary, "pane", "send-text", pane_id, text]

    def argv_pane_send_keys(self, pane_id, keys):
        return [self.binary, "pane", "send-keys", pane_id] + list(keys)

    def argv_send_slash_command(self, pane_id, text, enter_count=1):
        """The argv lists `send_slash_command` runs, in order.

        `enter_count` above 1 is for a composer whose autocomplete popup eats
        the first Enter: Codex's first Enter only accepts the completion. Each
        Enter is its own call so a --trace log shows how many were needed. An
        extra Enter on an already-empty composer is harmless.
        """
        argvs = [self.argv_pane_send_text(pane_id, text)]
        for _ in range(max(1, enter_count)):
            argvs.append(self.argv_pane_send_keys(pane_id, [SUBMIT_KEY]))
        return argvs

    def argv_deliver_slash_command(self, delivery, agent_name, pane_id, text, enter_count=1):
        """The argv lists `deliver_slash_command` runs, in order."""
        if delivery == SLASH_DELIVERY_PASTE:
            return [self.argv_agent_prompt(agent_name, text)]
        if delivery == SLASH_DELIVERY_TYPE:
            return self.argv_send_slash_command(pane_id, text, enter_count=enter_count)
        raise HerdrError(
            "Unknown slash_delivery {!r} for agent {!r} - use {}.".format(
                delivery, agent_name, " or ".join(repr(v) for v in SLASH_DELIVERIES)
            ),
            {"agent": agent_name, "slash_delivery": delivery},
        )

    def argv_pane_wait_output(self, pane_id, regex=None, match=None, source=None, lines=None, timeout_ms=None):
        if (regex is None) == (match is None):
            raise HerdrError(
                "pane wait-output needs exactly one of --regex or --match - "
                "pass a marker pattern for the agent's usage report.",
                {"pane_id": pane_id},
            )
        argv = [self.binary, "pane", "wait-output"]
        if regex is not None:
            argv += ["--regex", regex]
        else:
            argv += ["--match", match]
        if source:
            argv += ["--source", source]
        if lines is not None:
            argv += ["--lines", str(lines)]
        if timeout_ms is not None:
            argv += ["--timeout", str(timeout_ms)]
        argv.append(pane_id)
        return argv

    # -- execution -----------------------------------------------------------

    def _emit_trace(self, argv, completed=None, outcome=None):
        """Record one invocation on the trace sink, if there is one.

        Every field crosses `scrub_for_trace` on its way out: argv can carry a
        token an operator passed, and stdout/stderr are pane text that may hold
        anything the agent had on screen. `outcome` is this module's own
        literal and needs no scrubbing.
        """
        if self._trace is None:
            return
        command = scrub_for_trace(format_argv(argv))
        if completed is None:
            self._trace("herdr> {}{}".format(command, outcome or ""))
            return
        self._trace(
            "herdr> {}\nherdr< exit={} stdout={!r} stderr={!r}".format(
                command,
                completed.returncode,
                scrub_for_trace(completed.stdout or ""),
                scrub_for_trace(completed.stderr or ""),
            )
        )

    def _run(self, argv):
        """Execute `argv`, raising HerdrError on anything but a clean exit."""
        try:
            completed = self._runner(argv)
        except FileNotFoundError:
            self._emit_trace(argv, outcome="\nherdr< executable not found")
            raise HerdrError(
                "herdr executable {!r} not found on PATH - install Herdr or set "
                "TEAMLEAD_HERDR_BIN to its absolute path.".format(self.binary),
                {"command": format_argv(argv)},
            ) from None
        except subprocess.TimeoutExpired:
            self._emit_trace(argv, outcome="\nherdr< subprocess timeout")
            raise HerdrError(
                "herdr did not return within {}s running `{}` - check the pane "
                "with `herdr agent list`.".format(SUBPROCESS_TIMEOUT_SEC, format_argv(argv)),
                {"command": format_argv(argv)},
            ) from None

        self._emit_trace(argv, completed)

        if completed.returncode != 0:
            raise HerdrError(
                self._failure_message(argv, completed),
                {
                    "command": format_argv(argv),
                    "exit_code": completed.returncode,
                    "stderr": (completed.stderr or "").strip(),
                },
            )
        return completed.stdout or ""

    @staticmethod
    def _failure_message(argv, completed):
        stderr = (completed.stderr or "").strip()
        detail = stderr
        # Herdr reports server errors as JSON on stderr; surface its own code
        # and message when it gave us one, and the raw text when it did not.
        if stderr.startswith("{"):
            try:
                payload = json.loads(stderr)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                error = payload["error"]
                detail = "{}: {}".format(
                    error.get("code", "unknown"), error.get("message", stderr)
                )
        if completed.returncode == 2:
            return (
                "herdr rejected the command `{}` as a syntax error ({}) - this "
                "build of teamlead may not match the installed herdr; check "
                "`herdr agent --help`.".format(format_argv(argv), detail or "no detail")
            )
        return "herdr failed running `{}`: {}".format(argv and format_argv(argv), detail or "no detail")

    def _run_optional_json(self, argv):
        """Run a command whose result teamlead never reads.

        `pane send-text` and `pane send-keys` exit 0 with empty stdout on
        success rather than returning a JSON control response. Live, that made
        a successful `pane send-text w4:p1 /usage` look like a transport
        failure and the run died before pressing Enter, leaving `/usage`
        sitting unsent in Grok's input box.

        Exit status stays the contract: a non-zero exit is still a HerdrError
        carrying herdr's own stderr.
        """
        stdout = self._run(argv)
        if not stdout.strip():
            return {}
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return payload["result"]
        return {}

    def _run_json(self, argv):
        stdout = self._run(argv)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise HerdrError(
                "herdr returned non-JSON output for `{}` - expected a JSON "
                "control response. Got: {!r}".format(format_argv(argv), stdout[:200]),
                {"command": format_argv(argv)},
            ) from None
        if not isinstance(payload, dict) or "result" not in payload:
            raise HerdrError(
                "herdr returned JSON without a `result` field for `{}` - got "
                "keys {}.".format(
                    format_argv(argv),
                    sorted(payload) if isinstance(payload, dict) else type(payload).__name__,
                ),
                {"command": format_argv(argv)},
            )
        return payload["result"]

    def _run_json_object(self, argv):
        """Require an object only for lookups that consume named result fields."""
        result = self._run_json(argv)
        if not isinstance(result, dict):
            raise HerdrError(
                "herdr returned a non-object `result` for `{}`; expected a JSON "
                "object. Inspect this read's response and update Herdr before "
                "retrying.".format(format_argv(argv)),
                {"command": format_argv(argv), "result_type": type(result).__name__},
            )
        return result

    # -- operations ----------------------------------------------------------

    def argv_agent_start(self, name, kind, pane_id, flags):
        return [self.binary, "agent", "start", name, "--kind", kind, "--pane", pane_id, "--", *flags]

    def agent_start(self, name, kind, pane_id, flags):
        return self._run_json(self.argv_agent_start(name, kind, pane_id, flags))

    def argv_pane_process_info(self, pane_id):
        return [self.binary, "pane", "process-info", "--pane", pane_id]

    def pane_process_info(self, pane_id):
        result = self._run_json_object(self.argv_pane_process_info(pane_id))
        info = result.get("process_info")
        if not isinstance(info, dict) or info.get("pane_id") != pane_id:
            raise HerdrError("Herdr returned no process information for the requested pane; update Herdr before dispatch.", {})
        return info

    def process_args(self, pid):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise HerdrError("No usable foreground PID; inspect the pane before dispatch.", {})
        try:
            return shlex.split(self._run(["ps", "-o", "args=", "-p", str(pid)]))
        except ValueError:
            raise HerdrError("Process arguments could not be parsed; start a fresh tiered worker.", {}) from None

    def terminate_process(self, pid):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise HerdrError("No usable foreground PID; refusing to terminate an unknown process.", {})
        self._run(["kill", "-TERM", str(pid)])

    def agent_get(self, name):
        """Return the agent record for `name` (pane_id, agent_status, ...)."""
        result = self._run_json_object(self.argv_agent_get(name))
        agent = result.get("agent")
        if not isinstance(agent, dict):
            raise HerdrError(
                "herdr agent get {} returned no agent record - confirm the name "
                "with `herdr agent list`.".format(name),
                {"agent": name},
            )
        return agent

    def pane_get(self, pane_id):
        """Return native terminal identity and viewport metadata for a pane."""
        result = self._run_json([self.binary, "pane", "get", pane_id])
        pane = result.get("pane")
        if not isinstance(pane, dict):
            raise HerdrError("herdr pane get returned no pane record; restore the pane connection.", {"pane": pane_id})
        return pane

    def pane_read(self, pane_id, lines):
        """Read visible rows even while the agent status changes."""
        return self._run([self.binary, "pane", "read", pane_id, "--source", "visible", "--lines", str(lines)])

    def agent_list(self):
        """Return every live agent record."""
        result = self._run_json(self.argv_agent_list())
        agents = result.get("agents")
        if not isinstance(agents, list):
            raise HerdrError(
                "herdr agent list returned no agents array - check that the "
                "Herdr server is running.",
                {},
            )
        return agents

    def agent_read(self, name, source=None, lines=None, fmt=None):
        """Return raw pane text. `agent read` is the one non-JSON command."""
        return self._run(self.argv_agent_read(name, source=source, lines=lines, fmt=fmt))

    def agent_prompt(self, name, text, wait=False, until=(), timeout_ms=None):
        """Submit message text. Writes to the agent -- gate on status first.

        For a slash command use `send_slash_command`: this path pastes, and a
        pasted `/usage` is a chat message, not a command.
        """
        return self._run_json(
            self.argv_agent_prompt(name, text, wait=wait, until=until, timeout_ms=timeout_ms)
        )

    def agent_send_keys(self, name, keys):
        """Send logical key names (`esc`, `ctrl+c`) to the agent.

        Fire and forget: nothing reads the result, so exit 0 is success
        whether or not herdr prints a body.
        """
        return self._run_optional_json(self.argv_agent_send_keys(name, keys))

    def agent_wait(self, name, until=(), timeout_ms=None):
        """Block until the agent reaches one of `until` (default: settled)."""
        return self._run_json(self.argv_agent_wait(name, until=until, timeout_ms=timeout_ms))

    def argv_pane_rename(self, pane_id, label):
        """`pane rename` sets the pane's title in the sidebar."""
        return [self.binary, "pane", "rename", pane_id, label]

    def pane_rename(self, pane_id, label):
        """Retitle a pane. Returns the control response.

        Purely cosmetic, so every caller treats a failure as a warning: a
        sidebar label is not worth failing a dispatch that already landed.
        """
        return self._run_json(self.argv_pane_rename(pane_id, label))

    def pane_send_text(self, pane_id, text):
        """Type literal text into a pane, with no Enter and no paste framing.

        Exits 0 with empty stdout on success; see `_run_optional_json`.
        """
        return self._run_optional_json(self.argv_pane_send_text(pane_id, text))

    def pane_send_keys(self, pane_id, keys):
        """Send logical key names to a pane.

        Exits 0 with empty stdout on success; see `_run_optional_json`.
        """
        return self._run_optional_json(self.argv_pane_send_keys(pane_id, keys))

    def send_slash_command(self, pane_id, text, enter_count=1, before_input=None, after_submit=None):
        """Deliver a slash command the way a human types it.

        `agent prompt` delivers text through the pane's live bracketed-paste
        mode, and a TUI that has bracketed paste enabled reads the result as a
        pasted chat message rather than as a command. Live, `/usage` reached
        Grok as a user turn: it answered in prose about billing and started
        reading files instead of opening its usage panel. Typing the
        characters and then pressing Enter as a keystroke is what a slash
        command needs, so every slash command goes through here.

        `agent prompt` stays correct for real message text -- an assignment
        brief is a message, and pasting it is exactly right. `before_input`
        revalidates the caller's live guard before the text and each Enter.
        """
        if before_input is not None:
            before_input()
        text_result = self.pane_send_text(pane_id, text)
        key_results = []
        for _ in range(max(1, enter_count)):
            if before_input is not None:
                before_input()
            key_results.append(self.pane_send_keys(pane_id, [SUBMIT_KEY]))
            if after_submit is not None:
                after_submit()
        return {"send_text": text_result, "send_keys": key_results[-1]}

    def deliver_slash_command(self, delivery, agent_name, pane_id, text, enter_count=1, before_input=None, after_submit=None):
        """Send a slash command by whichever mechanism this agent needs.

        The choice is per-agent because the TUIs disagree: pasting is read as
        a chat message by one, and typing is swallowed by another's
        autocomplete popup. See SLASH_DELIVERIES.
        """
        if delivery == SLASH_DELIVERY_PASTE:
            if before_input is not None:
                before_input()
            result = {"paste": self.agent_prompt(agent_name, text)}
            if after_submit is not None:
                after_submit()
            return result
        if delivery == SLASH_DELIVERY_TYPE:
            return self.send_slash_command(pane_id, text, enter_count=enter_count, before_input=before_input, after_submit=after_submit)
        raise HerdrError(
            "Unknown slash_delivery {!r} for agent {!r} - use {}.".format(
                delivery, agent_name, " or ".join(repr(v) for v in SLASH_DELIVERIES)
            ),
            {"agent": agent_name, "slash_delivery": delivery},
        )

    def pane_wait_output(self, pane_id, regex=None, match=None, source=None, lines=None, timeout_ms=None):
        """Block until the pane snapshot contains the marker, or time out."""
        return self._run_json(
            self.argv_pane_wait_output(
                pane_id, regex=regex, match=match, source=source, lines=lines, timeout_ms=timeout_ms
            )
        )
