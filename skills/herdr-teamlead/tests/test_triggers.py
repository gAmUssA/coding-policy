"""Diff-time Team Composition trigger detection and its unaddressed-trigger gate."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import cli, triggers
from teamlead.errors import UsageError


DECLARATION = {
    "schema_version": 1,
    "package_roots": ["src/*", "tools"],
    "package_change_lines": 50,
    "trust_boundary_paths": ["src/auth/*", "verify-*.sh"],
    "cli_spec_paths": ["src/cli/*.py"],
    "cli_surface_markers": ["add_parser(", "add_argument("],
    "user_doc_paths": ["docs/*", "README.md"],
}


def declaration(**overrides):
    return {**DECLARATION, **overrides, "path": ".herdr/triggers.json"}


class TempCase(unittest.TestCase):
    def temp_dir(self):
        """A directory removed on teardown (rules/testing-standards.md)."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        return Path(holder.name)


def namespace(**overrides):
    fields = {"repo": ".", "base": "BASE", "head": None, "roles": None,
              "requirements": None, "decisions": None}
    return SimpleNamespace(**{**fields, **overrides})


class DeclarationTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        self.path = self.tmp / triggers.DECLARATION_FILE

    def write(self, payload):
        self.path.write_text(json.dumps(payload))

    def test_reads_a_complete_declaration(self):
        self.write(DECLARATION)
        result = triggers.load_declaration(self.tmp)
        self.assertEqual(result["package_change_lines"], 50)
        self.assertEqual(result["package_roots"], ["src/*", "tools"])
        self.assertEqual(result["path"], str(self.path))

    def test_missing_declaration_names_what_to_state(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("No trigger declaration at", caught.exception.message)
        self.assertIn("package roots", caught.exception.message)

    def test_invalid_json_is_refused(self):
        self.path.write_text("{not json")
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("invalid JSON", caught.exception.message)

    def test_every_surface_is_required(self):
        for field in triggers.DECLARATION_FIELDS - {"schema_version"}:
            payload = {key: value for key, value in DECLARATION.items() if key != field}
            self.write(payload)
            with self.assertRaises(UsageError) as caught:
                triggers.load_declaration(self.tmp)
            self.assertIn("requires exactly", caught.exception.message)

    def test_unknown_field_is_refused(self):
        self.write({**DECLARATION, "extra": []})
        with self.assertRaises(UsageError):
            triggers.load_declaration(self.tmp)

    def test_unsupported_schema_version_is_refused(self):
        self.write({**DECLARATION, "schema_version": 2})
        with self.assertRaises(UsageError) as caught:
            triggers.load_declaration(self.tmp)
        self.assertIn("schema_version 2", caught.exception.message)

    def test_unstated_package_size_is_refused(self):
        for value in (0, -1, None, "400", True):
            self.write({**DECLARATION, "package_change_lines": value})
            with self.assertRaises(UsageError) as caught:
                triggers.load_declaration(self.tmp)
            self.assertIn("package_change_lines", caught.exception.message)

    def test_empty_surface_list_states_the_absence(self):
        self.write({**DECLARATION, "trust_boundary_paths": []})
        self.assertEqual(triggers.load_declaration(self.tmp)["trust_boundary_paths"], [])

    def test_malformed_globs_are_refused(self):
        for value in ("src/*", [""], ["a\nb"], [3]):
            self.write({**DECLARATION, "user_doc_paths": value})
            with self.assertRaises(UsageError):
                triggers.load_declaration(self.tmp)

    def test_malformed_markers_are_refused(self):
        for value in ("add_parser(", [""], ["a\tb".replace("\t", "\x01")], [3]):
            self.write({**DECLARATION, "cli_surface_markers": value})
            with self.assertRaises(UsageError):
                triggers.load_declaration(self.tmp)


class DecisionsTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        self.path = self.tmp / "decisions.json"

    def test_absent_file_is_no_decision(self):
        self.assertEqual(triggers.load_decisions(None), {})

    def test_reads_a_recorded_reason(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"security": "  no foreign input reaches it  "}}))
        self.assertEqual(triggers.load_decisions(self.path), {"security": "no foreign input reaches it"})

    def test_unknown_trigger_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"perf": "later"}}))
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.path)
        self.assertIn("unknown trigger", caught.exception.message)

    def test_empty_reason_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 1, "decisions": {"security": "   "}}))
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.path)
        self.assertIn("silence is never that decision", caught.exception.message)

    def test_unreadable_file_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_decisions(self.tmp / "absent.json")
        self.assertIn("Cannot read staffing decisions", caught.exception.message)

    def test_wrong_shape_is_refused(self):
        self.path.write_text(json.dumps({"schema_version": 2, "decisions": {}}))
        with self.assertRaises(UsageError):
            triggers.load_decisions(self.path)


class RequirementsTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        self.path = self.tmp / "requirements.json"

    def test_collects_planned_specialties(self):
        self.path.write_text(json.dumps({"schema_version": 1, "assignments": {
            "advisor": {"specialty": "security", "required_capabilities": ["threat"],
                        "independent": False, "engagement": "boundary-review"}}}))
        self.assertEqual(triggers.load_requirements(self.path), {"security"})

    def test_a_developer_specialty_covers_no_trigger(self):
        # A developer with a security specialty is still the implementer, not
        # the consultation Team Composition requires before the work.
        path = self.tmp / "requirements.json"
        path.write_text(json.dumps({"schema_version": triggers.REQUIREMENTS_SCHEMA_VERSION, "assignments": {
            "developer": {"specialty": "security", "required_capabilities": ["threat"]},
            "tester": {"specialty": "documentation"},
            "advisor": {"specialty": "accessibility"}}}))
        self.assertEqual(triggers.load_requirements(path), {"accessibility"})

    def test_absent_path_staffs_nothing(self):
        self.assertEqual(triggers.load_requirements(None), set())

    def test_wrong_shape_is_refused(self):
        self.path.write_text(json.dumps({"assignments": {}}))
        with self.assertRaises(UsageError):
            triggers.load_requirements(self.path)


class MatchTest(unittest.TestCase):
    def test_path_globs_span_path_separators(self):
        self.assertTrue(triggers.matches("docs/guide/install.md", ["docs/*"]))
        self.assertFalse(triggers.matches("README.md", ["docs/*"]))

    def test_package_globs_match_one_segment(self):
        self.assertTrue(triggers.package_matches("src/auth", ["src/*"]))
        self.assertFalse(triggers.package_matches("src/auth/tokens", ["src/*"]))
        self.assertEqual(triggers.package_of("src/auth/tokens/rs256.py", ["src/*"]), "src/auth")

    def test_nearest_declared_ancestor_owns_the_file(self):
        self.assertEqual(triggers.package_of("src/auth/token.py", ["src/*"]), "src/auth")
        self.assertEqual(triggers.package_of("tools/build.sh", ["src/*", "tools"]), "tools")
        self.assertIsNone(triggers.package_of("README.md", ["src/*"]))


