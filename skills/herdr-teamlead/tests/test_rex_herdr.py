"""Tests for teamlead.rex_herdr, the Herdr-compatible adapter over Rex.

Every test drives `main()` with a fake `rex` executable at the process
boundary (`FakeRex`) and asserts the Herdr-shaped stdout, stderr and exit
code, plus the writes that reached the fake terminal. Snapshot fixtures copy
the field shapes recorded from Rex 0.1.0 (1853) on 2026-11-07: program status
records carry `app`, `effective_app`, `owner{pid,name}`, `state` and
`updated_at`; `process` carries `child`, `foreground` and `home`; an empty Lua
array arrives as `{}`. No test starts a process.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import copy
import io
import json
import unittest

from teamlead import rex_herdr
from teamlead.rex_herdr import SNAPSHOT_LUA, lua_value, main

from tests.fakes import FakeCompleted

SESSION = "session:01a1b842-e767-7b37-afdb-b2e419df7ba4"
WINDOW = "window:01a1b842-e77a-7cc9-861d-5de9fbb3a015"
CLAUDE = "block:01a1b842-e767-7bc7-8f9c-8185791a48fb"
CODEX = "block:01a1b83a-64a3-7bfe-9b47-e05d551aa290"
SHELL = "block:01a1b840-e8da-735f-8757-2fb4e4af2c3d"
STALE = "block:01a1b83a-7e10-7bdb-b8e1-a57736ca541b"
OPENCODE = "block:01a1b83b-0000-7000-8000-000000000001"


def shell_process(pid):
    return {"child": {"argv0": "/bin/zsh", "name": "zsh", "pid": pid},
            "foreground": {"argv0": "-zsh", "name": "zsh", "pid": pid, "cwd": "/repo"}, "home": "/Users/u"}


def agent_process(shell_pid, pid, argv0, name):
    return {"child": {"argv0": "/bin/zsh", "name": "zsh", "pid": shell_pid},
            "foreground": {"argv0": argv0, "name": name, "pid": pid, "cwd": "/repo/wt"}, "home": "/Users/u"}


def record(app, state, pid, **extra):
    return dict({"app": app, "effective_app": app, "owner": {"name": app, "pid": pid}, "state": state,
                 "updated_at": "2026-11-07T17:57:59.780-05:00"}, **extra)


def block(block_id, label, process, records=None, rows=24):
    return {"block_id": block_id, "label": label, "window_id": WINDOW, "creator": rex_herdr.TERMINAL,
            "process": process, "records": records if records is not None else {}, "rows": rows}


def team_snapshot(claude_state="idle"):
    """Claude (named, reporting), Codex (named, reporting, runs under node),
    a bare shell, a block whose Claude exited leaving a `done` record behind,
    and OpenCode, which reports no status."""
    return {"session_id": SESSION, "label": "team", "revision": 7, "blocks": [
        block(CLAUDE, "claude", agent_process(100, 101, "/Users/u/.local/bin/claude", "2.1.319"),
              [record("claude-code", claude_state, 101)]),
        block(CODEX, "codex", agent_process(200, 201, "node", "node"), [record("codex", "working", 201)]),
        block(SHELL, "shell", shell_process(300)),
        block(STALE, "old", shell_process(400), [record("claude-code", "done", 401)]),
        block(OPENCODE, "oc", agent_process(500, 501, "opencode", "opencode")),
        {"block_id": "block:onboarding", "label": "Onboarding", "window_id": WINDOW, "creator": "com.superlogical.onboarding"},
    ]}


class FakeRex:
    """The `rex` and `ps` executables. Snapshots are served from a queue (the
    last one repeats); `do` label scripts and terminal writes are recorded."""

    def __init__(self, *snapshots, screens=None, ps=None, fail=None, on_send=None, ps_result=None, raises=None, fail_verb=None):
        self.snapshots = [copy.deepcopy(s) for s in snapshots] or [team_snapshot()]
        self.screens = screens or {}
        self.ps = ps or {}
        self.ps_result = ps_result
        self.raises = raises
        self.fail = fail
        self.fail_verb = fail_verb
        self.on_send = on_send
        self.calls, self.writes = [], []

    def current(self):
        return self.snapshots[0]

    def ps_line(self, pid):
        """`pid ppid args`: an override from `ps`, else derived from the
        snapshot: a foreground under a separate `child` has that child as its
        parent, and a shell's arguments are its bare login name."""
        if str(pid) in self.ps:
            return "{} {}\n".format(pid, self.ps[str(pid)])
        for b in self.current()["blocks"]:
            process = b.get("process") or {}
            fg, child = process.get("foreground"), process.get("child") or {}
            if fg and fg.get("pid") == pid:
                ppid = 1 if child.get("pid") == pid else child.get("pid")
                args = "-" + fg["name"] if fg.get("name") in rex_herdr.SHELLS else fg.get("argv0")
                return "{} {} {}\n".format(pid, ppid, args)
        return None

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.raises and argv[0] == self.raises[0]:
            raise self.raises[1]
        if argv[0] == "ps":
            if self.ps_result is not None:
                return self.ps_result
            lines = [line for line in (self.ps_line(int(pid)) for pid in argv[-1].split(",")) if line]
            return FakeCompleted(0 if len(lines) == len(argv[-1].split(",")) else 1, "".join(lines), "")
        if self.fail and self.fail_verb in (None, argv[1]):
            return FakeCompleted(1, "", self.fail)
        verb = argv[1]
        if verb == "do":
            script = argv[5]
            if script == SNAPSHOT_LUA:
                snapshot = self.snapshots[0]
                if len(self.snapshots) > 1:
                    self.snapshots.pop(0)
                return FakeCompleted(0, json.dumps(snapshot) + "\n")
            self.writes.append(("lua", script))
            if "set_block_label" in script:
                target = json.loads(script.split("([[", 1)[1].split("]])", 1)[0])
                label = json.loads(script.split("label = rex.json.decode([[", 1)[1].split("]])", 1)[0])
                for b in self.current()["blocks"]:
                    if b["block_id"] == target:
                        b["label"] = label
            return FakeCompleted(0, '[{"revision":8}]\n')
        if verb == "capture":
            text = self.screens.get(argv[argv.index("-b") + 1], "")
            return FakeCompleted(0, text if text.endswith("\n") else text + "\n")
        if verb == "api":
            payload = json.loads(argv[4])
            self.writes.append(("paste", payload["block_id"], payload["event"]["payload"]["text"]))
            return FakeCompleted(0, "{}\n")
        if verb == "send-key":
            self.writes.append(("keys", argv[5], argv[6:]))
            return FakeCompleted(0, "")
        if verb == "send":
            self.writes.append(("text", argv[5], argv[6]))
            if self.on_send:
                self.on_send(self)
            return FakeCompleted(0, "")
        raise AssertionError("unexpected rex call: {}".format(argv))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(rex, *argv, env=None):
    out, err, clock = io.StringIO(), io.StringIO(), Clock()
    env = {"REX_SESSION": SESSION} if env is None else env
    code = main(list(argv), env=env, runner=rex, stdout=out, stderr=err, sleep=clock.sleep, clock=clock)
    return code, out.getvalue(), err.getvalue()


