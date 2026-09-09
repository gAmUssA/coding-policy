"""Post-stop release gate and same-name resumed start for a retained worker.

The lead stops a retained developer's own idle foreground process to restore
its YOLO mode (`references/dispatch-recovery.md`, Same-session YOLO
restoration). Herdr keeps the old agent name reserved briefly after the
process exits, and `agent start` under that name refuses with
`agent_name_taken` until the reservation lapses. This module owns the
deterministic part of the restoration: the bounded wait for the pane to hold
only its shell AND for Herdr to release the name, then the bounded same-name
start with the lead's already verified resume argv, retried only after a
name-reservation refusal and only once the pane and name re-prove released.

It chooses nothing. The lead supplies the archived name, kind, pane, shell
PID, the PID it stopped, and the resume argv; the helper never picks another
session, worker, pane, model, or provider, never renames, never sends a brief,
and never writes owner state. Every judgment the reference keeps with the lead
-- the empty composer, the archived identity, the matching task and tier, the
retrospective, the readiness turn -- stays there.

Release predicates: the pane is released when Herdr's foreground list for it
is exactly its archived shell PID; the name is released when `agent get`
answers `agent_not_found`. A foreground PID that is neither the shell nor the
stopped process, a shell PID other than the archived one, and a name bound to
another pane are refusals, never something to wait out. A start fails for any
reason other than `agent_name_taken` is never retried: Herdr may already have
started the process, and a second start would duplicate it.
"""

import time
from pathlib import PurePath

from .errors import HerdrError, UsageError
from .herdr import READY_STATES, error_code
from .tiers import verify_worker_permissions

#: How many times to re-read the pane and the name before giving up, and how
#: long to wait between reads: 15 s in total. A name Herdr has not released by
#: then needs a human's eyes, not a longer loop.
RELEASE_POLL_ATTEMPTS = 60
RELEASE_POLL_INTERVAL_SEC = 0.25

#: How many `agent_name_taken` refusals may each be followed by a fresh release
#: wait and another start. Every other start failure ends the restoration.
NAME_TAKEN_RETRIES = 2

#: Herdr's own error codes this module branches on.
NAME_RELEASED = "agent_not_found"
NAME_TAKEN = "agent_name_taken"

COMMANDS = frozenset({"restore-session"})


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _foreground_pids(info, pane):
    """Herdr's shell PID and foreground PIDs for `pane`; malformed data raises."""
    shell = info.get("shell_pid")
    if not _positive_int(shell):
        raise HerdrError(
            "Herdr reported no shell PID for pane {}; inspect the pane by hand before restarting anything.".format(pane),
            {"pane": pane},
        )
    foreground = info.get("foreground_processes")
    if not isinstance(foreground, list):
        raise HerdrError(
            "Herdr reported no foreground process list for pane {}; inspect the pane by hand before restarting anything.".format(pane),
            {"pane": pane},
        )
    pids = []
    for process in foreground:
        if not isinstance(process, dict) or not _positive_int(process.get("pid")):
            raise HerdrError(
                "Herdr reported a malformed foreground process record for pane {}; inspect the pane by hand before restarting anything.".format(pane),
                {"pane": pane},
            )
        pids.append(process["pid"])
    return shell, pids


def _pane_state(client, pane, shell_pid, stopped_pid):
    """`shell` once only the archived shell is in the foreground, `stopping` while
    the stopped process still is. A replaced shell or a foreign occupant raises."""
    shell, pids = _foreground_pids(client.pane_process_info(pane), pane)
    if shell != shell_pid:
        raise HerdrError(
            "Pane {} now reports shell PID {} instead of the archived {}; the pane was replaced. Restore nothing here; inspect it by hand.".format(pane, shell, shell_pid),
            {"pane": pane, "shell_pid": shell, "archived_shell_pid": shell_pid},
        )
    foreign = [pid for pid in pids if pid not in (shell_pid, stopped_pid)]
    if foreign:
        raise HerdrError(
            "Pane {} has a foreground process {} that is neither its shell {} nor the stopped process {}; another occupant holds the pane. No start was attempted; inspect it by hand.".format(pane, foreign[0], shell_pid, stopped_pid),
            {"pane": pane, "foreground_pid": foreign[0], "shell_pid": shell_pid, "stopped_pid": stopped_pid},
        )
    return "shell" if pids == [shell_pid] else "stopping"


