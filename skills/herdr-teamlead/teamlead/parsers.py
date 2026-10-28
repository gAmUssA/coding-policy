"""Pure text -> dict parsers for each agent's usage output.

Every function here is a pure function of the pane text it is handed. Nothing
in this module reads the clock, the filesystem, the environment, or a
subprocess -- that is what makes the parsers testable against inline fixture
strings and what keeps `measured_at` a CLI-layer concern.

Every parser returns the same shape::

    {
      "windows": {
        "<label>": {
          "used_pct": float,        # 0..100, how much of the window is spent
          "remaining_pct": float,   # 100 - used_pct
          "resets": str | None,     # verbatim reset text, never normalized
        },
        ...
      },
      "credits": float | None,      # informational, grok only
      "plan": str | None,           # informational, grok's dialog only
    }

`resets` stays a verbatim string on purpose: each agent prints a different
human date format in a different timezone, and turning those into timestamps
is reasoning, not parsing.
"""

import re

from .errors import ParseError

# --- claude -----------------------------------------------------------------

# A window header is "Current session" or "Current week (<something>)".
# "(<something>)" covers "(all models)" and a per-model line such as
# "(Fable)". Newer Claude Code appends the reset on that line, after a
# middle dot (U+00B7): "Current session · Resets 5pm (America/New_York)".
# A narrow pane can leave the dot at the end of the line and put
# "Resets ..." on the next one, or end the line on the word "Resets" and
# put the reset text on the next one. The label stops at the first ")",
# so an inline timezone is not swallowed into the label.
_CLAUDE_HEADER_RE = re.compile(
    r"^(?P<label>Current session|Current week \([^)]+\))"
    r"(?: ·(?P<saw_resets>\s+Resets(?:\s+(?P<resets>.+))?)?)?$"
)
# "████        8% used" -- the bar glyphs are decoration, only the number counts.
_CLAUDE_USED_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s+used\b")
_CLAUDE_RESETS_RE = re.compile(r"^Resets\s+(.+)$")
# A bar row is block glyphs only. It is not reset text.
_CLAUDE_BAR_RE = re.compile(r"^[░▒▓█▏▎▍▌▋▊▉\s]+$")
# Timezone wrapped onto the following line: "(America/New_York)" or "(UTC)".
# A parenthetical with a space, such as "(promo note)", is not one.
_CLAUDE_TZ_CONT_RE = re.compile(
    r"^\((?:[A-Z]{2,5}|[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)+)\)$"
)
# A weekly window scoped to one model family: "Current week (Fable)". The
# account-wide "(all models)" line is excluded by `_window_model`.
_CLAUDE_MODEL_WINDOW_RE = re.compile(r"^Current week \((?P<model>.+)\)$")
_CLAUDE_ALL_MODELS = "all models"
_WORD_RE = re.compile(r"[a-z]+")

# --- codex ------------------------------------------------------------------

#: Box-drawing frame glyphs stripped from either end of a line before
#: matching. Codex boxes its `/status` output and Grok boxes its `/usage`
#: dialog, so both parsers share this.
BOX_FRAME = "│┃|"  # box-drawing light/heavy vertical, ASCII pipe
# "Weekly limit:  [████░░░] 87% left (resets 17:26 on 7 Sep)"
# The bar is optional so a narrow terminal that drops it still parses.
_CODEX_LIMIT_RE = re.compile(
    r"^(?P<label>.+?)\s+limit:\s*"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*left"
    r"(?:\s*\(resets\s+(?P<resets>[^)]*)\))?"
)
# "GPT-5.3-Codex-Spark limit:" -- a section header; every limit row after it
# belongs to that model until the next header or the end of the block.
_CODEX_MODEL_RE = re.compile(r"^(?P<model>\S.*?)\s+limit:$")
_CODEX_ACCOUNT_RE = re.compile(r"^Account:\s*(?P<account>.+?)\s*$")
# Window labels codex uses for the *primary* model. A bare line ending in
# "limit:" carrying one of these is a malformed row, not a model header.
_CODEX_WINDOW_LABELS = frozenset({"Weekly", "5h", "Hourly", "Daily", "Monthly"})

# --- grok -------------------------------------------------------------------

