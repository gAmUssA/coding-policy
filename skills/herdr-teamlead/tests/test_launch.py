"""Relaunches preserve pane ownership and prove the requested launch argv."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.config import Agent
from teamlead.errors import AgentBusyError, ConfigError, HerdrError
from teamlead.launch import restart_worker, start_worker, verify_running


class Client:
    def __init__(self):
        self.info = {"name": "claude", "agent": "claude", "pane_id": "w1:p2",
                     "terminal_id": "term-2", "agent_status": "idle", "agent_session": {"value": "session-1"}}
        self.process = {"name": "claude", "pid": 200, "argv": ["claude", "--model", "sonnet-5", "--effort", "high"]}
        self.calls = []
        self.terminated = False
        self.shell_returns = True
        self.reply_argv: list[str] | None = None
        self.composer = "❯ "

    def agent_get(self, name):
        self.calls.append(("get", name))
        return copy.deepcopy(self.info)

    def agent_read(self, name, **kwargs):
        return "Ready\n" + self.composer

    def pane_process_info(self, pane):
        foreground = ([{"pid": 100, "name": "zsh", "argv": ["zsh"]}]
                      if self.terminated and self.shell_returns else [copy.deepcopy(self.process)])
        return {"pane_id": pane, "shell_pid": 100, "foreground_processes": foreground}

    def terminate_process(self, pid):
        self.calls.append(("terminate", pid))
        self.terminated = True

    def agent_start(self, name, kind, pane, flags):
        self.calls.append(("start", name, kind, pane, flags))
        return {"agent": self.info, "argv": self.reply_argv if self.reply_argv is not None else [kind] + flags}

    def process_args(self, pid):
        self.calls.append(("ps", pid))
        return ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]


def worker():
    return Agent("claude", "claude", "/usage", "Current week", "visible", "/clear", composer_glyph="❯ ")


TIER = {"model": "opus-5", "effort": "high"}


class LaunchTest(unittest.TestCase):
    def test_fresh_round_terminates_only_the_foreground_agent_and_starts_requested_flags(self):
        client = Client()
        proof = restart_worker(client, worker(), "w1:p2", TIER, sleep=lambda _: None)
        self.assertIn(("terminate", 200), client.calls)
        self.assertEqual(proof["argv"], ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"])
        self.assertEqual(proof["source"], "launch_argv")

    def test_busy_or_changed_pane_never_terminates(self):
        for state in ("working", "blocked", "unknown"):
            client = Client()
            client.info["agent_status"] = state
            with self.subTest(state=state), self.assertRaises(AgentBusyError):
                restart_worker(client, worker(), "w1:p2", TIER, sleep=lambda _: None)
            self.assertFalse(client.terminated)

    def test_unaccounted_composer_is_not_discarded(self):
        client = Client()
        client.composer = "❯ unfinished operator text"
        with self.assertRaises(HerdrError):
            restart_worker(client, worker(), "w1:p2", TIER, sleep=lambda _: None)
        self.assertFalse(client.terminated)

    def test_no_shell_return_never_starts(self):
        client = Client()
        client.shell_returns = False
        with self.assertRaises(HerdrError):
            restart_worker(client, worker(), "w1:p2", TIER, sleep=lambda _: None)
        self.assertFalse(any(call[0] == "start" for call in client.calls))

    def test_start_that_silently_drops_effort_is_unproven(self):
        client = Client()
        client.reply_argv = ["claude", "--model", "opus-5"]
        with self.assertRaises(HerdrError):
            start_worker(client, worker(), "w1:p2", TIER)

    def test_start_in_the_wrong_pane_is_unproven(self):
        client = Client()
        client.info["pane_id"] = "w2:p3"
        with self.assertRaises(HerdrError):
            start_worker(client, worker(), "w1:p2", TIER)

    def test_existing_worker_uses_live_arguments_without_relaunch(self):
        client = Client()
        client.process["argv"] = ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"]
        proof = verify_running(client, worker(), "w1:p2", TIER)
        self.assertEqual(proof["source"], "process_argv")
        self.assertEqual(client.calls, [])

    def test_missing_process_argv_uses_the_same_foreground_pid(self):
        client = Client()
        client.process["argv"] = None
        proof = verify_running(client, worker(), "w1:p2", TIER)
        self.assertEqual(proof["pid"], 200)
        self.assertEqual(client.calls, [("ps", 200)])

    def test_permission_alias_normalizes_to_one_yolo_flag(self):
        client = Client()
        agent = worker()
        agent.launch_args = ("--permission-mode", "bypassPermissions")
        proof = start_worker(client, agent, "w1:p2", TIER)
        self.assertEqual(proof["argv"], ["claude", "--dangerously-skip-permissions", "--model", "opus-5", "--effort", "high"])

    def test_restrictive_options_refuse_before_termination_or_start(self):
        for action in (start_worker, restart_worker):
            client = Client()
            agent = worker()
            agent.launch_args = ("--permission-mode", "acceptEdits")
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ConfigError, "remove restrictive.*config.json"):
                action(client, agent, "w1:p2", TIER)
            self.assertEqual(client.calls, [])
            self.assertFalse(client.terminated)

    def test_missing_yolo_flag_refuses_start_and_live_proof(self):
        for action in (start_worker, verify_running):
            client = Client()
            argv = ["claude", "--model", "opus-5", "--effort", "high"]
            client.reply_argv = argv
            client.process["argv"] = argv
            with self.subTest(action=action.__name__), self.assertRaisesRegex(HerdrError, "launch options"):
                action(client, worker(), "w1:p2", TIER)
            self.assertFalse(client.terminated)


if __name__ == "__main__":
    unittest.main()
