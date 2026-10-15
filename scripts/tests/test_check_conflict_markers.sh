#!/usr/bin/env bash
# Outcome-based tests for check-conflict-markers.sh.
#
# Each case builds a throwaway git repo whose tracked files carry the case's
# content, then runs the gate against it. Marker lines are assembled at run
# time, so this file holds none at column 0.
#
# Covers:
#   1. clean tree with a setext `=======` underline -> exit 0, clean true
#   2. full diff3 conflict                          -> exit 1, start/base/end flagged, separator not
#   3. lone end marker in a CHANGELOG               -> exit 1, path + line reported
#   4. eight `<` or a marker glued to text          -> exit 0 (not git's marker format)
#   5. marker in an untracked file                  -> exit 0 (tracked files only)
#   6. marker in a binary file                      -> exit 0 (binary skipped)
#   7. path with a space                            -> reported intact
#   8. not a git checkout                           -> exit 2, empty stdout
#   9. this repository                              -> exit 0
#
# Run: bash scripts/tests/test_check_conflict_markers.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-conflict-markers.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "fatal: git required" >&2; exit 2; }

# Hermetic git: no user/system config.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

LT="$(printf '<%.0s' 1 2 3 4 5 6 7)"
GT="$(printf '>%.0s' 1 2 3 4 5 6 7)"
BAR="$(printf '|%.0s' 1 2 3 4 5 6 7)"
EQ="$(printf '=%.0s' 1 2 3 4 5 6 7)"

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# repo <dir>: init an empty repo.
repo() {
  if ! { mkdir -p "$1" && git -C "$1" init -q; }; then echo "fatal: git init $1" >&2; exit 2; fi
}

# track <dir>: stage every file so git ls-files sees it.
track() {
  git -C "$1" add -A || { echo "fatal: git add in $1" >&2; exit 2; }
}

# run <dir|""> -> OUT, ERR, RC
run() {
  OUT="$(bash "$SCRIPT" ${1:+"$1"} 2>"$TMP/err")"; RC=$?
  ERR="$(cat "$TMP/err")"
}

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT

  # 1. setext underline only
  repo "$TMP/1"; printf 'Title\n%s\n\ntext\n' "$EQ" > "$TMP/1/README.md"; track "$TMP/1"
  run "$TMP/1"
  if [[ $RC -eq 0 ]] && jq -e '.clean == true and .markers == []' <<<"$OUT" >/dev/null; then pass
  else fail "setext: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. full diff3 conflict
  repo "$TMP/2"
  printf 'a\n%s HEAD\nours\n%s base\nbase\n%s\ntheirs\n%s feat/x\nz\n' "$LT" "$BAR" "$EQ" "$GT" > "$TMP/2/f.md"; track "$TMP/2"
  run "$TMP/2"
  if [[ $RC -eq 1 ]] && jq -e '.clean == false and ([.markers[].line] == [2,4,8]) and ([.markers[].path] | unique == ["f.md"])' <<<"$OUT" >/dev/null \
     && grep -q 'f.md:2, f.md:4, f.md:8' <<<"$ERR"; then pass
  else fail "diff3: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. lone end marker left in a CHANGELOG
  repo "$TMP/3"; printf '# Changelog\n\n- entry\n%s main\n' "$GT" > "$TMP/3/CHANGELOG.md"; track "$TMP/3"
  run "$TMP/3"
  if [[ $RC -eq 1 ]] && jq -e --arg t "$GT main" '.markers == [{"path":"CHANGELOG.md","line":4,"text":$t}]' <<<"$OUT" >/dev/null; then pass
  else fail "lone end marker: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 4. not git's marker format: eight chars, glued text, indented
  repo "$TMP/4"; printf '%s<\n%sfoo\n  %s HEAD\n' "$LT" "$GT" "$LT" > "$TMP/4/f.txt"; track "$TMP/4"
  run "$TMP/4"
  if [[ $RC -eq 0 ]] && jq -e '.clean == true' <<<"$OUT" >/dev/null; then pass
  else fail "near-misses: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 5. untracked file
  repo "$TMP/5"; printf 'ok\n' > "$TMP/5/a.md"; track "$TMP/5"; printf '%s HEAD\n' "$LT" > "$TMP/5/untracked.md"
  run "$TMP/5"
  if [[ $RC -eq 0 ]] && jq -e '.clean == true' <<<"$OUT" >/dev/null; then pass
  else fail "untracked: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 6. binary file
  repo "$TMP/6"; printf '\000bin\n%s HEAD\n' "$LT" > "$TMP/6/blob.bin"; track "$TMP/6"
  run "$TMP/6"
  if [[ $RC -eq 0 ]] && jq -e '.clean == true' <<<"$OUT" >/dev/null; then pass
  else fail "binary: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 7. path with a space
  repo "$TMP/7"; mkdir -p "$TMP/7/my dir"; printf '%s\n' "$BAR" > "$TMP/7/my dir/n.md"; track "$TMP/7"
  run "$TMP/7"
  if [[ $RC -eq 1 ]] && jq -e '.markers[0].path == "my dir/n.md" and .markers[0].line == 1' <<<"$OUT" >/dev/null; then pass
  else fail "space path: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 8. not a git checkout
  mkdir -p "$TMP/8"
  run "$TMP/8"
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q 'not a git checkout' <<<"$ERR"; then pass
  else fail "not a checkout: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 9. this repository
  run ""
  if [[ $RC -eq 0 ]] && jq -e '.clean == true' <<<"$OUT" >/dev/null; then pass
  else fail "this repo: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
