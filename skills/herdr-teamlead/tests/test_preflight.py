"""Tests for `teamlead preflight`.

Fakes sit at the process boundary only: `roster.sh` and `verify-authority.sh`
run for real as subprocesses against fake `herdr` and `gh` executables this
suite writes, measurement drives the injected fake Herdr transport, and the
trigger facts come from a real git repository built in setup. `--now` is fixed.
"""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead.cli import main
from teamlead.herdr import HerdrClient
from teamlead.state import add_assignment, empty_state, save_state

from tests.fakes import FakeRunner, agent_json, composer_reads, ok_json

AT = "2026-02-03T10:00:00+00:00"

CONFIG = {
    "schema_version": 1,
    "agents": [{
        "name": "claude", "kind": "claude", "usage_prompt": "/usage",
        "usage_marker": "Current week", "usage_read_source": "visible",
        "slash_delivery": "paste", "composer_glyph": "❯ ", "recover_keys": ["esc"],
        "close_keys": ["esc"], "clear_prompt": "/clear",
    }],
}

CLAUDE_PANE = (
    "   Current session\n   ████    8% used\n   Resets 12:49am (Europe/Oslo)\n"
    "   Current week (all models)\n   █    2% used\n   Resets Sep 5 at 11:59pm (Europe/Oslo)\n"
)

DECLARATION = {
    "schema_version": 1, "package_roots": ["src/*"], "package_change_lines": 1000,
    "trust_boundary_paths": ["hooks/*"], "cli_spec_paths": [], "cli_surface_markers": [],
    "user_doc_paths": ["docs/*"],
}

FAKE_HERDR = """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-} ${2:-}" == "agent list" ]]; then
  if [[ -n "${FAKE_ROSTER_EMPTY:-}" ]]; then
    printf '{"result":{"agents":[{"name":"lead","agent":"claude","pane_id":"lead-pane","agent_status":"working"}]}}\\n'
  else
    printf '{"result":{"agents":[{"name":"lead","agent":"claude","pane_id":"lead-pane","agent_status":"working"},{"name":"claude","agent":"claude","pane_id":"w2:p1","agent_status":"idle"}]}}\\n'
  fi
  exit 0
fi
echo "fake herdr: unsupported $*" >&2
exit 2
"""

FAKE_GH = """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-} ${2:-}" == "auth status" ]]; then printf 'Logged in\\n'; exit 0; fi
[[ "${1:-}" == "api" ]] || { echo "fake gh: unsupported $*" >&2; exit 2; }
case "${2:-}" in
  user)
    [[ -n "${FAKE_GH_FAIL:-}" ]] && { printf 'gh: HTTP 502\\n' >&2; exit 1; }
    printf '{"login":"viewer"}\\n' ;;
  repos/*)
    printf '{"owner":{"login":"%s","type":"User"},"permissions":{"admin":false,"maintain":false,"push":true,"triage":false,"pull":true}}\\n' "${FAKE_OWNER:-viewer}" ;;
  *) echo "fake gh: unsupported path $2" >&2; exit 2 ;;
esac
"""


def git(repo, *argv):
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *argv],
        capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    if completed.returncode != 0:
        raise AssertionError("git {} failed: {}".format(argv, completed.stderr))
    return completed.stdout.strip()


class PreflightCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="teamlead-preflight-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.config = self.tmp / "config.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.state = self.tmp / "state" / "state.json"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        herdr, gh = bin_dir / "herdr", bin_dir / "gh"
        herdr.write_text(FAKE_HERDR, encoding="utf-8")
        gh.write_text(FAKE_GH, encoding="utf-8")
        herdr.chmod(0o755)
        gh.chmod(0o755)
        self.env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "lead-pane", "HERDR_BIN": str(herdr), "GH_BIN": str(gh)}
        for name in ("FAKE_ROSTER_EMPTY", "FAKE_GH_FAIL", "FAKE_OWNER"):
            self.env.setdefault(name, "")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / ".herdr").mkdir()
        (self.repo / ".herdr" / "triggers.json").write_text(json.dumps(DECLARATION), encoding="utf-8")
        (self.repo / "notes.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
        git(self.repo, "commit", "-q", "-am", "quiet change")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.runner = FakeRunner()

    def client(self, failing=False):
        runner = FakeRunner()
        runner.set("agent get claude", agent_json("claude", "idle", "w2:p1"))
        runner.set("agent prompt claude", ok_json("agent_prompt"))
        runner.set("agent send-keys claude", ok_json("agent_send_keys"))
        runner.set("agent read claude", "nothing useful here\n" if failing else CLAUDE_PANE)
        for prefix, kind in (("pane wait-output", "output_matched"), ("pane send-text", "pane_send_text"),
                             ("pane send-keys", "pane_send_keys"), ("pane rename", "pane_rename")):
            runner.set(prefix, ok_json(kind))
        runner.responses["agent read claude --source visible --lines 20"] = composer_reads("claude")
        self.runner = runner
        return HerdrClient(runner=runner)

    def argv(self, *extra, base=True):
        argv = ["--config", str(self.config), "--state", str(self.state), "preflight",
                "--repo", str(self.repo), "--slug", "viewer/project", "--now", AT,
                "--marker-poll-interval", "0", "--composer-settle", "0"]
        if base:
            argv += ["--base", self.base, "--head", self.head]
        return argv + list(extra)

    def run_preflight(self, argv, client=None, **env):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {**self.env, **env}):
            code = main(argv, stdout=out, stderr=err, client=client if client is not None else self.client())
        return code, out.getvalue(), err.getvalue()

    def codes(self, payload):
        return [stop["code"] for stop in payload["stops"]]

    def make_retrospective_due(self):
        state = empty_state()
        add_assignment(state, AT, "developer", "claude")
        save_state(self.state, state)


class AllClearTest(PreflightCase):
    def test_all_clear_is_ready_with_nothing_extra_to_read(self):
        code, out, err = self.run_preflight(self.argv())
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["verdict"], "ready")
        self.assertEqual(payload["stops"], [])
        self.assertEqual(payload["read_next"], [])
        self.assertEqual([agent["name"] for agent in payload["roster"]["agents"]], ["claude"])
        self.assertFalse(payload["roster"]["empty"])
        self.assertTrue(payload["authority"]["result"]["authorized"])
        self.assertFalse(payload["retrospective"]["due"])
        self.assertEqual(payload["headroom"]["failed_agents"], [])
        self.assertEqual(payload["triggers"]["fired"], [])
        self.assertIsNone(payload["triggers_reason"])

    def test_the_headroom_snapshot_is_saved_like_measure(self):
        self.run_preflight(self.argv())
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(state["snapshots"]), 1)
        self.assertEqual(state["snapshots"][0]["measured_at"], AT)

    def test_without_a_base_triggers_are_null_with_a_reason(self):
        code, out, _err = self.run_preflight(self.argv(base=False))
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIsNone(payload["triggers"])
        self.assertIn("--base", payload["triggers_reason"])
        self.assertEqual(payload["verdict"], "ready")


