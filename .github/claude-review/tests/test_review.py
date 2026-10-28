"""Exercise context isolation, lane posting, failure and exact-head guarantees.

Mocks sit at the process boundary only. In-process cases replace
`subprocess.run` with a fake that plays git, gh and claude, so review.py's own
`run`, `api`, `lane_config` and `review_pr` execute for real; `bash` calls go
to the real skills/release/review-lanes.sh. The entry-point case runs
`review.py OWNER REPO PR BASE` as a process with stand-in git, gh and claude
executables on PATH. A posted body is classified by review-lanes.sh's own jq.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from typing import Any
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
REVIEW = HERE.parent / "review.py"
SPEC = importlib.util.spec_from_file_location("claude_review", REVIEW)
assert SPEC and SPEC.loader
reviewer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviewer)
REAL_RUN = subprocess.run
HEAD = "a" * 40
MARKER = "<!-- coding-policy:claude-quality-review:v1 -->"
LOGIN = "github-actions[bot]"
ENTRY_POINT_DEADLINE_SECONDS = 60


def classify(review: dict) -> str:
    """Lane the release scripts assign to a review, via review-lanes.sh."""
    lanes = REPO_ROOT / "skills/release/review-lanes.sh"
    script = (f'source "{lanes}"; set +e; cfg=$(review_lanes_config); '
              'jq -r --argjson cfg "$cfg" "$(review_lanes_jq) review_lane($cfg)"')
    result = REAL_RUN(["bash", "-c", script], input=json.dumps(review), text=True,
                      capture_output=True, check=True)
    return result.stdout.strip()


def done(args, stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GH_TOKEN": "github-secret", "GITHUB_TOKEN": "other-secret",
                              "CLAUDE_CODE_OAUTH_TOKEN": "subscription-secret",
                              "ANTHROPIC_API_KEY": "api-secret", "OPENAI_API_KEY": "openai-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.posts: list[dict] = []
        self.claude_calls: list[tuple] = []
        self.pr_reads = 0
        self.changed = False
        self.fork = False
        self.fetched = HEAD
        self.lane_override: dict[str, Any] | None = None
        self.posted_state: str | None = None
        self.post_fails = False
        self.output: dict[str, Any] = {"type": "result", "subtype": "success", "is_error": False,
                                       "structured_output": {"summary": "One concrete defect.", "findings": [
                                           {"path": "app.py", "line": 2, "severity": "blocking",
                                            "message": "Empty input divides by zero."}]}}
        boundary = patch.object(reviewer.subprocess, "run", self.process)
        boundary.start()
        self.addCleanup(boundary.stop)

    def process(self, args, *, cwd=None, env=None, input=None, **_kwargs):
        if args[:2] == ["git", "clone"]:
            Path(args[-1]).mkdir()
            return done(args)
        if args[:2] == ["git", "rev-parse"]:
            return done(args, self.fetched + "\n")
        if args[:2] == ["git", "diff"]:
            return done(args, "diff --git a/app.py b/app.py\n+return total / len(items)\n")
        if args[0] == "git":
            return done(args)
        if args[:2] == ["gh", "api"]:
            return self.gh_api(args, input)
        if args[0] == "claude":
            self.claude_calls.append((args, env, input))
            assert cwd is not None
            self.assertEqual(sorted(Path(cwd).iterdir()), [])
            return done(args, json.dumps(self.output))
        if args[0] == "bash":
            result = REAL_RUN(args, text=True, capture_output=True, check=True)
            lanes = json.loads(result.stdout)
            if self.lane_override:
                lanes["claude-quality"].update(self.lane_override)
            return done(args, json.dumps(lanes))
        raise AssertionError(f"unexpected process: {args[0]}")

    def gh_api(self, args, data):
        if "--method" in args:
            if self.post_fails:
                return done(args, "", 1, "HTTP 422 secret-token")
            payload = json.loads(data)
            self.posts.append(payload)
            state = self.posted_state or {"REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED",
                                          "APPROVE": "APPROVED"}[payload["event"]]
            return done(args, json.dumps({"state": state, "commit_id": payload["commit_id"],
                                          "html_url": "https://github.com/o/r/pull/1#review",
                                          "user": {"login": LOGIN}}))
        self.pr_reads += 1
        head = "b" * 40 if self.changed and self.pr_reads > 1 else HEAD
        return done(args, json.dumps({"state": "open", "base": {"ref": "main"},
                                      "head": {"sha": head, "repo": {"full_name": "fork/r" if self.fork else "o/r"}}}))

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

    def test_failed_post_never_becomes_a_clean_review(self):
        self.post_fails = True
        with self.assertRaises(reviewer.ReviewError) as caught:
            self.review()
        self.assertIn("gh failed (exit 1)", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

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

    def test_other_command_failures_do_not_claim_a_claude_diagnosis(self):
        result = subprocess.CompletedProcess(["git"], 128, "", "authentication_error secret-token")
        with patch.object(reviewer.subprocess, "run", return_value=result):
            with self.assertRaises(reviewer.ReviewError) as caught:
                reviewer.run(["git", "fetch"])
        self.assertIn("git failed (exit 128)", str(caught.exception))
        self.assertNotIn("authentication rejected", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))


# One stand-in for git, gh and claude, dispatched on its own name. Its state
# lives beside it: review.py hands claude an allowlisted environment, so the
# stand-ins locate the log by their own path, never by an env var.
STAND_IN = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    name, args = Path(sys.argv[0]).name, sys.argv[1:]
    head = "{head}"
    def log(entry):
        with open(here / "log.jsonl", "a") as handle:
            handle.write(json.dumps(entry) + "\\n")
    if name == "git":
        if args[0] == "clone":
            Path(args[-1]).mkdir()
        elif args[0] == "rev-parse":
            print(head)
        elif args[0] == "diff":
            print("diff --git a/app.py b/app.py\\n+return total / len(items)")
    elif name == "gh":
        if "--method" in args:
            payload = json.load(sys.stdin)
            log({{"post": payload}})
            print(json.dumps({{"state": "COMMENTED", "commit_id": payload["commit_id"],
                              "html_url": "https://github.com/o/r/pull/1#review",
                              "user": {{"login": "github-actions[bot]"}}}}))
        else:
            print(json.dumps({{"state": "open", "base": {{"ref": "main"}},
                              "head": {{"sha": head, "repo": {{"full_name": "o/r"}}}}}}))
    elif name == "claude":
        log({{"claude_env": sorted(os.environ)}})
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {{"summary": "No defects found.", "findings": []}}}}))
    """)


