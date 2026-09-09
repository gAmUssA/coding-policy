"""Public resolver outcomes for installed policy artifacts; no user HOME edits."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "resolve-policy-paths.sh"
RELEASE = Path("plugins/gamussa/coding-policy/skills/release/SKILL.md")


class ResolvePolicyPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.local = self.project / ".tessl"
        self.global_root = self.root / "global with spaces"

    def artifact(self, root, relative, content="policy fixture\n"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return str(path)

    def invoke(self, *args, env=None):
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *(args or (str(self.project), str(self.global_root)))],
            text=True, capture_output=True, check=False, env=env,
        )

    def test_local_precedes_global_and_spaces_survive(self):
        expected = {"POLICY_INDEX": self.artifact(self.local, Path("RULES.md")),
                    "RELEASE_SKILL": self.artifact(self.local, RELEASE)}
        self.artifact(self.global_root, Path("RULES.md"))
        self.artifact(self.global_root, RELEASE)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_global_only_install(self):
        expected = {"POLICY_INDEX": self.artifact(self.global_root, Path("RULES.md")),
                    "RELEASE_SKILL": self.artifact(self.global_root, RELEASE)}
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_each_artifact_selects_its_own_available_root(self):
        expected = {"POLICY_INDEX": self.artifact(self.local, Path("RULES.md")),
                    "RELEASE_SKILL": self.artifact(self.global_root, RELEASE)}
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)

    def test_missing_release_emits_no_partial_result(self):
        self.artifact(self.local, Path("RULES.md"))
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("install or repair", result.stderr)
        self.assertIn(str(RELEASE), result.stderr)

    def test_empty_or_directory_artifacts_cannot_supply_policy(self):
        self.artifact(self.local, Path("RULES.md"), "")
        (self.local / RELEASE).mkdir(parents=True)
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_invalid_roots_are_actionable(self):
        for args in [("relative", str(self.global_root)),
                     (str(self.project), "relative"),
                     (str(self.root / "absent"), str(self.global_root)),
                     (str(self.project), str(self.global_root) + "\n")]:
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn("absolute", result.stderr)

    def test_missing_jq_is_a_precondition_failure(self):
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        result = self.invoke(env={**os.environ, "PATH": str(empty_bin)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("install jq", result.stderr)


if __name__ == "__main__":
    unittest.main()
