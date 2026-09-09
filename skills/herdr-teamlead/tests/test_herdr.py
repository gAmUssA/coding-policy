"""Tests for teamlead.herdr. No test in this file starts a process."""

import os as _os
import sys as _sys

# Run as a script (`python3 tests/test_x.py`), Python puts tests/ on sys.path
# rather than the repo root, so neither `teamlead` nor `tests.fakes` would
# resolve. Under `-m unittest` from the root this is already true and the
# insert is a no-op. The consuming repo's runner executes files as scripts.
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import subprocess
import unittest

from teamlead.errors import HerdrError
from teamlead.herdr import (
    SECRET_MASK,
    TRACE_FIELD_CAP_BYTES,
    HerdrClient,
    bound_field,
    format_argv,
    redact_secrets,
    scrub_for_trace,
    trace_enabled_in_env,
)

from tests.fakes import FakeRunner, agent_json, ok_json


class ArgvBuilderTest(unittest.TestCase):
    def setUp(self):
        self.client = HerdrClient(binary="herdr", runner=FakeRunner())

    def test_agent_get(self):
        self.assertEqual(self.client.argv_agent_get("grok"), ["herdr", "agent", "get", "grok"])

    def test_agent_read_with_source_and_lines(self):
        self.assertEqual(
            self.client.argv_agent_read("codex", source="recent-unwrapped", lines=80),
            ["herdr", "agent", "read", "codex", "--source", "recent-unwrapped", "--lines", "80"],
        )

    def test_agent_read_defaults_omit_optional_flags(self):
        self.assertEqual(self.client.argv_agent_read("codex"), ["herdr", "agent", "read", "codex"])

    def test_agent_prompt_plain(self):
        self.assertEqual(
            self.client.argv_agent_prompt("grok", "/usage"),
            ["herdr", "agent", "prompt", "grok", "/usage"],
        )

    def test_agent_prompt_with_wait_until_and_timeout(self):
        self.assertEqual(
            self.client.argv_agent_prompt(
                "grok", "/new", wait=True, until=("idle", "done"), timeout_ms=60000
            ),
            [
                "herdr", "agent", "prompt", "grok", "/new", "--wait",
                "--until", "idle", "--until", "done", "--timeout", "60000",
            ],
        )

    def test_agent_send_keys_takes_several_keys(self):
        self.assertEqual(
            self.client.argv_agent_send_keys("claude", ["esc", "esc"]),
            ["herdr", "agent", "send-keys", "claude", "esc", "esc"],
        )

    def test_agent_wait(self):
        self.assertEqual(
            self.client.argv_agent_wait("claude", until=("idle",), timeout_ms=1000),
            ["herdr", "agent", "wait", "claude", "--until", "idle", "--timeout", "1000"],
        )

    def test_pane_wait_output_with_regex(self):
        self.assertEqual(
            self.client.argv_pane_wait_output(
                "w2:p1", regex="Current week", source="visible", lines=60, timeout_ms=20000
            ),
            [
                "herdr", "pane", "wait-output", "--regex", "Current week",
                "--source", "visible", "--lines", "60", "--timeout", "20000", "w2:p1",
            ],
        )

    def test_pane_wait_output_with_literal_match(self):
        self.assertEqual(
            self.client.argv_pane_wait_output("w3:p1", match="Weekly limit"),
            ["herdr", "pane", "wait-output", "--match", "Weekly limit", "w3:p1"],
        )

    def test_pane_wait_output_needs_exactly_one_matcher(self):
        with self.assertRaises(HerdrError):
            self.client.argv_pane_wait_output("w3:p1")
        with self.assertRaises(HerdrError):
            self.client.argv_pane_wait_output("w3:p1", regex="a", match="b")

    def test_binary_override_is_honoured(self):
        client = HerdrClient(binary="/opt/herdr", runner=FakeRunner())
        self.assertEqual(client.argv_agent_get("x")[0], "/opt/herdr")


