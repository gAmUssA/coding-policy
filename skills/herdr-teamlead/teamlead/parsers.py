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
from typing import NoReturn

from .errors import ParseError

# --- claude -----------------------------------------------------------------

# A window header is "Current session" or "Current week (<something>)".
# "(<something>)" covers "(all models)" and a per-model line such as
# "(Fable)". The same build renders two layouts. At a pane width of 62
# columns or more the header is alone on its line. Below 62 the reset is
# appended after a middle dot (U+00B7):
# "Current session · Resets 5pm (America/New_York)". A narrow pane can
# leave the dot at the end of the line and put "Resets ..." on the next
# one, or end the line on the word "Resets" and put the reset text on the
# next one. The label stops at the first ")", so an inline timezone is
# not swallowed into the label.
_CLAUDE_HEADER_RE = re.compile(
    r"^(?P<label>Current session|Current week \([^)]+\))"
    r"(?: ·(?P<saw_resets>\s+Resets(?:\s+(?P<resets>.+))?)?)?$"
)
# "████        8% used" -- the bar glyphs are decoration, only the number
# counts. Anchored so a prose row such as "Opus 5.5 is 20% used this week"
# is not a window percentage.
_CLAUDE_USED_RE = re.compile(
    r"^[░▒▓█▏▎▍▌▋▊▉\s]*(\d+(?:\.\d+)?)\s*%\s+used\s*$"
)
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


# Scroll indicators the dialog paints on the first and last viewport rows.
# U+2191 and U+2193. They are signals, not part of a reset or a percentage.
_CLAUDE_SCROLL_UP = "\u2191"
_CLAUDE_SCROLL_DOWN = "\u2193"
_CLAUDE_TALLER = "Read with a taller pane."


class _ClaudeLine:
    """One pane row after the scroll glyphs have been peeled off."""

    def __init__(self, text, up, down):
        self.text = text
        self.up = up
        self.down = down


class _ClaudeSection:
    """A title plus the percentage and reset rows that belong to it."""

    def __init__(self, title, up_before, down_before, up_on, down_on):
        self.title = title
        self.up_before = up_before
        self.down_before = down_before
        self.up_on = up_on
        self.down_on = down_on
        self.used: list[str] = []
        self.resets: str | None = None


def _peel_claude_scroll(raw):
    """Strip indent and scroll arrows from either end of one row.

    Returns the remaining text and whether an up or down arrow was on it.
    An arrow-only row comes back empty, with the arrow flags set.
    """
    text = raw.strip()
    up = False
    down = False
    changed = True
    while changed and text:
        changed = False
        end = text[0]
        if end in (_CLAUDE_SCROLL_UP, _CLAUDE_SCROLL_DOWN):
            up = up or end == _CLAUDE_SCROLL_UP
            down = down or end == _CLAUDE_SCROLL_DOWN
            text = text[1:].strip()
            changed = True
            continue
        end = text[-1]
        if end in (_CLAUDE_SCROLL_UP, _CLAUDE_SCROLL_DOWN):
            up = up or end == _CLAUDE_SCROLL_UP
            down = down or end == _CLAUDE_SCROLL_DOWN
            text = text[:-1].strip()
            changed = True
    return text, up, down


def _claude_reset_body(line):
    """Return the reset text carried by `line`.

    `None` means the line is not a reset. `""` means the line ends on the
    word `Resets` and the body is still to come. The label's own parentheses,
    as in `Current week (all models)`, are not part of the body.
    """
    header = _CLAUDE_HEADER_RE.match(line)
    if header is not None:
        if header.group("saw_resets") is None:
            return None
        inline = header.group("resets")
        if inline and inline.strip():
            return inline.strip()
        return ""
    titled = re.search(r" · Resets(?:\s+(\S.*?))?$", line)
    if titled:
        body = titled.group(1)
        return body.strip() if body else ""
    resets = _CLAUDE_RESETS_RE.match(line)
    if resets:
        return resets.group(1).strip()
    return None


def _claude_join(prev, curr):
    """True when `curr` is the wrapped tail of `prev`'s reset text."""
    if _CLAUDE_USED_RE.match(curr) or _CLAUDE_RESETS_RE.match(curr):
        return False
    if _CLAUDE_HEADER_RE.match(curr) or curr.startswith("Current "):
        return False
    body = _claude_reset_body(prev)
    if body is None or "(" in body:
        return False
    if curr.startswith("(") and curr.endswith(")") and not _CLAUDE_TZ_CONT_RE.match(curr):
        return False
    return True


def _claude_drop(prev, curr):
    """True when `curr` is a parenthetical that must not become reset text.

    `(promo note)` sits on its own line under an open reset. It is not a
    timezone, and it is not a new section: the percentage that follows still
    belongs to the window above.
    """
    body = _claude_reset_body(prev)
    if body is None or "(" in body:
        return False
    return bool(
        curr.startswith("(") and curr.endswith(")") and not _CLAUDE_TZ_CONT_RE.match(curr)
    )


