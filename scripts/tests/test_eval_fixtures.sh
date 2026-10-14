#!/usr/bin/env bash
# Outcome-based tests for eval-fixtures.sh.
#
#   1. export then import rebuilds the same refs, HEAD and a clean tree
#   2. import leaves an existing .git alone (idempotent)
#   3. import refuses a fixture whose committed files differ from its history
#   4. export refuses a fixture with no .git
#   5. a manifest head ref outside refs/heads/ is refused
#   6. bad usage exits 2
#
# Run: bash scripts/tests/test_eval_fixtures.sh

set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/eval-fixtures.sh"
[[ -r "$SCRIPT" ]] || { echo "fatal: eval-fixtures.sh not found at $SCRIPT" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

G=(git -c user.name=fixture -c user.email=fixture@example.com -c commit.gpgsign=false)

# root <dir>: a repo root holding one fixture at evals/s/inputs/repo with two
# branches, HEAD on the feature branch, and its manifest row.
root() {
  local d="$1" r="$1/evals/s/inputs/repo"
  mkdir -p "$r" || die "mkdir $r"
  { "${G[@]}" -C "$r" init -q -b main && printf 'base\n' > "$r/a.txt" && "${G[@]}" -C "$r" add -A \
    && GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z "${G[@]}" -C "$r" commit -qm base \
    && "${G[@]}" -C "$r" checkout -qb feat/x && printf 'feature\n' >> "$r/a.txt" && "${G[@]}" -C "$r" add -A \
    && GIT_AUTHOR_DATE=2026-01-02T00:00:00Z GIT_COMMITTER_DATE=2026-01-02T00:00:00Z "${G[@]}" -C "$r" commit -qm feature; } \
    || die "fixture repo in $r"
  printf 'evals/s/inputs/repo\trefs/heads/feat/x\tevals/s/repo.stream\n' > "$d/evals/git-fixtures.tsv" || die "manifest"
}

run() { OUT="$(cd "$1" && bash "$SCRIPT" "$2" 2>"$1.err")"; RC=$?; ERR="$(cat "$1.err")"; }
refs() { git -C "$1" for-each-ref --format='%(refname) %(objectname)' | LC_ALL=C sort; }

main() {
  TMP="$(mktemp -d)" || die "mktemp"
  trap cleanup EXIT
  local before after

  # 1. round trip
  root "$TMP/1"; before="$(refs "$TMP/1/evals/s/inputs/repo")"
  run "$TMP/1" export
  rm -rf "$TMP/1/evals/s/inputs/repo/.git" || die "rm .git"
  run "$TMP/1" import
  after="$(refs "$TMP/1/evals/s/inputs/repo")"
  if [[ $RC -eq 0 && "$before" == "$after" && "$(git -C "$TMP/1/evals/s/inputs/repo" symbolic-ref HEAD)" == "refs/heads/feat/x" \
        && -z "$(git -C "$TMP/1/evals/s/inputs/repo" status --porcelain)" ]] \
     && jq -e '.fixtures[0].action == "imported"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "round trip: RC=$RC OUT=$OUT ERR=$ERR before=$before after=$after"; fi

  # 2. idempotent import
  run "$TMP/1" import
  if [[ $RC -eq 0 ]] && jq -e '.fixtures[0].action == "present"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "idempotent: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. committed files drifted from the stored history
  root "$TMP/3"; run "$TMP/3" export
  rm -rf "$TMP/3/evals/s/inputs/repo/.git" || die "rm .git"
  printf 'drift\n' >> "$TMP/3/evals/s/inputs/repo/a.txt" || die "drift"
  run "$TMP/3" import
  if [[ $RC -eq 1 ]] && grep -q 'differs from refs/heads/feat/x' <<<"$ERR"; then pass
  else fail "drift: RC=$RC ERR=$ERR"; fi

  # 4. export without .git
  root "$TMP/4"; rm -rf "$TMP/4/evals/s/inputs/repo/.git" || die "rm .git"
  run "$TMP/4" export
  if [[ $RC -eq 1 ]] && grep -q '.git is missing' <<<"$ERR"; then pass
  else fail "export without .git: RC=$RC ERR=$ERR"; fi

  # 5. head ref outside refs/heads
  root "$TMP/5"; printf 'evals/s/inputs/repo\tfeat/x\tevals/s/repo.stream\n' > "$TMP/5/evals/git-fixtures.tsv" || die "manifest"
  run "$TMP/5" export
  if [[ $RC -eq 2 ]] && grep -q 'must be refs/heads/' <<<"$ERR"; then pass
  else fail "bad head ref: RC=$RC ERR=$ERR"; fi

  # 6. usage
  run "$TMP/1" frobnicate
  if [[ $RC -eq 2 ]] && grep -q 'usage' <<<"$ERR"; then pass
  else fail "usage: RC=$RC ERR=$ERR"; fi

  echo "─────────────────────────────────────────────" >&2
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