def result(out):
    return json.loads(out)["result"]


def error_code(err):
    return json.loads(err)["error"]["code"]


class AgentListTest(unittest.TestCase):
    def test_lists_live_agents_with_kind_name_and_status(self):
        code, out, err = run(FakeRex(), "agent", "list")
        self.assertEqual(code, 0, err)
        agents = {a["pane_id"]: a for a in result(out)["agents"]}
        self.assertEqual(sorted(agents), sorted([CLAUDE, CODEX, OPENCODE]))
        self.assertEqual(agents[CLAUDE], {"agent": "claude", "name": "claude", "agent_status": "idle", "pane_id": CLAUDE,
                                          "terminal_id": CLAUDE, "workspace_id": SESSION, "tab_id": WINDOW, "cwd": "/repo/wt"})

    def test_kind_comes_from_program_status_when_the_process_is_node(self):
        agents = {a["pane_id"]: a for a in result(run(FakeRex(), "agent", "list")[1])["agents"]}
        self.assertEqual((agents[CODEX]["agent"], agents[CODEX]["agent_status"]), ("codex", "working"))

    def test_codex_under_node_is_found_from_its_command_line_before_it_reports(self):
        snapshot = team_snapshot()
        snapshot["blocks"][1]["records"] = {}
        rex = FakeRex(snapshot, ps={"201": "200 node /opt/homebrew/bin/codex"})
        agents = {a["pane_id"]: a for a in result(run(rex, "agent", "list")[1])["agents"]}
        self.assertEqual((agents[CODEX]["agent"], agents[CODEX]["agent_status"]), ("codex", "unknown"))

    def test_agent_without_program_status_reads_unknown(self):
        agents = {a["pane_id"]: a for a in result(run(FakeRex(), "agent", "list")[1])["agents"]}
        self.assertEqual((agents[OPENCODE]["agent"], agents[OPENCODE]["agent_status"]), ("opencode", "unknown"))

    def test_done_record_left_by_an_exited_agent_is_not_an_agent(self):
        panes = [a["pane_id"] for a in result(run(FakeRex(), "agent", "list")[1])["agents"]]
        self.assertNotIn(STALE, panes)
        self.assertNotIn(SHELL, panes)

    def test_stale_record_under_another_program_is_not_an_agent(self):
        snapshot = team_snapshot()
        snapshot["blocks"][3]["process"] = agent_process(400, 402, "vim", "vim")
        panes = [a["pane_id"] for a in result(run(FakeRex(snapshot), "agent", "list")[1])["agents"]]
        self.assertNotIn(STALE, panes)

    def test_program_named_after_an_agent_is_not_an_agent(self):
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = agent_process(300, 310, "vim", "vim")
        rex = FakeRex(snapshot, ps={"310": "300 vim codex"})
        panes = [a["pane_id"] for a in result(run(rex, "agent", "list")[1])["agents"]]
        self.assertNotIn(SHELL, panes)

    def test_malformed_block_list_fails_instead_of_reading_empty(self):
        snapshot = team_snapshot()
        snapshot["blocks"] = {"block": "not an array"}
        code, out, err = run(FakeRex(snapshot), "agent", "list")
        self.assertEqual((code, out, error_code(err)), (1, "", "rex_malformed"))

    def test_rex_default_label_is_no_name(self):
        snapshot = team_snapshot()
        snapshot["blocks"][0]["label"] = "shell"
        agents = {a["pane_id"]: a for a in result(run(FakeRex(snapshot), "agent", "list")[1])["agents"]}
        self.assertIsNone(agents[CLAUDE]["name"])

    def test_error_state_reads_done(self):
        out = run(FakeRex(team_snapshot("error")), "agent", "get", "claude")[1]
        self.assertEqual(result(out)["agent"]["agent_status"], "done")


