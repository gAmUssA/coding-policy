"""Herdr-compatible adapter over Rex (docs/adr/0015-rex-backend-through-herdr-compatible-adapter.md).

Implements the subset of Herdr's command grammar this plugin calls, answers in
Herdr's JSON envelope, and drives Rex through the `rex` CLI. Point `HERDR_BIN`
and `TEAMLEAD_HERDR_BIN` at `skills/herdr-teamlead/rex-herdr.sh` to run the
team-lead skills on Rex.

Contract:
  argv  : `agent list|get|read|prompt|send-keys|wait|start|rename`,
          `pane get|read|wait-output|send-text|send-keys|process-info|rename`,
          `workspace list|rename`, in Herdr's grammar.
  stdout: one JSON object `{"result": ...}`, or raw text for `agent read` and
          `pane read`.
  stderr: `{"error":{"code","message"}}` on a Rex-side failure.
  exit  : 0 success; 1 failure with the stderr envelope; 2 usage error.
  env   : REX_SESSION (required) scopes every command to one Rex session.
          TEAMLEAD_REX_BIN overrides the `rex` executable.

Mapping: a Herdr pane is a Rex block, a workspace is the Rex session, an agent
name is a block label, and `agent_status` is the block's OSC 7501 program
status. Named constants below hold every lookup table and poll budget.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

TERMINAL = "com.superlogical.terminal"
DEFAULT_REX_BIN = "rex"
REX_CALL_TIMEOUT_SEC = 30
POLL_INTERVAL_SEC = 0.25
DEFAULT_WAIT_TIMEOUT_MS = 600_000
DEFAULT_START_TIMEOUT_MS = 30_000
PROMPT_STALL_MS = 5_000

#: OSC 7501 `app` values of the agents this plugin dispatches, mapped to the
#: Herdr kind. Recorded from Rex 0.1.0 (1853) on 2026-11-07.
APP_KINDS = {"claude-code": "claude", "codex": "codex", "grok-build": "grok", "opencode": "opencode", "pi": "pi"}
#: The executable `agent start` types for each kind.
KIND_BINARIES = {"claude": "claude", "codex": "codex", "grok": "grok", "opencode": "opencode", "pi": "pi"}
#: Program status states, translated to Herdr's lifecycle vocabulary.
STATUS_STATES = {"idle": "idle", "working": "working", "blocked": "blocked", "done": "done", "error": "done"}
SETTLED_STATES = ("idle", "done", "blocked")
SHELLS = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh", "nu"}
#: Herdr's agent-name grammar. `shell` is Rex's default block label, so it
#: never names an agent.
NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
RESERVED_NAMES = {"shell"}
READ_SOURCES = ("visible", "recent", "recent-unwrapped", "detection")
#: Herdr key names that Rex spells differently; every other name passes through.
KEY_NAMES = {"escape": "esc", "return": "enter", "space": "space"}

SNAPSHOT_LUA = """
local T = "com.superlogical.terminal"
local view = rex.session.view{}
local out = {session_id = view.session_id, label = view.label, revision = view.revision, blocks = {}}
for _, b in ipairs(rex.session.list_blocks{}.blocks or {}) do
  local e = {block_id = b.block_id, label = b.label, window_id = b.window_id, creator = b.creator_name}
  if b.creator_name == T then
    e.process = rex.block.call(T, "process", {block_id = b.block_id})
    local ps = rex.block.call(T, "program_status", {block_id = b.block_id})
    e.records = ps and ps.records
    local size = rex.block.call(T, "size", {block_id = b.block_id})
    e.rows = size and size.rows
  end
  out.blocks[#out.blocks + 1] = e
end
return out
"""


class AdapterError(Exception):
    """A Rex-side failure, reported in Herdr's stderr envelope with exit 1."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class PollTimeout(AdapterError):
    """A poll ran out its budget; Herdr's `timeout` code."""

    def __init__(self, message):
        super().__init__("timeout", message)


class UsageError(Exception):
    """A malformed command line; exit 2, as Herdr does for a syntax error."""


def subprocess_runner(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=REX_CALL_TIMEOUT_SEC, check=False)


def lua_value(value):
    """A Lua expression decoding `value` from JSON inside a long bracket."""
    text = json.dumps(value)
    level = 0
    while "]" + "=" * level + "]" in text:
        level += 1
    eq = "=" * level
    return "rex.json.decode([{0}[{1}]{0}])".format(eq, text)


def as_list(value):
    # Lua encodes an empty table as `{}`, so an empty array arrives as a dict.
    return value if isinstance(value, list) else []


def basename(path):
    return os.path.basename(path or "").lstrip("-")


class Rex:
    """The `rex` CLI, scoped to one session. Every call goes through `runner`."""

    def __init__(self, binary, session, runner):
        self.binary = binary
        self.session = session
        self.runner = runner

    def run(self, args):
        argv = [self.binary] + args
        try:
            completed = self.runner(argv)
        except FileNotFoundError:
            raise AdapterError("rex_not_found", "`{}` not found — install Rex or set TEAMLEAD_REX_BIN to the rex executable".format(self.binary)) from None
        except subprocess.TimeoutExpired:
            raise AdapterError("rex_timeout", "`{}` did not answer within {}s — check the Rex server with `rex whoami`".format(shlex.join(argv), REX_CALL_TIMEOUT_SEC)) from None
        if completed.returncode != 0:
            raise AdapterError("rex_failed", "`{}` exited {}: {}".format(shlex.join(argv), completed.returncode, (completed.stderr or "").strip()))
        return completed.stdout

    def lua(self, script):
        out = self.run(["do", "-s", self.session, "-e", script])
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            raise AdapterError("rex_failed", "rex do returned non-JSON output: {!r}".format(out[:200])) from None

    def snapshot(self):
        value = self.lua(SNAPSHOT_LUA)
        if not isinstance(value, dict):
            raise AdapterError("rex_failed", "rex returned no session snapshot for {} — check REX_SESSION".format(self.session))
        return value

    def capture(self, block_id, unwrap=False, vt=False):
        args = ["capture", "-s", self.session, "-b", block_id]
        if unwrap:
            args.append("--unwrap")
        if vt:
            args += ["--format", "vt"]
        return self.run(args)

    def paste(self, block_id, text):
        payload = {"session_id": self.session, "block_id": block_id, "event": {"type": "paste", "payload": {"text": text}}}
        self.run(["api", "call", "block.event", json.dumps(payload)])

    def send_keys(self, block_id, keys):
        self.run(["send-key", "-s", self.session, "-b", block_id] + [KEY_NAMES.get(k, k) for k in keys])

    def send_text(self, block_id, text):
        self.run(["send", "-s", self.session, "-b", block_id, text])

    def set_block_label(self, block_id, label):
        self.lua("return {{rex.session.set_block_label{{block_id = {}, label = {}}}}}".format(lua_value(block_id), lua_value(label)))

    def set_session_label(self, label):
        self.lua("return {{rex.session.set_label{{label = {}}}}}".format(lua_value(label)))

    def process_args(self, pid):
        try:
            completed = self.runner(["ps", "-o", "args=", "-p", str(pid)])
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return shlex.split(completed.stdout.strip()) or None


def root_record(block):
    for record in as_list(block.get("records")):
        if isinstance(record, dict) and not record.get("id"):
            return record
    return None


def foreground(block):
    process = block.get("process") or {}
    fg = process.get("foreground") or process.get("child")
    return fg if isinstance(fg, dict) else None


def holds_only_shell(block):
    fg = foreground(block)
    return fg is None or basename(fg.get("argv0")) in SHELLS or fg.get("name") in SHELLS


def agent_kind(block):
    """(kind, record) for a live agent in the block, or None.

    Program status outlives its process (`done` survives exit), so a record
    counts only while its owner is the block's foreground process.
    """
    fg = foreground(block)
    if fg is None:
        return None
    record = root_record(block)
    if record and record.get("app") in APP_KINDS and (record.get("owner") or {}).get("pid") == fg.get("pid"):
        return APP_KINDS[record["app"]], record
    for candidate in (fg.get("name"), basename(fg.get("argv0"))):
        if candidate in KIND_BINARIES:
            return candidate, None
    return None


def agent_name(block):
    label = block.get("label") or ""
    return label if NAME_RE.fullmatch(label) and label not in RESERVED_NAMES else None


def status_of(record):
    state = (record or {}).get("state")
    return STATUS_STATES.get(state, "unknown") if isinstance(state, str) else "unknown"


def agent_info(snapshot, block):
    found = agent_kind(block)
    if found is None:
        return None
    kind, record = found
    fg = foreground(block) or {}
    return {
        "agent": kind,
        "name": agent_name(block),
        "agent_status": status_of(record),
        "pane_id": block["block_id"],
        "terminal_id": block["block_id"],
        "workspace_id": snapshot.get("session_id"),
        "tab_id": block.get("window_id"),
        "cwd": fg.get("cwd"),
    }


def terminals(snapshot):
    return [b for b in as_list(snapshot.get("blocks")) if b.get("creator") == TERMINAL]


def find_pane(snapshot, pane_id):
    for block in terminals(snapshot):
        if block["block_id"] == pane_id:
            return block
    raise AdapterError("pane_not_found", "pane {} not found in Rex session {}".format(pane_id, snapshot.get("session_id")))


def find_agent(snapshot, target):
    matches = []
    for block in terminals(snapshot):
        info = agent_info(snapshot, block)
        if info and (target in (info["name"], info["pane_id"])):
            matches.append((block, info))
    if not matches:
        raise AdapterError("agent_not_found", "agent target {} not found".format(target))
    if len(matches) > 1:
        raise AdapterError("agent_target_ambiguous", "agent target {} matches {} panes — rename one with `agent rename`".format(target, len(matches)))
    return matches[0]


def check_name(snapshot, name, pane_id=None):
    if not NAME_RE.fullmatch(name) or name in RESERVED_NAMES:
        raise UsageError("agent name {!r} must match [a-z][a-z0-9_-]{{0,31}} and not be 'shell'".format(name))
    for block in terminals(snapshot):
        info = agent_info(snapshot, block)
        if info and info["name"] == name and block["block_id"] != pane_id:
            raise AdapterError("agent_name_taken", "agent name {} is in use by pane {}".format(name, block["block_id"]))


def read_text(rex, block, source, lines, ansi):
    if source not in READ_SOURCES:
        raise UsageError("--source must be one of " + ", ".join(READ_SOURCES))
    text = rex.capture(block["block_id"], unwrap=source == "recent-unwrapped", vt=ansi)
    rows = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    if source in ("visible", "detection") and isinstance(block.get("rows"), int) and block["rows"] > 0:
        rows = rows[-block["rows"]:]
    if lines is not None:
        rows = rows[-lines:] if lines > 0 else []
    return "\n".join(rows) + "\n"


class Options:
    """Herdr's flag grammar: `--flag value`, repeatable `--until`, bare switches."""

    def __init__(self, args, valued, switches=(), repeated=()):
        self.positional, self.values, self.flags, self.rest = [], {}, set(), []
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--":
                self.rest = args[index + 1:]
                break
            if arg in switches:
                self.flags.add(arg)
            elif arg in valued or arg in repeated:
                if index + 1 >= len(args):
                    raise UsageError("{} needs a value".format(arg))
                index += 1
                if arg in repeated:
                    self.values.setdefault(arg, []).append(args[index])
                else:
                    self.values[arg] = args[index]
            elif arg.startswith("--"):
                raise UsageError("unknown option " + arg)
            else:
                self.positional.append(arg)
            index += 1

    def int_value(self, flag, default=None):
        if flag not in self.values:
            return default
        try:
            return int(self.values[flag])
        except ValueError:
            raise UsageError("{} must be an integer".format(flag)) from None


def positional(opts, count, usage):
    if len(opts.positional) != count:
        raise UsageError("usage: " + usage)
    return opts.positional


class Adapter:
    def __init__(self, rex, sleep=time.sleep, clock=time.monotonic):
        self.rex = rex
        self.sleep = sleep
        self.clock = clock

    def poll(self, check, timeout_ms, message):
        deadline = self.clock() + timeout_ms / 1000.0
        while True:
            value = check()
            if value is not None:
                return value
            if self.clock() >= deadline:
                raise PollTimeout(message)
            self.sleep(POLL_INTERVAL_SEC)

    # -- agent -----------------------------------------------------------

    def agent_list(self, args):
        Options(args, ())
        snapshot = self.rex.snapshot()
        agents = [info for info in (agent_info(snapshot, b) for b in terminals(snapshot)) if info]
        return {"type": "agent_list", "agents": agents}

    def agent_get(self, args):
        target, = positional(Options(args, ()), 1, "agent get <target>")
        _, info = find_agent(self.rex.snapshot(), target)
        return {"type": "agent_info", "agent": info}

    def agent_read(self, args):
        opts = Options(args, ("--source", "--lines", "--format"), ("--ansi",))
        target, = positional(opts, 1, "agent read <target> [--source S] [--lines N] [--format text|ansi]")
        block, _ = find_agent(self.rex.snapshot(), target)
        ansi = "--ansi" in opts.flags or opts.values.get("--format") == "ansi"
        return read_text(self.rex, block, opts.values.get("--source", "visible"), opts.int_value("--lines"), ansi)

    def agent_prompt(self, args):
        opts = Options(args, ("--timeout",), ("--wait",), ("--until",))
        target, text = positional(opts, 2, "agent prompt <target> <text> [--wait] [--until S]... [--timeout MS]")
        block, info = find_agent(self.rex.snapshot(), target)
        if info["agent_status"] == "blocked":
            raise AdapterError("agent_blocked", "agent {} is at an approval or question dialog; answer it before prompting".format(target))
        before = info["agent_status"]
        self.rex.paste(block["block_id"], text)
        self.rex.send_keys(block["block_id"], ["enter"])
        if "--wait" not in opts.flags:
            return {"type": "agent_prompted", "agent": info}
        pane = block["block_id"]

        def changed():
            _, now = find_agent(self.rex.snapshot(), pane)
            return now if now["agent_status"] != before or before == "working" else None

        try:
            self.poll(changed, PROMPT_STALL_MS, "")
        except PollTimeout:
            raise AdapterError("agent_prompt_stalled", "agent {} showed no lifecycle change within {} ms of the prompt".format(target, PROMPT_STALL_MS)) from None
        return self.wait_for(pane, opts.values.get("--until") or list(SETTLED_STATES), opts.int_value("--timeout", DEFAULT_WAIT_TIMEOUT_MS))

    def wait_for(self, pane, until, timeout_ms):
        def settled():
            _, info = find_agent(self.rex.snapshot(), pane)
            return info if info["agent_status"] in until else None

        info = self.poll(settled, timeout_ms, "agent in pane {} did not reach {} within {} ms".format(pane, "/".join(until), timeout_ms))
        return {"type": "agent_status", "agent": info}

    def agent_wait(self, args):
        opts = Options(args, ("--timeout",), (), ("--until",))
        target, = positional(opts, 1, "agent wait <target> [--until S]... [--timeout MS]")
        block, _ = find_agent(self.rex.snapshot(), target)
        return self.wait_for(block["block_id"], opts.values.get("--until") or list(SETTLED_STATES), opts.int_value("--timeout", DEFAULT_WAIT_TIMEOUT_MS))

    def agent_send_keys(self, args):
        opts = Options(args, ())
        if len(opts.positional) < 2:
            raise UsageError("usage: agent send-keys <target> <key> [key ...]")
        block, _ = find_agent(self.rex.snapshot(), opts.positional[0])
        self.rex.send_keys(block["block_id"], opts.positional[1:])
        return {"type": "ok"}

    def agent_rename(self, args):
        opts = Options(args, (), ("--clear",))
        if "--clear" in opts.flags:
            target, = positional(opts, 1, "agent rename <target> <name>|--clear")
            name = ""
        else:
            target, name = positional(opts, 2, "agent rename <target> <name>|--clear")
        snapshot = self.rex.snapshot()
        block, _ = find_agent(snapshot, target)
        if name:
            check_name(snapshot, name, block["block_id"])
        self.rex.set_block_label(block["block_id"], name)
        _, info = find_agent(self.rex.snapshot(), block["block_id"])
        return {"type": "agent_info", "agent": info}

    def agent_start(self, args):
        opts = Options(args, ("--kind", "--pane", "--timeout"))
        name, = positional(opts, 1, "agent start <name> --kind KIND --pane ID [--timeout MS] [-- <agent-args...>]")
        kind, pane = opts.values.get("--kind"), opts.values.get("--pane")
        if kind not in KIND_BINARIES or not pane:
            raise UsageError("agent start needs --kind ({}) and --pane".format("|".join(sorted(KIND_BINARIES))))
        snapshot = self.rex.snapshot()
        block = find_pane(snapshot, pane)
        check_name(snapshot, name)
        if not holds_only_shell(block):
            raise AdapterError("pane_busy", "pane {} is running {}; start an agent only in a pane at a shell prompt".format(pane, (foreground(block) or {}).get("argv0")))
        argv = [KIND_BINARIES[kind]] + opts.rest
        self.rex.set_block_label(pane, name)
        self.rex.send_text(pane, shlex.join(argv))
        self.rex.send_keys(pane, ["enter"])

        def started():
            current = find_pane(self.rex.snapshot(), pane)
            info = agent_info({"session_id": self.rex.session}, current)
            return info if info and info["agent"] == kind else None

        timeout = opts.int_value("--timeout", DEFAULT_START_TIMEOUT_MS)
        info = self.poll(started, timeout, "{} did not start in pane {} within {} ms".format(kind, pane, timeout))
        return {"type": "agent_started", "agent": info, "argv": argv}

    # -- pane --------------------------------------------------------------

    def pane_get(self, args):
        pane, = positional(Options(args, ()), 1, "pane get <pane_id>")
        snapshot = self.rex.snapshot()
        block = find_pane(snapshot, pane)
        info = agent_info(snapshot, block) or {}
        result = {
            "pane_id": pane,
            "terminal_id": pane,
            "workspace_id": snapshot.get("session_id"),
            "tab_id": block.get("window_id"),
            "label": block.get("label"),
            "revision": snapshot.get("revision"),
            # Reads capture the whole buffer, bottom-anchored, whatever the viewer scrolled to.
            "scroll": {"offset_from_bottom": 0},
            "agent_status": info.get("agent_status", "unknown"),
        }
        if info:
            result["agent"] = info["agent"]
        return {"type": "pane_info", "pane": result}

    def pane_read(self, args):
        opts = Options(args, ("--source", "--lines", "--format"), ("--ansi",))
        pane, = positional(opts, 1, "pane read <pane_id> [--source S] [--lines N] [--format text|ansi]")
        block = find_pane(self.rex.snapshot(), pane)
        ansi = "--ansi" in opts.flags or opts.values.get("--format") == "ansi"
        return read_text(self.rex, block, opts.values.get("--source", "visible"), opts.int_value("--lines"), ansi)

    def pane_wait_output(self, args):
        opts = Options(args, ("--match", "--regex", "--source", "--lines", "--timeout"), ("--raw",))
        pane, = positional(opts, 1, "pane wait-output <pane_id> (--match TEXT | --regex PATTERN) [--source S] [--lines N] [--timeout MS]")
        match, pattern = opts.values.get("--match"), opts.values.get("--regex")
        if (match is None) == (pattern is None):
            raise UsageError("pane wait-output needs exactly one of --match or --regex")
        try:
            regex = re.compile(pattern) if pattern is not None else None
        except re.error as exc:
            raise UsageError("--regex does not compile: {}".format(exc)) from None
        source, lines = opts.values.get("--source", "recent"), opts.int_value("--lines")

        def matched():
            block = find_pane(self.rex.snapshot(), pane)
            for line in read_text(self.rex, block, source, lines, False).split("\n"):
                if (match is not None and match in line) or (regex is not None and regex.search(line)):
                    return {"type": "output_matched", "pane_id": pane, "matched_line": line}
            return None

        timeout = opts.int_value("--timeout", DEFAULT_WAIT_TIMEOUT_MS)
        return self.poll(matched, timeout, "timed out waiting for output match in pane {}".format(pane))

    def pane_send_text(self, args):
        pane, text = positional(Options(args, ()), 2, "pane send-text <pane_id> <text>")
        find_pane(self.rex.snapshot(), pane)
        self.rex.send_text(pane, text)
        return {"type": "ok"}

    def pane_send_keys(self, args):
        opts = Options(args, ())
        if len(opts.positional) < 2:
            raise UsageError("usage: pane send-keys <pane_id> <key> [key ...]")
        find_pane(self.rex.snapshot(), opts.positional[0])
        self.rex.send_keys(opts.positional[0], opts.positional[1:])
        return {"type": "ok"}

    def pane_process_info(self, args):
        opts = Options(args, ("--pane",))
        pane = opts.values.get("--pane") or (opts.positional[0] if len(opts.positional) == 1 else None)
        if not pane:
            raise UsageError("usage: pane process-info --pane <pane_id>")
        snapshot = self.rex.snapshot()
        block = find_pane(snapshot, pane)
        process = block.get("process") or {}
        fg, child = foreground(block) or {}, process.get("child") or {}
        if holds_only_shell(block):
            return {"type": "process_info", "process_info": {"pane_id": pane, "shell_pid": fg.get("pid"), "foreground_processes": []}}
        found = agent_kind(block)
        entry = {"pid": fg.get("pid"), "name": found[0] if found else (fg.get("name") or basename(fg.get("argv0")))}
        argv = self.rex.process_args(fg.get("pid")) if fg.get("pid") else None
        if argv:
            entry["argv"] = argv
        return {"type": "process_info", "process_info": {"pane_id": pane, "shell_pid": child.get("pid"), "foreground_processes": [entry]}}

    def pane_rename(self, args):
        opts = Options(args, (), ("--clear",))
        if "--clear" in opts.flags:
            pane, = positional(opts, 1, "pane rename <pane_id> <label>|--clear")
            label = ""
        else:
            pane, label = positional(opts, 2, "pane rename <pane_id> <label>|--clear")
        find_pane(self.rex.snapshot(), pane)
        self.rex.set_block_label(pane, label)
        return {"type": "ok"}

    # -- workspace -----------------------------------------------------------

    def workspace_list(self, args):
        Options(args, ())
        snapshot = self.rex.snapshot()
        return {"type": "workspace_list", "workspaces": [{"workspace_id": snapshot.get("session_id"), "name": snapshot.get("label")}]}

    def workspace_rename(self, args):
        workspace, label = positional(Options(args, ()), 2, "workspace rename <workspace_id> <label>")
        if workspace != self.rex.snapshot().get("session_id"):
            raise AdapterError("workspace_not_found", "workspace {} is not the Rex session in REX_SESSION".format(workspace))
        self.rex.set_session_label(label)
        return {"type": "ok"}


COMMANDS = {
    ("agent", "list"): Adapter.agent_list,
    ("agent", "get"): Adapter.agent_get,
    ("agent", "read"): Adapter.agent_read,
    ("agent", "prompt"): Adapter.agent_prompt,
    ("agent", "send-keys"): Adapter.agent_send_keys,
    ("agent", "wait"): Adapter.agent_wait,
    ("agent", "start"): Adapter.agent_start,
    ("agent", "rename"): Adapter.agent_rename,
    ("pane", "get"): Adapter.pane_get,
    ("pane", "read"): Adapter.pane_read,
    ("pane", "wait-output"): Adapter.pane_wait_output,
    ("pane", "send-text"): Adapter.pane_send_text,
    ("pane", "send-keys"): Adapter.pane_send_keys,
    ("pane", "process-info"): Adapter.pane_process_info,
    ("pane", "rename"): Adapter.pane_rename,
    ("workspace", "list"): Adapter.workspace_list,
    ("workspace", "rename"): Adapter.workspace_rename,
}


def main(argv=None, env=None, runner=None, stdout=None, stderr=None, sleep=time.sleep, clock=time.monotonic):
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    command = COMMANDS.get((argv[0], argv[1])) if len(argv) >= 2 else None
    if command is None:
        stderr.write("rex-herdr: unsupported command {!r}; supported: {}\n".format(" ".join(argv[:2]), ", ".join(" ".join(k) for k in COMMANDS)))
        return 2
    session = env.get("REX_SESSION", "")
    if not session:
        stderr.write(json.dumps({"error": {"code": "not_in_rex", "message": "REX_SESSION is unset — run the team lead inside a Rex terminal"}}) + "\n")
        return 1
    adapter = Adapter(Rex(env.get("TEAMLEAD_REX_BIN") or DEFAULT_REX_BIN, session, runner or subprocess_runner), sleep=sleep, clock=clock)
    try:
        result = command(adapter, argv[2:])
    except UsageError as exc:
        stderr.write("rex-herdr: {}\n".format(exc))
        return 2
    except AdapterError as exc:
        stderr.write(json.dumps({"error": {"code": exc.code, "message": exc.message}}) + "\n")
        return 1
    stdout.write(result if isinstance(result, str) else json.dumps({"result": result}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