class SendSlashCommandTest(unittest.TestCase):
    """A slash command is typed, not pasted.

    Live, `agent prompt grok /usage` went through the pane's bracketed-paste
    mode and Grok read it as a chat message: it answered in prose about
    billing and started reading files instead of opening its usage panel.
    """

    def _client(self):
        self.runner = FakeRunner()
        self.runner.set("pane send-text", ok_json("pane_send_text"))
        self.runner.set("pane send-keys", ok_json("pane_send_keys"))
        return HerdrClient(binary="herdr", runner=self.runner)

    def test_argv_builder_returns_text_then_enter(self):
        client = HerdrClient(binary="herdr", runner=FakeRunner())
        self.assertEqual(
            client.argv_send_slash_command("w4:p1", "/usage"),
            [
                ["herdr", "pane", "send-text", "w4:p1", "/usage"],
                ["herdr", "pane", "send-keys", "w4:p1", "enter"],
            ],
        )

    def test_it_runs_send_text_then_send_keys_in_that_order(self):
        self._client().send_slash_command("w4:p1", "/usage")
        self.assertEqual(
            self.runner.commands(),
            ["pane send-text w4:p1 /usage", "pane send-keys w4:p1 enter"],
        )

    def test_it_never_touches_agent_prompt(self):
        self._client().send_slash_command("w4:p1", "/usage")
        self.assertEqual(self.runner.pasted_prompts(), [])

    def test_live_guard_runs_before_text_and_each_configured_enter(self):
        expected = ["pane send-text w4:p1 /new", "pane send-keys w4:p1 enter", "pane send-keys w4:p1 enter"]
        for stop_after in (0, 1, 2):
            client = self._client()

            def guard():
                if len(self.runner.writes()) == stop_after:
                    raise HerdrError("Worker changed; inspect the pane before further input.", {})

            with self.subTest(stop_after=stop_after), self.assertRaisesRegex(HerdrError, "Worker changed"):
                client.deliver_slash_command("type", "grok", "w4:p1", "/new", enter_count=2, before_input=guard)
            self.assertEqual(self.runner.writes(), expected[:stop_after])

    def test_pasted_slash_command_checks_guard_before_prompt(self):
        client = self._client()

        def guard():
            raise HerdrError("Worker changed; inspect the pane before further input.", {})

        with self.assertRaisesRegex(HerdrError, "Worker changed"):
            client.deliver_slash_command("paste", "claude", "w4:p1", "/clear", before_input=guard)
        self.assertEqual(self.runner.writes(), [])

    def test_the_newline_is_a_keystroke_not_part_of_the_text(self):
        self._client().send_slash_command("w4:p1", "/clear")
        sent = [c for c in self.runner.commands() if c.startswith("pane send-text")]
        self.assertEqual(sent, ["pane send-text w4:p1 /clear"])

    def test_it_returns_both_results(self):
        result = self._client().send_slash_command("w4:p1", "/new")
        self.assertEqual(result["send_text"]["type"], "pane_send_text")
        self.assertEqual(result["send_keys"]["type"], "pane_send_keys")

    def test_a_failed_send_text_stops_before_the_enter(self):
        client = self._client()
        self.runner.set(
            "pane send-text",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"pane_gone","message":"pane not found"}}',
        )
        with self.assertRaises(HerdrError):
            client.send_slash_command("w4:p1", "/usage")
        self.assertEqual(self.runner.commands(), ["pane send-text w4:p1 /usage"])