class AgentGetTest(unittest.TestCase):
    def test_by_name_and_by_pane(self):
        for target in ("claude", CLAUDE):
            code, out, _ = run(FakeRex(), "agent", "get", target)
            self.assertEqual((code, result(out)["type"], result(out)["agent"]["pane_id"]), (0, "agent_info", CLAUDE))

    def test_released_name_is_agent_not_found(self):
        code, _, err = run(FakeRex(), "agent", "get", "old")
        self.assertEqual((code, error_code(err)), (1, "agent_not_found"))

    def test_duplicate_label_is_ambiguous(self):
        snapshot = team_snapshot()
        snapshot["blocks"][1]["label"] = "claude"
        code, _, err = run(FakeRex(snapshot), "agent", "get", "claude")
        self.assertEqual((code, error_code(err)), (1, "agent_target_ambiguous"))


class ReadTest(unittest.TestCase):
    SCREEN = "\n".join("row{}".format(i) for i in range(30))

    def test_visible_keeps_the_screen_rows_then_the_last_lines(self):
        rex = FakeRex(screens={CLAUDE: self.SCREEN})
        code, out, _ = run(rex, "agent", "read", "claude", "--source", "visible", "--lines", "3")
        self.assertEqual((code, result(out)), (0, {"type": "pane_text", "pane_id": CLAUDE, "text": "row27\nrow28\nrow29\n"}))
        self.assertEqual(result(run(rex, "pane", "read", CLAUDE, "--source", "visible")[1])["text"].count("\n"), 24)

    def test_recent_reads_the_scrollback(self):
        out = run(FakeRex(screens={CLAUDE: self.SCREEN}), "pane", "read", CLAUDE, "--source", "recent")[1]
        self.assertEqual(result(out)["text"].count("\n"), 30)

    def test_unwrapped_and_ansi_reach_rex_capture(self):
        rex = FakeRex(screens={CLAUDE: "x"})
        run(rex, "agent", "read", "claude", "--source", "recent-unwrapped", "--format", "ansi")
        capture = [c for c in rex.calls if c[1] == "capture"][0]
        self.assertEqual(capture[2:], ["-s", SESSION, "-b", CLAUDE, "--unwrap", "--format", "vt"])

    def test_failed_read_emits_no_text_and_the_error_envelope(self):
        for argv in (("agent", "read", "ghost"), ("pane", "read", "block:nope")):
            code, out, err = run(FakeRex(screens={CLAUDE: "x"}), *argv)
            self.assertEqual((code, out), (1, ""), argv)
            self.assertIn(error_code(err), ("agent_not_found", "pane_not_found"))

    def test_malformed_reads_emit_no_text_and_the_usage_envelope(self):
        for argv in (("pane", "read", CLAUDE, "--source", "everything"), ("agent", "read", "claude", "--lines", "ten"),
                     ("pane", "read"), ("agent", "read", "claude", "--source")):
            code, out, err = run(FakeRex(screens={CLAUDE: "x"}), *argv)
            self.assertEqual((code, out, error_code(err)), (2, "", "usage_error"), argv)