def _name_state(client, name, pane):
    """`released` once Herdr answers agent_not_found, `reserved` while the old
    record still names this pane. Any other answer raises."""
    try:
        record = client.agent_get(name)
    except HerdrError as exc:
        if error_code(exc) == NAME_RELEASED:
            return "released"
        raise
    if record.get("pane_id") != pane:
        raise HerdrError(
            "Agent name {!r} is bound to pane {!r}, not the restoration pane {!r}; the name belongs to another worker. No start was attempted.".format(name, record.get("pane_id"), pane),
            {"agent": name, "pane": pane, "bound_pane": record.get("pane_id")},
        )
    return "reserved"


def await_release(client, name, pane, shell_pid, stopped_pid, sleep=time.sleep):
    """Wait, bounded, until the pane holds only its shell and the name is released.

    Both predicates are re-read on every attempt and must hold on the same
    read; a pane that regresses after the shell appeared is not released.
    """
    pane_state = name_state = None
    for attempt in range(1, RELEASE_POLL_ATTEMPTS + 1):
        pane_state = _pane_state(client, pane, shell_pid, stopped_pid)
        name_state = _name_state(client, name, pane)
        if pane_state == "shell" and name_state == "released":
            return {"attempts": attempt, "shell_pid": shell_pid}
        if attempt < RELEASE_POLL_ATTEMPTS:
            sleep(RELEASE_POLL_INTERVAL_SEC)
    pending = []
    if pane_state != "shell":
        pending.append("pane {} still runs the stopped process {}".format(pane, stopped_pid))
    if name_state != "released":
        pending.append("Herdr still reserves the agent name {!r}".format(name))
    raise HerdrError(
        "Release wait exhausted after {} reads: {}. No start was attempted; inspect the pane by hand before retrying.".format(RELEASE_POLL_ATTEMPTS, "; ".join(pending)),
        {"pane": pane, "agent": name, "attempts": RELEASE_POLL_ATTEMPTS},
    )


def _verify_started(result, name, kind, pane, tokens):
    """The started record and argv must be exactly what was requested."""
    info = result.get("agent") if isinstance(result, dict) else None
    argv = result.get("argv") if isinstance(result, dict) else None
    if (not isinstance(info, dict) or info.get("name") != name or info.get("pane_id") != pane
            or info.get("agent") != kind or info.get("agent_status") not in READY_STATES):
        raise HerdrError(
            "Herdr started an agent whose identity or readiness differs from the requested name {!r}, kind {!r}, and pane {!r}; a process may now be running there. Send no brief and inspect the pane by hand; the start was not retried.".format(name, kind, pane),
            {"agent": name, "pane": pane, "started": info},
        )
    if (not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv)
            or PurePath(argv[0]).name != kind or argv[1:] != list(tokens)):
        raise HerdrError(
            "Herdr started {!r} with argv {!r} instead of the requested resume argv; the session or its permissions may differ. Send no brief and inspect the pane by hand; the start was not retried.".format(name, argv),
            {"agent": name, "pane": pane, "argv": argv, "requested": list(tokens)},
        )
    return info, argv


