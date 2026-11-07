"""Tests for the worker identity `rex-herdr.sh agent start` injects
(docs/adr/0016-rex-team-mode-through-herdr-environment.md).

The typed shell line is parsed back with `shlex` and its `env` assignments
compared to the identity Herdr gives a managed pane, so the assertions hold
whatever quoting the adapter chooses. No test starts a process.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import shlex
import unittest

from teamlead.rex_herdr import ADAPTER_PATH, worker_command

from tests import test_rex_herdr as base
from tests.test_rex_herdr import SESSION, SHELL, FakeRex, result, run


def parse(line):
    words = shlex.split(line)
    assert words[0] == "env", line
    assignments, index = {}, 1
    while "=" in words[index]:
        key, value = words[index].split("=", 1)
        assignments[key] = value
        index += 1
    return assignments, words[index:]


class WorkerCommandTest(unittest.TestCase):
    def test_carries_the_herdr_identity_of_the_worker_pane(self):
        assignments, argv = parse(worker_command(["claude", "--model", "haiku"], "block:w1", "session:s"))
        self.assertEqual(assignments, {
            "HERDR_ENV": "1", "HERDR_PANE_ID": "block:w1", "HERDR_WORKSPACE_ID": "session:s",
            "HERDR_BIN": ADAPTER_PATH, "TEAMLEAD_HERDR_BIN": ADAPTER_PATH, "TEAMLEAD_BACKEND": "rex"})
        self.assertEqual(argv, ["claude", "--model", "haiku"])

    def test_agent_arguments_survive_quoting(self):
        tricky = ["codex", "--config", "model_instructions=it's \"quoted\"; $(rm -rf /)"]
        self.assertEqual(parse(worker_command(tricky, "block:w1", "session:s"))[1], tricky)

    def test_adapter_path_is_the_co_shipped_launcher(self):
        self.assertEqual(_os.path.basename(ADAPTER_PATH), "rex-herdr.sh")
        self.assertTrue(_os.path.isfile(ADAPTER_PATH))


class AgentStartIdentityTest(unittest.TestCase):
    def test_start_types_the_worker_identity_and_returns_the_bare_argv(self):
        rex = FakeRex(on_send=base.StartTest.launch)
        code, out, err = run(rex, "agent", "start", "worker1", "--kind", "claude", "--pane", SHELL, "--", "--model", "haiku")
        self.assertEqual(code, 0, err)
        self.assertEqual(result(out)["argv"], ["claude", "--model", "haiku"])
        typed = [w[2] for w in rex.writes if w[0] == "text"]
        assignments, argv = parse(typed[0])
        self.assertEqual((assignments["HERDR_PANE_ID"], assignments["HERDR_WORKSPACE_ID"], argv),
                         (SHELL, SESSION, ["claude", "--model", "haiku"]))


if __name__ == "__main__":
    unittest.main()