class PromptTest(unittest.TestCase):
    def test_pastes_then_presses_enter(self):
        rex = FakeRex()
        code, out, _ = run(rex, "agent", "prompt", "claude", "line one\nline two")
        self.assertEqual((code, result(out)["type"]), (0, "agent_prompted"))
        self.assertEqual(rex.writes, [("paste", CLAUDE, "line one\nline two"), ("keys", CLAUDE, ["enter"])])

    def test_blocked_agent_refuses_before_any_write(self):
        rex = FakeRex(team_snapshot("blocked"))
        code, _, err = run(rex, "agent", "prompt", "claude", "hi")
        self.assertEqual((code, error_code(err), rex.writes), (1, "agent_blocked", []))

    def test_wait_returns_the_settled_state(self):
        rex = FakeRex(team_snapshot("idle"), team_snapshot("idle"), team_snapshot("working"), team_snapshot("done"))
        code, out, err = run(rex, "agent", "prompt", "claude", "hi", "--wait", "--timeout", "60000")
        self.assertEqual(code, 0, err)
        self.assertEqual((result(out)["type"], result(out)["agent"]["agent_status"]), ("agent_status", "done"))

    def test_wait_without_a_lifecycle_change_is_stalled(self):
        code, _, err = run(FakeRex(team_snapshot("idle")), "agent", "prompt", "claude", "hi", "--wait")
        self.assertEqual((code, error_code(err)), (1, "agent_prompt_stalled"))


class WaitTest(unittest.TestCase):
    def test_until_state(self):
        rex = FakeRex(team_snapshot("working"), team_snapshot("working"), team_snapshot("idle"))
        code, out, _ = run(rex, "agent", "wait", "claude", "--until", "idle", "--timeout", "10000")
        self.assertEqual((code, result(out)["agent"]["agent_status"]), (0, "idle"))

    def test_budget_exhausted_is_timeout(self):
        code, _, err = run(FakeRex(team_snapshot("working")), "agent", "wait", "claude", "--until", "idle", "--timeout", "1000")
        self.assertEqual((code, error_code(err)), (1, "timeout"))

    def test_agent_exiting_mid_wait_is_agent_not_found(self):
        gone = team_snapshot()
        gone["blocks"][0]["process"] = shell_process(100)
        code, _, err = run(FakeRex(team_snapshot("working"), gone), "agent", "wait", "claude", "--timeout", "10000")
        self.assertEqual((code, error_code(err)), (1, "agent_not_found"))


