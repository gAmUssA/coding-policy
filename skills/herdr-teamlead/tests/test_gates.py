"""A report's `## Gates` lines decide whether an accepted outcome may be recorded."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import gates
from teamlead.cli import main
from teamlead.errors import UsageError

GREEN = """# Report

Implemented the parser fix.

## Gates

- tests exit=0 cmd=`bash scripts/run-tests.sh`
- diagnostics exit=0 cmd=bash scripts/run-diagnostics.sh | tee /tmp/d.log
Both ran from the worktree root.

## Handoff observations

- none
"""


class ParseTest(unittest.TestCase):
    def test_green_section_lists_each_gate_with_its_exact_command(self):
        result = gates.check(GREEN)
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual(result["gates"], [
            {"name": "tests", "exit": 0, "command": "bash scripts/run-tests.sh"},
            {"name": "diagnostics", "exit": 0, "command": "bash scripts/run-diagnostics.sh | tee /tmp/d.log"},
        ])

    def test_rejects_every_report_that_does_not_prove_its_gates(self):
        cases = {
            "missing section": ("# Report\n\nAll tests pass.\n", "missing `## Gates` section"),
            "empty section": ("## Gates\n\nAll green.\n", "lists no gate"),
            "non-zero exit": (GREEN.replace("tests exit=0", "tests exit=1"), "gate tests exit=1"),
            "did not run": (GREEN.replace("tests exit=0", "tests exit=127"), "gate tests exit=127"),
            "unknown exit": (GREEN.replace("tests exit=0", "tests exit=unknown"), "gate tests exit=unknown"),
            "prose bullet": (GREEN.replace("- tests exit=0 cmd=`bash scripts/run-tests.sh`", "- tests passed"), "malformed gate line: - tests passed"),
            "missing command": (GREEN.replace(" cmd=`bash scripts/run-tests.sh`", ""), "malformed gate line"),
            "duplicate name": (GREEN.replace("- diagnostics", "- tests"), "duplicate gate tests"),
            "fenced example only": ("```\n## Gates\n- tests exit=0 cmd=true\n```\n", "missing `## Gates` section"),
        }
        for label, (body, failure) in cases.items():
            with self.subTest(label):
                result = gates.check(body)
                self.assertFalse(result["ok"])
                self.assertTrue(any(failure in item for item in result["failures"]), result["failures"])

    def test_fenced_content_is_never_gate_evidence(self):
        cases = {
            "tilde-fenced example": ("~~~\n## Gates\n\n- tests exit=0 cmd=true\n~~~\n", "missing `## Gates` section"),
            "shorter backtick fence inside a longer one": (
                "````markdown\n```\n## Gates\n\n- tests exit=0 cmd=true\n```\n````\n", "missing `## Gates` section"),
            "fenced bullets under a real heading": ("## Gates\n\n```\n- tests exit=0 cmd=true\n```\n", "lists no gate"),
            "tilde fence closed only by tildes": (
                "## Gates\n\n~~~~\n```\n- tests exit=0 cmd=true\n~~~~\n", "lists no gate"),
        }
        for label, (body, failure) in cases.items():
            with self.subTest(label):
                result = gates.check(body)
                self.assertFalse(result["ok"], result)
                self.assertTrue(any(failure in item for item in result["failures"]), result["failures"])

    def test_gates_under_a_child_heading_are_validated(self):
        body = GREEN.replace("Both ran from the worktree root.\n",
                             "### Diagnostics\n\n- lint exit=1 cmd=ruff check .\n")
        result = gates.check(body)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failures"], ["gate lint exit=1"])
        self.assertEqual([gate["name"] for gate in result["gates"]], ["tests", "diagnostics", "lint"])

    def test_non_canonical_list_entries_are_malformed_not_prose(self):
        for entry in ("* lint exit=1 cmd=ruff check .", "+ lint exit=1 cmd=ruff check .",
                      "1. lint exit=1 cmd=ruff check .", "2) lint exit=1 cmd=ruff check .",
                      "  - lint exit=1 cmd=ruff check .", "\t- lint exit=1 cmd=ruff check ."):
            with self.subTest(entry=entry):
                result = gates.check(GREEN.replace("Both ran from the worktree root.\n", entry + "\n"))
                self.assertFalse(result["ok"])
                self.assertIn("malformed gate line: " + entry, result["failures"])

    def test_thematic_break_inside_the_section_is_not_a_list_entry(self):
        self.assertTrue(gates.check(GREEN.replace("Both ran from the worktree root.\n", "* * *\n"))["ok"])

    def test_blank_command_is_rejected(self):
        for command in ("``", "` `", "`   `"):
            with self.subTest(command=command):
                result = gates.check(GREEN.replace("cmd=`bash scripts/run-tests.sh`", "cmd=" + command))
                self.assertFalse(result["ok"])
                self.assertIn("blank command for gate tests", result["failures"])

    def test_required_gate_must_be_listed(self):
        self.assertTrue(gates.check(GREEN, ["tests", "diagnostics"])["ok"])
        result = gates.check(GREEN, ["tests", "lint"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failures"], ["missing required gate lint"])

    def test_two_sections_are_refused(self):
        with self.assertRaisesRegex(UsageError, "more than one"):
            gates.check(GREEN + "\n## Gates\n\n- tests exit=0 cmd=true\n")

    def test_every_variant_gates_heading_is_a_duplicate_section(self):
        for heading in (" ## Gates", "   ## Gates", "##\tGates", "## Gates ##", "##  Gates  #####", "## gates", "# GATES"):
            with self.subTest(heading=heading), self.assertRaisesRegex(UsageError, "more than one"):
                gates.check(GREEN + "\n" + heading + "\n\n- lint exit=1 cmd=ruff check .\n")

    def test_variant_gates_heading_alone_is_the_section(self):
        result = gates.check("# Report\n\n ##\tgates ##\n\n- tests exit=0 cmd=true\n")
        self.assertTrue(result["ok"], result["failures"])
        self.assertEqual([gate["name"] for gate in result["gates"]], ["tests"])

    def test_other_headings_are_not_gates_sections(self):
        for heading in ("### Gates", "    ## Gates", "##Gates", "## Gates run"):
            with self.subTest(heading=heading):
                result = gates.check(GREEN + "\n" + heading + "\n\n- lint exit=1 cmd=ruff check .\n")
                self.assertNotIn("more than one", " ".join(result["failures"]))

    def test_require_green_refuses_red_and_accepts_green(self):
        with self.assertRaisesRegex(UsageError, "gate tests exit=2"):
            gates.require_green(GREEN.replace("tests exit=0", "tests exit=2"), "An approved review")
        self.assertTrue(gates.require_green(GREEN, "An approved review")["ok"])


class CheckGatesCommandTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.report = self.root / "report.md"

    def run_check(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        code = main(["check-gates", "--state", str(self.root / "state.json"), "--report", str(self.report), *extra], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_green_report_exits_zero_with_bound_digest(self):
        self.report.write_text(GREEN)
        code, out, err = self.run_check("--require", "tests")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], str(self.report))
        self.assertEqual(len(result["sha256"]), 64)
        self.assertFalse((self.root / "state.json").exists())

    def test_red_report_exits_non_zero_and_names_the_failure(self):
        self.report.write_text(GREEN.replace("diagnostics exit=0", "diagnostics exit=1"))
        code, out, err = self.run_check()
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])
        failure = json.loads(err)
        self.assertEqual(failure["error"], "gates_not_green")
        self.assertEqual(failure["details"]["failures"], ["gate diagnostics exit=1"])


if __name__ == "__main__":
    unittest.main()
