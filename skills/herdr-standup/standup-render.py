#!/usr/bin/env python3
"""Render one standup: a Markdown file, and a block that survives a terminal.

The lead reads the answers; this only lays them out. Parsing four fixed lines
and wrapping cells to a fixed width is a pure function of its inputs, so it
lives here rather than in the lead's hands (`rules/script-delegation.md`).

Two outputs, because they have different readers. The Markdown file is the
record. The fenced block is what the operator scrolls back through a wall of
agent output to find, which is why it opens with a box-drawn banner: at 3am,
in 200 lines of transcript, a heavy line is findable and a `##` heading is not.

Contract:
  argv  : --reports <dir> --now <ISO-8601> [--agent name=path]...
          [--extra <json-file>] [--team <label>] [--out <markdown-path>]
  stdin : not read.
  stdout: one JSON object — {"markdown_path": "<path>", "block": "<fenced
          terminal table>", "answered": [...], "unasked": [...]}. The skill
          relays `block` verbatim.
  stderr: diagnostics only.
  exit  : 0 rendered,
          1 usage or a missing input file,
          2 a report file that does not carry the four-line shape.
  The `--extra` JSON supplies rows for workers that were never asked:
      {"<agent>": {"roles": "...", "done": "...", "plan": "...",
                   "blocked": "...", "note": "busy: refactor"}}
  Every field is optional except the key. `note` renders in the Agent cell.

`--now` is required and never defaults to the clock: the same inputs must
render the same bytes on any day (`rules/testing-standards.md` Determinism).
"""

import argparse
import json
import os
import sys
import textwrap

#: The four keys a worker answers with, in the order they must appear.
REQUIRED_FIELDS = ("DONE", "PLAN", "BLOCKED", "REPORT")

#: Column widths for the terminal table, in characters. The total sits inside
#: 100 columns so the block survives a split pane without re-wrapping.
COLUMN_WIDTHS = {
    "agent": 14,
    "roles": 12,
    "done": 26,
    "plan": 22,
    "blocked": 18,
}

#: Total printable width of the table: each cell padded by a space either
#: side, plus one separator per boundary. Derived rather than written down, so
#: a column change cannot leave the banner a different width from the table.
TABLE_WIDTH = sum(w + 3 for w in COLUMN_WIDTHS.values()) + 1

#: Heavy box-drawing, so the banner is findable while scrolling fast.
BANNER_TOP = "╔" + "═" * (TABLE_WIDTH - 2) + "╗"
BANNER_BOTTOM = "╚" + "═" * (TABLE_WIDTH - 2) + "╝"


class ReportError(Exception):
    """A report file that does not carry the four-line standup shape."""


def parse_report(text, source):
    """Parse the four standup lines out of `text`.

    Tolerant about blank lines, strict about everything else: a worker may
    wrap its answer in blank lines, and every other line must be one of the
    four named fields, each present exactly once, in order. A line that is
    none of them -- prose before, a sign-off after -- is refused by name,
    because a report that carries the four fields plus commentary is exactly
    the non-compliant answer the four-line contract exists to catch, and a
    half-parsed standup row is worse than a missing one: it looks answered.
    """
    found = {}
    order = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for field in REQUIRED_FIELDS:
            prefix = field + ":"
            if stripped.upper().startswith(prefix):
                if field in found:
                    raise ReportError(
                        "{}: '{}' appears more than once; the standup answer is "
                        "exactly four lines, one per field.".format(source, field)
                    )
                found[field] = stripped[len(prefix):].strip()
                order.append(field)
                break
        else:
            raise ReportError(
                "{}: unexpected line {!r} — the answer is exactly four lines "
                "(DONE:, PLAN:, BLOCKED:, REPORT:), nothing before or after.".format(
                    source, stripped
                )
            )

    missing = [f for f in REQUIRED_FIELDS if f not in found]
    if missing:
        raise ReportError(
            "{}: missing {} — the answer must carry all four lines "
            "(DONE:, PLAN:, BLOCKED:, REPORT:), each on its own line.".format(
                source, ", ".join(missing)
            )
        )
    if order != list(REQUIRED_FIELDS):
        raise ReportError(
            "{}: fields are in the order {} — they must read DONE, PLAN, "
            "BLOCKED, REPORT.".format(source, ", ".join(order))
        )
    return found


def _wrap(text, width):
    """Wrap one cell into a list of lines, never wider than `width`."""
    text = " ".join((text or "").split()) or "—"
    return textwrap.wrap(text, width=width) or ["—"]


def _row_lines(cells):
    """Lay one row out across however many lines its tallest cell needs."""
    columns = [
        _wrap(cells.get(key, ""), width) for key, width in COLUMN_WIDTHS.items()
    ]
    height = max(len(col) for col in columns)
    lines = []
    for index in range(height):
        parts = []
        for (key, width), col in zip(COLUMN_WIDTHS.items(), columns):
            parts.append((col[index] if index < len(col) else "").ljust(width))
        lines.append("│ " + " │ ".join(parts) + " │")
    return lines


def _separator(left, mid, right, fill="─"):
    """One horizontal rule, matching the column widths exactly."""
    parts = [fill * (width + 2) for width in COLUMN_WIDTHS.values()]
    return left + mid.join(parts) + right


