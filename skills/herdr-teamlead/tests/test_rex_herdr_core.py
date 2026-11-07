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
from teamlead.herdr import HerdrClient

from tests.fakes import FakeCompleted
from tests.test_rex_herdr import CLAUDE, SESSION, SHELL, Clock, FakeRex, agent_process, record, team_snapshot


def client_over(rex):
    clock = Clock()

    def bridge(argv):
        out, err = io.StringIO(), io.StringIO()
        code = rex_herdr.main(list(argv[1:]), env={"REX_SESSION": SESSION}, runner=rex,
                              stdout=out, stderr=err, sleep=clock.sleep, clock=clock)
        return FakeCompleted(code, out.getvalue(), err.getvalue())

    return HerdrClient(binary="rex-herdr.sh", runner=bridge)


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


class RestorationTest(unittest.TestCase):
    def test_pane_back_at_its_shell_reads_shell(self):
        state = restoration._pane_state(client_over(FakeRex()), SHELL, 300, 301)
        self.assertEqual(state, "shell")

    def test_shell_pid_is_stable_across_an_agent_run(self):
        # Archived while Claude ran under shell 100, then read after it exited.
        rex = FakeRex(ps={"101": "100 claude"})
        archived = client_over(rex).pane_process_info(CLAUDE)["shell_pid"]
        after = team_snapshot()
        after["blocks"][0]["process"] = {"child": {"argv0": None, "name": "login", "pid": 99},
                                         "foreground": {"argv0": "-zsh", "name": "zsh", "pid": 100, "cwd": "/repo"}}
        state = restoration._pane_state(client_over(FakeRex(after)), CLAUDE, archived, 101)
        self.assertEqual((archived, state), (100, "shell"))


if __name__ == "__main__":
    unittest.main()