def start_resumed(client, name, kind, pane, tokens, shell_pid, stopped_pid, sleep=time.sleep):
    """Start `name` in `pane` with `tokens`, retrying only after a name reservation."""
    retries = 0
    attempts = 0
    while True:
        attempts += 1
        try:
            result = client.agent_start(name, kind, pane, list(tokens))
        except HerdrError as exc:
            code = error_code(exc)
            if code == NAME_TAKEN and retries < NAME_TAKEN_RETRIES:
                retries += 1
                await_release(client, name, pane, shell_pid, stopped_pid, sleep=sleep)
                continue
            if code == NAME_TAKEN:
                raise HerdrError(
                    "Herdr refused the name {!r} {} times after it had read as released; inspect `herdr agent list` and the pane by hand. No process was started under this name.".format(name, attempts),
                    {"agent": name, "pane": pane, "start_attempts": attempts},
                ) from exc
            raise HerdrError(
                "Starting {!r} in pane {} failed with {}; a process may already be running there, so the start was not retried. Inspect the pane by hand before anything else. {}".format(name, pane, code or "no Herdr error code", exc.message),
                {**exc.details, "agent": name, "pane": pane},
            ) from exc
        info, argv = _verify_started(result, name, kind, pane, tokens)
        return {"agent": info, "argv": argv, "start_attempts": attempts, "name_taken_retries": retries}


def restore(client, name, kind, pane, tokens, shell_pid, stopped_pid, sleep=time.sleep):
    """Gate the release, then start the same name once with the given resume argv."""
    if not isinstance(name, str) or not name.strip() or not isinstance(pane, str) or not pane.strip():
        raise UsageError("restore-session needs the archived agent name and pane from step 1 of the restoration.", {})
    if not _positive_int(shell_pid) or not _positive_int(stopped_pid) or shell_pid == stopped_pid:
        raise UsageError(
            "restore-session needs the archived shell PID and the stopped foreground PID from step 1, and they differ; the shell is never the process to stop.",
            {"shell_pid": shell_pid, "stopped_pid": stopped_pid},
        )
    tokens = list(tokens)
    if not tokens or any(not isinstance(token, str) or not token for token in tokens):
        raise UsageError("Pass the runtime's resume argv after `--`: its resume form naming the archived session UUID, the explicit YOLO flag, and the unchanged model and effort options.", {})
    try:
        verify_worker_permissions(kind, [kind] + tokens)
    except HerdrError as exc:
        raise HerdrError("Resume argv refused before any Herdr call: " + exc.message, {"agent": name, "argv": tokens}) from None
    release = await_release(client, name, pane, shell_pid, stopped_pid, sleep=sleep)
    started = start_resumed(client, name, kind, pane, tokens, shell_pid, stopped_pid, sleep=sleep)
    return {
        "agent": name,
        "kind": kind,
        "pane_id": pane,
        "argv": started["argv"],
        "release": release,
        "start": {"attempts": started["start_attempts"], "name_taken_retries": started["name_taken_retries"]},
        "started": started["agent"],
    }


def register_commands(sub, common):
    parser = sub.add_parser(
        "restore-session", parents=[common],
        help="After the lead stopped a retained worker's own process: wait for its pane shell and released name, then restart the same name with the resume argv given after --.",
    )
    parser.add_argument("--agent", required=True, metavar="NAME", help="The archived Herdr agent name.")
    parser.add_argument("--kind", required=True, choices=("claude", "codex", "grok"))
    parser.add_argument("--pane", required=True, metavar="PANE", help="The archived pane id.")
    parser.add_argument("--shell-pid", required=True, type=int, metavar="PID", help="The archived shell_pid from `herdr pane process-info`.")
    parser.add_argument("--stopped-pid", required=True, type=int, metavar="PID", help="The foreground PID the lead stopped.")
    parser.add_argument("resume_argv", nargs="*", metavar="RESUME_ARGV",
                        help="After --: the runtime's resume form naming the archived session UUID, its explicit YOLO flag, and the unchanged model and effort options, each a separate token.")


def run_command(args, client, *, sleep):
    tokens = list(args.resume_argv)
    if tokens[:1] == ["--"]:
        tokens = tokens[1:]
    return restore(client, args.agent, args.kind, args.pane, tokens, args.shell_pid, args.stopped_pid, sleep=sleep)
