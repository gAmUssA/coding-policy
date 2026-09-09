"""Tests for standup-render.py: parsing, layout, and byte-stable output."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the skill dir, so the module under test would not resolve. The
# consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

# The module under test has a hyphen in its name, so it is loaded by path
# rather than imported. `spec_from_file_location` returns None when the path
# is not loadable, which would surface as an unrelated AttributeError.
_SPEC = importlib.util.spec_from_file_location(
    "standup_render", _os.path.join(_ROOT, "standup-render.py")
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load standup-render.py from {}".format(_ROOT))
standup_render = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(standup_render)

parse_report = standup_render.parse_report
ReportError = standup_render.ReportError
render_terminal = standup_render.render_terminal
build_rows = standup_render.build_rows
main = standup_render.main

NOW = "2026-09-02T09:15"

GOOD = """\
DONE: Landed the parser fix and its tests
PLAN: Review the pushed branch
BLOCKED: none
REPORT: /w/reports/claude.md
"""


class ParseReportTest(unittest.TestCase):
    def test_the_four_lines_parse(self):
        fields = parse_report(GOOD, "claude.md")
        self.assertEqual(fields["DONE"], "Landed the parser fix and its tests")
        self.assertEqual(fields["PLAN"], "Review the pushed branch")
        self.assertEqual(fields["BLOCKED"], "none")
        self.assertEqual(fields["REPORT"], "/w/reports/claude.md")

    def test_blank_lines_around_the_answer_are_tolerated(self):
        fields = parse_report("\n\n" + GOOD + "\n\n", "claude.md")
        self.assertEqual(fields["BLOCKED"], "none")

    def test_lowercase_field_names_parse(self):
        fields = parse_report(GOOD.lower(), "claude.md")
        self.assertEqual(fields["PLAN"], "review the pushed branch")

    def test_a_missing_field_is_named(self):
        text = "DONE: a\nPLAN: b\nREPORT: /w/r.md\n"
        with self.assertRaises(ReportError) as caught:
            parse_report(text, "codex.md")
        message = str(caught.exception)
        self.assertIn("codex.md", message)
        self.assertIn("BLOCKED", message)

    def test_a_repeated_field_is_refused(self):
        text = "DONE: a\nDONE: b\nPLAN: c\nBLOCKED: none\nREPORT: /w/r.md\n"
        with self.assertRaises(ReportError) as caught:
            parse_report(text, "codex.md")
        self.assertIn("more than once", str(caught.exception))

    def test_fields_out_of_order_are_refused(self):
        text = "PLAN: b\nDONE: a\nBLOCKED: none\nREPORT: /w/r.md\n"
        with self.assertRaises(ReportError) as caught:
            parse_report(text, "codex.md")
        self.assertIn("must read DONE, PLAN, BLOCKED, REPORT", str(caught.exception))

    def test_prose_is_refused_rather_than_half_parsed(self):
        # A half-parsed row is worse than a missing one: it looks answered.
        with self.assertRaises(ReportError):
            parse_report("Sure! Here's my standup:\n- did stuff\n", "grok.md")

    def test_prose_before_the_four_lines_is_refused(self):
        # The four fields plus commentary is the non-compliant answer the
        # four-line contract exists to catch; it must not parse as compliant.
        with self.assertRaisesRegex(ReportError, "unexpected line .Sure!"):
            parse_report("Sure! Here's my standup:\n" + GOOD, "grok.md")

    def test_a_sign_off_after_the_four_lines_is_refused(self):
        with self.assertRaisesRegex(ReportError, "unexpected line .Let me know"):
            parse_report(GOOD + "Let me know if you need more.\n", "grok.md")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ReportError):
            parse_report("", "grok.md")


class TerminalLayoutTest(unittest.TestCase):
    def rows(self):
        return build_rows(
            {
                "claude": {
                    "DONE": "Landed the parser fix and its tests",
                    "PLAN": "Review the pushed branch",
                    "BLOCKED": "none",
                    "REPORT": "/w/r.md",
                    "roles": "reviewer",
                }
            },
            {"grok": {"roles": "developer", "note": "busy: refactor", "done": "mid-task"}},
        )

    def test_every_line_is_the_same_width(self):
        block = render_terminal(self.rows(), NOW, "acme")
        widths = {len(line) for line in block.splitlines()}
        self.assertEqual(len(widths), 1, "ragged block: {}".format(sorted(widths)))

    def test_the_banner_carries_the_date_and_the_team(self):
        block = render_terminal(self.rows(), NOW, "acme")
        self.assertIn("STANDUP — {} acme".format(NOW), block)
        self.assertTrue(block.splitlines()[0].startswith("╔"))

    def test_a_long_cell_wraps_instead_of_widening_the_table(self):
        rows = build_rows(
            {
                "claude": {
                    "DONE": " ".join(["word"] * 40),
                    "PLAN": "x",
                    "BLOCKED": "none",
                    "REPORT": "/w/r.md",
                    "roles": "reviewer",
                }
            },
            {},
        )
        block = render_terminal(rows, NOW, "")
        widths = {len(line) for line in block.splitlines()}
        self.assertEqual(len(widths), 1)
        self.assertGreater(len(block.splitlines()), 8)

    def test_an_empty_cell_renders_a_dash(self):
        block = render_terminal(build_rows({}, {"gemini": {"note": "absent"}}), NOW, "")
        # The agent cell wraps, so the note lands on the row below the name.
        self.assertIn("gemini", block)
        self.assertIn("(absent)", block)
        self.assertIn("—", block)

    def test_rows_are_ordered_answered_then_unasked_each_by_name(self):
        rows = build_rows(
            {
                "zeta": {"DONE": "a", "PLAN": "b", "BLOCKED": "none", "REPORT": "/r", "roles": ""},
                "alpha": {"DONE": "a", "PLAN": "b", "BLOCKED": "none", "REPORT": "/r", "roles": ""},
            },
            {"yankee": {}, "bravo": {}},
        )
        self.assertEqual([r["agent"] for r in rows], ["alpha", "zeta", "bravo", "yankee"])


class GoldenTest(unittest.TestCase):
    """A three-agent standup: one answered, one busy, one absent."""

    GOLDEN = "\n".join(
        [
            "╔══════════════════════════════════════════════════════════════════════════════════════════════════════════╗",
            "║ STANDUP — 2026-09-02T09:15 acme fleet                                                                    ║",
            "╚══════════════════════════════════════════════════════════════════════════════════════════════════════════╝",
            "┌────────────────┬──────────────┬────────────────────────────┬────────────────────────┬────────────────────┐",
            "│ Agent          │ Role(s)      │ Done                       │ Plan                   │ Blocked            │",
            "├────────────────┼──────────────┼────────────────────────────┼────────────────────────┼────────────────────┤",
            "│ claude         │ reviewer     │ Landed the parser fix and  │ Review the pushed      │ none               │",
            "│                │              │ its tests                  │ branch                 │                    │",
            "│ gemini         │ —            │ —                          │ —                      │ —                  │",
            "│ (absent)       │              │                            │                        │                    │",
            "│ grok (busy:    │ developer    │ mid-task                   │ finish the refactor    │ none               │",
            "│ refactor)      │              │                            │                        │                    │",
            "└────────────────┴──────────────┴────────────────────────────┴────────────────────────┴────────────────────┘",
        ]
    )

    def test_the_block_is_byte_stable(self):
        rows = build_rows(
            {
                "claude": {
                    "DONE": "Landed the parser fix and its tests",
                    "PLAN": "Review the pushed branch",
                    "BLOCKED": "none",
                    "REPORT": "/w/r.md",
                    "roles": "reviewer",
                }
            },
            {
                "grok": {
                    "roles": "developer",
                    "note": "busy: refactor",
                    "done": "mid-task",
                    "plan": "finish the refactor",
                    "blocked": "none",
                },
                "gemini": {"roles": "—", "note": "absent"},
            },
        )
        self.assertEqual(render_terminal(rows, NOW, "acme fleet"), self.GOLDEN)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="standup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.reports = self.tmp / "reports"
        self.reports.mkdir()
        self.report = self.reports / "claude.md"
        self.report.write_text(GOOD, encoding="utf-8")
        self.out = io.StringIO()
        self.err = io.StringIO()

    def run_cli(self, argv):
        code = main(argv, stdout=self.out, stderr=self.err)
        return code, self.out.getvalue(), self.err.getvalue()

    def test_it_writes_the_markdown_and_emits_the_block_inside_json(self):
        code, out, _err = self.run_cli(
            [
                "--reports", str(self.reports),
                "--now", NOW,
                "--agent", "claude={}".format(self.report),
            ]
        )
        self.assertEqual(code, 0)
        # One JSON line on stdout, never the block itself: the skill relays it.
        self.assertEqual(out.count("\n"), 1)
        payload = json.loads(out)
        self.assertTrue(payload["block"].startswith("```\n"))
        self.assertTrue(payload["block"].endswith("```"))
        written = self.reports / "standup-2026-09-02.md"
        self.assertEqual(payload["markdown_path"], str(written))
        self.assertEqual(payload["answered"], ["claude"])
        self.assertEqual(payload["unasked"], [])
        self.assertTrue(written.exists())
        self.assertIn("| claude |", written.read_text(encoding="utf-8"))

    def test_an_unreadable_roles_file_names_the_fix(self):
        code, out, err = self.run_cli(
            [
                "--reports", str(self.reports),
                "--now", NOW,
                "--agent", "claude={}".format(self.report),
                "--roles", str(self.tmp / "nope" / "roles.json"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--roles", err)
        self.assertIn("rerun", err)

    def test_an_unwritable_out_path_names_the_fix(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        code, out, err = self.run_cli(
            [
                "--reports", str(self.reports),
                "--now", NOW,
                "--agent", "claude={}".format(self.report),
                "--out", str(blocker / "standup.md"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--out", err)
        self.assertIn("rerun", err)

    def test_the_same_inputs_render_the_same_bytes(self):
        first = self.run_cli(
            ["--reports", str(self.reports), "--now", NOW, "--agent", "claude={}".format(self.report)]
        )[1]
        self.out = io.StringIO()
        second = self.run_cli(
            ["--reports", str(self.reports), "--now", NOW, "--agent", "claude={}".format(self.report)]
        )[1]
        self.assertEqual(first, second)

    def test_a_malformed_report_exits_two_and_names_the_file(self):
        bad = self.reports / "grok.md"
        bad.write_text("I had a productive day!\n", encoding="utf-8")
        code, out, err = self.run_cli(
            ["--reports", str(self.reports), "--now", NOW, "--agent", "grok={}".format(bad)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("grok.md", err)

    def test_a_missing_report_exits_one_and_says_what_to_do(self):
        code, _out, err = self.run_cli(
            ["--reports", str(self.reports), "--now", NOW, "--agent", "ghost=/nope/none.md"]
        )
        self.assertEqual(code, 1)
        self.assertIn("--extra", err)

    def test_extra_rows_need_no_report_file(self):
        extra = self.tmp / "extra.json"
        extra.write_text(json.dumps({"grok": {"note": "busy: refactor"}}), encoding="utf-8")
        code, out, _err = self.run_cli(
            [
                "--reports", str(self.reports),
                "--now", NOW,
                "--agent", "claude={}".format(self.report),
                "--extra", str(extra),
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("grok (busy:", payload["block"])
        self.assertEqual(payload["unasked"], ["grok"])

    def test_a_bad_agent_pair_is_refused(self):
        code, _out, err = self.run_cli(
            ["--reports", str(self.reports), "--now", NOW, "--agent", "claude"]
        )
        self.assertEqual(code, 1)
        self.assertIn("NAME=PATH", err)


if __name__ == "__main__":
    unittest.main()
