#!/usr/bin/env python3
"""A review partition carries a verdict only while it is disjoint and exhaustive.

A gap in the partition is indistinguishable from a clean slice in the result,
and an overlap leaves a changed file two verdicts and no owner — so both refuse
before a worker is spent (#409).
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from teamlead import partition
from teamlead.errors import UsageError

SLICES = [{"name": "api", "paths": ["src/api/*"]},
          {"name": "core", "paths": ["src/core/*", "README.md"]}]


def document(**overrides):
    base = {"schema_version": partition.PARTITION_SCHEMA_VERSION, "slices": SLICES}
    base.update(overrides)
    return base


class LoadPartition(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)

    def write(self, payload):
        path = self.tmp / "partition.json"
        path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
        return str(path)

    def test_a_valid_document_loads(self):
        loaded = partition.load_partition(self.write(document()))
        self.assertEqual([entry["name"] for entry in loaded["slices"]], ["api", "core"])

    def test_a_missing_path_is_refused(self):
        with self.assertRaisesRegex(UsageError, "Pass --partition"):
            partition.load_partition(None)

    def test_unreadable_or_wrong_shape_is_refused(self):
        for label, payload, pattern in (
            ("not json", "{", "Cannot read the partition"),
            ("not an object", [1, 2], "schema_version"),
            ("wrong version", document(schema_version=99), "schema_version"),
            ("one slice", document(slices=[SLICES[0]]), "at least two slices"),
            ("unknown field", document(extra=1), "unknown field"),
            ("duplicate name", document(slices=[SLICES[0], SLICES[0]]), "appears twice"),
            ("empty paths", document(slices=[{"name": "api", "paths": []}, SLICES[1]]), "non-empty array"),
            ("slice not an object", document(slices=["api", SLICES[1]]), "non-empty name"),
            ("role with the separator", document(role="rev#iew"), "without"),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(UsageError, pattern):
                    partition.load_partition(self.write(payload))

    def test_the_seated_role_defaults_to_reviewer(self):
        self.assertEqual(partition.partition_role(document()), "reviewer")
        self.assertEqual(partition.partition_role(document(role="tester")), "tester")


class Validate(unittest.TestCase):
    def test_a_disjoint_exhaustive_partition_reports_its_ownership(self):
        result = partition.validate({"src/api/routes.py", "src/core/db.py", "README.md"}, document())
        self.assertEqual(result["slices"],
                         [{"name": "api", "paths": ["src/api/routes.py"]},
                          {"name": "core", "paths": ["README.md", "src/core/db.py"]}])
        self.assertEqual(result["changed"], ["README.md", "src/api/routes.py", "src/core/db.py"])

    def test_an_unowned_path_is_refused_by_name(self):
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/api/routes.py", "src/core/db.py", "docs/guide.md"}, document())
        self.assertIn("docs/guide.md", raised.exception.message)
        self.assertEqual(raised.exception.details["unowned"], ["docs/guide.md"])
        self.assertEqual(raised.exception.details["overlaps"], [])

    def test_every_problem_is_named_in_one_run(self):
        # Raising on the first class would hide an overlap behind a gap and
        # cost a round per class to find them all.
        mixed = document(slices=[{"name": "api", "paths": ["src/*/*"]},
                                 {"name": "core", "paths": ["src/core/*"]},
                                 {"name": "docs", "paths": ["nothing/*"]}])
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/core/db.py", "README.md"}, mixed)
        details = raised.exception.details
        self.assertEqual(details["unowned"], ["README.md"])
        self.assertEqual(details["overlaps"], [{"path": "src/core/db.py", "slices": ["api", "core"]}])
        self.assertEqual(details["empty"], ["docs"])
        for expected in ("README.md", "src/core/db.py", "docs"):
            self.assertIn(expected, raised.exception.message)

    def test_an_overlap_is_refused_naming_both_slices(self):
        overlapping = document(slices=[{"name": "api", "paths": ["src/*/*"]},
                                       {"name": "core", "paths": ["src/core/*"]}])
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/core/db.py"}, overlapping)
        self.assertEqual(raised.exception.details["overlaps"],
                         [{"path": "src/core/db.py", "slices": ["api", "core"]}])
        self.assertEqual(raised.exception.details["unowned"], [])

    def test_a_slice_owning_nothing_is_refused(self):
        with self.assertRaises(UsageError) as raised:
            partition.validate({"src/api/routes.py"}, document())
        self.assertEqual(raised.exception.details["empty"], ["core"])


class RunCommand(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.path = self.tmp / "partition.json"
        self.path.write_text(json.dumps(document()))

    def runner(self, changed):
        def run(args):
            self.assertIn("--name-status", args)
            return "".join("M\0{}\0".format(path) for path in changed)
        return run

    def test_it_validates_the_round_diff(self):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head="HEAD", partition=str(self.path))
        result, failure = partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py"]))
        self.assertIsNone(failure)
        self.assertEqual([entry["name"] for entry in result["slices"]], ["api", "core"])

    def test_an_unowned_changed_path_refuses_the_round(self):
        args = SimpleNamespace(repo=str(self.tmp), base="BASE", head=None, partition=str(self.path))
        with self.assertRaisesRegex(UsageError, "unowned"):
            partition.run_command(args, runner=self.runner(["src/api/routes.py", "src/core/db.py", "other.txt"]))


if __name__ == "__main__":
    _sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