class DetectTest(unittest.TestCase):
    def test_a_package_absent_from_the_base_fires_the_architect(self):
        fired = triggers.detect(declaration(), {"src/new/mod.py": "A"}, {"src/new/mod.py": 3},
                                {"src/new": False})
        self.assertEqual([signal["signal"] for signal in fired["architect"]], ["new_package"])
        self.assertEqual(fired["architect"][0]["evidence"], "src/new")

    def test_a_package_above_the_stated_size_fires_the_architect(self):
        fired = triggers.detect(declaration(), {"src/old/a.py": "M", "src/old/b.py": "M"},
                                {"src/old/a.py": 30, "src/old/b.py": 21}, {"src/old": True})
        self.assertEqual(fired["architect"][0]["signal"], "package_change_lines")
        self.assertIn("51 lines > 50", fired["architect"][0]["evidence"])

    def test_a_package_at_the_stated_size_stays_quiet(self):
        fired = triggers.detect(declaration(), {"src/old/a.py": "M"}, {"src/old/a.py": 50},
                                {"src/old": True})
        self.assertNotIn("architect", fired)

    def test_a_changed_trust_boundary_path_fires_security(self):
        fired = triggers.detect(declaration(), {"src/auth/token.py": "M"}, {"src/auth/token.py": 2},
                                {"src/auth": True})
        self.assertEqual(fired["security"], [{"signal": "trust_boundary_path", "evidence": "src/auth/token.py"}])

    def test_an_added_user_document_fires_documentation(self):
        fired = triggers.detect(declaration(), {"docs/install.md": "A"}, {"docs/install.md": 9}, {})
        self.assertEqual(fired["documentation"][0]["evidence"], "docs/install.md")

    def test_an_edited_user_document_does_not_fire_documentation(self):
        fired = triggers.detect(declaration(), {"docs/install.md": "M"}, {"docs/install.md": 9}, {})
        self.assertNotIn("documentation", fired)

    def test_an_undeclared_path_fires_nothing(self):
        self.assertEqual(triggers.detect(declaration(), {"CHANGELOG.md": "M"}, {"CHANGELOG.md": 4}, {}), {})

    def test_binary_churn_counts_no_lines(self):
        fired = triggers.detect(declaration(), {"src/old/logo.png": "M"}, {"src/old/logo.png": 0},
                                {"src/old": True})
        self.assertNotIn("architect", fired)


class CliSurfaceTest(unittest.TestCase):
    def test_a_declared_marker_on_an_added_line_fires(self):
        found = triggers.cli_surface(declaration(), {"src/cli/main.py": "M"},
                                     {"src/cli/main.py": ['    sub.add_parser("ship")']})
        self.assertEqual(found[0]["signal"], "added_cli_surface")
        self.assertIn('add_parser("ship")', found[0]["evidence"])

    def test_an_added_line_without_a_marker_stays_quiet(self):
        self.assertEqual(triggers.cli_surface(declaration(), {"src/cli/main.py": "M"},
                                              {"src/cli/main.py": ["    return payload"]}), [])

    def test_a_declared_refusal_marker_fires(self):
        # rules/agent-team-operation.md: a new user-facing refusal path is a
        # UX and product trigger, not only a new command or flag.
        found = triggers.cli_surface(declaration(cli_surface_markers=["raise UsageError("]),
                                     {"src/cli/main.py": "M"},
                                     {"src/cli/main.py": ['        raise UsageError("state it", {})']})
        self.assertEqual(found[0]["signal"], "added_cli_surface")
        self.assertIn("raise UsageError(", found[0]["evidence"])

    def test_a_marker_outside_the_spec_surface_stays_quiet(self):
        self.assertEqual(triggers.cli_surface(declaration(), {"src/old/a.py": "M"},
                                              {"src/old/a.py": ["add_argument("]}), [])


class ParseTest(unittest.TestCase):
    def test_name_status_pairs_records(self):
        self.assertEqual(triggers.parse_name_status("M\0a.py\0A\0b.py\0"), {"a.py": "M", "b.py": "A"})

    def test_odd_name_status_output_is_refused(self):
        with self.assertRaises(UsageError):
            triggers.parse_name_status("M\0a.py\0A\0")

    def test_numstat_sums_added_and_deleted(self):
        self.assertEqual(triggers.parse_numstat("3\t4\ta.py\0-\t-\tlogo.png\0"), {"a.py": 7, "logo.png": 0})

    def test_added_lines_come_from_the_hunks_alone(self):
        patch = "\n".join([
            "diff --git a/x.py b/x.py",
            "--- a/x.py",
            "+++ b/x.py",
            "@@ -1 +1,2 @@",
            "+added one",
            "+++ content that looks like a file header",
        ])
        self.assertEqual(triggers.parse_added_lines(patch),
                         ["added one", "++ content that looks like a file header"])

    def test_a_quoted_path_header_is_never_read(self):
        # git quotes a path carrying a quote, a backslash or a non-ASCII byte.
        patch = "\n".join([
            'diff --git "a/src/cli/a\\"b.py" "b/src/cli/a\\"b.py"',
            '--- "a/src/cli/a\\"b.py"',
            '+++ "b/src/cli/a\\"b.py"',
            "@@ -1,0 +2 @@",
            '+    sub.add_parser("ship")',
        ])
        self.assertEqual(triggers.parse_added_lines(patch), ['    sub.add_parser("ship")'])