class SingleStopTest(PreflightCase):
    def assert_single_stop(self, code, out, expected, reference):
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["verdict"], "stop")
        self.assertEqual(self.codes(payload), [expected])
        self.assertEqual(payload["stops"][0]["reference"], reference)
        self.assertIn(reference, payload["read_next"])
        return payload

    def test_an_empty_roster_stops_and_contacts_no_worker(self):
        code, out, _err = self.run_preflight(self.argv(), FAKE_ROSTER_EMPTY="1")
        payload = self.assert_single_stop(code, out, "roster_empty", "skills/herdr-teamlead/references/round-setup.md")
        self.assertTrue(payload["roster"]["empty"])
        self.assertIn("herdr agent rename", payload["stops"][0]["message"])
        self.assertEqual(payload["headroom"]["skipped"], "roster not usable")
        self.assertEqual(self.runner.calls, [])

    def test_a_roster_outside_herdr_stops_with_the_script_diagnostic(self):
        code, out, _err = self.run_preflight(self.argv(), HERDR_ENV="")
        payload = self.assert_single_stop(code, out, "roster_unavailable", "skills/herdr-teamlead/references/round-setup.md")
        self.assertEqual(payload["roster"]["exit"], 1)
        self.assertIn("not running inside Herdr", payload["stops"][0]["message"])

    def test_an_unanswerable_authority_check_stops(self):
        code, out, _err = self.run_preflight(self.argv(), FAKE_GH_FAIL="1")
        payload = self.assert_single_stop(code, out, "authority_failed", "skills/herdr-teamlead/references/round-setup.md")
        self.assertEqual(payload["authority"]["exit"], 2)
        self.assertIsNone(payload["authority"]["result"])

    def test_a_repo_the_operator_does_not_own_stops(self):
        code, out, _err = self.run_preflight(self.argv(), FAKE_OWNER="someone-else")
        payload = self.assert_single_stop(code, out, "authority_not_owned", "skills/herdr-teamlead/references/round-setup.md")
        self.assertFalse(payload["authority"]["result"]["authorized"])
        self.assertEqual(payload["authority"]["result"]["viewer_permission"], "write")

    def test_a_due_retrospective_stops(self):
        self.make_retrospective_due()
        code, out, _err = self.run_preflight(self.argv())
        payload = self.assert_single_stop(code, out, "retrospective_due", "skills/herdr-teamlead/references/retrospectives.md")
        self.assertTrue(payload["retrospective"]["due"])
        self.assertEqual(payload["retrospective"]["cadence"]["reason"], "existing_work_without_history")

    def test_a_failed_headroom_reading_stops(self):
        code, out, _err = self.run_preflight(self.argv(), client=self.client(failing=True))
        payload = self.assert_single_stop(code, out, "headroom_failed", "skills/herdr-teamlead/references/round-setup.md")
        self.assertEqual(payload["headroom"]["failed_agents"], ["claude"])

    def test_a_missing_trigger_declaration_stops(self):
        (self.repo / ".herdr" / "triggers.json").unlink()
        code, out, _err = self.run_preflight(self.argv())
        payload = self.assert_single_stop(code, out, "trigger_declaration", "skills/herdr-teamlead/references/specialists.md")
        self.assertIsNone(payload["triggers"])
        self.assertIn("No trigger declaration", payload["stops"][0]["message"])

    def test_a_non_utf8_trigger_declaration_stops(self):
        (self.repo / ".herdr" / "triggers.json").write_bytes(b"{\"schema_version\": \xff}")
        code, out, _err = self.run_preflight(self.argv())
        payload = self.assert_single_stop(code, out, "trigger_declaration", "skills/herdr-teamlead/references/specialists.md")
        self.assertIn("not UTF-8", payload["stops"][0]["message"])

    def test_a_round_that_classifies_nothing_stops(self):
        code, out, _err = self.run_preflight(self.argv(base=False) + ["--base", self.head, "--head", self.head])
        self.assert_single_stop(code, out, "triggers_unclassified", "skills/herdr-teamlead/references/specialists.md")


class SeveralStopsTest(PreflightCase):
    def test_every_blocker_is_reported_in_one_run(self):
        self.make_retrospective_due()
        (self.repo / ".herdr" / "triggers.json").unlink()
        code, out, _err = self.run_preflight(self.argv(), client=self.client(failing=True), FAKE_OWNER="someone-else")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["verdict"], "stop")
        self.assertEqual(sorted(self.codes(payload)),
                         ["authority_not_owned", "headroom_failed", "retrospective_due", "trigger_declaration"])
        self.assertEqual(payload["read_next"], [
            "skills/herdr-teamlead/references/retrospectives.md",
            "skills/herdr-teamlead/references/round-setup.md",
            "skills/herdr-teamlead/references/specialists.md",
        ])


