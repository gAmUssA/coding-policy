"""Guard: every test file must run as a script and run ALL of its tests.

Two defects this file exists to catch, both of which shipped:

1. `unittest.main()` landing mid-file. Appending a class after the entry-point
   guard silently strands it: as a script, `unittest.main()` runs against the
   globals defined so far and then exits, so nothing below the guard is even
   defined. Three files had 87, 122, and 404 stranded lines and the suite
   stayed green, because `-m unittest` imports the whole module first and
   never executes the guard.
2. A test file that cannot run as a script at all. `python3 tests/test_x.py`
   puts tests/ on sys.path rather than the repo root, so `teamlead` does not
   import. Every file failed that way while `discover` passed.

The consuming repo's runner executes files as scripts, so both are invisible
here and fatal there. Hence: assert the guard is the LAST top-level statement,
and assert the two invocations run the same number of tests.
"""

import ast
import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

#: Set in a child process so it does not spawn grandchildren. The child still
#: runs its AST checks and still reports the same `Ran N`, because unittest
#: counts a skipped test.
RECURSION_ENV = "TEAMLEAD_ENTRYPOINT_CHILD"

#: `Ran 42 tests in 0.1s` -> 42
RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)

#: How long a single test file gets to run as a script. Bounded so a child
#: that somehow re-spawns fails loudly instead of wedging the suite.
SCRIPT_TIMEOUT_SEC = 120


def test_files():
    """Every test module in tests/, this one included."""
    return sorted(TESTS_DIR.glob("test_*.py"))


def is_entrypoint_guard(node):
    """True when `node` is `if __name__ == "__main__":`."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    )


#: One subprocess per file per run, shared by every assertion below.
_SCRIPT_RESULTS = {}


def script_test_count(path):
    """Run `path` as a script and return `(test_count, exit_code)`, cached."""
    key = str(path)
    if key not in _SCRIPT_RESULTS:
        _SCRIPT_RESULTS[key] = _run_script(path)
    return _SCRIPT_RESULTS[key]


def _run_script(path):
    env = dict(os.environ)
    env[RECURSION_ENV] = "1"
    completed = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(REPO_ROOT),
        env=env,  # without this the child re-spawns every file, recursively
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=SCRIPT_TIMEOUT_SEC,
        check=False,
        text=True,
    )
    match = RAN_RE.search(completed.stdout or "")
    if match is None:
        raise AssertionError(
            "`{} {}` reported no test count. Output:\n{}".format(
                sys.executable, path.name, (completed.stdout or "")[-2000:]
            )
        )
    return int(match.group(1)), completed.returncode


def loader_test_count(path):
    """How many tests `python3 -m unittest tests.<module>` would run."""
    suite = unittest.TestLoader().loadTestsFromName("tests." + path.stem)
    return suite.countTestCases()


class GuardPlacementTest(unittest.TestCase):
    """Cheap, no subprocesses: the guard must be the last top-level statement."""

    def test_every_test_file_has_exactly_one_guard(self):
        for path in test_files():
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                guards = [node for node in tree.body if is_entrypoint_guard(node)]
                self.assertEqual(len(guards), 1, "expected one entry-point guard")

    def test_the_guard_is_the_last_top_level_statement(self):
        for path in test_files():
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    is_entrypoint_guard(tree.body[-1]),
                    "the entry-point guard must come last, or every class "
                    "defined after it is stranded when the file runs as a "
                    "script",
                )

    def test_no_class_is_defined_after_the_guard(self):
        # The same rule stated the way it actually bites.
        for path in test_files():
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                guard_at = next(
                    index
                    for index, node in enumerate(tree.body)
                    if is_entrypoint_guard(node)
                )
                after = [
                    node.name
                    for node in tree.body[guard_at + 1 :]
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                ]
                self.assertEqual(after, [], "stranded after the guard")

    def test_this_file_is_covered_by_its_own_check(self):
        self.assertIn(Path(__file__).resolve(), test_files())

    def test_there_is_something_to_check(self):
        # A glob that silently matched nothing would make every test above
        # vacuously true.
        self.assertGreaterEqual(len(test_files()), 2)


class ScriptExecutionTest(unittest.TestCase):
    """The empirical half: run each file both ways and compare the counts."""

    def setUp(self):
        if os.environ.get(RECURSION_ENV):
            self.skipTest("running as a child; not spawning grandchildren")

    def test_each_file_runs_the_same_tests_as_a_script(self):
        for path in test_files():
            with self.subTest(file=path.name):
                ran, _code = script_test_count(path)
                self.assertEqual(
                    ran,
                    loader_test_count(path),
                    "running {} as a script ran a different number of tests "
                    "than `-m unittest tests.{}` -- something after the "
                    "entry-point guard is stranded".format(path.name, path.stem),
                )

    def test_each_file_passes_when_run_as_a_script(self):
        for path in test_files():
            with self.subTest(file=path.name):
                _ran, code = script_test_count(path)
                self.assertEqual(code, 0, "{} failed as a script".format(path.name))

    def test_a_stranded_class_would_be_caught(self):
        # Prove the check has teeth: a file whose guard sits mid-body reports
        # fewer tests than the loader finds.
        stranded = ast.parse(
            "import unittest\n"
            "class A(unittest.TestCase):\n"
            "    def test_a(self):\n        pass\n"
            'if __name__ == "__main__":\n    unittest.main()\n'
            "class B(unittest.TestCase):\n"
            "    def test_b(self):\n        pass\n"
        )
        guard_at = next(
            index for index, node in enumerate(stranded.body) if is_entrypoint_guard(node)
        )
        after = [
            node.name
            for node in stranded.body[guard_at + 1 :]
            if isinstance(node, ast.ClassDef)
        ]
        self.assertEqual(after, ["B"])


if __name__ == "__main__":
    unittest.main()
