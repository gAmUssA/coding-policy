#!/usr/bin/env python3
"""The checkout's hand-maintained file lists agree with what is on disk.

Two lists in this repo enumerate files by hand, and both go stale silently.
`.claude/CLAUDE.md` @-imports the rules an agent working in this checkout
reads: a rule declared in `.tessl-plugin/plugin.json` but missing there ships
to consumers while staying invisible to maintainers (#368).
`pyrightconfig.json` enumerates the files the diagnostics gate type-checks: a
Python file missing there is checked by nothing, and CI still reports zero
findings (`rules/language-diagnostics.md` Gate It Deterministically).

Neither drift breaks a build, so nothing surfaced either one. This suite is
that check.
"""

import json
import re
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = ROOT / ".tessl-plugin" / "plugin.json"
INDEX = ROOT / ".claude" / "CLAUDE.md"
PYRIGHT = ROOT / "pyrightconfig.json"

#: An `@`-import line in the index, relative to `.claude/`.
IMPORT = re.compile(r"^@\.\./(rules/[^\s]+\.md)\s*$", re.MULTILINE)


def declared_rules():
    """Rule paths the plugin manifest publishes, repo-relative."""
    rules = json.loads(MANIFEST.read_text())["rules"]
    if isinstance(rules, str):
        base = ROOT / rules
        return sorted(str(p.relative_to(ROOT)) for p in base.glob("*.md"))
    return sorted(rules)


def indexed_rules():
    """Rule paths the source instruction index imports, repo-relative."""
    return sorted(IMPORT.findall(INDEX.read_text()))


def duplicates(paths):
    """Paths listed more than once — a set difference would hide them."""
    return sorted(path for path, count in Counter(paths).items() if count > 1)


class SourceIndex(unittest.TestCase):
    def test_index_matches_manifest(self):
        declared, indexed = declared_rules(), indexed_rules()
        self.assertEqual(duplicates(declared), [], "rule declared twice in .tessl-plugin/plugin.json")
        self.assertEqual(duplicates(indexed), [], "rule imported twice by .claude/CLAUDE.md")
        self.assertEqual(
            declared, indexed,
            ".tessl-plugin/plugin.json and .claude/CLAUDE.md do not name the same rule files",
        )

    def test_every_indexed_rule_exists(self):
        missing = [r for r in indexed_rules() if not (ROOT / r).is_file()]
        self.assertEqual(missing, [], "index imports a rule file that does not exist")

    def test_index_covers_every_rule_file(self):
        on_disk = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "rules").glob("*.md"))
        self.assertEqual(
            sorted(set(on_disk) - set(indexed_rules())), [],
            "rules/*.md file missing from .claude/CLAUDE.md",
        )


class DiagnosticsScope(unittest.TestCase):
    """pyrightconfig.json's include list, files or directories, covers every tracked module."""

    def tracked_python(self):
        listing = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                                 capture_output=True, text=True, check=True)
        return sorted(listing.stdout.split())

    def included(self):
        return sorted(json.loads(PYRIGHT.read_text())["include"])

    def test_no_path_is_included_twice(self):
        self.assertEqual(duplicates(self.included()), [], "pyrightconfig.json includes a path twice")

    def covered(self, path):
        """Is `path` named by the include list, or under an included directory?"""
        for entry in self.included():
            if path == entry or path.startswith(entry.rstrip("/") + "/"):
                return True
        return False

    def test_every_tracked_module_is_type_checked(self):
        uncovered = sorted(p for p in self.tracked_python() if not self.covered(p))
        self.assertEqual(
            uncovered, [],
            "tracked .py file outside pyrightconfig.json's include list — the "
            "diagnostics gate does not see it",
        )

    def test_every_included_path_exists(self):
        missing = [path for path in self.included() if not (ROOT / path).exists()]
        self.assertEqual(missing, [], "pyrightconfig.json includes a path that does not exist")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