# Grok reports usage in two shapes depending on the build, and a restart can
# switch between them:
#
#   inline  "Weekly limit: 0%" / "Next reset: ..." / "Credits: $16.42"
#   dialog  a boxed modal on the alternate screen --
#           "Weekly limit (X Premium+)" / "░░░░  1%" / "Resets: ..." / "Credits: ..."
#
# Both are accepted. In both the percentage is percent USED.
#
# Inline: label, colon, and percentage all on one line.
_GROK_INLINE_RE = re.compile(r"^Weekly limit:\s*(?P<pct>\d+(?:\.\d+)?)\s*%")
# Dialog: the label line carries the plan name and no percentage at all.
_GROK_DIALOG_LABEL_RE = re.compile(r"^Weekly limit(?:\s*\((?P<plan>[^)]*)\))?\s*$")
# Dialog: the percentage sits on the row after the label, behind a block-glyph
# bar. Anchored at both ends so a stray percentage elsewhere cannot match.
_GROK_DIALOG_PCT_RE = re.compile(r"^[░▒▓█▏▎▍▌▋▊▉\s]*(?P<pct>\d+(?:\.\d+)?)\s*%\s*$")
# "Next reset:" inline, "Resets:" in the dialog.
_GROK_RESET_RE = re.compile(r"^(?:Next reset|Resets):\s*(?P<resets>.+?)\s*$")
_GROK_CREDITS_RE = re.compile(r"^Credits:\s*\$?\s*(?P<credits>-?\d+(?:\.\d+)?)")


def _strip_frame(line):
    """Strip whitespace and box-drawing verticals from both ends of a line."""
    return line.strip().strip(BOX_FRAME).strip()


def _box_interior(line):
    """Return the text inside a box drawn over other content.

    Grok's usage modal is painted OVER the transcript, so a row reads::

        /usage is credit/bill│  Weekly limit (X Premium+)   │   10:36 PM

    Stripping the frame from the ends leaves the transcript text attached.
    Taking what lies between the first and last vertical drops it.

    A row with a single vertical is a box edge whose other side ran off the
    viewport: keep what follows it. A row with none is not boxed at all --
    Grok's inline report, for one -- so it is returned whole.
    """
    first = -1
    last = -1
    for index, char in enumerate(line):
        if char in BOX_FRAME:
            if first < 0:
                first = index
            last = index
    if first < 0:
        return line.strip()
    if first == last:
        return line[first + 1 :].strip()
    return line[first + 1 : last].strip()


def _window(used_pct, resets):
    """Build one window record from a used-percentage and a reset string."""
    used = round(float(used_pct), 6)
    return {
        "used_pct": used,
        "remaining_pct": round(100.0 - used, 6),
        "resets": resets,
    }


