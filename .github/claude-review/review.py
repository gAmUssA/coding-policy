#!/usr/bin/env python3
"""Post a subscription-backed, context-isolated Claude code review on one PR.

Usage: review.py OWNER REPO PR BASE
Env:   GH_TOKEN (the workflow token) and CLAUDE_CODE_OAUTH_TOKEN (the
       subscription secret, seeded with `claude setup-token`).
Out:   one JSON object on stdout:
         {"state": "posted", "reviewer": "claude", "head_sha", "findings",
          "blocking", "advisory", "url"}
Exit:  0 posted; 1 the review failed and nothing was posted, with an actionable
       stderr diagnostic; 2 usage or missing environment.

The model receives only its subscription token, a replacement system prompt
and a throwaway source checkout. No policy installation, GitHub token,
settings, history or execution tools enter its session (check-isolation.py
proves this against the real pinned CLI). The verdict is bound to the exact
analyzed head; a failed, invalid or stale review fails visibly without posting.

Lane contract (docs/adr/0010-review-lanes.md): the body opens with the
claude-quality marker from skills/release/review-lanes.sh, and the review is
always a COMMENT. The lane shares `github-actions[bot]` with the Codex policy
reviewer, and GitHub's own merge gate keys CHANGES_REQUESTED on that login, so
a Claude REQUEST_CHANGES would block GitHub-side while the lane is advisory.
The script refuses to run once the lane is marked gating: promotion needs the
dedicated GitHub App identity the ADR names.
"""

import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
LANE = "claude-quality"
# Model and effort are pinned so the review does not drift with the CLI's
# default. claude-opus-5-5 is Claude's top model
# (skills/herdr-teamlead/teamlead/tiers.py TOP_MODELS). Renovate does not track
# this pin: renew it by hand whenever TOP_MODELS["claude"] changes, and
# re-check it at each @anthropic-ai/claude-code bump
# (rules/dependency-management.md Freshness).
MODEL = "claude-opus-5-5"
EFFORT = "high"
SHARED_LOGIN = "github-actions[bot]"


class ReviewError(Exception):
    """An actionable failure safe to print without credentials or model output."""


def claude_failure_hint(stdout: str, stderr: str) -> str:
    """Return fixed diagnostic text only; CLI output may carry protected data."""
    text = (stdout + "\n" + stderr).casefold().replace("’", "'")
    signatures = (
        (("authentication_error", "invalid authentication credentials", "oauth token has expired",
          "token has expired", "401 unauthorized", "invalid api key", "not logged in",
          "please run /login", "api error: 401"),
         "authentication rejected; re-seed CLAUDE_CODE_OAUTH_TOKEN with `claude setup-token`"),
        (("you've hit your limit", "you have hit your limit", "usage limit reached"),
         "subscription usage limit reached; wait for its reset, then re-run the review"),
        (("rate_limit_error", "api error: 429"),
         "provider rate limit; wait, then re-run the review"),
        (("model_not_found", "invalid model", "does not exist or you do not have access"),
         "model unavailable; check the pinned MODEL in .github/claude-review/review.py"),
        (("unknown option", "unknown argument", "unrecognized option"),
         "CLI argument rejected; check the pinned CLI version's supported options"),
        (("econnreset", "econnrefused", "etimedout", "enotfound", "certificate verify failed"),
         "network or TLS failure; re-run the review"),
    )
    for patterns, hint in signatures:
        if any(pattern in text for pattern in patterns):
            return hint
    return "unclassified CLI failure; check the CLAUDE_CODE_OAUTH_TOKEN secret and the CLI install step"


