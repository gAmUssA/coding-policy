"""The core's own Herdr consumers, run against the Rex adapter.

`HerdrClient` is wired to a runner that calls `teamlead.rex_herdr.main` in
process, and the adapter's `rex` boundary is the `FakeRex` of
`tests/test_rex_herdr.py`. Each test runs an unchanged core function
(`launch`, `restoration`) and asserts the decision it reaches, so a shape the
adapter returns that the core reads differently fails here. No test starts a
process.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import io
import unittest
from types import SimpleNamespace

from teamlead import launch, restoration, rex_herdr
from teamlead.errors import HerdrError
from teamlead.herdr import HerdrClient, default_backend

from tests.fakes import FakeCompleted
from tests.test_rex_herdr import CLAUDE, SESSION, SHELL, Clock, FakeRex, agent_process, record, team_snapshot


def client_over(rex, backend="rex"):
    clock = Clock()

    def bridge(argv):
        out, err = io.StringIO(), io.StringIO()
        code = rex_herdr.main(list(argv[1:]), env={"REX_SESSION": SESSION}, runner=rex,
                              stdout=out, stderr=err, sleep=clock.sleep, clock=clock)
        return FakeCompleted(code, out.getvalue(), err.getvalue())

    return HerdrClient(binary="rex-herdr.sh", runner=bridge, backend=backend)


def launch_into_shell(rex):
    """The typed command starts Claude under the shell, which then reports idle."""
    block = rex.current()["blocks"][2]
    block["process"] = agent_process(300, 301, "/Users/u/.local/bin/claude", "2.1.319")
    block["records"] = [record("claude-code", "idle", 301)]


class LaunchTest(unittest.TestCase):
    def test_bare_shell_pane_holds_only_its_shell(self):
        self.assertTrue(launch.holds_only_shell(client_over(FakeRex()), SHELL))

    def test_agent_pane_does_not_hold_only_its_shell(self):
        rex = FakeRex(ps={"101": "100 claude --model opus-5"})
        self.assertFalse(launch.holds_only_shell(client_over(rex), CLAUDE))

    def test_start_worker_accepts_a_ready_tiered_start(self):
        rex = FakeRex(on_send=launch_into_shell)
        agent = SimpleNamespace(name="worker1", kind="claude", launch_args=[])
        proof = launch.start_worker(client_over(rex), agent, SHELL, {"model": "opus-5", "effort": "high"})
        self.assertEqual(proof["pane_id"], SHELL)
        typed = [w[2] for w in rex.writes if w[0] == "text"][0]
        self.assertTrue(typed.startswith("claude ") and "--model opus-5 --effort high" in typed, typed)


class ReadTest(unittest.TestCase):
    def test_rex_backend_reads_the_pane_text(self):
        rex = FakeRex(screens={CLAUDE: "work\nREPORT: /r/x.md"})
        self.assertEqual(client_over(rex).agent_read("claude", source="visible", lines=2), "work\nREPORT: /r/x.md\n")
        self.assertEqual(client_over(rex).pane_read(CLAUDE, 1), "REPORT: /r/x.md\n")

    def test_rex_backend_refuses_text_that_is_not_a_pane_text_result(self):
        raw = HerdrClient(binary="herdr", runner=lambda argv: FakeCompleted(0, "REPORT: /r/x.md\n", ""), backend="rex")
        with self.assertRaises(HerdrError):
            raw.pane_read("w1:p1", 1)

    def test_herdr_backend_keeps_herdr_raw_rows(self):
        raw = HerdrClient(binary="herdr", runner=lambda argv: FakeCompleted(0, "REPORT: /r/x.md\n", ""), backend="herdr")
        self.assertEqual(raw.pane_read("w1:p1", 1), "REPORT: /r/x.md\n")

    def test_backend_setting(self):
        self.assertEqual((default_backend({}), default_backend({"TEAMLEAD_BACKEND": "rex"})), ("herdr", "rex"))
        with self.assertRaises(HerdrError):
            default_backend({"TEAMLEAD_BACKEND": "tmux"})


class RestorationTest(unittest.TestCase):
    def test_release_completes_once_the_agent_exits_to_its_shell(self):
        # The shell PID is archived while Claude runs under shell 100 (Rex's
        # `child`, 99, is a login wrapper), then the release wait reads the pane
        # after Claude exited: the same shell PID and a released name.
        archived = client_over(FakeRex(ps={"101": "100 claude"})).pane_process_info(CLAUDE)["shell_pid"]
        after = team_snapshot()
        after["blocks"][0]["process"] = {"child": {"argv0": None, "name": "login", "pid": 99},
                                         "foreground": {"argv0": "-zsh", "name": "zsh", "pid": 100, "cwd": "/repo"}}
        released = restoration.await_release(client_over(FakeRex(after)), "claude", CLAUDE, archived, 101, sleep=lambda _: None)
        self.assertEqual(released, {"attempts": 1, "shell_pid": 100})


if __name__ == "__main__":
    unittest.main()