class ReportTest(unittest.TestCase):
    def fire(self, **kwargs):
        fired = {"security": [{"signal": "trust_boundary_path", "evidence": "src/auth/token.py"}]}
        return triggers.report(declaration(), "BASE", "HEAD", fired, **{
            "roles": [], "specialties": set(), "decisions": {}, **kwargs})

    def test_an_unstaffed_fired_trigger_fails_the_round(self):
        payload, failure = self.fire()
        self.assertEqual(payload["unaddressed"], ["security"])
        assert failure is not None
        self.assertEqual(failure["error"], "unaddressed_trigger")
        self.assertIn("security", failure["message"])

    def test_a_planned_specialty_addresses_the_trigger(self):
        payload, failure = self.fire(specialties={"security"})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "security")
        self.assertEqual(row["addressed"], "specialty:security")

    def test_a_planned_role_addresses_the_architect(self):
        payload, failure = triggers.report(
            declaration(), "BASE", "HEAD", {"architect": [{"signal": "new_package", "evidence": "src/new"}]},
            roles=["developer", "architect"], specialties=set(), decisions={})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "architect")
        self.assertEqual(row["addressed"], "role:architect")

    def test_a_recorded_decision_addresses_the_trigger(self):
        payload, failure = self.fire(decisions={"security": "the changed line is a comment"})
        self.assertIsNone(failure)
        row = next(row for row in payload["triggers"] if row["trigger"] == "security")
        self.assertEqual(row["addressed"], "decision")
        self.assertEqual(row["decision"], "the changed line is a comment")

    def test_a_decision_for_a_quiet_trigger_is_reported_unused(self):
        payload, failure = self.fire(specialties={"security"}, decisions={"documentation": "no docs here"})
        self.assertIsNone(failure)
        self.assertEqual(payload["unused_decisions"], ["documentation"])

    def test_every_trigger_is_reported(self):
        payload, _failure = self.fire(specialties={"security"})
        self.assertEqual([row["trigger"] for row in payload["triggers"]], list(triggers.TRIGGERS))
        self.assertEqual(payload["fired"], ["security"])


