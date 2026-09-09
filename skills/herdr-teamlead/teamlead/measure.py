"""Measure each agent's remaining subscription budget.

The flow per agent is always the same:

1. read live status with `herdr agent get` -- never trust the last snapshot
2. when herdr says `working`, confirm it against the pane (see teamlead/probe.py)
3. refuse to write to a `working` or `blocked` agent, always
4. send the agent's usage slash command by the mechanism its config names
   (see SLASH_DELIVERIES in teamlead/herdr.py -- the TUIs disagree), and
   confirm the composer consumed it (see teamlead/composer.py)
5. wait for its marker, `herdr pane wait-output` first and a bounded
   `herdr agent read` poll second
6. read the pane and parse it
7. close the dialog when the agent opens one -- always, including on failure

Step 5 has two mechanisms on purpose. `pane wait-output` is event-driven and
cheap, but it did not deliver against Grok's modal on the alternate screen,
while prompt-then-read is the sequence that works by hand. Neither is trusted
alone: the wait is best-effort, and the marker is confirmed in the text that
actually gets parsed.

Step 7 runs in a `finally`: a usage dialog left open flips the agent to
`working` and swallows the next prompt, so a failed read must not leave one
standing.

`measured_at` is passed in. Nothing in this module reads the clock: that is a
CLI-layer concern, which is what lets the tests assert on an exact timestamp.
"""

import time

from .composer import COMPOSER_SETTLE_SEC, DispatchSession, send_command
from .errors import HerdrError, ParseError
from .herdr import BUSY_STATES, DEFAULT_MARKER_TIMEOUT_MS, READY_STATES, format_argv
from .parsers import headroom_pct, parse_usage
from .probe import resolve_status, stderr_warn
from .state import SNAPSHOT_SCHEMA_VERSION
from .billing import tier_billing

#: Snapshot document version. 2 adds `window_group` to every agent record.
#: `state.py` owns the file a snapshot is stored in, so it owns both the
#: version and the migration that carries a version-1 snapshot up to it
#: (rules/stateful-artifacts.md Migration Policy). Read from there rather than
#: declared again here: two constants for one shape drift apart, and a writer
#: stamping a version no migration table knows is exactly the state that rule
#: exists to prevent.
MEASURE_SCHEMA_VERSION = SNAPSHOT_SCHEMA_VERSION

#: Lines to pull when reading a usage report. Always passed: the read that
#: works by hand names a line count explicitly, and relying on herdr's default
#: risks a viewport clipped short of the numbers.
DEFAULT_READ_LINES = 80

#: Bounded fallback poll, used when `pane wait-output` does not deliver the
#: marker. Ten attempts a second apart, then the measurement fails loudly.
DEFAULT_MARKER_POLL_ATTEMPTS = 10
DEFAULT_MARKER_POLL_INTERVAL_SEC = 1.0

#: A usage dialog can have several tabs, and the report may not be on the one
#: it opens with. Grok's has three (Context usage / Usage limit / Session
#: info). Bounded so a dialog whose tabs do not cycle cannot spin forever.
MAX_DIALOG_TABS = 3