class EntryPointTest(unittest.TestCase):
    def run_main(self, args, env):
        return REAL_RUN([sys.executable, str(REVIEW), *args], text=True, capture_output=True,
                        check=False, env={"PATH": os.environ["PATH"], **env},
                        timeout=ENTRY_POINT_DEADLINE_SECONDS)

    def test_documented_command_posts_a_marked_comment(self):
        with tempfile.TemporaryDirectory(prefix="review-stand-ins-") as temp:
            bin_dir = Path(temp)
            script = bin_dir / "stand-in.py"
            script.write_text(STAND_IN.format(python=sys.executable, head=HEAD))
            for name in ("git", "gh", "claude"):
                (bin_dir / name).symlink_to(script)
            script.chmod(0o755)
            env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                   "GH_TOKEN": "github-secret", "CLAUDE_CODE_OAUTH_TOKEN": "subscription-secret"}
            result = self.run_main(["o", "r", "1", "main"], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual((summary["state"], summary["head_sha"], summary["findings"]), ("posted", HEAD, 0))
            entries = [json.loads(line) for line in (bin_dir / "log.jsonl").read_text().splitlines()]
            posts = [entry["post"] for entry in entries if "post" in entry]
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0]["event"], "COMMENT")
            self.assertEqual(classify({"user": {"login": LOGIN}, "body": posts[0]["body"]}), "claude-quality")
            claude_env = next(entry["claude_env"] for entry in entries if "claude_env" in entry)
            self.assertNotIn("GH_TOKEN", claude_env)

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
