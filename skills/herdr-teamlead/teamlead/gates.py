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
holds no malformed bullet and no duplicate name, names every required gate,
and every gate exited 0. An `unknown` or non-zero exit is never green. Prose
lines that are not bullets are ignored, so a worker can add a sentence of
context under the list.
"""

import re

from .errors import UsageError

HEADING = "## Gates"
LINE = re.compile(r"^- (?P<name>[A-Za-z0-9][A-Za-z0-9_.:/-]*) exit=(?P<exit>-?[0-9]+|unknown) cmd=(?P<cmd>\S.*)$")


def _section(body):
    """Return the Gates section's lines, or None when absent; refuse two."""
    lines, found, inside, fenced = [], 0, False, False
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            fenced = not fenced
            if inside:
                lines.append(line)
            continue
        if not fenced and line.startswith("#"):
            inside = line == HEADING
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
        if not line.startswith("- "):
            continue
        match = LINE.match(line)
        if match is None:
            errors.append("malformed gate line: {}".format(line))
            continue
        name, code, command = match.group("name"), match.group("exit"), match.group("cmd").strip()
        if len(command) > 1 and command.startswith("`") and command.endswith("`"):
            command = command[1:-1]
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