class EmptyStdoutTest(unittest.TestCase):
    """`pane send-text` succeeds with exit 0 and NO output.

    Live, demanding a JSON body turned a successful send-text into a
    transport error, and the run died before pressing Enter -- leaving
    `/usage` sitting unsent in Grok's input box.
    """

    def _silent_runner(self):
        runner = FakeRunner()
        runner.set("pane send-text", stdout="")
        runner.set("pane send-keys", stdout="")
        runner.set("agent send-keys", stdout="")
        return runner

    def test_send_text_accepts_exit_zero_with_no_output(self):
        runner = self._silent_runner()
        self.assertEqual(HerdrClient(runner=runner).pane_send_text("w4:p1", "/usage"), {})

    def test_send_keys_accepts_exit_zero_with_no_output(self):
        runner = self._silent_runner()
        self.assertEqual(HerdrClient(runner=runner).pane_send_keys("w4:p1", ["enter"]), {})

    def test_agent_send_keys_accepts_exit_zero_with_no_output(self):
        # The close-keys path runs in a finally; a body it never reads must
        # not be able to mask the error it is cleaning up after.
        runner = self._silent_runner()
        self.assertEqual(HerdrClient(runner=runner).agent_send_keys("grok", ["esc"]), {})

    def test_whitespace_only_output_is_also_success(self):
        runner = FakeRunner()
        runner.set("pane send-text", stdout="\n  \n")
        self.assertEqual(HerdrClient(runner=runner).pane_send_text("w4:p1", "/new"), {})

    def test_a_json_body_is_still_unwrapped_when_herdr_sends_one(self):
        runner = FakeRunner()
        runner.set("pane send-text", ok_json("pane_send_text"))
        self.assertEqual(
            HerdrClient(runner=runner).pane_send_text("w4:p1", "/usage"),
            {"type": "pane_send_text"},
        )

    def test_unparseable_output_is_tolerated_not_fatal(self):
        runner = FakeRunner()
        runner.set("pane send-text", stdout="sent 6 bytes")
        self.assertEqual(HerdrClient(runner=runner).pane_send_text("w4:p1", "/usage"), {})

    def test_a_non_zero_exit_is_still_an_error_carrying_stderr(self):
        runner = FakeRunner()
        runner.set(
            "pane send-text",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"pane_not_found","message":"pane w9:p9 not found"}}',
        )
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).pane_send_text("w9:p9", "/usage")
        message = str(caught.exception)
        self.assertIn("pane_not_found", message)
        self.assertIn("pane w9:p9 not found", message)

    def test_a_syntax_error_exit_is_still_an_error(self):
        runner = FakeRunner()
        runner.set("pane send-keys", stdout="", returncode=2, stderr="unknown key 'retrun'")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).pane_send_keys("w4:p1", ["retrun"])
        self.assertIn("syntax error", str(caught.exception))

    def test_commands_whose_result_is_read_stay_strict(self):
        # agent get feeds the busy-agent gate; an empty body there is a real
        # failure and must not be waved through.
        runner = FakeRunner()
        runner.set("agent get grok", stdout="")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("grok")
        self.assertIn("non-JSON", str(caught.exception))

    def test_pane_wait_output_stays_strict(self):
        runner = FakeRunner()
        runner.set("pane wait-output", stdout="")
        with self.assertRaises(HerdrError):
            HerdrClient(runner=runner).pane_wait_output("w4:p1", match="Weekly limit")


class ExecutionTest(unittest.TestCase):
    def test_agent_get_returns_the_agent_record(self):
        runner = FakeRunner({"agent get grok": None})
        runner.set("agent get grok", agent_json("grok", "idle", "w4:p1"))
        client = HerdrClient(runner=runner)
        agent = client.agent_get("grok")
        self.assertEqual(agent["agent_status"], "idle")
        self.assertEqual(agent["pane_id"], "w4:p1")

    def test_agent_list_returns_the_array(self):
        runner = FakeRunner()
        runner.set(
            "agent list",
            '{"id":"cli:agent:list","result":{"type":"agent_list","agents":[{"name":"grok"}]}}',
        )
        self.assertEqual(HerdrClient(runner=runner).agent_list(), [{"name": "grok"}])

    def test_agent_read_returns_raw_text_not_json(self):
        runner = FakeRunner()
        runner.set("agent read grok", "Weekly limit: 0%\nCredits: $16.42\n")
        text = HerdrClient(runner=runner).agent_read("grok", source="recent-unwrapped")
        self.assertIn("Weekly limit: 0%", text)

    def test_prompt_and_send_keys_return_the_result_body(self):
        runner = FakeRunner()
        runner.set("agent prompt grok", ok_json("agent_prompt"))
        runner.set("agent send-keys claude", ok_json("agent_send_keys"))
        client = HerdrClient(runner=runner)
        self.assertEqual(client.agent_prompt("grok", "/usage")["type"], "agent_prompt")
        self.assertEqual(client.agent_send_keys("claude", ["esc"])["type"], "agent_send_keys")

    def test_pane_wait_output_executes_the_built_argv(self):
        runner = FakeRunner()
        runner.set("pane wait-output", ok_json("pane_wait_output"))
        HerdrClient(runner=runner).pane_wait_output("w2:p1", regex="Current week", timeout_ms=5000)
        self.assertEqual(
            runner.commands(),
            ["pane wait-output --regex 'Current week' --timeout 5000 w2:p1"],
        )