def wait_for_usage_report(client, agent, pane_id, marker_timeout_ms=DEFAULT_MARKER_TIMEOUT_MS, read_lines=DEFAULT_READ_LINES, poll_attempts=DEFAULT_MARKER_POLL_ATTEMPTS, poll_interval_sec=DEFAULT_MARKER_POLL_INTERVAL_SEC, sleep=time.sleep, warn=None, max_tabs=MAX_DIALOG_TABS):
    """Return pane text containing `agent.usage_marker`.

    The marker is a literal substring, never a pattern, so the wait uses
    herdr's `--match` rather than `--regex`. An escaped regex is one more
    engine to be wrong about, and the config field is documented as literal
    text.

    Two mechanisms, tried in order, with the second covering the first:

    1. `herdr pane wait-output --match ...` -- event-driven, no sleeping.
    2. a bounded `herdr agent read` poll -- the prompt-then-read sequence that
       works by hand against a modal on the alternate screen.

    If neither finds the marker and the agent configures
    `dialog_next_tab_keys`, the dialog is tabbed through up to `max_tabs`
    times, re-reading each time: a multi-tab usage dialog may not open on the
    tab carrying the report.

    A failed wait is a warning, not the end: the marker is confirmed in the
    text that will actually be parsed. Raises HerdrError naming both
    mechanisms when the marker never appears.
    """
    warn = warn or stderr_warn
    argv = client.argv_pane_wait_output(
        pane_id,
        match=agent.usage_marker,
        source=agent.usage_read_source,
        timeout_ms=marker_timeout_ms,
    )
    try:
        client.pane_wait_output(
            pane_id,
            match=agent.usage_marker,
            source=agent.usage_read_source,
            timeout_ms=marker_timeout_ms,
        )
    except HerdrError as exc:
        warn(
            "`{}` did not deliver {!r} for {} ({}). Falling back to "
            "polling `herdr agent read` up to {} times.".format(
                format_argv(argv), agent.usage_marker, agent.name, exc.message, poll_attempts
            )
        )

    def read():
        return client.agent_read(
            agent.name, source=agent.usage_read_source, lines=read_lines
        )

    text = read()
    attempts = 0
    while agent.usage_marker not in text and attempts < poll_attempts:
        attempts += 1
        sleep(poll_interval_sec)
        text = read()

    # Still nothing: the dialog may have opened on a tab that does not carry
    # the report. Cycle through the others before giving up.
    tabs = 0
    while (
        agent.usage_marker not in text
        and agent.dialog_next_tab_keys
        and tabs < max_tabs
    ):
        tabs += 1
        client.agent_send_keys(agent.name, agent.dialog_next_tab_keys)
        sleep(poll_interval_sec)
        text = read()

    if agent.usage_marker not in text:
        raise HerdrError(
            "{!r} never appeared in {}'s pane. Tried `herdr pane wait-output "
            "--match` for {}ms, then {} reads of `herdr agent read {} --source "
            "{} --lines {}` {}s apart.{} Check the marker against what the agent "
            "actually prints, and rerun with --trace to see every command and "
            "its raw output.".format(
                agent.usage_marker,
                agent.name,
                marker_timeout_ms,
                poll_attempts + 1,
                agent.name,
                agent.usage_read_source,
                read_lines,
                poll_interval_sec,
                " Tabbed through the dialog {} times too.".format(tabs) if tabs else "",
            ),
            {
                "agent": agent.name,
                "marker": agent.usage_marker,
                "pane_id": pane_id,
                "poll_attempts": poll_attempts,
                "dialog_tabs_tried": tabs,
            },
        )
    return text


def skipped_record(agent, status, herdr_status, state_source):
    """The record for an agent teamlead declined to interrupt."""
    return {
        "kind": agent.kind,
        "state": status,
        "herdr_state": herdr_status,
        "state_source": state_source,
        "windows": None,
        "credits": None,
        "plan": None,
        "headroom_pct": None,
        "window_group": agent.window_group,
        "skipped": True,
    }


