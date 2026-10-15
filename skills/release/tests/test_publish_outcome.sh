#!/usr/bin/env bash
# Outcome-based tests for publish-outcome.sh.
#
# Covers the answers the script promises:
#   1. run success + Publish success  -> published, rc 0
#   2. run success + Publish skipped  -> no_publication, rc 0
#   3. run failure + Publish skipped  -> failed, rc 1 (an earlier step failed;
#      never read as no publication)
#   4. run success + Publish failure  -> failed, rc 1
#   5. no Publish step                -> ambiguous, rc 1
#   6. two Publish steps              -> ambiguous, rc 1
#   7. run still in progress          -> rc 2, empty stdout
#   8. gh fails                       -> rc 2, empty stdout
#   9. unparseable payload            -> rc 2, empty stdout
#  10. bad arguments                  -> rc 2
#
# Approach: GH_BIN points at a fake gh that prints the fixture payload in
# FAKE_GH_PAYLOAD and exits FAKE_GH_RC — the process boundary, nothing of the
# script itself is replaced.
#
# Run: bash skills/release/tests/test_publish_outcome.sh

set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$(cd "$HERE/.." && pwd)/publish-outcome.sh"
# shellcheck source=skills/release/tests/gh_pin_gate.sh
source "$HERE/gh_pin_gate.sh" || { echo "fatal: cannot source gh_pin_gate.sh" >&2; exit 2; }
[[ -r "$SCRIPT" ]] || { echo "fatal: publish-outcome.sh not found at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq is required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# payload <status> <conclusion> <step-conclusion>...: a run view payload with
# one job whose steps are Lint plus one Publish step per extra argument.
payload() {
  local status="$1" conclusion="$2"; shift 2
  local steps='[{"name":"Lint","conclusion":"success"}' s
  for s in "$@"; do steps+=",{\"name\":\"Publish\",\"conclusion\":\"${s}\"}"; done
  steps+="]"
  printf '{"status":"%s","conclusion":"%s","jobs":[{"steps":%s}]}' "$status" "$conclusion" "$steps"
}

# run <payload> [gh-rc] [args...] -> OUT, ERR, RC
run() {
  local p="$1" grc="${2:-0}"; shift 2 || shift $#
  local args=("$@"); (( ${#args[@]} )) || args=(gAmUssA coding-policy 42)
  OUT="$(GH_BIN="$TMP/gh" FAKE_GH_PAYLOAD="$p" FAKE_GH_RC="$grc" bash "$SCRIPT" "${args[@]}" 2>"$TMP/err")"; RC=$?
  ERR="$(cat "$TMP/err")"
}

outcome_is() { jq -e --arg o "$1" '.outcome == $o' <<<"$OUT" >/dev/null 2>&1; }

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  # The fake passes every call through the github.com host pin gate first
  # (gh_pin_gate.sh), with GH_HOST pointing at another server.
  # shellcheck disable=SC2016 # the fake's variables expand when the fake runs
  printf '#!/usr/bin/env bash\nsource %q || exit 2\nfake() { printf "%%s" "$FAKE_GH_PAYLOAD"; exit "${FAKE_GH_RC:-0}"; }\ngh_pin_gate fake "$@"\n' \
    "$HERE/gh_pin_gate.sh" > "$TMP/gh" \
    || { echo "fatal: cannot write the fake gh" >&2; exit 2; }
  chmod +x "$TMP/gh" || { echo "fatal: chmod fake gh" >&2; exit 2; }

  run "$(payload completed success success)" 0
  if [[ $RC -eq 0 ]] && outcome_is published; then pass; else fail "published: RC=$RC OUT=$OUT"; fi

  run "$(payload completed success skipped)" 0
  if [[ $RC -eq 0 ]] && outcome_is no_publication; then pass; else fail "no publication: RC=$RC OUT=$OUT"; fi

  run "$(payload completed failure skipped)" 0
  if [[ $RC -eq 1 ]] && outcome_is failed && grep -q "concluded 'failure'" <<<"$ERR" && gh_hints_pinned "$ERR"; then pass
  else fail "failed run, skipped publish: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "$(payload completed success failure)" 0
  if [[ $RC -eq 1 ]] && outcome_is failed && gh_hints_pinned "$ERR"; then pass; else fail "publish step failed: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "$(payload completed success)" 0
  if [[ $RC -eq 1 ]] && outcome_is ambiguous && grep -q "0 steps named 'Publish'" <<<"$ERR"; then pass
  else fail "no publish step: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "$(payload completed success success skipped)" 0
  if [[ $RC -eq 1 ]] && outcome_is ambiguous; then pass; else fail "two publish steps: RC=$RC OUT=$OUT"; fi

  run "$(payload in_progress "" skipped)" 0
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "not completed" <<<"$ERR" && gh_hints_pinned "$ERR"; then pass
  else fail "in flight: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "" 1
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "gh run view 42 --repo github.com/gAmUssA/coding-policy failed" <<<"$ERR" && gh_hints_pinned "$ERR"; then pass
  else fail "gh failure: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "not json" 0
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "unparseable" <<<"$ERR" && gh_hints_pinned "$ERR"; then pass
  else fail "unparseable: RC=$RC OUT=$OUT ERR=$ERR"; fi

  run "$(payload completed success success)" 0 gAmUssA coding-policy abc
  if [[ $RC -eq 2 && -z "$OUT" ]]; then pass; else fail "bad run id: RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