class StartTest(unittest.TestCase):
    @staticmethod
    def launch(rex):
        b = rex.current()["blocks"][2]
        b["process"] = agent_process(300, 301, "/Users/u/.local/bin/claude", "2.1.319")
        b["records"] = [record("claude-code", "idle", 301)]

    def test_types_the_command_names_the_pane_and_returns_the_agent(self):
        rex = FakeRex(on_send=self.launch)
        code, out, err = run(rex, "agent", "start", "worker1", "--kind", "claude", "--pane", SHELL, "--", "--model", "haiku")
        self.assertEqual(code, 0, err)
        self.assertEqual(result(out)["argv"], ["claude", "--model", "haiku"])
        self.assertEqual((result(out)["agent"]["name"], result(out)["agent"]["agent"]), ("worker1", "claude"))
        self.assertIn(("text", SHELL, "claude --model haiku"), rex.writes)
        self.assertIn(("keys", SHELL, ["enter"]), rex.writes)

    def test_name_held_by_a_live_agent_is_taken(self):
        rex = FakeRex()
        code, _, err = run(rex, "agent", "start", "codex", "--kind", "claude", "--pane", SHELL)
        self.assertEqual((code, error_code(err), rex.writes), (1, "agent_name_taken", []))

    def test_released_name_can_be_reused(self):
        code, _, err = run(FakeRex(on_send=self.launch), "agent", "start", "old", "--kind", "claude", "--pane", SHELL)
        self.assertEqual(code, 0, err)

    def test_pane_running_a_program_is_refused(self):
        rex = FakeRex()
        code, _, err = run(rex, "agent", "start", "w2", "--kind", "claude", "--pane", CLAUDE)
        self.assertEqual((code, error_code(err), rex.writes), (1, "pane_busy", []))

    def test_agent_without_a_ready_prompt_times_out(self):
        def trust_dialog(rex):
            # Claude Code reports no status while its trust dialog is up.
            rex.current()["blocks"][2]["process"] = agent_process(300, 301, "/Users/u/.local/bin/claude", "2.1.319")

        code, _, err = run(FakeRex(on_send=trust_dialog), "agent", "start", "w2", "--kind", "claude", "--pane", SHELL, "--timeout", "1000")
        self.assertEqual((code, error_code(err)), (1, "timeout"))
        self.assertIn("startup dialog", json.loads(err)["error"]["message"])

    def test_agent_that_never_appears_times_out(self):
        code, _, err = run(FakeRex(), "agent", "start", "w2", "--kind", "claude", "--pane", SHELL, "--timeout", "1000")
        self.assertEqual((code, error_code(err)), (1, "timeout"))

    def test_reserved_or_malformed_name_is_a_usage_error(self):
        for name in ("shell", "Worker", "9lives"):
            self.assertEqual(run(FakeRex(), "agent", "start", name, "--kind", "claude", "--pane", SHELL)[0], 2, name)

    def test_unknown_kind_is_a_usage_error(self):
        self.assertEqual(run(FakeRex(), "agent", "start", "w2", "--kind", "vim", "--pane", SHELL)[0], 2)


class RenameTest(unittest.TestCase):
    def test_rename_relabels_the_block(self):
        rex = FakeRex()
        code, out, err = run(rex, "agent", "rename", "claude", "lead")
        self.assertEqual(code, 0, err)
        self.assertEqual(result(out)["agent"]["name"], "lead")

    def test_rename_to_a_live_name_is_taken(self):
        code, _, err = run(FakeRex(), "agent", "rename", "claude", "codex")
        self.assertEqual((code, error_code(err)), (1, "agent_name_taken"))

    def test_clear_restores_no_name(self):
        rex = FakeRex()
        run(rex, "agent", "rename", "claude", "--clear")
        label_script = [w[1] for w in rex.writes if w[0] == "lua"][0]
        self.assertIn('label = rex.json.decode([[""]])', label_script)


