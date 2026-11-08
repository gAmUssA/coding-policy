#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/rex-team.sh.
#
# The script only maps REX_SESSION/REX_BLOCK onto the Herdr team environment
# and prints it, so each case runs the real script and parses its stdout and
# stderr with jq. No Rex server is contacted.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Inside Rex      -> exit 0, the env object on stdout, nothing on stderr.
#   2. Escaping        -> quotes, backslashes and control bytes stay valid JSON
#                         and round-trip exactly.
#   3. Launch line     -> the one-liner in references/herdr.md starts a command
#                         with exactly that environment, hostile ids included.
#   4. Outside Rex     -> exit 1, `not_in_rex` envelope, empty stdout, for
#                         either variable missing; the launch line starts nothing.
#   5. Usage           -> any argument is exit 2, `usage_error` envelope.
#   6. Missing adapter -> exit 1, `setup_error` envelope naming the reinstall.
#
# Run: bash skills/herdr-teamlead/tests/test_rex_team.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Runs the script with the given env assignments and arguments; sets OUT, ERR, RC.
run() { # <script> [VAR=value...] -- [args...]
  local script="$1"; shift
  local -a assigns=()
  while (( $# )) && [[ "$1" != "--" ]]; do assigns+=("$1"); shift; done
  [[ "${1:-}" == "--" ]] && shift
  OUT="$(env -u REX_SESSION -u REX_BLOCK "${assigns[@]}" bash "$script" "$@" 2>"$TMP/err")"; RC=$?
  ERR="$(cat "$TMP/err")" || die "could not read $TMP/err"
}

envelope_code() { printf '%s' "$1" | jq -er '.error.code' 2>/dev/null; }

# The launch line documented in references/herdr.md Running on Rex, verbatim.
# LAUNCH_LINE is checked against the reference; a fake `claude` on PATH
# records the environment it was started with.
# shellcheck disable=SC2016 # the line is shell source, expanded later by bash -c
LAUNCH_LINE='TEAM_ENV="$(bash "$CP/skills/herdr-teamlead/rex-team.sh" | jq -er '"'"'.env | to_entries | map("\(.key)=\(.value)" | @sh) | join(" ")'"'"')" && eval "env $TEAM_ENV claude"'

launch() { # <CP> <REX_SESSION> <REX_BLOCK-or-empty> <capture-file>
  local -a assigns=(REX_SESSION="$2")
  [[ -n "$3" ]] && assigns+=(REX_BLOCK="$3")
  env -u REX_SESSION -u REX_BLOCK "${assigns[@]}" CP="$1" CAPTURE="$4" PATH="$TMP/bin:$PATH" bash -c "$LAUNCH_LINE"
}

main() {
  local skill plugin script
  skill="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  plugin="$(cd "$skill/../.." && pwd)"
  script="${skill}/rex-team.sh"
  [[ -f "$script" && -r "$script" ]] || die "rex-team.sh not found/readable at $script"
  command -v jq >/dev/null 2>&1 || die "jq is required by this harness"

  TMP="$(mktemp -d -t rex-team-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  mkdir -p "$TMP/bin" || die "could not create $TMP/bin"
  # shellcheck disable=SC2016 # $CAPTURE expands inside the fake, not here
  printf '#!/usr/bin/env bash\nenv > "$CAPTURE"\n' > "$TMP/bin/claude" || die "could not write the fake claude"
  chmod +x "$TMP/bin/claude" || die "could not chmod the fake claude"

  FAIL=0; PASS=0
  local expected

  # 1. Inside Rex: the env object, nothing else.
  run "$script" REX_SESSION=session:abc REX_BLOCK=block:lead
  expected="$(jq -nc --arg a "${skill}/rex-herdr.sh" '{env:{HERDR_ENV:"1",HERDR_PANE_ID:"block:lead",HERDR_WORKSPACE_ID:"session:abc",HERDR_BIN:$a,TEAMLEAD_HERDR_BIN:$a,TEAMLEAD_BACKEND:"rex"}}')"
  if [[ $RC -eq 0 && -z "$ERR" ]] && [[ "$(printf '%s' "$OUT" | jq -c -S .)" == "$(printf '%s' "$expected" | jq -c -S .)" ]]; then
    pass; else fail "inside Rex: expected exit 0 and the env object, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. Hostile ids stay valid JSON and round-trip byte for byte.
  local hostile=$'block:"q\\ \x01\ttab;$(touch x)'
  run "$script" REX_SESSION=session:abc REX_BLOCK="$hostile"
  if [[ $RC -eq 0 ]] && [[ "$(printf '%s' "$OUT" | jq -r '.env.HERDR_PANE_ID')" == "$hostile" ]]; then
    pass; else fail "escaping: expected the hostile id to round-trip, got RC=$RC OUT=$OUT"; fi

  # 3. The documented launch line starts the command with exactly that env.
  local capture="$TMP/started"
  launch "$plugin" session:abc "$hostile" "$capture"
  if [[ -s "$capture" ]] && grep -qxF "HERDR_ENV=1" "$capture" && grep -qxF "HERDR_PANE_ID=${hostile}" "$capture" \
     && grep -qxF "HERDR_WORKSPACE_ID=session:abc" "$capture" && grep -qxF "TEAMLEAD_BACKEND=rex" "$capture" \
     && grep -qxF "HERDR_BIN=${skill}/rex-herdr.sh" "$capture" && [[ ! -e "$PWD/x" ]]; then
    pass; else fail "launch line: expected the team env in the started command, got $(cat "$capture" 2>/dev/null)"; fi

  # The reference documents exactly the line exercised above.
  if grep -qF -- "$LAUNCH_LINE" "${skill}/references/herdr.md"; then
    pass; else fail "launch line: references/herdr.md does not carry the tested line verbatim"; fi

  # 4. Outside Rex: the envelope, nothing on stdout, nothing started.
  local kept
  for kept in REX_SESSION=session:abc REX_BLOCK=block:lead; do
    run "$script" "$kept"
    if [[ $RC -eq 1 && -z "$OUT" && "$(envelope_code "$ERR")" == not_in_rex ]] && printf '%s' "$ERR" | grep -q "run this from a Rex terminal"; then
      pass; else fail "outside Rex (only $kept): expected exit 1 and not_in_rex, got RC=$RC OUT=$OUT ERR=$ERR"; fi
  done
  rm -f "$capture" || die "could not remove $capture"
  launch "$plugin" session:abc "" "$capture" 2>/dev/null
  if [[ ! -s "$capture" ]]; then pass; else fail "launch line outside Rex: expected nothing started, got $(cat "$capture")"; fi

  # 5. Any argument is a usage error.
  local arg
  for arg in claude --help; do
    run "$script" REX_SESSION=session:abc REX_BLOCK=block:lead -- "$arg"
    if [[ $RC -eq 2 && -z "$OUT" && "$(envelope_code "$ERR")" == usage_error ]]; then
      pass; else fail "usage ($arg): expected exit 2 and usage_error, got RC=$RC OUT=$OUT ERR=$ERR"; fi
  done

  # 6. A script copied without its adapter refuses.
  mkdir -p "$TMP/bare" || die "could not create $TMP/bare"
  cp "$script" "$TMP/bare/rex-team.sh" || die "could not copy the script"
  run "$TMP/bare/rex-team.sh" REX_SESSION=session:abc REX_BLOCK=block:lead
  if [[ $RC -eq 1 && -z "$OUT" && "$(envelope_code "$ERR")" == setup_error ]] && printf '%s' "$ERR" | grep -q "tessl install gamussa/coding-policy"; then
    pass; else fail "missing adapter: expected exit 1 and setup_error, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
