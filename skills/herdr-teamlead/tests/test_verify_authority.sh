#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/verify-authority.sh.
#
# Every case points GH_BIN at a fake this harness writes, replaying payloads
# built in the test (rules/testing-standards.md Fixtures — no network, no real
# GitHub session, no binary fixtures).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Own user repo     -> namespace_owner + authorized true.
#   2. Case-folded login -> logins compare case-insensitively.
#   3. Write collaborator-> permission write, authorized FALSE (not ownership).
#   4. Admin collaborator-> permission admin on someone else's repo, still false.
#   5. Org admin         -> authorized true through org membership.
#   6. Org member        -> role member is not ownership.
#   7. Org 404           -> not a member, not an owner, still a verdict, and
#                          SILENT: a non-member is the ordinary case.
#  7b. Org fault         -> a non-404 membership failure is exit 2 and NO
#                          verdict; a fault is not a denial.
#   8. Usage / bad slug  -> exit 1, no verdict.
#   9. gh absent         -> exit 1.
#  9b. gh not logged in  -> exit 1 naming `gh auth login`, before any API call.
#  10. API failure       -> exit 2, never a verdict.
#
# Run: bash skills/herdr-teamlead/tests/test_verify_authority.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_fake_gh() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake gh"
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-} ${2:-}" == "auth status" ]]; then
  [[ -n "${FAKE_AUTH_ERR:-}" ]] && { printf 'You are not logged into any GitHub hosts.\n' >&2; exit 1; }
  printf 'Logged in to github.com\n'; exit 0