def _glue_claude_lines(items):
    """Join a wrapped reset onto the line it continues.

    A header that ended on `Resets`, a time phrase split before the zone,
    and a `(Area/City)` or `(UTC)` line are one reset. Arrow-only rows
    between the pieces stay with that reset instead of splitting the section.
    """
    glued = []
    for item in items:
        if not item.text:
            glued.append(item)
            continue
        prev_at = None
        for index in range(len(glued) - 1, -1, -1):
            if glued[index].text:
                prev_at = index
                break
        if prev_at is None:
            glued.append(item)
            continue
        prev = glued[prev_at]
        if _claude_join(prev.text, item.text) or _claude_drop(prev.text, item.text):
            if _claude_join(prev.text, item.text):
                prev.text = "{} {}".format(prev.text, item.text)
            prev.up = prev.up or item.up
            prev.down = prev.down or item.down
            for mid in glued[prev_at + 1 :]:
                prev.up = prev.up or mid.up
                prev.down = prev.down or mid.down
            del glued[prev_at + 1 :]
            continue
        glued.append(item)
    return glued


def _normalise_claude_pane(text):
    """Stage 1: strip chrome, drop bars and blanks, join wrapped resets."""
    items = []
    for raw in text.splitlines():
        line, up, down = _peel_claude_scroll(raw)
        if not line or _CLAUDE_BAR_RE.match(line):
            if up or down:
                items.append(_ClaudeLine("", up, down))
            continue
        items.append(_ClaudeLine(line, up, down))
    return _glue_claude_lines(items)


def _rate_header(line):
    """`(label, inline resets)` when `line` is a rate-limit header, else None."""
    match = _CLAUDE_HEADER_RE.match(line)
    if match is None:
        return None
    inline = None
    if match.group("saw_resets") is not None:
        raw = match.group("resets")
        inline = raw.strip() if raw and raw.strip() else None
    return match.group("label"), inline


def _claude_sections(items):
    """Stage 2: group rows into sections.

    A line that is neither `N% used` nor `Resets ...` starts a section.
    Arrow-only rows between a title and its percentage stay inside the
    section. Arrow-only rows between sections belong to the gap before the
    next one. Returns `(sections, orphan_used, trailing_down)`.
    """
    sections = []
    current = None
    pending_up = False
    pending_down = False
    orphan_used = None
    for item in items:
        if not item.text:
            pending_up = pending_up or item.up
            pending_down = pending_down or item.down
            continue
        used = _CLAUDE_USED_RE.match(item.text)
        resets = _CLAUDE_RESETS_RE.match(item.text)
        if used or resets:
            if current is None:
                if used and orphan_used is None:
                    orphan_used = used.group(1)
                pending_up = False
                pending_down = False
                continue
            current.up_on = current.up_on or pending_up or item.up
            current.down_on = current.down_on or pending_down or item.down
            pending_up = False
            pending_down = False
            if used:
                current.used.append(used.group(1))
            elif resets is not None and current.resets is None:
                body = resets.group(1).strip()
                current.resets = body or None
            continue
        if current is not None:
            sections.append(current)
        current = _ClaudeSection(item.text, pending_up, pending_down, item.up, item.down)
        pending_up = False
        pending_down = False
    if current is not None:
        sections.append(current)
    return sections, orphan_used, pending_down


def _claude_error(message) -> NoReturn:
    raise ParseError(message, {"kind": "claude"})


def _missing_percentage(label) -> NoReturn:
    _claude_error(
        "Header {!r} has no 'N% used' row before the next header "
        "or the end of the read, so the dialog was cut and this "
        "read is rejected instead of dropping that window. {}".format(label, _CLAUDE_TALLER)
    )


def _percent_before_header(used_pct) -> NoReturn:
    _claude_error(
        "A '{}% used' row appeared before any recognised "
        "header, so a rate-limit window scrolled off the top "
        "of the pane and this read is rejected instead of "
        "dropping it. {}".format(used_pct, _CLAUDE_TALLER)
    )


def _lost_wrapped_header(used_pct, label, fragment) -> NoReturn:
    if fragment:
        wrapped = " The wrapped line {!r} did not match a rate-limit header.".format(fragment)
    else:
        wrapped = ""
    _claude_error(
        "A second '{}% used' row appeared under {!r} before the next "
        "header, so a wrapped header was lost and this read is rejected "
        "instead of dropping that window.{} {}".format(used_pct, label, wrapped, _CLAUDE_TALLER)
    )


def _wrapped_closed_early(fragment, label) -> NoReturn:
    _claude_error(
        "A wrapped header {!r} under {!r} closed before its "
        "'N% used' row, so a rate-limit window was lost and "
        "this read is rejected instead of dropping it. {}".format(fragment, label, _CLAUDE_TALLER)
    )


def _duplicate_label(label) -> NoReturn:
    _claude_error(
        "A second section titled {!r} repeats a rate-limit window "
        "already read, so this read is rejected instead of overwriting "
        "that window. {}".format(label, _CLAUDE_TALLER)
    )