class ThisRepoDeclarationTest(unittest.TestCase):
    """This repo's own declaration, dogfooded as a consuming repo."""

    def setUp(self):
        self.repo = Path(__file__).resolve().parents[3]
        self.declaration = triggers.load_declaration(self.repo)

    def test_the_modules_that_gate_generated_evidence_are_trust_boundaries(self):
        # rules/agent-team-operation.md: anything deciding whether generated
        # content or a proposed change is safe triggers security.
        for path in ("skills/herdr-teamlead/teamlead/recovery.py",
                     "skills/herdr-teamlead/teamlead/engagement.py",
                     "skills/herdr-teamlead/teamlead/report_delivery.py",
                     "skills/herdr-teamlead/teamlead/supervision.py",
                     "skills/herdr-teamlead/teamlead/assign.py",
                     "skills/herdr-teamlead/teamlead/composition.py",
                     "skills/herdr-teamlead/teamlead/cli.py",
                     "skills/herdr-teamlead/teamlead/tiers.py",
                     "skills/herdr-teamlead/teamlead/launch.py",
                     "skills/herdr-teamlead/teamlead/triggers.py",
                     "skills/herdr-teamlead/teamlead.sh",
                     ".herdr/triggers.json",
                     "rules/agent-team-operation.md",
                     "rules/review-severity.md",
                     "skills/release/watch-pr-reviews.sh",
                     "skills/release/poll-pr-reviews.sh",
                     "skills/onboard-repo/templates/post-review.sh",
                     ".github/workflows/tests.yml"):
            with self.subTest(path=path):
                self.assertTrue((self.repo / path).exists(), path)
                fired = triggers.detect(self.declaration, {path: "M"}, {path: 1}, {})
                self.assertEqual([signal["evidence"] for signal in fired["security"]], [path])

    def test_each_skill_is_one_package_root(self):
        self.assertEqual(triggers.package_of("skills/herdr-teamlead/teamlead/cli.py",
                                             self.declaration["package_roots"]), "skills/herdr-teamlead")

    def test_a_new_refusal_path_fires_ux_product(self):
        found = triggers.cli_surface(self.declaration, {"skills/herdr-teamlead/teamlead/recovery.py": "M"},
                                     {"skills/herdr-teamlead/teamlead/recovery.py":
                                      ['        raise UsageError("state the surface", {})']})
        self.assertEqual(len(found), 1)

    def test_a_shipped_shell_command_is_a_cli_surface(self):
        # A shell command carries its own flags and refusals, and a new one
        # must fire UX and product the way a new Python flag does.
        for path, line in (("skills/herdr-teamlead/prune-worktrees.sh",
                            '      --dry-run) warn "usage: prune-worktrees.sh <shared-checkout>"; return 1 ;;'),
                           ("skills/release/watch-pr-reviews.sh", 'echo "usage: watch-pr-reviews.sh" >&2'),
                           ("scripts/run-tests.sh", 'echo "Usage: run-tests.sh [base-dir]" >&2')):
            with self.subTest(path=path):
                self.assertTrue((self.repo / path).exists(), path)
                self.assertTrue(triggers.matches(path, self.declaration["cli_spec_paths"]), path)
                found = triggers.cli_surface(self.declaration, {path: "M"}, {path: [line]})
                self.assertEqual(len(found), 1, path)