class FailureTest(unittest.TestCase):
    def test_server_error_json_is_surfaced_with_its_code(self):
        runner = FakeRunner()
        runner.set(
            "agent get ghost",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"agent_not_found","message":"agent target ghost not found"}}',
        )
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("ghost")
        message = str(caught.exception)
        self.assertIn("agent_not_found", message)
        self.assertIn("ghost not found", message)

    def test_exit_two_is_reported_as_a_syntax_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="", returncode=2, stderr="unexpected argument")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("syntax error", str(caught.exception))

    def test_non_json_stderr_is_passed_through(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="", returncode=1, stderr="boom")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("boom", str(caught.exception))

    def test_non_json_stdout_on_a_control_command_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout="not json at all")
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("non-JSON", str(caught.exception))

    def test_json_without_a_result_field_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout='{"id":"cli:agent:get"}')
        with self.assertRaises(HerdrError):
            HerdrClient(runner=runner).agent_get("x")

    def test_result_without_an_agent_record_is_an_error(self):
        runner = FakeRunner()
        runner.set("agent get x", stdout='{"id":"cli:agent:get","result":{"type":"agent_info"}}')
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("herdr agent list", str(caught.exception))

    def test_missing_binary_names_the_env_override(self):
        runner = FakeRunner(raises={"agent get x": FileNotFoundError()})
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_get("x")
        self.assertIn("TEAMLEAD_HERDR_BIN", str(caught.exception))

    def test_subprocess_timeout_is_reported_with_the_command(self):
        runner = FakeRunner(
            raises={"agent wait x": subprocess.TimeoutExpired(cmd="herdr", timeout=1)}
        )
        with self.assertRaises(HerdrError) as caught:
            HerdrClient(runner=runner).agent_wait("x")
        self.assertIn("agent wait x", str(caught.exception))


class TraceTest(unittest.TestCase):
    """Tracing exists so a live run that goes wrong is diagnosable."""

    def test_no_sink_means_no_output(self):
        runner = FakeRunner()
        runner.set("agent get grok", agent_json("grok", "idle", "w4:p1"))
        HerdrClient(runner=runner).agent_get("grok")  # must not raise

    def test_a_successful_call_records_argv_exit_and_raw_streams(self):
        runner = FakeRunner()
        runner.set("agent read grok", "Weekly limit: 0%\n")
        lines = []
        HerdrClient(runner=runner, trace=lines.append).agent_read(
            "grok", source="visible", lines=80
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("herdr> herdr agent read grok --source visible --lines 80", lines[0])
        self.assertIn("exit=0", lines[0])
        self.assertIn("Weekly limit: 0%", lines[0])

    def test_a_failing_call_records_the_stderr_payload(self):
        runner = FakeRunner()
        runner.set(
            "pane wait-output",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"timeout","message":"timed out waiting for output match"}}',
        )
        lines = []
        client = HerdrClient(runner=runner, trace=lines.append)
        with self.assertRaises(HerdrError):
            client.pane_wait_output("w4:p1", match="Weekly limit", timeout_ms=20000)
        self.assertEqual(len(lines), 1)
        self.assertIn("pane wait-output --match 'Weekly limit'", lines[0])
        self.assertIn("exit=1", lines[0])
        self.assertIn("timed out waiting for output match", lines[0])

    def test_a_missing_binary_is_traced_too(self):
        runner = FakeRunner(raises={"agent get x": FileNotFoundError()})
        lines = []
        with self.assertRaises(HerdrError):
            HerdrClient(runner=runner, trace=lines.append).agent_get("x")
        self.assertIn("executable not found", lines[0])

    def test_every_call_is_traced_in_order(self):
        runner = FakeRunner()
        runner.set("agent get grok", agent_json("grok", "idle", "w4:p1"))
        runner.set("agent read grok", "text\n")
        lines = []
        client = HerdrClient(runner=runner, trace=lines.append)
        client.agent_get("grok")
        client.agent_read("grok")
        self.assertEqual(len(lines), 2)
        self.assertIn("agent get grok", lines[0])
        self.assertIn("agent read grok", lines[1])


