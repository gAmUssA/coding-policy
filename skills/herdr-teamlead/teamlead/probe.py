"""Deterministic idle probe over an agent's pane text.

Herdr derives an agent's lifecycle state from its window title, and that title
goes stale. Grok in particular keeps reporting `working` after a `/usage`
dialog has been opened and dismissed, while it sits at an empty prompt. Taking
herdr's word for it would make teamlead refuse that agent forever.

So when -- and only when -- herdr says `working`, teamlead reads the pane and
decides from the footer. Two rules govern the design:

* `blocked` is never probed. A blocked agent is waiting on a human at an
  approval dialog, and herdr recognizing that UI is a positive signal, not a
  stale one.
* The bias runs toward refusing. A false `working` costs a skipped round; a
  false `idle` types over somebody's work. Anything the probe cannot read
  confidently comes back INCONCLUSIVE, and INCONCLUSIVE keeps the refusal.

`probe_state` is a pure function of pane text plus the marker lists from the
agent's config, so the per-agent signatures live with the operator and the
decision logic is testable against inline fixtures.
"""

from .diagnostics import stderr_warn  # noqa: F401  (re-exported for callers)

#: Probe verdicts.
IDLE = "idle"
WORKING = "working"
INCONCLUSIVE = "inconclusive"

#: How the probe reads the pane. The footer is on the alternate screen for
#: every agent kind here, so `visible` is the only source that shows it.
PROBE_READ_SOURCE = "visible"
PROBE_READ_LINES = 40

#: How many non-empty rows from the bottom count as "the footer". Scoping the
#: match to the footer keeps a `…` in ordinary transcript text from reading as
#: a spinner and pinning the agent at `working`.
FOOTER_LINES = 15

#: A spinner row: a status verb followed by the ellipsis glyph ("Responding…",
#: "Creating…", "Waiting for response…"). Present in the footer on every kind
#: here while a turn is running, absent when the agent is at its prompt.
SPINNER_GLYPH = "…"


def footer(text, limit=FOOTER_LINES):
    """The last `limit` non-empty rows of `text`, stripped, in order."""
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line][-limit:]


def probe_state(text, idle_markers=(), working_markers=(), limit=FOOTER_LINES):
    """Classify pane text as IDLE, WORKING, or INCONCLUSIVE.

    Precedence, highest first:

    1. Any `working_markers` substring in the footer -> WORKING. Grok's working
       footer is its idle footer plus `Esc:cancel`, so working has to win.
    2. A spinner row in the footer -> WORKING.
    3. Any `idle_markers` substring in the footer -> IDLE.
    4. Otherwise INCONCLUSIVE.

    With no markers configured the probe can only ever return WORKING or
    INCONCLUSIVE, so an unconfigured agent keeps herdr's verdict.
    """
    rows = footer(text, limit=limit)
    haystack = "\n".join(rows)

    for marker in working_markers:
        if marker in haystack:
            return WORKING
    if SPINNER_GLYPH in haystack:
        return WORKING
    for marker in idle_markers:
        if marker in haystack:
            return IDLE
    return INCONCLUSIVE


def resolve_status(client, agent, herdr_status, warn=None):
    """Return `(status, state_source)` for `agent`.

    `state_source` is `"probe"` when the pane read reached a verdict and
    `"herdr"` when herdr's own state stands -- because the status was not
    `working`, because the agent configures no markers, or because the probe
    came back inconclusive.

    Writes to the agent are never made here; the probe is a read.
    """
    if herdr_status != WORKING:
        return herdr_status, "herdr"
    if not agent.idle_markers and not agent.working_markers:
        return herdr_status, "herdr"

    text = client.agent_read(agent.name, source=PROBE_READ_SOURCE, lines=PROBE_READ_LINES)
    verdict = probe_state(text, agent.idle_markers, agent.working_markers)

    if verdict == IDLE:
        (warn or stderr_warn)(
            "herdr reports {!r} as working, but its pane footer says "
            "idle - herdr's title-derived state is stale. Proceeding on the "
            "pane read.".format(agent.name)
        )
        return IDLE, "probe"
    if verdict == WORKING:
        return WORKING, "probe"
    return herdr_status, "herdr"