def _scroll_cut(up, down, label) -> NoReturn:
    """Raise when a scroll arrow says a rate-limit window is off-screen."""
    if up and down:
        reason = (
            "a scroll-up indicator before the first rate-limit window and "
            "a scroll-down indicator after {!r}. The rate-limit list is "
            'only proven complete by the "What\'s contributing" section, '
            "so a rate-limit window may be off-screen".format(label)
        )
    elif up:
        reason = (
            "a scroll-up indicator before the first rate-limit window, so "
            "a rate-limit window may be above the viewport"
        )
    else:
        reason = (
            "a scroll-down indicator after {!r}. The rate-limit list is "
            'only proven complete by the "What\'s contributing" section, '
            "so a rate-limit window may be below the viewport".format(label)
        )
    _claude_error(
        "The pane shows {}, and this read is rejected instead of dropping "
        "that window. {}".format(reason, _CLAUDE_TALLER)
    )


def _up_arrow_before_first(sections):
    """True when an up arrow sits on or before the first rate-limit section.

    An arrow on a wrapped zone row is folded into that header, so `up_on`
    covers it. The rows above may be prose or another window.
    """
    for section in sections:
        if _rate_header(section.title) is not None:
            return section.up_before or section.up_on
        if section.up_before or section.up_on:
            return True
    return False


def _down_arrow_after_last(sections, trailing_down):
    """True when a down arrow sits on or after the last rate-limit section.

    The section titled "What's contributing to your limits usage?" is the
    only proof the rate-limit list has ended. A title that starts with
    "What's contributing" ends the walk, so a narrow pane that wraps the
    title still counts. A down arrow on or after any earlier section fails
    the read: a promo row or a credit section can sit between windows.
    """
    last = None
    for index, section in enumerate(sections):
        if _rate_header(section.title) is not None:
            last = index
    if last is None:
        return False
    if sections[last].down_on:
        return True
    for section in sections[last + 1 :]:
        if section.title.startswith("What's contributing"):
            return False
        if section.down_before or section.down_on:
            return True
    return trailing_down


def parse_claude_usage(text):
    """Parse the full-screen `/usage` dialog Claude Code renders in its pane.

    Reads the `visible` snapshot in two stages. Normalise strips the indent
    and a scroll indicator (`↑` or `↓`) from either end of each row, drops
    blank rows and bar rows, and joins a reset that wrapped: a header that
    ended on the word `Resets`, a time phrase split before the zone, and a
    `(Area/City)` or `(UTC)` continuation. The same build stacks the header,
    `N% used`, and `Resets` at 62 columns or more, and below 62 prints the
    reset on the header line.

    Segment treats every remaining line that is neither `N% used` nor
    `Resets ...` as the start of a section. A section titled `Current
    session` or `Current week (...)` must hold exactly one `N% used` row.
    Any other title, including a credit or usage-credit section, is ignored
    with its rows. Zero percentages means the pane cut the window. Two means
    a header was lost. A title that merely starts with `Current` and is
    followed by a percentage is that lost header. An `N% used` row before
    the first section means a window scrolled off the top. A second section
    with a rate-limit label already stored raises instead of overwriting
    that window.

    An up arrow on or before the first rate-limit section, or a down arrow
    on the last one's own rows, means another window may be off-screen.
    The section titled "What's contributing to your limits usage?" ends
    the rate-limit list. A down arrow on or after any other section fails
    the read. The read fails and names a taller pane. A down arrow on or
    after that contributing section, as in the full dialog, does not.
    """
    sections, orphan_used, trailing_down = _claude_sections(_normalise_claude_pane(text))
    windows = {}
    previous_label = None
    lost_fragment = None
    seen_rate = False

    for section in sections:
        matched = _rate_header(section.title)
        if lost_fragment is not None and matched is not None:
            _wrapped_closed_early(lost_fragment, previous_label)
        if matched is not None:
            label, inline = matched
            if orphan_used is not None and not seen_rate:
                _percent_before_header(orphan_used)
            if not section.used:
                _missing_percentage(label)
            if len(section.used) > 1:
                _lost_wrapped_header(section.used[1], label, None)
            if label in windows:
                _duplicate_label(label)
            resets = inline if inline is not None else section.resets
            windows[label] = _window(section.used[0], resets)
            previous_label = label
            seen_rate = True
            lost_fragment = None
            continue
        if section.title.startswith("Current"):
            fragment = section.title if lost_fragment is None else lost_fragment
            lost_fragment = section.title
            if section.used:
                _lost_wrapped_header(section.used[0], previous_label, fragment)
            continue
        if lost_fragment is not None and section.used:
            _lost_wrapped_header(section.used[0], previous_label, lost_fragment)

    up_before = _up_arrow_before_first(sections)
    down_after = _down_arrow_after_last(sections, trailing_down)
    if up_before or down_after:
        last_label = previous_label if previous_label is not None else "the last window"
        _scroll_cut(up_before, down_after, last_label)
    if not windows:
        _claude_error(
            "No usage windows found in the claude pane text - send /usage and "
            "read with `herdr agent read <name> --source visible` while the "
            "dialog is open."
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
