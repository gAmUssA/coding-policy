"""Verified worker starts and fresh-round relaunches.

Herdr 0.8.2's bundled schema names agent.start's {agent, argv} result and
pane.process_info's foreground process records. The installed CLI returns
JSON for both. Older builds without that contract fail before dispatch.
https://herdr.dev/docs/socket-api/

Relaunch terminates only the identified, idle foreground agent after an empty
composer check, waits for its shell, then starts the selected tier. It never
terminates a working/blocked agent or guesses a PID from a transcript.
"""

import time
from pathlib import PurePath

from .composer import ensure_ready
from .errors import AgentBusyError, HerdrError
from .herdr import READY_STATES
from .tiers import launch_flags, verify_argv, verify_worker_permissions, worker_launch_args

SHELL_POLL_ATTEMPTS = 30
SHELL_POLL_INTERVAL = 0.2


def foreground_agent(client, pane, kind):
    info = client.pane_process_info(pane)
    matches = []
    for process in info.get("foreground_processes", []):
        if not isinstance(process, dict):
            raise HerdrError("Malformed foreground-process record; inspect the pane.", {})
        argv = process.get("argv")
        name = process.get("name")
        if name == kind or (isinstance(argv, list) and argv and isinstance(argv[0], str) and PurePath(argv[0]).name == kind):
            if argv is None:
                argv = client.process_args(process.get("pid"))
            matches.append({**process, "argv": argv})
    if len(matches) != 1:
        raise HerdrError("Cannot identify one {} foreground process in {}; inspect the pane before relaunch.".format(kind, pane), {})
    return matches[0]


def verify_running(client, agent, pane, tier):
    launch_args = worker_launch_args(agent.kind, agent.launch_args)
    process = foreground_agent(client, pane, agent.kind)
    proof = verify_argv(agent.kind, tier, process["argv"], launch_args)
    return {**proof, "source": "process_argv", "pid": process["pid"], "pane_id": pane}


def verify_running_permissions(client, agent, pane):
    worker_launch_args(agent.kind, agent.launch_args)
    process = foreground_agent(client, pane, agent.kind)
    verify_worker_permissions(agent.kind, process["argv"])


def start_worker(client, agent, pane, tier, before_start=None):
    launch_args = worker_launch_args(agent.kind, agent.launch_args)
    flags = launch_args + launch_flags(agent.kind, tier)
    if before_start is not None:
        before_start()
    result = client.agent_start(agent.name, agent.kind, pane, flags)
    info = result.get("agent") if isinstance(result, dict) else None
    if not isinstance(info, dict) or (
        info.get("pane_id") != pane or info.get("name") != agent.name
        or info.get("agent") != agent.kind or info.get("agent_status") not in READY_STATES
    ):
        raise HerdrError("Started worker identity or readiness differs from the requested pane and kind; no brief was sent.", {})
    proof = verify_argv(agent.kind, tier, result.get("argv"), launch_args)
    return {**proof, "pane_id": pane}


def restart_worker(client, agent, pane, tier, sleep=time.sleep, before_transition=None, before_start=None):
    worker_launch_args(agent.kind, agent.launch_args)
    if not isinstance(pane, str) or not pane or not agent.composer_glyph:
        raise HerdrError("Tier relaunch needs a live pane and configured composer glyph; fix the agent config.", {})
    info = client.agent_get(agent.name)
    if (info.get("agent_status") not in READY_STATES or info.get("pane_id") != pane
            or info.get("name") != agent.name or info.get("agent") != agent.kind
            or not isinstance(info.get("terminal_id"), str) or not info["terminal_id"]):
        raise AgentBusyError("Worker is no longer idle in the planned pane; wait before relaunch.", {})
    ensure_ready(client, agent, pane_id=pane, sleep=sleep)
    process = foreground_agent(client, pane, agent.kind)
    # Recheck native occupant and readiness immediately before termination.
    fresh = client.agent_get(agent.name)
    if (fresh.get("agent_status") not in READY_STATES or fresh.get("pane_id") != pane
            or fresh.get("terminal_id") != info.get("terminal_id")
            or fresh.get("agent_session") != info.get("agent_session")):
        raise AgentBusyError("Worker changed during relaunch checks; no process was terminated.", {})
    if foreground_agent(client, pane, agent.kind).get("pid") != process.get("pid"):
        raise HerdrError("Foreground PID changed during relaunch checks; inspect the pane.", {})
    if before_transition is not None:
        before_transition()
    client.terminate_process(process.get("pid"))
    for attempt in range(SHELL_POLL_ATTEMPTS):
        current = client.pane_process_info(pane)
        shell = current.get("shell_pid")
        foreground = current.get("foreground_processes", [])
        if (isinstance(shell, int) and not isinstance(shell, bool) and shell > 0
                and isinstance(foreground, list) and len(foreground) == 1
                and isinstance(foreground[0], dict) and foreground[0].get("pid") == shell):
            return start_worker(client, agent, pane, tier, before_start=before_start)
        if attempt + 1 < SHELL_POLL_ATTEMPTS:
            sleep(SHELL_POLL_INTERVAL)
    raise HerdrError("Worker termination did not return the pane to its shell; inspect it before retrying. No start or brief was sent.", {})