class PaneTest(unittest.TestCase):
    def test_get_reports_identity_and_status(self):
        code, out, _ = run(FakeRex(), "pane", "get", CLAUDE)
        self.assertEqual(code, 0)
        self.assertEqual(result(out)["pane"], {"pane_id": CLAUDE, "terminal_id": CLAUDE, "workspace_id": SESSION, "tab_id": WINDOW,
                                               "label": "claude", "revision": 7, "scroll": {"offset_from_bottom": 0},
                                               "agent_status": "idle", "agent": "claude"})

    def test_unknown_pane(self):
        code, _, err = run(FakeRex(), "pane", "get", "block:nope")
        self.assertEqual((code, error_code(err)), (1, "pane_not_found"))

    def test_process_info_for_a_bare_shell_lists_the_shell(self):
        out = run(FakeRex(), "pane", "process-info", "--pane", SHELL)[1]
        self.assertEqual(result(out)["process_info"], {"pane_id": SHELL, "shell_pid": 300,
                                                       "foreground_processes": [{"pid": 300, "name": "zsh"}]})

    def test_process_info_names_the_agent_its_argv_and_its_parent_shell(self):
        # Rex's `child` (199) can be a login wrapper, not the shell; the agent's parent is the shell.
        snapshot = team_snapshot()
        snapshot["blocks"][1]["process"]["child"]["pid"] = 199
        out = run(FakeRex(snapshot, ps={"201": "200 node /opt/homebrew/bin/codex --yolo"}), "pane", "process-info", "--pane", CODEX)[1]
        self.assertEqual(result(out)["process_info"], {"pane_id": CODEX, "shell_pid": 200, "foreground_processes": [
            {"pid": 201, "name": "codex", "argv": ["node", "/opt/homebrew/bin/codex", "--yolo"]}]})

    def test_unquotable_process_arguments_do_not_crash(self):
        code, out, err = run(FakeRex(ps={"101": "100 claude --append-system-prompt don't"}), "pane", "process-info", "--pane", CLAUDE)
        self.assertEqual(code, 0, err)
        self.assertEqual(result(out)["process_info"]["foreground_processes"], [{"pid": 101, "name": "claude"}])

    def test_rename_of_an_agent_pane_is_refused_and_keeps_its_name(self):
        rex = FakeRex()
        code, _, err = run(rex, "pane", "rename", CLAUDE, "developer · task-7")
        self.assertEqual((code, error_code(err), rex.writes), (1, "pane_title_unsupported", []))
        self.assertEqual(result(run(rex, "agent", "get", "claude")[1])["agent"]["pane_id"], CLAUDE)

    def test_rename_of_a_bare_pane_relabels_it(self):
        rex = FakeRex()
        code, _, err = run(rex, "pane", "rename", SHELL, "spare")
        self.assertEqual(code, 0, err)
        self.assertEqual([b["label"] for b in rex.current()["blocks"] if b["block_id"] == SHELL], ["spare"])

    def test_pane_running_a_shell_script_is_busy(self):
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = agent_process(300, 320, "/bin/bash", "bash")
        rex = FakeRex(snapshot, ps={"320": "300 bash ./build.sh"})
        code, _, err = run(rex, "agent", "start", "w2", "--kind", "claude", "--pane", SHELL)
        self.assertEqual((code, error_code(err), rex.writes), (1, "pane_busy", []))
        info = result(run(rex, "pane", "process-info", "--pane", SHELL)[1])["process_info"]
        self.assertEqual((info["shell_pid"], [p["pid"] for p in info["foreground_processes"]]), (300, [320]))

    def test_block_whose_own_shell_runs_a_script_is_busy(self):
        # `rex split -- bash ./build.sh`: the block's child is the script's shell.
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = {"child": {"argv0": "/bin/bash", "name": "bash", "pid": 340},
                                            "foreground": {"argv0": "/bin/bash", "name": "bash", "pid": 340, "cwd": "/repo"}}
        rex = FakeRex(snapshot, ps={"340": "1 /bin/bash ./build.sh"})
        self.assertEqual(error_code(run(rex, "agent", "start", "w2", "--kind", "claude", "--pane", SHELL)[2]), "pane_busy")

    def test_nested_shell_is_not_the_panes_own_shell(self):
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = agent_process(300, 330, "bash", "bash")
        rex = FakeRex(snapshot, ps={"330": "300 bash"})
        self.assertEqual(error_code(run(rex, "agent", "start", "w2", "--kind", "claude", "--pane", SHELL)[2]), "pane_busy")

    def test_shell_under_a_login_wrapper_is_the_panes_shell(self):
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = {"child": {"argv0": None, "name": "login", "pid": 299},
                                            "foreground": {"argv0": "-zsh", "name": "zsh", "pid": 300, "cwd": "/repo"}}
        info = result(run(FakeRex(snapshot), "pane", "process-info", "--pane", SHELL)[1])["process_info"]
        self.assertEqual(info, {"pane_id": SHELL, "shell_pid": 300, "foreground_processes": [{"pid": 300, "name": "zsh"}]})

    def test_pane_rex_cannot_describe_is_busy(self):
        snapshot = team_snapshot()
        snapshot["blocks"][2]["process"] = None
        self.assertEqual(error_code(run(FakeRex(snapshot), "agent", "start", "w2", "--kind", "claude", "--pane", SHELL)[2]), "pane_busy")

    def test_ps_tool_failure_is_reported(self):
        for rex in (FakeRex(ps_result=FakeCompleted(2, "", "ps: illegal option")), FakeRex(raises=("ps", FileNotFoundError("ps")))):
            code, out, err = run(rex, "agent", "list")
            self.assertEqual((code, out, error_code(err)), (1, "", "ps_failed"))

    def test_send_text_and_keys(self):
        rex = FakeRex()
        run(rex, "pane", "send-text", SHELL, "/usage")
        run(rex, "pane", "send-keys", SHELL, "escape", "enter")
        self.assertEqual(rex.writes, [("text", SHELL, "/usage"), ("keys", SHELL, ["esc", "enter"])])