def parse_claude_usage(text):
    """Parse the full-screen `/usage` dialog Claude Code renders in its pane.

    Reads the `visible` snapshot. Older builds put the header alone, then
    `N% used`, then `Resets ...`. Newer builds put the reset on the header
    (`Current session · Resets 5pm (America/New_York)`); a timezone that
    does not fit continues as `(Area/City)` or `(UTC)` on the next line,
    the bar is its own row, and `N% used` follows the bar. A narrow pane
    may split the header so `Resets ...` starts the next line, or end the
    header on the word `Resets` and put the reset text on the next line.
    A second `N% used` under a label that already has one is rejected:
    that row belongs to a header this parser did not recognize.
    """
    windows = {}
    label = None
    pending_used = None
    pending_resets = None
    # The header ended on the word "Resets"; the next content line is the
    # reset text. Distinct from a header that ended at the middle dot,
    # whose next line is itself a "Resets ..." line.
    awaiting_reset_body = False
    # True only until the next content line, so a later parenthetical in
    # the dialog is not glued onto a reset that already moved on.
    awaiting_tz = False

    def flush():
        if label is not None and pending_used is not None:
            windows[label] = _window(pending_used, pending_resets)

    def lost_header(used_pct):
        raise ParseError(
            "A second '{}% used' row appeared under {!r} before the next "
            "header, so a wrapped header was lost and this read is rejected "
            "instead of dropping that window. Send /usage and read with "
            "`herdr agent read <name> --source visible` while the dialog "
            "is open.".format(used_pct, label),
            {"kind": "claude"},
        )

    for raw in text.splitlines():
        line = raw.strip()
        header = _CLAUDE_HEADER_RE.match(line)
        if header:
            flush()
            label = header.group("label")
            pending_used = None
            inline = header.group("resets")
            pending_resets = inline.strip() if inline and inline.strip() else None
            awaiting_reset_body = header.group("saw_resets") is not None and pending_resets is None
            awaiting_tz = pending_resets is not None and "(" not in pending_resets
            continue
        if label is None or not line:
            continue
        if awaiting_reset_body:
            if _CLAUDE_BAR_RE.match(line):
                continue
            if _CLAUDE_USED_RE.search(line) is None:
                pending_resets = line
                awaiting_reset_body = False
                awaiting_tz = "(" not in pending_resets
                continue
            awaiting_reset_body = False
        if awaiting_tz:
            if _CLAUDE_BAR_RE.match(line) or _CLAUDE_USED_RE.search(line):
                awaiting_tz = False
            elif _CLAUDE_TZ_CONT_RE.match(line):
                pending_resets = "{} {}".format(pending_resets, line)
                awaiting_tz = False
                continue
            elif line.startswith("(") and line.endswith(")"):
                awaiting_tz = False
            else:
                pending_resets = "{} {}".format(pending_resets, line)
                awaiting_tz = "(" not in line
                continue
        used = _CLAUDE_USED_RE.search(line)
        if used:
            if pending_used is not None:
                lost_header(used.group(1))
            pending_used = used.group(1)
            continue
        resets = _CLAUDE_RESETS_RE.match(line)
        if resets and pending_resets is None:
            pending_resets = resets.group(1).strip() or None
            awaiting_tz = pending_resets is not None and "(" not in pending_resets
    flush()

    if not windows:
        raise ParseError(
            "No usage windows found in the claude pane text - send /usage and "
            "read with `herdr agent read <name> --source visible` while the "
            "dialog is open.",
            {"kind": "claude"},
        )
    return {"windows": windows, "credits": None, "plan": None}


def _codex_last_block(text):
    """Return the lines of the last `/status` block in `text`.

    Blocks are delimited by the `Account:` row codex prints first. With no
    such row the whole text is treated as a single block, so a snapshot that
    scrolled the account line away still parses.
    """
    lines = [_strip_frame(line) for line in text.splitlines()]
    start = 0
    for index, line in enumerate(lines):
        if _CODEX_ACCOUNT_RE.match(line):
            start = index
    return lines[start:]


def parse_codex_usage(text):
    """Parse the boxed `/status` block Codex prints inline.

    Codex reports **% left** (remaining), the inverse of Claude and Grok.
    Rows following a `<model> limit:` section header are attributed to that
    model rather than to the primary one.
    """
    windows = {}
    model = None
    for line in _codex_last_block(text):
        limit = _CODEX_LIMIT_RE.match(line)
        if limit:
            label = limit.group("label").strip()
            resets = limit.group("resets")
            key = "{} limit".format(label) if model is None else "{} {} limit".format(model, label)
            remaining = round(float(limit.group("pct")), 6)
            windows[key] = {
                "used_pct": round(100.0 - remaining, 6),
                "remaining_pct": remaining,
                "resets": resets.strip() if resets else None,
            }
            continue
        header = _CODEX_MODEL_RE.match(line)
        if header and header.group("model") not in _CODEX_WINDOW_LABELS:
            model = header.group("model")

    if not windows:
        raise ParseError(
            "No `<label> limit: ... N% left` rows found in the codex pane text - "
            "send /status and read with "
            "`herdr agent read <name> --source recent-unwrapped --lines 80`.",
            {"kind": "codex"},
        )
    return {"windows": windows, "credits": None, "plan": None}