class PlannedSurfacesTest(TempCase):
    """A pre-implementation round has no diff; it declares its surfaces."""

    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        self.plan = self.tmp / "planned.json"
        self.calls = []

    def runner(self, responses=None):
        responses = responses or {}
        def run(arguments):
            self.calls.append(arguments)
            for key, value in responses.items():
                if key in arguments:
                    return value
            return ""
        return run

    def write(self, **overrides):
        payload = {"schema_version": 1, "added": [], "changed": [], "package_lines": {}, "cli_surface": []}
        payload.update(overrides)
        self.plan.write_text(json.dumps(payload))
        return str(self.plan)

    def test_an_empty_round_without_a_plan_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp), runner=self.runner())
        self.assertIn("--planned", caught.exception.message)

    def test_an_empty_plan_classifies_no_more_than_an_absent_one(self):
        # A well-formed but empty plan would otherwise report success with no
        # triggers, which is the silence the detector exists to end.
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write()), runner=self.runner())
        self.assertIn("classifies nothing", caught.exception.message)

    def test_a_plan_declaring_only_a_package_size_classifies(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(package_lines={"src/old": 51})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], ["architect"])

    def test_a_plan_declaring_only_a_cli_surface_classifies(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"])),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertEqual(payload["fired"], ["ux-product"])

    def test_a_planned_new_package_fires_the_architect(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["src/new/mod.py"])), runner=self.runner())
        self.assertEqual(payload["fired"], ["architect"])
        assert failure is not None

    def test_a_planned_package_above_the_stated_size_fires_the_architect(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/old/a.py"], package_lines={"src/old": 51})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], ["architect"])

    def test_a_planned_package_at_the_stated_size_stays_quiet(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/old/a.py"], package_lines={"src/old": 50})),
            runner=self.runner({"ls-tree": "src/old/a.py\n"}))
        self.assertEqual(payload["fired"], [])
        self.assertIsNone(failure)

    def test_a_planned_trust_boundary_fires_security(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(changed=["src/auth/token.py"])),
            runner=self.runner({"ls-tree": "src/auth/token.py\n"}))
        self.assertEqual(payload["fired"], ["security"])

    def test_a_planned_document_fires_documentation(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["docs/guide.md"])), runner=self.runner())
        self.assertEqual(payload["fired"], ["documentation"])

    def test_a_planned_cli_surface_fires_ux_product(self):
        payload, _failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"])),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertIn("ux-product", payload["fired"])
        row = next(row for row in payload["triggers"] if row["trigger"] == "ux-product")
        self.assertEqual(row["signals"][0]["signal"], "planned_cli_surface")

    def test_a_package_declared_only_by_its_size_is_checked_against_the_base(self):
        # Its only mention is the size, so nothing puts it in `changes`; a
        # missing candidate would read as a package the base already held.
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(package_lines={"src/new": 1})),
            runner=self.runner())
        self.assertEqual(payload["fired"], ["architect"])
        self.assertEqual(payload["triggers"][0]["signals"][0]["signal"], "new_package")
        assert failure is not None
        self.assertIn(["ls-tree", "--name-only", "BASE", "--", "src/new/"], self.calls)

    def test_planned_package_lines_name_a_declared_package_root(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write(package_lines={"src/new/deep": 1})),
                                 runner=self.runner())
        self.assertIn("declared package roots", caught.exception.message)

    def test_a_planned_cli_surface_is_classified_against_every_surface(self):
        # A spec path that is also a trust boundary fires security too;
        # answering UX and product alone must not let the round pass.
        declared = {**DECLARATION, "trust_boundary_paths": ["src/cli/*"]}
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(declared))
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(cli_surface=["src/cli/main.py"]),
                      requirements=None, roles=None),
            runner=self.runner({"ls-tree": "src/cli/main.py\n"}))
        self.assertEqual(payload["fired"], ["security", "ux-product"])
        assert failure is not None
        self.assertEqual(failure["details"]["unaddressed"], ["security", "ux-product"])

    def test_a_planned_cli_surface_outside_the_spec_paths_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.run_command(namespace(repo=self.tmp, planned=self.write(cli_surface=["src/old/a.py"])),
                                 runner=self.runner())
        self.assertIn("declared CLI spec paths", caught.exception.message)

    def test_a_staffed_plan_passes(self):
        payload, failure = triggers.run_command(
            namespace(repo=self.tmp, planned=self.write(added=["src/new/mod.py"]), roles="developer,architect"),
            runner=self.runner())
        self.assertIsNone(failure)
        self.assertEqual(payload["unaddressed"], [])

    def test_a_malformed_plan_is_refused(self):
        for payload in ({"schema_version": 2, "added": [], "changed": [], "package_lines": {}, "cli_surface": []},
                        {"schema_version": 1, "added": [], "changed": [], "package_lines": []},
                        {"schema_version": 1, "added": [], "changed": [], "package_lines": {"src/old": -1}, "cli_surface": []}):
            self.plan.write_text(json.dumps(payload))
            with self.assertRaises(UsageError):
                triggers.load_plan(self.plan)

    def test_an_unreadable_plan_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            triggers.load_plan(self.tmp / "absent.json")
        self.assertIn("Cannot read planned surfaces", caught.exception.message)