fi
[[ "${1:-}" == "api" ]] || { echo '{"error":"unsupported"}' >&2; exit 2; }
[[ -n "${FAKE_ARGV_FILE:-}" ]] && printf '%s\n' "$*" >> "$FAKE_ARGV_FILE"
case "${2:-}" in
  user)
    [[ -n "${FAKE_USER_ERR:-}" ]] && { printf 'gh: auth required\n' >&2; exit 1; }
    printf '{"login":"%s"}\n' "${FAKE_VIEWER:-jbaruch}"
    ;;
  repos/*)
    [[ -n "${FAKE_REPO_ERR:-}" ]] && { printf 'gh: HTTP 404\n' >&2; exit 1; }
    printf '{"owner":{"login":"%s","type":"%s"},"permissions":{"admin":%s,"maintain":false,"push":%s,"triage":false,"pull":true}}\n' \
      "${FAKE_OWNER:-jbaruch}" "${FAKE_OWNER_TYPE:-User}" "${FAKE_ADMIN:-true}" "${FAKE_PUSH:-true}"
    ;;
  orgs/*/memberships/*)
    [[ -n "${FAKE_ORG_404:-}" ]] && { printf 'gh: HTTP 404: Not Found\n' >&2; exit 1; }
    [[ -n "${FAKE_ORG_FAULT:-}" ]] && { printf 'gh: HTTP 500: server error\n' >&2; exit 1; }
    printf '%s\n' "${FAKE_ORG_ROLE:-member}"
    ;;
  *) echo '{"error":"unsupported path"}' >&2; exit 2 ;;
esac
exit 0
FAKE
  chmod +x "$1" || die "could not chmod the fake gh"
}

run() { # [env...] -- <slug>
  local slug="${!#}"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env GH_BIN="$FAKE" "${@:1:$#-1}" bash "$SCRIPT" "$slug" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/verify-authority.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "verify-authority.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t teamlead-authority-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  FAKE="$TMP/gh"; mk_fake_gh "$FAKE"
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. The operator's own repo.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=jbaruch gamussa/coding-policy
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      .authorized == true and .namespace_owner == true
      and .repo == "gamussa/coding-policy" and .viewer_permission == "admin"' >/dev/null 2>&1; then
    pass; else fail "own repo: expected authorized true, got RC=$RC OUT=$OUT"; fi

  # 2. GitHub logins are case-insensitive.
  run FAKE_VIEWER=JBaruch FAKE_OWNER=jbaruch gamussa/coding-policy
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.authorized == true' >/dev/null 2>&1; then
    pass; else fail "case folding: expected authorized true, got OUT=$OUT"; fi

  # 3. Write access is not ownership (rules/external-repo-contributions.md).
  run FAKE_VIEWER=jbaruch FAKE_OWNER=someoneelse FAKE_ADMIN=false FAKE_PUSH=true someoneelse/thing
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      .authorized == false and .viewer_permission == "write"' >/dev/null 2>&1; then
    pass; else fail "collaborator: expected authorized false with write, got OUT=$OUT"; fi

  # 4. Even admin permission on somebody else's repo is not ownership.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=someoneelse FAKE_ADMIN=true someoneelse/thing
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      .authorized == false and .viewer_permission == "admin"' >/dev/null 2>&1; then
    pass; else fail "admin collaborator: expected authorized false, got OUT=$OUT"; fi

  # 5. An org the operator administers IS their namespace.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=acme FAKE_OWNER_TYPE=Organization FAKE_ORG_ROLE=admin acme/thing
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.authorized == true and .owner_type == "Organization"' >/dev/null 2>&1; then
    pass; else fail "org admin: expected authorized true, got OUT=$OUT"; fi

  # 6. Org membership alone is not administering it.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=acme FAKE_OWNER_TYPE=Organization FAKE_ORG_ROLE=member acme/thing
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.authorized == false' >/dev/null 2>&1; then
    pass; else fail "org member: expected authorized false, got OUT=$OUT"; fi

  # 7. A 404 on the membership endpoint is "not a member", not a failure.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=acme FAKE_OWNER_TYPE=Organization FAKE_ORG_404=1 acme/thing
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.authorized == false' >/dev/null 2>&1; then
    pass; else fail "org 404: expected a false verdict, got RC=$RC OUT=$OUT"; fi
  # Not being in an org is the ordinary case, so it must not warn — a warning
  # on every run is one nobody reads by the time it matters.
  if [[ -z "$ERRTEXT" ]]; then
    pass; else fail "org 404: expected no warning, got ERR=$ERRTEXT"; fi

  # 7b. A membership call that fails for any other reason is an unanswered
  # question: exit 2, nothing on stdout, and the diagnostic names the fix.
  run FAKE_VIEWER=jbaruch FAKE_OWNER=acme FAKE_OWNER_TYPE=Organization FAKE_ORG_FAULT=1 acme/thing
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "could not read org membership"; then
    pass; else fail "org fault: expected exit 2 and no verdict, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 8. A bare name is not a slug.
  run FAKE_VIEWER=jbaruch coding-policy
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "owner"; then
    pass; else fail "bad slug: expected exit 1, got RC=$RC OUT=$OUT"; fi

  # 8b. No argument at all.
  OUT="$(env GH_BIN="$FAKE" bash "$SCRIPT" 2>"$TMP/e8b")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e8b"; then
    pass; else fail "usage: expected exit 1 with a usage line, got RC=$RC"; fi

  # 9. gh absent.
  OUT="$(env GH_BIN="$TMP/no-such-gh" bash "$SCRIPT" gamussa/coding-policy 2>"$TMP/e9")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "no-such-gh" "$TMP/e9"; then
    pass; else fail "gh absent: expected exit 1 naming it, got RC=$RC"; fi

  # 9b. Not logged in is a precondition (exit 1) with the fix named, and no
  #     API call is made — it must not surface as exit 2 "could not answer".
  ARGVLOG="$TMP/argv.9b"; : > "$ARGVLOG" || die "could not create $ARGVLOG"
  OUT="$(env GH_BIN="$FAKE" FAKE_AUTH_ERR=1 FAKE_ARGV_FILE="$ARGVLOG" bash "$SCRIPT" gamussa/coding-policy 2>"$TMP/e9b")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" && ! -s "$ARGVLOG" ]] && grep -q "auth login" "$TMP/e9b"; then
    pass; else fail "not logged in: expected exit 1 naming gh auth login and no api call, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e9b") CALLS=$(cat "$ARGVLOG")"; fi

  # 10. An API failure is never a verdict — an unanswerable question must not
  #     read as "not authorized" OR as "authorized".
  run FAKE_REPO_ERR=1 gamussa/coding-policy
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "api failure: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 10b. Same for the viewer lookup.
  run FAKE_USER_ERR=1 gamussa/coding-policy
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "user lookup failure: expected exit 2, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
