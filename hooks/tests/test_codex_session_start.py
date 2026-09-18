#!/usr/bin/env python3
"""Exercise the native Codex package at its JSON/process boundaries."""

import importlib.util
import io
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "hooks/codex-session-start.py"
EVENT = {"hook_event_name": "SessionStart", "cwd": "/fixture/repository"}


class CodexSessionStart(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-policy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / "plugin with spaces").resolve()
        for directory in ("hooks", "rules", ".tessl-plugin"):
            (self.root / directory).mkdir(parents=True)
        shutil.copy2(ADAPTER, self.root / "hooks/codex-session-start.py")
        self.manifest = {
            "version": "1.2.3",
            "rules": ["rules/always.md", "rules/scoped.md"],
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "args": ["${TESSL_PLUGIN_DIR}/hooks/check.sh"],
                            }
                        ]
                    }
                ]
            },
        }
        self.write_manifest()
        (self.root / "rules/always.md").write_text(
            "---\nalwaysApply: true\n---\n# Always\n"
        )
        (self.root / "rules/scoped.md").write_text(
            '---\nalwaysApply: false\napplyTo: "hooks/** — editing hooks"\n---\n# Scoped\n'
        )

    def write_manifest(self):
        (self.root / ".tessl-plugin/plugin.json").write_text(json.dumps(self.manifest))

    def run_hook(self, *args, event=EVENT):
        return subprocess.run(
            [sys.executable, str(self.root / "hooks/codex-session-start.py"), *args],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_context_names_installed_version_paths_and_scopes(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        text = output["additionalContext"]
        self.assertIn("gamussa/coding-policy 1.2.3", text)
        self.assertIn(f"{self.root}/rules/always.md (always-on)", text)
        self.assertIn(
            f"{self.root}/rules/scoped.md (conditional; read frontmatter scope)", text
        )
        self.assertIn("use this installed plugin root instead", text)

    def test_other_events_are_silent(self):
        result = self.run_hook(event={"hook_event_name": "Stop"})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_invalid_input_is_reported(self):
        result = self.run_hook(event=[])
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON object", result.stderr)

    def test_missing_rule_is_reported(self):
        (self.root / "rules/always.md").unlink()
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("inspect the plugin installation", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_invalid_scope_is_reported(self):
        (self.root / "rules/scoped.md").write_text("---\nalwaysApply: false\n---\n")
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("applyTo", result.stderr)

    def test_rule_path_cannot_escape_plugin(self):
        self.manifest["rules"] = ["../outside.md"]
        self.write_manifest()
        result = self.run_hook()
        self.assertEqual(result.returncode, 1)
        self.assertIn("escapes the plugin root", result.stderr)

    def test_undeclared_hook_is_not_executed(self):
        result = self.run_hook("../check.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("manifest-declared", result.stderr)

    def test_tessl_context_and_stdin_are_preserved(self):
        (self.root / "hooks/check.sh").write_text(
            "set -euo pipefail\n"
            "python3 -c 'import json, os, sys; event = json.load(sys.stdin); "
            'print(json.dumps({"additionalContext": event["cwd"] + "|" + os.environ["TESSL_PLUGIN_DIR"]}))\'\n'
        )
        result = self.run_hook("check.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"],
            {
                "hookEventName": "SessionStart",
                "additionalContext": f"/fixture/repository|{self.root}",
            },
        )

    def test_silent_child_stays_silent(self):
        (self.root / "hooks/check.sh").write_text("exit 0\n")
        result = self.run_hook("check.sh")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_child_failure_is_preserved(self):
        (self.root / "hooks/check.sh").write_text(
            "echo 'fixture failure' >&2\nexit 7\n"
        )
        result = self.run_hook("check.sh")
        self.assertEqual(result.returncode, 7)
        self.assertIn("fixture failure", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_malformed_child_output_is_reported(self):
        for output in ("not JSON", "[]", '{"additionalContext":false}'):
            with self.subTest(output=output):
                (self.root / "hooks/check.sh").write_text(
                    f"printf '%s\\n' '{output}'\n"
                )
                result = self.run_hook("check.sh")
                self.assertEqual(result.returncode, 1)
                self.assertIn("inspect the plugin installation", result.stderr)

    def test_child_timeout_is_reported(self):
        spec = importlib.util.spec_from_file_location("codex_session_start", ADAPTER)
        assert spec is not None and spec.loader is not None
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        errors = io.StringIO()
        with (
            patch.object(adapter, "ROOT", self.root),
            patch.object(sys, "argv", ["hook", "check.sh"]),
            patch.object(sys, "stdin", io.StringIO(json.dumps(EVENT))),
            patch.object(
                adapter,
                "run_hook",
                side_effect=subprocess.TimeoutExpired("fixture", 45),
            ),
            redirect_stderr(errors),
        ):
            self.assertEqual(adapter.main(), 1)
        self.assertIn("timed out", errors.getvalue())

    def test_timeout_closes_a_real_descendants_output_pipe(self):
        # Trigger the timeout only after the grandchild reports readiness. Its
        # pipe reaches EOF only when both Bash and the descendant have exited.
        (self.root / "hooks/check.sh").write_text(
            "set -euo pipefail\n"
            "python3 -c 'import os, signal; print(os.getpid(), flush=True); signal.pause()' &\n"
            "wait\n"
        )
        spec = importlib.util.spec_from_file_location("codex_session_start", ADAPTER)
        assert spec is not None and spec.loader is not None
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        communicate = subprocess.Popen.communicate
        descendants = []

        def timeout_when_ready(process, input=None, timeout=None):
            if timeout is not None:
                self.assertIsNotNone(process.stdout)
                self.assertTrue(
                    select.select([process.stdout], [], [], 5)[0],
                    "child did not report readiness",
                )
                pid = int(process.stdout.readline().strip())
                descendants.append((pid, os.dup(process.stdout.fileno())))
                raise subprocess.TimeoutExpired(process.args, timeout)
            # Bound cleanup in the test, so a regression fails instead of hanging.
            return communicate(process, input=input, timeout=5)

        errors = io.StringIO()
        try:
            with (
                patch.object(adapter, "ROOT", self.root),
                patch.object(sys, "argv", ["hook", "check.sh"]),
                patch.object(sys, "stdin", io.StringIO(json.dumps(EVENT))),
                patch.object(subprocess.Popen, "communicate", timeout_when_ready),
                redirect_stderr(errors),
            ):
                self.assertEqual(adapter.main(), 1)
            self.assertIn("timed out", errors.getvalue())
            self.assertEqual(len(descendants), 1)
            _, pipe = descendants[0]
            self.assertTrue(
                select.select([pipe], [], [], 0)[0],
                "descendant survived timeout and still owns stdout",
            )
            self.assertEqual(os.read(pipe, 1), b"")
        finally:
            for pid, pipe in descendants:
                os.close(pipe)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    # Expected after successful process-group cleanup.
                    continue


class CodexPackage(unittest.TestCase):
    def test_manifest_version_and_skills_match_tessl(self):
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        tessl = json.loads((ROOT / ".tessl-plugin/plugin.json").read_text())
        self.assertEqual(codex["version"].split("+", 1)[0], tessl["version"])
        self.assertEqual(codex["name"], "gamussa-coding-policy")
        discovered = {
            str(path.parent.relative_to(ROOT))
            for path in (ROOT / codex["skills"]).glob("*/SKILL.md")
        }
        self.assertEqual(discovered, set(tessl["skills"]))

    def test_hooks_cover_original_session_and_stop_handlers(self):
        codex = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
        tessl = json.loads((ROOT / ".tessl-plugin/plugin.json").read_text())
        session = [
            hook["command"]
            for group in codex["SessionStart"]
            for hook in group["hooks"]
        ]
        self.assertIn('python3 "${PLUGIN_ROOT}/hooks/codex-session-start.py"', session)
        originals = [
            Path(hook["args"][0]).name
            for group in tessl["hooks"]["SessionStart"]
            for hook in group["hooks"]
        ]
        self.assertEqual(
            {command.rsplit(" ", 1)[-1] for command in session[1:]}, set(originals)
        )
        expected_stop = [
            hook["command"].replace("${TESSL_PLUGIN_DIR}", "${PLUGIN_ROOT}")
            for group in tessl["nativeHooks"]["codex"]["Stop"]
            for hook in group["hooks"]
        ]
        self.assertEqual(
            [hook["command"] for group in codex["Stop"] for hook in group["hooks"]],
            expected_stop,
        )


if __name__ == "__main__":
    unittest.main()