class RunCommandTest(TempCase):
    def setUp(self):
        self.tmp = self.temp_dir()
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        self.calls = []

    def runner(self, responses):
        def run(arguments):
            self.calls.append(arguments)
            for key, value in responses.items():
                if key in arguments:
                    return value
            return ""
        return run

    def test_collects_the_diff_facts_and_reports(self):
        responses = {"--name-status": "A\0src/new/mod.py\0", "--numstat": "9\t0\tsrc/new/mod.py\0"}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["architect"])
        assert failure is not None
        self.assertEqual(failure["details"]["unaddressed"], ["architect"])
        self.assertIn(["ls-tree", "--name-only", "BASE", "--", "src/new/"], self.calls)

    def test_a_head_compares_from_the_merge_base(self):
        responses = {"merge-base": "MERGEBASE\n", "--name-status": "A\0src/new/mod.py\0",
                     "--numstat": "9\t0\tsrc/new/mod.py\0"}
        triggers.run_command(namespace(repo=self.tmp, head="HEAD"), runner=self.runner(responses))
        self.assertEqual(self.calls[0], ["merge-base", "BASE", "HEAD"])
        self.assertIn("BASE...HEAD", self.calls[1])
        # "Absent from the base" is read at the merge base, not at BASE.
        self.assertIn(["ls-tree", "--name-only", "MERGEBASE", "--", "src/new/"], self.calls)

    def test_no_head_reads_the_working_tree(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        payload, _failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertIn("BASE", self.calls[0])
        self.assertEqual(payload["head"], "worktree")

    def test_the_cli_surface_patch_is_only_read_for_spec_paths(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertFalse(any("--unified=0" in call for call in self.calls))

    def test_an_added_parser_fires_ux_product(self):
        responses = {"--name-status": "M\0src/cli/main.py\0", "--numstat": "2\t0\tsrc/cli/main.py\0",
                     "ls-tree": "src/cli/main.py\n",
                     "--unified=0": ("diff --git a/src/cli/main.py b/src/cli/main.py\n"
                                     "--- a/src/cli/main.py\n+++ b/src/cli/main.py\n"
                                     "@@ -1,0 +2 @@\n+    sub.add_parser(\"ship\")\n")}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["ux-product"])
        assert failure is not None

    def test_a_refusal_only_change_fires_ux_product(self):
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(
            {**DECLARATION, "cli_surface_markers": ["raise UsageError("]}))
        responses = {"--name-status": "M\0src/cli/main.py\0", "--numstat": "1\t0\tsrc/cli/main.py\0",
                     "ls-tree": "src/cli/main.py\n",
                     "--unified=0": ("diff --git a/src/cli/main.py b/src/cli/main.py\n"
                                     "--- a/src/cli/main.py\n+++ b/src/cli/main.py\n"
                                     "@@ -9,0 +10 @@\n+    raise UsageError(\"state the surface\", {})\n")}
        payload, failure = triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertEqual(payload["fired"], ["ux-product"])
        assert failure is not None

    def test_untracked_files_are_only_collected_without_a_head(self):
        responses = {"--name-status": "M\0README.md\0", "--numstat": "1\t1\tREADME.md\0"}
        triggers.run_command(namespace(repo=self.tmp, head="HEAD"), runner=self.runner(responses))
        self.assertFalse(any("--others" in call for call in self.calls))
        self.calls.clear()
        triggers.run_command(namespace(repo=self.tmp), runner=self.runner(responses))
        self.assertTrue(any("--others" in call for call in self.calls))

    def test_roles_are_read_from_the_comma_list(self):
        responses = {"--name-status": "A\0src/new/mod.py\0", "--numstat": "9\t0\tsrc/new/mod.py\0"}
        payload, failure = triggers.run_command(namespace(repo=self.tmp, roles="developer,architect"),
                                                runner=self.runner(responses))
        self.assertIsNone(failure)
        self.assertEqual(payload["unaddressed"], [])


