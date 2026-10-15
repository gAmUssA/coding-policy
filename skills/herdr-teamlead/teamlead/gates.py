"""Parse a worker report's `## Gates` section and decide whether it is green.

A developer, reviewer or tester report lists every gate it ran, one bullet
per gate, in the format the brief templates prescribe:

    ## Gates

    - <name> exit=<code> cmd=<exact command>

`<name>` is a single token, `<code>` is the integer the command exited with
(or `unknown` when it never completed), and the command runs to the end of
the line, optionally wrapped in one pair of backticks.

`check` reports every failure for the lead's `check-gates` command;
`require_green` refuses an accepted outcome. The section is green only when
it is present exactly once outside code fences, carries at least one gate,
holds no malformed list entry, blank command or duplicate name, names every
required gate, and every gate exited 0. An `unknown` or non-zero exit is never green.

Any level-1 or level-2 ATX heading reading `Gates` (case, spacing and
closing hashes ignored) opens a section; a second one is refused. The
section runs to the next level-1 or level-2 heading, so gates under a
`###` child heading are validated. Fenced code never counts as evidence. Any
Markdown list entry (`-`, `*`, `+`, `1.`, `1)`, indented or not) is a gate
line and must be canonical; other prose lines are ignored.
"""

import re

from .errors import UsageError

LINE = re.compile(r"^- (?P<name>[A-Za-z0-9][A-Za-z0-9_.:/-]*) exit=(?P<exit>-?[0-9]+|unknown) cmd=(?P<cmd>\S.*)$")
FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FENCE_CLOSE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
#: A CommonMark ATX heading: 0-3 spaces, 1-6 hashes, then spaces/tabs or end.
ATX_HEADING = re.compile(r"^ {0,3}(?P<level>#{1,6})(?:[ \t]+(?P<text>.*?))?[ \t]*$")
CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+$")
LIST_ENTRY = re.compile(r"^[ \t]*(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]|$)")
THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")


def _unfenced(body):
    """Yield the lines outside fenced code blocks, per CommonMark fences.

    A fence closes only on a line of the opener's character at least as long
    as the opener; an unclosed fence runs to the end of the report.
    """
    opener = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if opener is None:
            match = FENCE_OPEN.match(line)
            if match and not (match.group("fence")[0] == "`" and "`" in match.group("info")):
                opener = match.group("fence")
                continue
            yield line
        else:
            match = FENCE_CLOSE.match(line)
            if match and match.group("fence")[0] == opener[0] and len(match.group("fence")) >= len(opener):
                opener = None


def _heading(line):
    """Return `(level, normalized text)` for an ATX heading line, else None."""
    match = ATX_HEADING.match(line)
    if match is None:
        return None
    text = CLOSING_HASHES.sub("", match.group("text") or "").strip()
    return len(match.group("level")), text.casefold()


def _section(body):
    """Return the Gates section's unfenced lines, or None when absent; refuse two.

    Any level-1 or level-2 heading whose normalized text is `gates` opens a
    Gates section, whatever its spacing, closing hashes or case; any other
    level-1 or level-2 heading ends it. Deeper headings stay inside it.
    """
    lines, found, inside = [], 0, False
    for line in _unfenced(body):
        heading = _heading(line)
        if heading is not None and heading[0] <= 2:
            inside = heading[1] == "gates"
            found += inside
            continue
        if inside:
            lines.append(line)
    if found > 1:
        raise UsageError("The report carries more than one `## Gates` section; list each gate's final run once.", {})
    return lines if found else None


def parse(body):
    """Return `{gates, errors}`: the listed gates and every structural defect."""
    section = _section(body)
    if section is None:
        return {"gates": [], "errors": ["missing `## Gates` section"]}
    gates, errors, names = [], [], set()
    for line in section:
        if not LIST_ENTRY.match(line) or THEMATIC_BREAK.match(line):
            continue
        match = LINE.match(line)
        if match is None:
            errors.append("malformed gate line: {}".format(line))
            continue
        name, code, command = match.group("name"), match.group("exit"), match.group("cmd").strip()
        if len(command) > 1 and command.startswith("`") and command.endswith("`"):
            command = command[1:-1].strip()
        if not command:
            errors.append("blank command for gate {}".format(name))
            continue
        if name in names:
            errors.append("duplicate gate {}".format(name))
            continue
        names.add(name)
        gates.append({"name": name, "exit": None if code == "unknown" else int(code), "command": command})
    if not gates and not errors:
        errors.append("`## Gates` lists no gate")
    return {"gates": gates, "errors": errors}


def check(body, required=()):
    """Return `{ok, gates, failures}`: structural, red and missing-required."""
    result = parse(body)
    present = {gate["name"] for gate in result["gates"]}
    failures = list(result["errors"])
    failures.extend("gate {} exit={}".format(gate["name"], "unknown" if gate["exit"] is None else gate["exit"])
                    for gate in result["gates"] if gate["exit"] != 0)
    failures.extend("missing required gate {}".format(name) for name in required if name not in present)
    return {"ok": not failures, "gates": result["gates"], "failures": failures}


def require_green(body, context):
    """Refuse an accepted outcome whose report gates are missing or red."""
    result = check(body)
    if not result["ok"]:
        raise UsageError("{} needs a green `## Gates` section ({}); send the report back as a fix round instead of accepting it.".format(
            context, "; ".join(result["failures"])), {"failures": result["failures"]})
    return result