class TriggerFactsTest(PreflightCase):
    def test_a_trust_boundary_diff_fires_security_without_a_staffing_refusal(self):
        (self.repo / "hooks").mkdir()
        (self.repo / "hooks" / "gate.sh").write_text("echo gate\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "boundary")
        head = git(self.repo, "rev-parse", "HEAD")
        code, out, err = self.run_preflight(self.argv(base=False) + ["--base", self.base, "--head", head])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["triggers"]["fired"], ["security"])
        security = next(row for row in payload["triggers"]["triggers"] if row["trigger"] == "security")
        self.assertEqual(security["signals"], [{"signal": "trust_boundary_path", "evidence": "hooks/gate.sh"}])
        self.assertNotIn("unaddressed", payload["triggers"])
        self.assertEqual(payload["verdict"], "ready")
        self.assertEqual(payload["read_next"], ["skills/herdr-teamlead/references/specialists.md"])

    def test_a_planned_trust_boundary_fires_security(self):
        planned = self.tmp / "planned.json"
        planned.write_text(json.dumps({"schema_version": 1, "added": ["hooks/new.sh"], "changed": [],
                                       "package_lines": {}, "cli_surface": []}), encoding="utf-8")
        code, out, err = self.run_preflight(self.argv(base=False) + ["--base", self.base, "--planned", str(planned)])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["triggers"]["fired"], ["security"])


class BadArgumentsTest(PreflightCase):
    def assert_refused(self, argv, fragment, error="usage_error"):
        code, out, err = self.run_preflight(argv)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        failure = json.loads(err)
        self.assertEqual(failure["error"], error)
        self.assertIn(fragment, failure["message"])
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.state.exists())

    def test_a_malformed_slug_is_refused_before_any_contact(self):
        argv = self.argv()
        argv[argv.index("viewer/project")] = "not-a-slug"
        self.assert_refused(argv, "<owner>/<repo>")

    def test_a_slug_with_a_trailing_newline_is_refused_before_any_contact(self):
        argv = self.argv()
        argv[argv.index("viewer/project")] = "viewer/project\n"
        self.assert_refused(argv, "<owner>/<repo>")

    def test_a_malformed_now_is_refused_before_any_contact(self):
        for bad in ("yesterday", "2026-10-14T12:00:00"):
            with self.subTest(now=bad):
                argv = self.argv()
                argv[argv.index("--now") + 1] = bad
                self.assert_refused(argv, "ISO-8601 timestamp with a timezone")

    def test_head_without_base_is_refused(self):
        self.assert_refused(self.argv(base=False) + ["--head", self.head], "--base")

    def test_a_missing_repo_directory_is_refused(self):
        argv = self.argv()
        argv[argv.index(str(self.repo))] = str(self.tmp / "absent")
        self.assert_refused(argv, "not a directory")

    def test_an_unknown_base_is_refused(self):
        self.assert_refused(self.argv(base=False) + ["--base", "no-such-ref"], "git")

    def test_an_unreadable_planned_file_is_refused(self):
        self.assert_refused(self.argv(base=False) + ["--base", self.base, "--planned", str(self.tmp / "absent.json")],
                            "--planned")

    def test_a_non_utf8_planned_file_is_refused(self):
        planned = self.tmp / "planned.json"
        planned.write_bytes(b"{\"paths\": [\"\xff\"]}")
        self.assert_refused(self.argv(base=False) + ["--base", self.base, "--planned", str(planned)], "not UTF-8")

    def test_an_unreadable_config_is_refused(self):
        self.config.write_text("{broken", encoding="utf-8")
        code, _out, err = self.run_preflight(self.argv())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "config_error")
        self.assertEqual(self.runner.calls, [])

    def test_a_missing_required_flag_is_an_argparse_error(self):
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["preflight", "--repo", str(self.repo)])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