class DetectTriggersCommandTest(TempCase):
    """The packaged command against a real git repository."""

    def setUp(self):
        self.tmp = self.temp_dir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Tests")
        (self.tmp / ".herdr").mkdir()
        (self.tmp / triggers.DECLARATION_FILE).write_text(json.dumps(DECLARATION))
        (self.tmp / "README.md").write_text("start\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *arguments):
        completed = subprocess.run(["git", "-C", str(self.tmp), *arguments],
                                   capture_output=True, text=True, check=True)
        return completed.stdout

    def run_cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", self.base, *arguments],
                        stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_a_quiet_diff_exits_zero(self):
        (self.tmp / "README.md").write_text("start\nmore\n")
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_a_new_package_fails_until_it_is_addressed(self):
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "mod.py").write_text("value = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add package")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["architect"])
        self.assertEqual(json.loads(err)["error"], "unaddressed_trigger")
        code, out, err = self.run_cli("--head", "HEAD", "--roles", "architect,developer")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["unaddressed"], [])

    def test_an_added_document_fires_documentation(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add doc")
        code, out, _err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["documentation"])

    def test_an_untracked_package_fires_in_the_working_tree(self):
        # coding-policy#415: `git diff` reports tracked changes only, so a whole
        # new package would fire nothing while it sits untracked.
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "mod.py").write_text("value = 1\n")
        code, out, _err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["architect"])

    def test_an_untracked_document_fires_documentation(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        code, out, _err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["documentation"])

    def test_an_untracked_spec_file_fires_ux_product(self):
        (self.tmp / "src" / "cli").mkdir(parents=True)
        (self.tmp / "src" / "cli" / "ship.py").write_text('sub.add_parser("ship")\n')
        code, out, _err = self.run_cli("--roles", "architect")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unaddressed"], ["ux-product"])

    def test_an_untracked_binary_file_counts_no_lines(self):
        (self.tmp / "src" / "new").mkdir(parents=True)
        (self.tmp / "src" / "new" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        code, out, _err = self.run_cli()
        # It is still an added file in a package absent from the base.
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["fired"], ["architect"])

    def test_a_pushed_head_ignores_the_working_tree(self):
        (self.tmp / "README.md").write_text("start\nmore\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "edit readme")
        # Untracked in the working tree, absent from the pushed commits.
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        code, out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fired"], [])

    def test_a_round_with_nothing_to_classify_is_refused(self):
        code, _out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("--planned", json.loads(err)["message"])

    def test_a_quoted_filename_still_fires_ux_product(self):
        # git quotes a path carrying a quote or a non-ASCII byte in its patch
        # header; the added command must still be seen.
        spec = self.tmp / "src" / "cli"
        spec.mkdir(parents=True)
        awkward = spec / 'a"b\u00e9.py'
        awkward.write_text("# spec\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add spec")
        base = self.git("rev-parse", "HEAD").strip()
        awkward.write_text('# spec\nsub.add_parser("ship")\n')
        self.git("add", "-A")
        self.git("commit", "-qm", "add command")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["detect-triggers", "--repo", str(self.tmp), "--base", base, "--head", "HEAD"],
                        stdout=out, stderr=err)
        self.assertEqual(code, 1, err.getvalue())
        self.assertIn("ux-product", json.loads(out.getvalue())["fired"])

    def test_a_missing_declaration_refuses_the_round(self):
        (self.tmp / triggers.DECLARATION_FILE).unlink()
        self.git("add", "-A")
        self.git("commit", "-qm", "drop declaration")
        code, _out, err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("No trigger declaration at", json.loads(err)["message"])

    def test_a_rename_reads_as_a_delete_and_an_add(self):
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "install.md").write_text("how to install\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "add doc")
        moved = self.base
        self.git("mv", "docs/install.md", "docs/setup.md")
        self.git("commit", "-qm", "rename doc")
        code, out, _err = self.run_cli("--head", "HEAD")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["triggers"][1]["trigger"], "documentation")
        self.assertEqual(self.base, moved)


if __name__ == "__main__":
    unittest.main()