def run(args: list[str], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, data: str | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, input=data, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        # Child output can contain credentials or prompt-injected source text.
        if args[0] == "claude":
            raise ReviewError(f"claude failed (exit {result.returncode}); "
                              + claude_failure_hint(result.stdout, result.stderr))
        raise ReviewError(f"{args[0]} failed (exit {result.returncode}); "
                          "check CI authentication, permissions and CLI availability")
    return result.stdout


def api(endpoint: str, payload: dict | None = None) -> dict:
    # ADR 0008: pin the host so GH_HOST can never redirect a read or a post.
    args = ["gh", "api", "--hostname", "github.com", endpoint]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    value = json.loads(run(args, data=json.dumps(payload) if payload is not None else None))
    if not isinstance(value, dict):
        raise ReviewError("GitHub API returned a non-object response; re-run the review")
    return value


def lane_config() -> dict:
    lanes = json.loads(run(["bash", str(REPO_ROOT / "skills/release/review-lanes.sh")]))
    lane = lanes.get(LANE) if isinstance(lanes, dict) else None
    if (not isinstance(lane, dict) or not isinstance(lane.get("marker"), str) or not lane["marker"]
            or not isinstance(lane.get("logins"), list) or type(lane.get("gating")) is not bool):
        raise ReviewError(f"skills/release/review-lanes.sh has no usable {LANE} lane; fix its config")
    if SHARED_LOGIN not in lane["logins"]:
        raise ReviewError(f"the {LANE} lane does not list {SHARED_LOGIN}; this workflow posts as that login")
    if lane["gating"]:
        raise ReviewError(f"the {LANE} lane is gating, which needs a dedicated GitHub App "
                          "identity (docs/adr/0010-review-lanes.md); set up the App before promoting")
    return lane


def validate_review(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"summary", "findings"}:
        raise ReviewError("Claude returned an invalid review object; re-run the review")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise ReviewError("Claude returned an empty summary; re-run the review")
    if not isinstance(value["findings"], list):
        raise ReviewError("Claude returned invalid findings; re-run the review")
    for finding in value["findings"]:
        if not isinstance(finding, dict) or set(finding) != {"path", "line", "severity", "message"}:
            raise ReviewError("Claude returned a malformed finding; re-run the review")
        path, line = finding["path"], finding["line"]
        if (not isinstance(path, str) or not path or "\\" in path
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
                or type(line) is not int or line < 1
                or finding["severity"] not in ("blocking", "advisory")
                or not isinstance(finding["message"], str) or not finding["message"].strip()):
            raise ReviewError("Claude returned an invalid finding location or text; re-run the review")
    return value


def claude_environment(config: Path) -> dict[str, str]:
    # Allowlist instead of removing a growing list of provider/CI credentials.
    allowed = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(CLAUDE_CONFIG_DIR=str(config),
               CLAUDE_CODE_OAUTH_TOKEN=os.environ["CLAUDE_CODE_OAUTH_TOKEN"],
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return env


def claude_command() -> list[str]:
    """One command owner, exercised by the real-CLI isolation probe too."""
    return [
        "claude", "--print", "--safe-mode", "--restricted", "--setting-sources", "",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands", "--no-session-persistence",
        "--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep",
        "--system-prompt", (HERE / "system-prompt.md").read_text(),
        "--model", MODEL, "--effort", EFFORT, "--output-format", "json",
        "--json-schema", (HERE / "schema.json").read_text(),
    ]


def render_body(marker: str, head: str, review: dict) -> str:
    body = f"{marker}\n## Claude code review\n\n{review['summary']}\n\n"
    body += f"Reviewed commit: {head}. Code quality and bugs only; advisory lane.\n"
    for severity in ("blocking", "advisory"):
        findings = [finding for finding in review["findings"] if finding["severity"] == severity]
        if findings:
            body += f"\n## {severity.capitalize()} findings\n"
            for finding in findings:
                body += f"\n- {finding['path']}:{finding['line']} — {finding['message']}\n"
    return body


def review_pr(owner: str, repo: str, pr: str, base: str) -> dict:
    lane = lane_config()
    endpoint = f"repos/{owner}/{repo}/pulls/{pr}"
    current = api(endpoint)
    if current["state"] != "open" or current["head"]["repo"]["full_name"] != f"{owner}/{repo}":
        raise ReviewError("review requires an open same-repository PR; adopt fork PRs first")
    if current["base"]["ref"] != base:
        raise ReviewError("PR base changed; re-run the review against its current base")
    head = current["head"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ReviewError("GitHub returned an invalid PR head SHA")

    with tempfile.TemporaryDirectory(prefix="claude-quality-") as temp:
        root = Path(temp)
        source, config = root / "source", root / "config"
        config.mkdir(mode=0o700)
        # Transient git configuration: never persist credentials in the clone.
        git_env = os.environ.copy()
        header = base64.b64encode(f"x-access-token:{os.environ['GH_TOKEN']}".encode()).decode()
        git_env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                       GIT_CONFIG_VALUE_0=f"Authorization: Basic {header}")
        run(["git", "clone", "--quiet", "--no-checkout", "--no-tags",
             f"https://github.com/{owner}/{repo}.git", str(source)], env=git_env)
        run(["git", "fetch", "--quiet", "origin", f"refs/pull/{pr}/head"], cwd=source, env=git_env)
        fetched = run(["git", "rev-parse", "FETCH_HEAD"], cwd=source).strip()
        if fetched != head:
            raise ReviewError("PR head changed during fetch; re-run against the new head")
        run(["git", "checkout", "--quiet", "--detach", head], cwd=source)
        diff = run(["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                    f"origin/{base}...{head}"], cwd=source)
        prompt = f"Review {owner}/{repo} PR #{pr}, exact head {head}, base {base}.\nDiff:\n{diff}"
        result = run(claude_command(), cwd=source, env=claude_environment(config), data=prompt)
        for secret in (os.environ["CLAUDE_CODE_OAUTH_TOKEN"], os.environ["GH_TOKEN"], header):
            if secret in result:
                raise ReviewError("Claude output contained credential material; refusing to post")
        envelope = json.loads(result)
        if (not isinstance(envelope, dict) or envelope.get("is_error") is not False
                or envelope.get("subtype") != "success"):
            raise ReviewError("Claude did not complete a successful review; check subscription access, then re-run")
        review = validate_review(envelope.get("structured_output"))

    latest = api(endpoint)
    if latest["state"] != "open" or latest["head"]["sha"] != head or latest["base"]["ref"] != base:
        raise ReviewError("PR changed during review; refusing to post a stale verdict; re-run")
    body = render_body(lane["marker"], head, review)
    posted = api(endpoint + "/reviews", {"commit_id": head, "event": "COMMENT", "body": body})
    if (posted.get("state") != "COMMENTED" or posted.get("commit_id") != head
            or posted.get("user", {}).get("login") not in lane["logins"]):
        raise ReviewError("GitHub did not confirm the posted review at the analyzed head; inspect PR reviews")
    blocking = sum(1 for finding in review["findings"] if finding["severity"] == "blocking")
    return {"state": "posted", "reviewer": "claude", "head_sha": head,
            "findings": len(review["findings"]), "blocking": blocking,
            "advisory": len(review["findings"]) - blocking, "url": posted.get("html_url")}


def main() -> int:
    if (len(sys.argv) != 5 or not all(re.fullmatch(r"[A-Za-z0-9._-]+", s) for s in sys.argv[1:3])
            or not sys.argv[3].isdigit() or not re.fullmatch(r"[A-Za-z0-9._/-]+", sys.argv[4])):
        print("usage: review.py OWNER REPO PR BASE", file=sys.stderr)
        return 2
    if not os.environ.get("GH_TOKEN") or not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("error: set GH_TOKEN to the workflow token and CLAUDE_CODE_OAUTH_TOKEN to the "
              "subscription secret (`claude setup-token`, then "
              "`gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo github.com/<owner>/<repo>`)", file=sys.stderr)
        return 2
    try:
        print(json.dumps(review_pr(*sys.argv[1:])))
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        # Parsing errors never echo the raw response or credentials.
        message = str(exc) if isinstance(exc, ReviewError) else (
            type(exc).__name__ + ": review failed; check the CLI/API response format and runner setup")
        print("error: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