def render_terminal(rows, now, team):
    """The block the operator scrolls back to find."""
    title = "STANDUP — {} {}".format(now, team).rstrip()
    lines = [BANNER_TOP, "║ " + title.ljust(TABLE_WIDTH - 4) + " ║", BANNER_BOTTOM]
    lines.append(_separator("┌", "┬", "┐"))
    lines.extend(
        _row_lines(
            {
                "agent": "Agent",
                "roles": "Role(s)",
                "done": "Done",
                "plan": "Plan",
                "blocked": "Blocked",
            }
        )
    )
    lines.append(_separator("├", "┼", "┤"))
    for row in rows:
        lines.extend(_row_lines(row))
    lines.append(_separator("└", "┴", "┘"))
    return "\n".join(lines)


def render_markdown(rows, now, team):
    """The record, for the round's reports directory."""
    head = "# Standup — {} {}".format(now, team).rstrip()
    out = [head, ""]
    out.append("| Agent | Role(s) | Done | Plan | Blocked |")
    out.append("| --- | --- | --- | --- | --- |")
    for row in rows:
        out.append(
            "| {} | {} | {} | {} | {} |".format(
                row.get("agent", ""),
                row.get("roles", ""),
                row.get("done", "") or "—",
                row.get("plan", "") or "—",
                row.get("blocked", "") or "—",
            )
        )
    return "\n".join(out) + "\n"


def build_rows(answered, extra):
    """One row per agent, answered first, then the ones nobody could ask.

    Sorted by name inside each group: a standup that reorders itself run to
    run is one the operator has to re-read every morning.
    """
    rows = []
    for name in sorted(answered):
        fields = answered[name]
        rows.append(
            {
                "agent": name,
                "roles": fields.get("roles", ""),
                "done": fields["DONE"],
                "plan": fields["PLAN"],
                "blocked": fields["BLOCKED"],
            }
        )
    for name in sorted(extra):
        record = extra[name] or {}
        note = record.get("note", "")
        rows.append(
            {
                "agent": "{} ({})".format(name, note) if note else name,
                "roles": record.get("roles", ""),
                "done": record.get("done", ""),
                "plan": record.get("plan", ""),
                "blocked": record.get("blocked", ""),
            }
        )
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="standup-render.py", description="Render one standup table."
    )
    parser.add_argument("--reports", required=True, help="The round's reports directory.")
    parser.add_argument("--now", required=True, help="ISO-8601 timestamp for the banner.")
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="An agent that answered, and its report file. Repeatable.",
    )
    parser.add_argument("--extra", help="JSON file of rows for agents nobody asked.")
    parser.add_argument("--team", default="", help="Team label for the banner.")
    parser.add_argument("--out", help="Markdown path (default: <reports>/standup-<date>.md).")
    parser.add_argument("--roles", help="JSON file mapping agent name to its role(s).")
    return parser.parse_args(argv)


def main(argv=None, stdout=sys.stdout, stderr=sys.stderr):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    roles = {}
    if args.roles:
        try:
            with open(args.roles, encoding="utf-8") as handle:
                roles = json.load(handle)
        except (OSError, ValueError) as exc:
            print(
                "standup-render: cannot read --roles {}: {} — check that the path "
                "exists and holds a JSON object mapping agent name to role, then "
                "rerun; or drop --roles to leave the roles column empty.".format(
                    args.roles, exc
                ),
                file=stderr,
            )
            return 1

    answered = {}
    for pair in args.agent:
        if "=" not in pair:
            print(
                "standup-render: --agent takes NAME=PATH, got {!r}".format(pair), file=stderr
            )
            return 1
        name, path = pair.split("=", 1)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(
                "standup-render: cannot read the report for {} at {}: {} — ask it "
                "again, or move it to --extra".format(name, path, exc),
                file=stderr,
            )
            return 1
        try:
            fields = parse_report(text, path)
        except ReportError as exc:
            print("standup-render: {}".format(exc), file=stderr)
            return 2
        fields["roles"] = roles.get(name, "")
        answered[name] = fields

    extra = {}
    if args.extra:
        try:
            with open(args.extra, encoding="utf-8") as handle:
                extra = json.load(handle)
        except (OSError, ValueError) as exc:
            print(
                "standup-render: cannot read --extra {}: {} — check that the path "
                "exists and holds a JSON object keyed by agent name, then rerun; "
                "or drop --extra if nobody was skipped.".format(args.extra, exc),
                file=stderr,
            )
            return 1
        if not isinstance(extra, dict):
            print("standup-render: --extra must hold a JSON object keyed by agent name", file=stderr)
            return 1

    rows = build_rows(answered, extra)
    date = args.now.split("T")[0] if "T" in args.now else args.now.split(" ")[0]
    out_path = args.out or os.path.join(args.reports, "standup-{}.md".format(date))
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(rows, args.now, args.team))
    except OSError as exc:
        print(
            "standup-render: cannot write {}: {} — create that directory, or pass "
            "--out with a path in a writable directory, then rerun.".format(
                out_path, exc
            ),
            file=stderr,
        )
        return 1

    # stdout is one JSON object (rules/script-delegation.md): the fenced block
    # travels inside it and the skill relays `block` verbatim. `sort_keys` and
    # a fixed `--now` keep the bytes identical for identical inputs.
    payload = {
        "markdown_path": out_path,
        "block": "```\n" + render_terminal(rows, args.now, args.team) + "\n```",
        "answered": sorted(answered),
        "unasked": sorted(extra),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
