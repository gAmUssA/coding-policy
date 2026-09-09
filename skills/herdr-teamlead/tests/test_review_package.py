"""Real Git fixtures for review packages and their brief-composition gate."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]


class ReviewPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key != "TEAMLEAD_REPORTS_DIR"}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.test",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.test",
            "GIT_AUTHOR_DATE": "2020-01-02T03:04:05+00:00",
            "GIT_COMMITTER_DATE": "2020-01-02T03:04:05+00:00",
            "LC_ALL": "C",
        })
        self.git("init", "-q")
        self.lines = [f"line {number}" for number in range(1, 51)]
        self.source = self.repo / "source.txt"
        self.base = self.commit("initial")
        self.lines[14] = "first change"
        self.first = self.commit("first implementation")
        self.lines[34] = "second change"
        self.head = self.commit("second implementation")
        self.output = self.root / "round reports" / "package.diff"

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              text=True, capture_output=True, check=True).stdout.strip()

    def commit(self, message):
        self.source.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def package(self, *args, env=None, cwd=None):
        return subprocess.run(["bash", str(SKILL / "review-package.sh"), *map(str, args)],
                              cwd=cwd or self.repo, env=env or self.env,
                              text=True, capture_output=True, check=False)

    def test_multicommit_range_contains_both_commits_stat_and_ten_context_lines(self):
        result = self.package(self.base, self.head, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{self.output}\n")
        text = self.output.read_text(encoding="utf-8")
        self.assertIn(f"BASE: {self.base}\nHEAD: {self.head}", text)
        self.assertIn(f"{self.first} first implementation", text)
        self.assertIn(f"{self.head} second implementation", text)
        self.assertIn("1 file changed, 2 insertions(+), 2 deletions(-)", text)
        self.assertIn("+first change", text)
        self.assertIn("+second change", text)
        self.assertIn(" line 5\n", text)
        self.assertNotIn(" line 4", text.splitlines())
        self.assertIn(" line 45\n", text)
        self.assertNotIn(" line 46", text.splitlines())
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_default_name_changes_after_fix_and_old_package_stays_unchanged(self):
        env = dict(self.env, TEAMLEAD_REPORTS_DIR=str(self.output.parent))
        before = self.package(self.base, self.first, env=env)
        after = self.package(self.base, self.head, env=env)
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(after.returncode, 0, after.stderr)
        old_path = self.output.parent / f"review-{self.base[:7]}..{self.first[:7]}.diff"
        new_path = self.output.parent / f"review-{self.base[:7]}..{self.head[:7]}.diff"
        self.assertEqual(before.stdout, f"{old_path}\n")
        self.assertEqual(after.stdout, f"{new_path}\n")
        self.assertNotIn("second change", old_path.read_text(encoding="utf-8"))
        self.assertIn("second change", new_path.read_text(encoding="utf-8"))

    def test_identical_run_reuses_artifact_even_with_display_config_overrides(self):
        self.assertEqual(self.package(self.base, self.head, self.output).returncode, 0)
        before = self.output.read_bytes()
        for key, value in (
            ("color.ui", "always"), ("log.decorate", "full"),
            ("log.showSignature", "true"), ("diff.context", "1"),
            ("diff.noprefix", "true"), ("diff.relative", "true"),
            ("diff.algorithm", "histogram"), ("diff.indentHeuristic", "true"),
        ):
            self.git("config", key, value)
        result = self.package(self.base, self.head, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), before)

    def test_bad_refs_exit_two_without_creating_output(self):
        tree = self.git("rev-parse", "HEAD^{tree}")
        for base, head in (("missing-ref", self.head), (self.base, "missing-ref"),
                           ("--help", self.head), (tree, self.head)):
            with self.subTest(base=base, head=head):
                result = self.package(base, head, self.output)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("fetch", result.stderr)
                self.assertFalse(self.output.parent.exists())

    def test_usage_and_missing_default_output_directory_are_actionable(self):
        for args in ((), (self.base,), (self.base, self.head),
                     (self.base, self.head, self.output, "extra")):
            with self.subTest(args=args):
                result = self.package(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr)

    def test_relative_output_and_empty_predevelopment_range(self):
        result = self.package(self.base, self.base, "round/package.diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.repo / "round/package.diff"
        self.assertEqual(result.stdout, f"{path}\n")
        self.assertIn(f"BASE: {self.base}\nHEAD: {self.base}", path.read_text())
        self.assertNotIn("diff --git", path.read_text())

    def test_different_existing_file_is_preserved(self):
        self.output.parent.mkdir()
        self.output.write_text("unrelated report\n", encoding="utf-8")
        result = self.package(self.base, self.head, self.output)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.output.read_text(), "unrelated report\n")
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_symlink_and_multiline_output_are_refused(self):
        self.output.parent.mkdir()
        self.output.symlink_to(self.source)
        before = self.source.read_bytes()
        for path in (self.output, str(self.output) + "\nsecond", self.output.parent):
            with self.subTest(path=path):
                result = self.package(self.base, self.head, path)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.source.read_bytes(), before)

    def test_comparison_failure_is_not_reported_as_different_content(self):
        self.output.parent.mkdir()
        self.output.write_text("existing artifact\n", encoding="utf-8")
        fakebin = self.root / "bin"
        fakebin.mkdir()
        fakecmp = fakebin / "cmp"
        fakecmp.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexit 2\n', encoding="utf-8")
        fakecmp.chmod(0o755)
        env = dict(self.env, PATH=f"{fakebin}:{self.env['PATH']}")
        result = self.package(self.base, self.head, self.output, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot compare", result.stderr)
        self.assertIn("cmp exit 2", result.stderr)
        self.assertNotIn("different content", result.stderr)
        self.assertEqual(self.output.read_text(), "existing artifact\n")
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_binary_change_is_not_reduced_to_a_filename(self):
        (self.repo / "binary.dat").write_bytes(bytes(range(256)))
        head = self.commit("binary fixture")
        result = self.package(self.base, head, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GIT binary patch", self.output.read_text())

    def test_git_failure_does_not_publish_partial_content(self):
        executable = shutil.which("git")
        self.assertIsNotNone(executable)
        fakebin = self.root / "bin"
        fakebin.mkdir()
        fakegit = fakebin / "git"
        fakegit.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'if [[ "${2:-}" == diff ]]; then printf "partial diff\\n"; exit 42; fi\n'
            'exec "$PACKAGE_TEST_REAL_GIT" "$@"\n', encoding="utf-8")
        fakegit.chmod(0o755)
        env = dict(self.env, PATH=f"{fakebin}:{self.env['PATH']}",
                   PACKAGE_TEST_REAL_GIT=str(executable))
        result = self.package(self.base, self.head, self.output, env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("no artifact published", result.stderr)
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_concurrent_directory_cannot_be_reported_as_a_written_package(self):
        executable = shutil.which("ln")
        self.assertIsNotNone(executable)
        fakebin = self.root / "bin"
        fakebin.mkdir()
        fakeln = fakebin / "ln"
        fakeln.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'mkdir "$PACKAGE_TEST_OUTPUT"\n'
            'exec "$PACKAGE_TEST_REAL_LN" "$@"\n', encoding="utf-8")
        fakeln.chmod(0o755)
        env = dict(self.env, PATH=f"{fakebin}:{self.env['PATH']}",
                   PACKAGE_TEST_REAL_LN=str(executable), PACKAGE_TEST_OUTPUT=str(self.output))
        result = self.package(self.base, self.head, self.output, env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_sourcing_does_not_build_or_print_a_package(self):
        result = subprocess.run(["bash", "-c", 'source "$1"', "test",
                                 str(SKILL / "review-package.sh")],
                                cwd=self.root, env=self.env, text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_package_gate_leaves_entire_round_unwritten(self):
        templates = self.root / "templates"
        templates.mkdir()
        (templates / "COMMON.md").write_text("Common instructions\n", encoding="utf-8")
        (templates / "brief-developer.md").write_text("Develop {{ISSUE}}\nREPORT: {{REPORT}}\n", encoding="utf-8")
        for role in ("reviewer", "tester"):
            (templates / f"brief-{role}.md").write_text(
                "Review {{ISSUE}} with {{REVIEW_PACKAGE}} over {{REVIEW_BASE}}..{{REVIEW_HEAD}}\nREPORT: {{REPORT}}\n", encoding="utf-8")
        empty = self.root / "empty.diff"
        empty.touch()
        missing = self.root / "missing.diff"
        values = self.root / "values.json"
        for role in ("reviewer", "tester"):
            for package in (None, str(missing), str(empty), str(self.repo), "relative.diff"):
                with self.subTest(role=role, package=package):
                    review_values = {"REVIEW_BASE": self.base, "REVIEW_HEAD": self.head, "REPORT": f"/r/{role}.md"}
                    if package is not None:
                        review_values["REVIEW_PACKAGE"] = package
                    values.write_text(json.dumps({"shared": {"ISSUE": "#323"},
                        "roles": {"developer": {"REPORT": "/r/developer.md"}, role: review_values}}), encoding="utf-8")
                    outdir = self.root / "briefs"
                    result = subprocess.run(["bash", str(SKILL / "compose-briefs.sh"),
                                             str(templates), str(values), str(outdir)],
                                            env=self.env, text=True, capture_output=True, check=False)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("REVIEW_PACKAGE", result.stderr)
                    self.assertFalse(outdir.exists())
        # A real generated package flows through the same gate into both briefs.
        result = self.package(self.base, self.head, self.output)
        self.assertEqual(result.returncode, 0, result.stderr)
        values.write_text(json.dumps({"shared": {"ISSUE": "#323"}, "roles": {
            role: {"REVIEW_PACKAGE": result.stdout.strip(), "REVIEW_BASE": self.base, "REVIEW_HEAD": self.head, "REPORT": f"/r/{role}.md"} for role in ("reviewer", "tester")
        }}), encoding="utf-8")
        outdir = self.root / "briefs"
        result = subprocess.run(["bash", str(SKILL / "compose-briefs.sh"),
                                 str(templates), str(values), str(outdir)],
                                env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        for role in ("reviewer", "tester"):
            self.assertIn(str(self.output), (outdir / f"brief-{role}.md").read_text())


    def test_malformed_review_range_refuses_before_writing_any_brief(self):
        templates = self.root / "range-templates"
        templates.mkdir()
        (templates / "COMMON.md").write_text("Common\n", encoding="utf-8")
        (templates / "brief-developer.md").write_text("Develop\nREPORT: {{REPORT}}\n", encoding="utf-8")
        for role in ("reviewer", "tester"):
            (templates / f"brief-{role}.md").write_text(
                "Review {{REVIEW_BASE}}..{{REVIEW_HEAD}} in {{REVIEW_PACKAGE}}\nREPORT: {{REPORT}}\n", encoding="utf-8")
        self.assertEqual(self.package(self.base, self.head, self.output).returncode, 0)
        values = self.root / "invalid-range.json"
        for role in ("reviewer", "tester"):
            for key in ("REVIEW_BASE", "REVIEW_HEAD"):
                for invalid in ("", "main", "abc123", " " + self.base, "G" * 40, 123):
                    with self.subTest(role=role, key=key, value=invalid):
                        fields = {"REVIEW_BASE": self.base, "REVIEW_HEAD": self.head, "REVIEW_PACKAGE": str(self.output), "REPORT": f"/r/{role}.md", key: invalid}
                        values.write_text(json.dumps({"shared": {}, "roles": {"developer": {"REPORT": "/r/developer.md"}, role: fields}}))
                        outdir = self.root / "invalid-briefs"
                        result = subprocess.run(["bash", str(SKILL / "compose-briefs.sh"), str(templates), str(values), str(outdir)],
                                                env=self.env, capture_output=True, text=True, check=False)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn(key, result.stderr)
                        self.assertFalse(outdir.exists())


if __name__ == "__main__":
    unittest.main()