class TraceEnvTest(unittest.TestCase):
    def test_unset_or_empty_is_off(self):
        self.assertFalse(trace_enabled_in_env({}))
        self.assertFalse(trace_enabled_in_env({"TEAMLEAD_TRACE": ""}))
        self.assertFalse(trace_enabled_in_env({"TEAMLEAD_TRACE": "  "}))

    def test_zero_is_off(self):
        self.assertFalse(trace_enabled_in_env({"TEAMLEAD_TRACE": "0"}))

    def test_anything_else_is_on(self):
        self.assertTrue(trace_enabled_in_env({"TEAMLEAD_TRACE": "1"}))
        self.assertTrue(trace_enabled_in_env({"TEAMLEAD_TRACE": "yes"}))


class FormatArgvTest(unittest.TestCase):
    def test_quotes_arguments_containing_spaces(self):
        self.assertEqual(
            format_argv(["herdr", "agent", "prompt", "grok", "hello world"]),
            "herdr agent prompt grok 'hello world'",
        )


#: One credential shape per row: (label, text carrying it, the part that must
#: not survive). Every fixture is a made-up literal, never a real credential.
SECRET_FIXTURES = (
    ("classic github pat", "GH_TOKEN is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 ok", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
    ("github oauth token", "using gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"),
    ("fine-grained pat", "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz", "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz"),
    ("openai-style key", "OPENAI key sk-proj-abcdefghijklmnopqrstuvwxyz0123", "sk-proj-abcdefghijklmnopqrstuvwxyz0123"),
    ("aws access key id", "aws_access_key_id AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("bearer token", "curl -H 'Bearer abcdefghijklmnopqrst'", "abcdefghijklmnopqrst"),
    ("authorization header", "Authorization: Bearer abcdefghijklmnopqrst", "abcdefghijklmnopqrst"),
    ("authorization in json", '{"Authorization": "Bearer abcdefghijklmnopqrst"}', "abcdefghijklmnopqrst"),
    ("token assignment", "GITHUB_TOKEN=abcdef123456", "abcdef123456"),
    ("key assignment in json", '{"api_key": "abcdef123456"}', "abcdef123456"),
    ("password assignment", "password=hunter2", "hunter2"),
    ("secret assignment", "client_secret = s3cr3tvalue", "s3cr3tvalue"),
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    ),
)


class RedactSecretsTest(unittest.TestCase):
    """The trace redactor is a floor under `no-secrets`: shapes never reach a log."""

    def test_every_known_shape_is_masked(self):
        for label, text, secret in SECRET_FIXTURES:
            with self.subTest(shape=label):
                out = redact_secrets(text)
                self.assertNotIn(secret, out)
                self.assertIn(SECRET_MASK, out)

    def test_the_key_name_survives_so_the_trace_still_says_what_was_sent(self):
        self.assertEqual(redact_secrets("GITHUB_TOKEN=abcdef123456"), "GITHUB_TOKEN=" + SECRET_MASK)
        self.assertEqual(
            redact_secrets("Authorization: Bearer abcdefghijklmnopqrst"),
            "Authorization: " + SECRET_MASK,
        )

    def test_ordinary_pane_text_is_untouched(self):
        text = "Weekly limit: 0% (resets September 6)  Credits: $16.42"
        self.assertEqual(redact_secrets(text), text)

    def test_a_herdr_json_response_is_untouched(self):
        payload = '{"id":"cli:agent:get","result":{"agent":{"agent_status":"idle","pane_id":"w4:p1"}}}'
        self.assertEqual(redact_secrets(payload), payload)

    def test_a_config_shaped_list_value_is_not_mangled(self):
        text = '{"close_keys": ["esc"], "usage_marker": "Weekly limit"}'
        self.assertEqual(redact_secrets(text), text)

    def test_empty_input_is_returned_as_is(self):
        self.assertEqual(redact_secrets(""), "")


class BoundFieldTest(unittest.TestCase):
    def test_text_under_the_cap_is_returned_whole(self):
        self.assertEqual(bound_field("short"), "short")

    def test_text_over_the_cap_is_cut_and_marked(self):
        out = bound_field("x" * 3000)
        self.assertTrue(out.startswith("x" * 100))
        self.assertIn("[truncated 952 bytes]", out)
        self.assertLess(len(out), 3000)

    def test_the_cap_counts_bytes_not_characters(self):
        # 4 bytes per character: 10 characters is 40 bytes, so a 20-byte cap
        # keeps 5 of them and reports the other 20 bytes as dropped.
        out = bound_field("🔑" * 10, cap=20)
        self.assertEqual(out.count("🔑"), 5)
        self.assertIn("[truncated 20 bytes]", out)

    def test_a_cut_through_a_multibyte_character_drops_it_cleanly(self):
        out = bound_field("🔑" * 10, cap=18)
        self.assertEqual(out.count("🔑"), 4)
        self.assertNotIn("\ufffd", out)


class ScrubForTraceTest(unittest.TestCase):
    def test_redaction_runs_before_the_cap(self):
        # A token straddling the cap boundary must be masked whole, never cut
        # in half with its front half emitted.
        secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        text = "a" * (TRACE_FIELD_CAP_BYTES - 10) + secret + "b" * 100
        out = scrub_for_trace(text)
        self.assertNotIn("ghp_", out)
        self.assertIn(SECRET_MASK, out)

    def test_it_both_masks_and_bounds(self):
        out = scrub_for_trace("password=hunter2 " + "z" * 4000)
        self.assertNotIn("hunter2", out)
        self.assertIn("[truncated", out)


class TraceRedactionTest(unittest.TestCase):
    """Nothing reaches the sink unscrubbed - argv, stdout, and stderr alike."""

    def test_a_token_in_argv_is_masked(self):
        runner = FakeRunner()
        runner.set("agent prompt grok", ok_json("agent_prompt"))
        lines = []
        HerdrClient(runner=runner, trace=lines.append).agent_prompt(
            "grok", "export GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        )
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", lines[0])
        self.assertIn(SECRET_MASK, lines[0])

    def test_a_credential_in_pane_output_is_masked(self):
        runner = FakeRunner()
        runner.set("agent read grok", "$ echo $GH_TOKEN\nghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n")
        lines = []
        HerdrClient(runner=runner, trace=lines.append).agent_read("grok", lines=40)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", lines[0])
        self.assertIn(SECRET_MASK, lines[0])

    def test_a_credential_in_a_failure_payload_is_masked(self):
        runner = FakeRunner()
        runner.set(
            "pane wait-output",
            stdout="",
            returncode=1,
            stderr='{"error":{"code":"auth","message":"bad token=abcdef123456"}}',
        )
        lines = []
        client = HerdrClient(runner=runner, trace=lines.append)
        with self.assertRaises(HerdrError):
            client.pane_wait_output("w4:p1", match="Weekly limit", timeout_ms=20000)
        self.assertNotIn("abcdef123456", lines[0])
        self.assertIn("exit=1", lines[0])

    def test_a_huge_pane_read_is_capped_in_the_trace(self):
        runner = FakeRunner()
        runner.set("agent read grok", "y" * 8000)
        lines = []
        HerdrClient(runner=runner, trace=lines.append).agent_read("grok", lines=200)
        self.assertIn("[truncated", lines[0])
        self.assertLess(len(lines[0]), 4000)

    def test_the_returned_payload_is_never_the_scrubbed_one(self):
        # Scrubbing is a property of the TRACE, not of the data teamlead
        # consumes: stdout still parses into the full document.
        runner = FakeRunner()
        runner.set("agent get grok", agent_json("grok", "idle", "w4:p1"))
        lines = []
        result = HerdrClient(runner=runner, trace=lines.append).agent_get("grok")
        self.assertEqual(result["pane_id"], "w4:p1")
        self.assertEqual(result["agent_status"], "idle")
        self.assertTrue(lines)

    def test_a_long_pane_read_is_returned_in_full_to_the_caller(self):
        runner = FakeRunner()
        runner.set("agent read grok", "y" * 8000)
        lines = []
        text = HerdrClient(runner=runner, trace=lines.append).agent_read("grok", lines=200)
        self.assertEqual(len(text), 8000)


if __name__ == "__main__":
    unittest.main()