def measure_agent(client, agent, marker_timeout_ms=DEFAULT_MARKER_TIMEOUT_MS, read_lines=DEFAULT_READ_LINES, warn=None, poll_attempts=DEFAULT_MARKER_POLL_ATTEMPTS, poll_interval_sec=DEFAULT_MARKER_POLL_INTERVAL_SEC, sleep=time.sleep, max_tabs=MAX_DIALOG_TABS, settle_sec=COMPOSER_SETTLE_SEC, session=None):
    """Measure one agent and return its record.

    Raises HerdrError or ParseError; the caller decides whether one bad agent
    fails the whole run.
    """
    info = client.agent_get(agent.name)
    herdr_status = info.get("agent_status")
    pane_id = info.get("pane_id")
    status, state_source = resolve_status(client, agent, herdr_status, warn=warn)

    if status in BUSY_STATES:
        record = skipped_record(agent, status, herdr_status, state_source)
        record["pane_id"] = pane_id
        return record

    if not pane_id:
        raise HerdrError(
            "herdr reported no pane for agent {!r} - confirm it is live with "
            "`herdr agent list`.".format(agent.name),
            {"agent": agent.name},
        )

    send_command(
        client,
        agent,
        pane_id,
        agent.usage_prompt,
        session=session,
        sleep=sleep,
        warn=warn,
        settle_sec=settle_sec,
        # A usage command opens a dialog; whether the screen "changed" is not
        # a question this flow asks, and waiting on it would cost reads.
        screen_attempts=0,
    )
    try:
        text = wait_for_usage_report(
            client,
            agent,
            pane_id,
            marker_timeout_ms=marker_timeout_ms,
            read_lines=read_lines,
            poll_attempts=poll_attempts,
            poll_interval_sec=poll_interval_sec,
            sleep=sleep,
            warn=warn,
            max_tabs=max_tabs,
        )
        parsed = parse_usage(agent.kind, text)
    finally:
        # Always dismiss the report, including when the wait timed out, the
        # read failed, or the parse failed. A usage dialog left open flips the
        # agent to `working` and swallows the next prompt teamlead sends.
        if agent.close_keys:
            client.agent_send_keys(agent.name, agent.close_keys)

    windows = parsed["windows"]
    return {
        "kind": agent.kind,
        "state": status,
        "herdr_state": herdr_status,
        "state_source": state_source,
        "pane_id": pane_id,
        "windows": windows,
        "credits": parsed["credits"],
        "plan": parsed.get("plan"),
        "headroom_pct": headroom_pct(windows),
        # Copied through so `plan` reads one window's membership from the
        # snapshot, the same place it reads the headroom it belongs to.
        "window_group": agent.window_group,
        "skipped": False,
    }


def measure(client, agents, measured_at, marker_timeout_ms=DEFAULT_MARKER_TIMEOUT_MS, read_lines=DEFAULT_READ_LINES, warn=None, poll_attempts=DEFAULT_MARKER_POLL_ATTEMPTS, poll_interval_sec=DEFAULT_MARKER_POLL_INTERVAL_SEC, sleep=time.sleep, settle_sec=COMPOSER_SETTLE_SEC, allow_recovery=False):
    """Measure every agent in `agents` and return the snapshot document.

    A failure on one agent is recorded on that agent's record and does not
    stop the others; the caller inspects `failed_agents` to decide the exit
    code. Only HerdrError and ParseError are absorbed -- anything else is a
    bug and propagates.
    """
    records = {}
    failures = []
    # One session per run: recovery may only ever clear a command teamlead
    # itself typed during it.
    session = DispatchSession(allow_recovery=allow_recovery)
    for agent in agents:
        try:
            records[agent.name] = measure_agent(
                client,
                agent,
                marker_timeout_ms=marker_timeout_ms,
                read_lines=read_lines,
                warn=warn,
                poll_attempts=poll_attempts,
                poll_interval_sec=poll_interval_sec,
                sleep=sleep,
                settle_sec=settle_sec,
                session=session,
            )
        except (HerdrError, ParseError) as exc:
            failures.append(agent.name)
            records[agent.name] = {
                "kind": agent.kind,
                "state": None,
                "herdr_state": None,
                "state_source": None,
                "windows": None,
                "credits": None,
                "plan": None,
                "headroom_pct": None,
                # Pool membership survives a failed measurement. Dropping it
                # would unlink this worker from its window, and affordability
                # reads the minimum across that window -- a judge whose own
                # measurement failed would then be judged against nobody.
                "window_group": agent.window_group,
                "skipped": False,
                "error": {"code": exc.code, "message": exc.message},
            }
    return {
        "schema_version": MEASURE_SCHEMA_VERSION,
        "measured_at": measured_at,
        "agents": {
            agent.name: {**records[agent.name], "tier_billing": tier_billing(agent.tiers)}
            for agent in agents
        },
        "failed_agents": failures,
    }


def ready_agents(snapshot):
    """Names of agents in the snapshot that were ready for input."""
    return sorted(
        name
        for name, record in snapshot.get("agents", {}).items()
        if record.get("state") in READY_STATES
    )
