#!/usr/bin/env bash
# Outcome-based tests for origin-repo.sh.
#
# Each case builds a throwaway git repo whose `origin` carries the case's URLs;
# nothing is fetched, so no network is touched.
#
# Covers:
#   1. https, ssh, scp, .git and trailing-slash forms -> owner/name resolved
#   2. userinfo in an https URL                       -> resolved, URL never echoed
#   3. non-GitHub host                                -> exit 1
#   4. GitHub URL with an extra path segment          -> exit 1
#   5. push URL naming a different repository         -> exit 1
#   6. push URL differing only in case                -> exit 0, fetch spelling kept
#   7. no origin remote                               -> exit 2
#   8. not a git checkout                             -> exit 2
#
# Run: bash skills/release/tests/test_origin_repo.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/origin-repo.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "fatal: git required" >&2; exit 2; }

# Hermetic git: no user/system config (no insteadOf rewrites).
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

PASS=0; FAIL=0; N=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# repo_with <fetch-url|""> [push-url]: a fresh repo; sets D.
repo_with() {
  N=$((N+1)); D="$TMP/r$N"
  if ! { mkdir -p "$D" && git -C "$D" init -q; }; then echo "fatal: git init $D" >&2; exit 2; fi
  if [[ -n "$1" ]]; then
    git -C "$D" remote add origin "$1" || { echo "fatal: remote add in $D" >&2; exit 2; }
  fi
  if [[ -n "${2:-}" ]]; then
    git -C "$D" remote set-url --push origin "$2" || { echo "fatal: set push url in $D" >&2; exit 2; }
  fi
}

# run <dir> -> OUT, ERR, RC
run() {
  OUT="$(bash "$SCRIPT" "$1" 2>"$TMP/err")"; RC=$?
  ERR="$(cat "$TMP/err")"
}

# expect_repo <url> <owner/name>
expect_repo() {
  repo_with "$1"; run "$D"
  if [[ $RC -eq 0 ]] && jq -e --arg r "$2" '.repo == $r and (.owner + "/" + .name) == $r' <<<"$OUT" >/dev/null; then pass
  else fail "url $1: RC=$RC OUT=$OUT ERR=$ERR"; fi
}

# expect_rc <rc> <label>
expect_rc() {
  if [[ $RC -eq $1 && -z "$OUT" ]]; then pass
  else fail "$2: RC=$RC OUT=$OUT ERR=$ERR"; fi
}

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT

  # 1. accepted forms
  expect_repo https://github.com/acme/widgets.git acme/widgets
  expect_repo https://github.com/acme/widgets/ acme/widgets
  expect_repo ssh://git@github.com:22/acme/my.repo acme/my.repo
  expect_repo git@github.com:Acme-Co/widgets.git Acme-Co/widgets

  # 2. userinfo is accepted and never echoed
  repo_with "https://user:s3cret@github.com/acme/widgets.git"; run "$D"
  if [[ $RC -eq 0 ]] && jq -e '.repo == "acme/widgets"' <<<"$OUT" >/dev/null && [[ "$OUT$ERR" != *s3cret* ]]; then pass
  else fail "userinfo: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. non-GitHub host
  repo_with https://gitlab.com/acme/widgets.git; run "$D"
  expect_rc 1 "gitlab host"
  if [[ "$ERR" == *"not a GitHub repository URL"* ]]; then pass; else fail "gitlab diagnostic: $ERR"; fi

  # 4. extra path segment
  repo_with https://github.com/acme/widgets/tree/main; run "$D"
  expect_rc 1 "extra path"

  # 5. push URL names another repository (a fork pushing upstream)
  repo_with git@github.com:me/widgets.git git@github.com:acme/widgets.git; run "$D"
  expect_rc 1 "push mismatch"
  if [[ "$ERR" == *"different repository"* ]]; then pass; else fail "push mismatch diagnostic: $ERR"; fi

  # 6. push URL differs only in case
  repo_with git@github.com:Acme/Widgets.git https://github.com/acme/widgets; run "$D"
  if [[ $RC -eq 0 ]] && jq -e '.repo == "Acme/Widgets"' <<<"$OUT" >/dev/null; then pass
  else fail "case-insensitive push: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 7. no origin
  repo_with ""; run "$D"
  expect_rc 2 "no origin"

  # 8. not a checkout
  mkdir -p "$TMP/plain"; run "$TMP/plain"
  expect_rc 2 "not a checkout"

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