class WaitOutputTest(unittest.TestCase):
    def test_literal_match(self):
        rex = FakeRex(screens={CLAUDE: "work\nREPORT: /r/x.md"})
        code, out, _ = run(rex, "pane", "wait-output", "--match", "REPORT: ", "--source", "visible", "--lines", "40", CLAUDE)
        self.assertEqual((code, result(out)["matched_line"]), (0, "REPORT: /r/x.md"))

    def test_no_match_within_budget_is_timeout(self):
        code, _, err = run(FakeRex(screens={CLAUDE: "work"}), "pane", "wait-output", CLAUDE, "--match", "REPORT: ", "--timeout", "500")
        self.assertEqual((code, error_code(err)), (1, "timeout"))

    def test_needs_exactly_one_matcher(self):
        self.assertEqual(run(FakeRex(), "pane", "wait-output", CLAUDE)[0], 2)
        self.assertEqual(run(FakeRex(), "pane", "wait-output", CLAUDE, "--match", "a", "--regex", "b")[0], 2)


class WorkspaceTest(unittest.TestCase):
    def test_list_is_the_session(self):
        out = run(FakeRex(), "workspace", "list")[1]
        self.assertEqual(result(out)["workspaces"], [{"workspace_id": SESSION, "name": "team"}])

    def test_rename_of_another_workspace_is_refused(self):
        rex = FakeRex()
        code, _, err = run(rex, "workspace", "rename", "session:other", "x")
        self.assertEqual((code, error_code(err), rex.writes), (1, "workspace_not_found", []))


class EnvelopeTest(unittest.TestCase):
    def test_outside_rex(self):
        code, _, err = run(FakeRex(), "agent", "list", env={})
        self.assertEqual((code, error_code(err)), (1, "not_in_rex"))

    def test_unsupported_command_is_a_usage_error(self):
        code, _, err = run(FakeRex(), "agent", "explain", "claude")
        self.assertEqual((code, error_code(err)), (2, "usage_error"))
        self.assertIn("unsupported command", json.loads(err)["error"]["message"])

    def test_group_without_a_verb_is_a_usage_error(self):
        self.assertEqual(run(FakeRex(), "agent")[0], 2)

    def test_rex_failure_is_reported_with_its_stderr(self):
        code, _, err = run(FakeRex(fail="Error: session not found"), "agent", "list")
        self.assertEqual((code, error_code(err)), (1, "rex_failed"))
        self.assertIn("session not found", json.loads(err)["error"]["message"])

    def test_failure_diagnostics_withhold_typed_text_and_redact_stderr(self):
        token = "ghp_" + "A" * 36
        rex = FakeRex(fail="Error: rejected input " + token, fail_verb="send")
        code, _, err = run(rex, "pane", "send-text", SHELL, "export GITHUB_TOKEN=" + token)
        self.assertEqual((code, error_code(err)), (1, "rex_failed"))
        self.assertNotIn(token, err)
        self.assertIn("rex send -s {} -b {} <".format(SESSION, SHELL), json.loads(err)["error"]["message"])

    def test_unexpected_failure_is_an_envelope_with_no_stdout(self):
        for argv in (("agent", "read", "claude"), ("pane", "read", CLAUDE)):
            code, out, err = run(FakeRex(raises=("rex", RuntimeError("boom"))), *argv)
            self.assertEqual((code, out, error_code(err)), (1, "", "internal_error"), argv)

    def test_missing_rex_binary(self):
        def missing(argv):
            raise FileNotFoundError(argv[0])

        code, _, err = run(missing, "agent", "list")
        self.assertEqual((code, error_code(err)), (1, "rex_not_found"))

    def test_rex_bin_override(self):
        rex = FakeRex()
        run(rex, "agent", "list", env={"REX_SESSION": SESSION, "TEAMLEAD_REX_BIN": "/opt/rex"})
        self.assertEqual(rex.calls[0][0], "/opt/rex")


class LuaValueTest(unittest.TestCase):
    def test_closing_bracket_in_the_value_raises_the_bracket_level(self):
        expression = lua_value("a]]b")
        self.assertEqual(expression, 'rex.json.decode([=["a]]b"]=])')


if __name__ == "__main__":
    unittest.main()