def parse_grok_usage(text):
    """Parse either shape of Grok's `/usage` report.

    Grok renders usage inline on some builds and as a boxed modal on the
    alternate screen on others; a restart can switch between them. Both are
    read here, and in both the percentage is percent **used**.

    Inline::

        Weekly limit: 0%
        Next reset: September 6, 12:55
        Credits: $16.42

    Dialog -- painted over the transcript, so only the box interior is
    matched (see `_box_interior`)::

        Weekly limit (X Premium+)
        ░░░░░░░░░░░░░░░░░░░░░░░░  1%
        Resets: September 6, 12:55
        Credits: $16.42

    The last report in the snapshot wins, so a pane holding several `/usage`
    runs yields the newest. `Credits` and the dialog's plan name are captured
    as informational values and never feed headroom.
    """
    windows = {}
    credits = None
    plan = None
    # A dialog label seen but not yet followed by its percentage row. Tracked
    # as a flag plus a value because the plan name is legitimately None on a
    # label that carries no plan.
    awaiting_pct = False
    pending_plan = None

    for raw in text.splitlines():
        line = _box_interior(raw)
        if not line:
            continue

        # The dialog puts the percentage on the row right after the label, so
        # the very next non-empty row decides. Anything else abandons the wait
        # and is re-examined below as an ordinary line.
        if awaiting_pct:
            awaiting_pct = False
            dialog_pct = _GROK_DIALOG_PCT_RE.match(line)
            if dialog_pct:
                windows["Weekly limit"] = _window(dialog_pct.group("pct"), None)
                plan = pending_plan
                continue

        inline = _GROK_INLINE_RE.match(line)
        if inline:
            windows["Weekly limit"] = _window(inline.group("pct"), None)
            plan = None
            continue

        label = _GROK_DIALOG_LABEL_RE.match(line)
        if label:
            awaiting_pct = True
            pending_plan = label.group("plan")
            continue

        reset = _GROK_RESET_RE.match(line)
        if reset and "Weekly limit" in windows:
            windows["Weekly limit"]["resets"] = reset.group("resets")
            continue

        found_credits = _GROK_CREDITS_RE.match(line)
        if found_credits:
            credits = round(float(found_credits.group("credits")), 6)

    if not windows:
        raise ParseError(
            "No `Weekly limit` reading found in the grok pane text - send "
            "/usage and read the pane. Grok renders the report inline on some "
            "builds and as a modal dialog on others; read the dialog with "
            "`herdr agent read <name> --source visible --lines 60`.",
            {"kind": "grok"},
        )
    return {"windows": windows, "credits": credits, "plan": plan}


PARSERS = {
    "claude": parse_claude_usage,
    "codex": parse_codex_usage,
    "grok": parse_grok_usage,
}


def parse_usage(kind, text):
    """Dispatch to the parser for `kind`.

    Raises ParseError for an agent kind this build does not know how to read.
    """
    parser = PARSERS.get(kind)
    if parser is None:
        raise ParseError(
            "No usage parser for agent kind {!r} - supported kinds are {}. "
            "Fix the `kind` field in the agent's config entry.".format(
                kind, ", ".join(sorted(PARSERS))
            ),
            {"kind": kind},
        )
    return parser(text)


def _window_model(label):
    """The model family a window is scoped to, lowercased, or None.

    `Current week (Fable)` is scoped to `fable`. `Current session`,
    `Current week (all models)`, and every codex or grok label bind every
    model on the account and return None.
    """
    match = _CLAUDE_MODEL_WINDOW_RE.match(label)
    if match is None:
        return None
    scope = match.group("model").strip().lower()
    if scope == _CLAUDE_ALL_MODELS:
        return None
    words = _WORD_RE.findall(scope)
    return words[0] if words else None


def window_binds(label, model):
    """Whether the window named `label` limits a seat running `model`.

    `model` is a display label ("Opus 5.5") or a model id ("claude-opus-5-5").
    A per-model window binds only a model whose words include its family,
    compared case-insensitively and by whole word. An empty or None `model`
    is unknown: every window binds it, the conservative reading.
    """
    family = _window_model(label)
    if family is None or not model:
        return True
    return family in _WORD_RE.findall(model.lower())


def headroom_pct(windows, model=None):
    """Smallest remaining_pct across the windows that bind `model`.

    Headroom is the binding constraint: an agent with 100% of its weekly
    budget left but 2% of its 5-hour budget left has 2% of headroom. A
    per-model window such as `Current week (Fable)` counts only for a seat
    running that model (`window_binds`); with no `model` every window counts.
    None when no window binds.
    """
    if not windows:
        return None
    binding = [
        window["remaining_pct"]
        for label, window in windows.items()
        if window_binds(label, model)
    ]
    return min(binding) if binding else None
