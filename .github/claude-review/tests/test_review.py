"""Exercise context isolation, lane posting, failure and exact-head guarantees.

Mocks sit at the process boundary only: `run` (git, gh, claude subprocesses)
and `api` (the GitHub REST calls made through gh). The lane config comes from
the real skills/release/review-lanes.sh, and a posted body is classified by
that script's own jq classifier.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location("claude_review", HERE.parent / "review.py")
assert SPEC and SPEC.loader
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)
API = reviewer.api
HEAD = "a" * 40
MARKER = "<!-- coding-policy:claude-quality-review:v1 -->"
LOGIN = "github-actions[bot]"


def classify(review: dict) -> str:
    """Lane the release scripts assign to a review, via review-lanes.sh."""
    lanes = REPO_ROOT / "skills/release/review-lanes.sh"
    script = (f'source "{lanes}"; set +e; cfg=$(review_lanes_config); '
              'jq -r --argjson cfg "$cfg" "$(review_lanes_jq) review_lane($cfg)"')
    result = subprocess.run(["bash", "-c", script], input=json.dumps(review), text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GH_TOKEN": "github-secret", "GITHUB_TOKEN": "other-secret",
                              "CLAUDE_CODE_OAUTH_TOKEN": "subscription-secret",
                              "ANTHROPIC_API_KEY": "api-secret", "OPENAI_API_KEY": "openai-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.posts = []
        self.claude_calls = []
        self.api_reads = 0
        self.changed = False
        self.fork = False
        self.fetched = HEAD
        self.lane_override = None
        self.posted_state = None
        self.output = {"type": "result", "subtype": "success", "is_error": False,
                       "structured_output": {"summary": "One concrete defect.", "findings": [
                           {"path": "app.py", "line": 2, "severity": "blocking", "message": "Empty input divides by zero."}]}}
        self.runner = patch.object(reviewer, "run", self.run_command)
        self.runner.start()
        self.addCleanup(self.runner.stop)
        self.api_patch = patch.object(reviewer, "api", self.api)
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)

    def api(self, endpoint, payload=None):
        if payload is not None:
            self.posts.append(payload)
            state = self.posted_state or {"REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED",
                                          "APPROVE": "APPROVED"}[payload["event"]]
            return {"state": state, "commit_id": payload["commit_id"], "body": payload["body"],
                    "html_url": "https://github.com/o/r/pull/1#review", "user": {"login": LOGIN}}
        self.api_reads += 1
        return {"state": "open", "base": {"ref": "main"},
                "head": {"sha": "b" * 40 if self.changed and self.api_reads > 1 else HEAD,
                         "repo": {"full_name": "fork/r" if self.fork else "o/r"}}}

    def run_command(self, args, *, cwd=None, env=None, data=None):
        if args[:2] == ["git", "clone"]:
            Path(args[-1]).mkdir()
        elif args[:2] == ["git", "rev-parse"]:
            return self.fetched + "\n"
        elif args[:2] == ["git", "diff"]:
            return "diff --git a/app.py b/app.py\n+return total / len(items)\n"
        elif args[0] == "claude":
            self.claude_calls.append((args, env, data))
            self.assertEqual(sorted(Path(cwd).iterdir()), [])
            return json.dumps(self.output)
        elif args[0] == "bash":
            lanes = json.loads(subprocess.run(args, text=True, check=True, capture_output=True).stdout)
            if self.lane_override:
                lanes["claude-quality"].update(self.lane_override)
            return json.dumps(lanes)
        return ""

    def review(self):
        return reviewer.review_pr("o", "r", "1", "main")

    def test_subscription_review_has_no_customizations_or_execution_tools(self):
        result = self.review()
        args, env, prompt = self.claude_calls[0]
        for flag in ("--safe-mode", "--restricted", "--disable-slash-commands",
                     "--strict-mcp-config", "--no-session-persistence"):
            self.assertIn(flag, args)
        self.assertNotIn("--bare", args)  # Bare mode does not support subscription auth.
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertIn("purely for code quality and bugs", args[args.index("--system-prompt") + 1])
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(key, env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "subscription-secret")
        self.assertIn("return total", prompt)
        self.assertEqual(result["state"], "posted")
        self.assertEqual(result["blocking"], 1)

    def test_blocking_findings_still_post_a_comment_in_the_claude_lane(self):
        self.review()
        post = self.posts[0]
        self.assertEqual(post["event"], "COMMENT")
        self.assertEqual(post["commit_id"], HEAD)
        self.assertTrue(post["body"].startswith(MARKER + "\n"))
        self.assertIn("## Blocking findings", post["body"])
        self.assertIn("app.py:2", post["body"])
        self.assertEqual(classify({"user": {"login": LOGIN}, "body": post["body"]}), "claude-quality")

    def test_advisory_and_clean_reviews_post_a_comment(self):
        self.output["structured_output"]["findings"][0]["severity"] = "advisory"
        self.assertEqual(self.review()["advisory"], 1)
        self.assertEqual(self.posts[-1]["event"], "COMMENT")
        self.assertIn("## Advisory findings", self.posts[-1]["body"])
        self.output["structured_output"] = {"summary": "No defects found.", "findings": []}
        self.assertEqual(self.review()["findings"], 0)
        self.assertEqual(self.posts[-1]["event"], "COMMENT")
        self.assertEqual(len(self.posts), 2)

    def test_gating_lane_is_refused_before_any_model_or_post(self):
        self.lane_override = {"gating": True}
        with self.assertRaisesRegex(reviewer.ReviewError, "dedicated GitHub App"):
            self.review()
        self.assertEqual((self.posts, self.claude_calls), ([], []))

    def test_lane_without_the_shared_login_is_refused(self):
        self.lane_override = {"logins": ["claude-reviewer[bot]"]}
        with self.assertRaisesRegex(reviewer.ReviewError, "does not list"):
            self.review()
        self.assertEqual((self.posts, self.claude_calls), ([], []))

    def test_unconfirmed_post_state_fails(self):
        self.posted_state = "PENDING"
        with self.assertRaisesRegex(reviewer.ReviewError, "did not confirm"):
            self.review()

    def test_post_errors_never_become_a_clean_review(self):
        with patch.object(reviewer, "run", side_effect=reviewer.ReviewError("post failed")) as run:
            with self.assertRaises(reviewer.ReviewError):
                API("repos/o/r/pulls/1/reviews", {"event": "COMMENT", "commit_id": HEAD, "body": "review"})
            self.assertEqual(run.call_count, 1)

    def test_invalid_output_never_becomes_a_clean_review(self):
        for output in ({"is_error": True}, {"is_error": False, "subtype": "success"},
                       {"is_error": False, "subtype": "error_max_turns"}):
            self.output = output
            with self.assertRaises(reviewer.ReviewError):
                self.review()
        self.assertEqual(self.posts, [])

    def test_credential_echo_never_posts(self):
        self.output["structured_output"]["summary"] = "subscription-secret"
        with self.assertRaisesRegex(reviewer.ReviewError, "credential"):
            self.review()
        self.assertEqual(self.posts, [])

    def test_head_move_during_review_refuses_stale_verdict(self):
        self.changed = True
        with self.assertRaisesRegex(reviewer.ReviewError, "stale"):
            self.review()
        self.assertEqual(self.posts, [])

    def test_head_move_during_fetch_runs_no_model(self):
        self.fetched = "b" * 40
        with self.assertRaisesRegex(reviewer.ReviewError, "during fetch"):
            self.review()
        self.assertEqual(self.claude_calls, [])

    def test_fork_receives_no_model_call(self):
        self.fork = True
        with self.assertRaisesRegex(reviewer.ReviewError, "same-repository"):
            self.review()
        self.assertEqual(self.claude_calls, [])

    def test_invalid_locations_fail_closed(self):
        for path, line in (("../secret", 1), ("/etc/passwd", 1), ("file.py", 0), ("file.py", True)):
            self.output["structured_output"]["findings"][0].update(path=path, line=line)
            with self.assertRaises(reviewer.ReviewError):
                self.review()
        self.assertEqual(self.posts, [])


class FailureDiagnosticsTest(unittest.TestCase):
    def test_failed_cli_emits_fixed_actionable_categories_without_echoing_output(self):
        cases = (
            ("authentication_error: secret-token", "authentication rejected"),
            ("OAuth token has expired: secret-token", "claude setup-token"),
            ("You’ve hit your limit: private-source", "subscription usage limit reached"),
            ("rate_limit_error private-source", "provider rate limit"),
            ("model_not_found: private-source", "model unavailable"),
            ("unknown option '--private-source'", "CLI argument rejected"),
            ("ECONNRESET private-source", "network or TLS failure"),
            ("unexpected private-source secret-token", "unclassified CLI failure"),
        )
        for output, expected in cases:
            for stdout, stderr in ((output, ""), ("", output)):
                with self.subTest(output=output, stream=bool(stdout)):
                    result = subprocess.CompletedProcess(["claude"], 1, stdout, stderr)
                    with patch.object(reviewer.subprocess, "run", return_value=result):
                        with self.assertRaises(reviewer.ReviewError) as caught:
                            reviewer.run(["claude", "--print"])
                    message = str(caught.exception)
                    self.assertIn(expected, message)
                    self.assertIn("exit 1", message)
                    self.assertNotIn("secret-token", message)
                    self.assertNotIn("private-source", message)

    def test_success_output_remains_unchanged(self):
        result = subprocess.CompletedProcess(["claude"], 0, '{"ok":true}', "")
        with patch.object(reviewer.subprocess, "run", return_value=result):
            self.assertEqual(reviewer.run(["claude", "--print"]), '{"ok":true}')

    def test_other_command_failures_do_not_claim_a_claude_diagnosis(self):
        result = subprocess.CompletedProcess(["git"], 128, "", "authentication_error secret-token")
        with patch.object(reviewer.subprocess, "run", return_value=result):
            with self.assertRaises(reviewer.ReviewError) as caught:
                reviewer.run(["git", "fetch"])
        self.assertIn("git failed (exit 128)", str(caught.exception))
        self.assertNotIn("authentication rejected", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))


class EntryPointTest(unittest.TestCase):
    def run_main(self, args, env):
        return subprocess.run(["python3", str(HERE.parent / "review.py"), *args], text=True,
                              capture_output=True, check=False,
                              env={"PATH": os.environ["PATH"], **env})

    def test_missing_credentials_exit_2_naming_setup(self):
        result = self.run_main(["o", "r", "1", "main"], {})
        self.assertEqual(result.returncode, 2)
        self.assertIn("claude setup-token", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_bad_arguments_exit_2(self):
        result = self.run_main(["o", "r", "x", "main"], {"GH_TOKEN": "t", "CLAUDE_CODE_OAUTH_TOKEN": "t"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()
