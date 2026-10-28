"""Feed check-isolation.py stand-in CLIs it must accept and must reject.

The real pinned CLI runs in the review workflow (its documented entry point).
Here a stand-in `claude` on PATH plays the CLI's network side: it posts one
model request to the probe's loopback server, shaped per case. Each case runs
the probe as a process and observes its exit code and output.
"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROBE = Path(__file__).resolve().parents[1] / "check-isolation.py"
PROBE_DEADLINE_SECONDS = 60

FAKE_CLI = textwrap.dedent("""\
    #!{python}
    import json, os, sys, urllib.error, urllib.request
    mode = {mode!r}
    tools = ["Read", "Glob", "Grep", "StructuredOutput"]
    if mode == "extra-tool":
        tools.append("Bash")
    if mode == "missing-tool":
        tools.remove("Grep")
    system = "review"
    if mode == "leak":
        with open("CLAUDE.md") as handle:
            system += handle.read()
    if mode == "hook":
        settings = json.load(open(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")))
        os.system(settings["hooks"]["SessionStart"][0]["hooks"][0]["command"])
    if mode != "silent":
        body = json.dumps({{"system": system, "tools": [{{"name": name}} for name in tools]}}).encode()
        request = urllib.request.Request(os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages", data=body,
                                         headers={{"Content-Type": "application/json"}})
        try:
            urllib.request.urlopen(request)
        except urllib.error.HTTPError:
            pass  # The probe answers 401 by design.
    sys.exit(1)
    """)


class IsolationProbeTest(unittest.TestCase):
    def probe(self, mode):
        with tempfile.TemporaryDirectory(prefix="fake-claude-") as temp:
            fake = Path(temp) / "claude"
            fake.write_text(FAKE_CLI.format(python=sys.executable, mode=mode))
            fake.chmod(0o755)
            env = {**os.environ, "PATH": f"{temp}{os.pathsep}{os.environ['PATH']}"}
            return subprocess.run([sys.executable, str(PROBE)], env=env, text=True,
                                  capture_output=True, check=False, timeout=PROBE_DEADLINE_SECONDS)

    def test_isolated_cli_passes(self):
        result = self.probe("clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"isolation": "pass"', result.stdout)

    def test_cli_loading_a_planted_instruction_file_fails(self):
        result = self.probe("leak")
        self.assertEqual(result.returncode, 1)
        self.assertIn("planted instruction file", result.stderr)

    def test_cli_running_a_planted_hook_fails(self):
        result = self.probe("hook")
        self.assertEqual(result.returncode, 1)
        self.assertIn("planted hook", result.stderr)

    def test_cli_exposing_an_execution_tool_fails(self):
        result = self.probe("extra-tool")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Bash", result.stderr)

    def test_cli_missing_a_read_only_tool_fails(self):
        result = self.probe("missing-tool")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unexpected tool set", result.stderr)


if __name__ == "__main__":
    unittest.main()
