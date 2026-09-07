#!/usr/bin/env bash
# Outcome-based tests for check-tessl-latest.sh.
#
# The hook runs `tessl update --yes` and reads each gAmUssA/* dep's installed
# version from the resolved state before and after, so tests put a FAKE `tessl`
# on PATH and drive it via env: STUB_BUMP_FILE/STUB_BUMP_TO rewrite a fixture
# tessl-package.json to simulate an update, STUB_UPDATE_EXIT makes the update
# fail. Manifest and resolved state are fixture-driven via TESSL_LATEST_MANIFEST
# and TESSL_STATE_DIR — no real tessl, no network, deterministic.
#
# Covers:
#   1. updated         -> "X → Y (updated)" segment, marker present, exit 0.
#   2. already latest  -> "Y (latest)" segment, marker present, exit 0.
#   3. update failed    -> status still emits with "update failed: <reason>", exit 0.
#   4. tessl missing   -> status emits with "update failed" (unavailable), exit 0.
#   5. install pending  -> no resolved-state file -> "(install pending)", exit 0.
#   6. pinned dep      -> "NOTE:" pin warning present, marker present, exit 0.
#   7. no manifest     -> silent no-op, exit 0.
#   8. third-party only -> silent (no gAmUssA/* deps), exit 0.
#   9. malformed JSON   -> silent no-op, exit 0 (never aborts SessionStart).
#  10. unreadable state -> warns to stderr AND labels the dep "version unknown"
#                          rather than "(install pending)" (existing-but-broken
#                          is a tool failure, not an absent-file non-result),
#                          exit 0.
#  11. unparseable state -> same label from invalid JSON in the state file, no
#                          chmod needed so it runs as root too.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup command
# is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Run: bash hooks/tests/test_check_tessl_latest.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Write a resolved-state tessl-package.json fixture for <dep> at <version>.
seed_pkg() { # <state-dir> <dep> <version>
  local d="$1/plugins/$2"
  mkdir -p "$d" || die "seed_pkg: mkdir $d failed"
  printf '{"name":"%s","version":"%s"}\n' "$2" "$3" > "$d/tessl-package.json" \
    || die "seed_pkg: write $d/tessl-package.json failed"
}

# run <manifest> <state-dir> [extra env...] -> OUT, RC  (fake tessl on PATH)
run() {
  local manifest="$1" state="$2"; shift 2
  OUT="$(env "PATH=$STUBBIN:$PATH" TESSL_LATEST_MANIFEST="$manifest" TESSL_STATE_DIR="$state" "$@" bash "$SCRIPT" </dev/null 2>/dev/null)"
  RC=$?
}

# has <regex>: true when the emitted additionalContext matches <regex>.
has() { printf '%s' "$OUT" | jq -e ".additionalContext | test(\"$1\")" >/dev/null 2>&1; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-tessl-latest.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "hook not found/readable at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"

  TMP="$(mktemp -d -t tessl-latest-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  # A FAKE `tessl`: on `update`, optionally rewrite a fixture tessl-package.json
  # to a new version (simulating a resolved-state bump), then exit
  # STUB_UPDATE_EXIT (default 0). Any other subcommand is a no-op success.
  STUBBIN="$TMP/bin"; mkdir -p "$STUBBIN" || die "could not create $STUBBIN"
  cat > "$STUBBIN/tessl" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "update" ]]; then
  if [[ -n "${STUB_BUMP_FILE:-}" && -n "${STUB_BUMP_TO:-}" ]]; then
    printf '{"name":"gAmUssA/coding-policy","version":"%s"}\n' "$STUB_BUMP_TO" > "$STUB_BUMP_FILE"
  fi
  if [[ -n "${STUB_UPDATE_EXIT:-}" ]]; then
    printf 'stub tessl: simulated update failure\n' >&2
    exit "$STUB_UPDATE_EXIT"
  fi
fi
exit 0
STUB
  chmod +x "$STUBBIN/tessl" || die "chmod stub tessl failed"

  FAIL=0; PASS=0

  # 1. updated: coding-policy at latest, resolved state 0.3.147, update bumps to
  #    0.3.153 -> "0.3.147 → 0.3.153 (updated)".
  local m1="$TMP/m1.json" s1="$TMP/s1"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m1" || die "write m1 failed"
  seed_pkg "$s1" "gAmUssA/coding-policy" "0.3.147"
  run "$m1" "$s1" STUB_BUMP_FILE="$s1/plugins/gAmUssA/coding-policy/tessl-package.json" STUB_BUMP_TO="0.3.153"
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "versions:" && has "0.3.147" && has "0.3.153" && has "updated"; then
    pass; else fail "updated: expected transition status, got RC=$RC OUT=$OUT"; fi

  # 2. already latest: resolved state 0.3.153, update leaves it unchanged ->
  #    "0.3.153 (latest)".
  local m2="$TMP/m2.json" s2="$TMP/s2"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m2" || die "write m2 failed"
  seed_pkg "$s2" "gAmUssA/coding-policy" "0.3.153"
  run "$m2" "$s2"
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "0.3.153" && has "latest"; then
    pass; else fail "already-latest: expected (latest) status, got RC=$RC OUT=$OUT"; fi

  # 3. update failed: fake tessl exits non-zero. Status still emits, hook exits 0.
  local m3="$TMP/m3.json" s3="$TMP/s3"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m3" || die "write m3 failed"
  seed_pkg "$s3" "gAmUssA/coding-policy" "0.3.147"
  run "$m3" "$s3" STUB_UPDATE_EXIT=1
  # A failed update must NOT claim "latest" for the unchanged version — it is
  # labeled "(installed)" since freshness was never verified.
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "update failed" && has "installed" && ! has "latest"; then
    pass; else fail "update-failed: expected 'update failed' + 'installed' (not 'latest'), got RC=$RC OUT=$OUT"; fi

  # 4. tessl missing: PATH lacks tessl (jq + bash only). Update marked
  #    unavailable, status still emits, hook exits 0.
  local minbin="$TMP/minbin"; mkdir -p "$minbin" || die "could not create $minbin"
  ln -s "$(command -v bash)" "$minbin/bash" || die "symlink bash failed"
  ln -s "$(command -v jq)"   "$minbin/jq"   || die "symlink jq failed"
  local m4="$TMP/m4.json" s4="$TMP/s4"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m4" || die "write m4 failed"
  seed_pkg "$s4" "gAmUssA/coding-policy" "0.3.147"
  OUT="$(env "PATH=$minbin" TESSL_LATEST_MANIFEST="$m4" TESSL_STATE_DIR="$s4" bash "$SCRIPT" </dev/null 2>/dev/null)"; RC=$?
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "update failed"; then
    pass; else fail "tessl-missing: expected status with 'update failed', got RC=$RC OUT=$OUT"; fi

  # 5. install pending: manifest lists coding-policy but no resolved-state file
  #    exists (and the update does not create one) -> "(install pending)".
  local m5="$TMP/m5.json" s5="$TMP/s5"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m5" || die "write m5 failed"
  run "$m5" "$s5"
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "install pending"; then
    pass; else fail "install-pending: expected '(install pending)', got RC=$RC OUT=$OUT"; fi

  # 6. pinned dep: coding-policy pinned to 0.3.99 -> the "NOTE:" pin warning is
  #    appended and the marker status is present.
  local m6="$TMP/m6.json" s6="$TMP/s6"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"0.3.99"}}}\n' > "$m6" || die "write m6 failed"
  seed_pkg "$s6" "gAmUssA/coding-policy" "0.3.99"
  run "$m6" "$s6"
  if [[ $RC -eq 0 ]] && has "Session-start status" && has "NOTE:" && has "0.3.99"; then
    pass; else fail "pinned: expected NOTE pin warning, got RC=$RC OUT=$OUT"; fi

  # 7. no manifest -> silent no-op.
  run "$TMP/does-not-exist.json" "$TMP/s7"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no-manifest: expected silence, got RC=$RC OUT=$OUT"; fi

  # 8. third-party only (no gAmUssA/* deps) -> silent.
  local m8="$TMP/m8.json" s8="$TMP/s8"
  printf '{"dependencies":{"tessl/npm-react":{"version":"19.2.0"}}}\n' > "$m8" || die "write m8 failed"
  run "$m8" "$s8"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "third-party only: expected silence, got RC=$RC OUT=$OUT"; fi

  # 9. malformed JSON -> silent no-op, exit 0.
  local m9="$TMP/m9.json" s9="$TMP/s9"
  printf 'not json\n' > "$m9" || die "write m9 failed"
  run "$m9" "$s9"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "malformed: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 10. unreadable resolved-state file: an existing but unreadable tessl-package
  #     .json is a tool failure, not an absent-file non-result — the hook warns
  #     to stderr, labels the dep "version unknown", and still exits 0. The
  #     label is the user-facing half: "(install pending)" here would report a
  #     broken state file as a routine not-installed-yet state (case 5), which
  #     is the one thing the stderr warning cannot correct. Skipped as root:
  #     chmod 000 does not bind root.
  if [[ "$(id -u)" -ne 0 ]]; then
    local m10="$TMP/m10.json" s10="$TMP/s10" errU pkg10
    printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m10" || die "write m10 failed"
    seed_pkg "$s10" "gAmUssA/coding-policy" "0.3.147"
    pkg10="$s10/plugins/gAmUssA/coding-policy/tessl-package.json"
    chmod 000 "$pkg10" || die "chmod 000 $pkg10 failed"
    OUT="$(env "PATH=$STUBBIN:$PATH" TESSL_LATEST_MANIFEST="$m10" TESSL_STATE_DIR="$s10" bash "$SCRIPT" </dev/null 2>"$TMP/err10")"; RC=$?
    errU="$(cat "$TMP/err10")" || die "read $TMP/err10 failed"
    if [[ $RC -eq 0 ]] && printf '%s' "$errU" | grep -q "unreadable" \
       && has "version unknown" && ! has "install pending"; then
      pass; else fail "unreadable state file: expected 'unreadable' warning + 'version unknown' label, got RC=$RC err=$errU OUT=$OUT"; fi
  fi

  # 11. unparseable resolved-state file: valid permissions, invalid JSON. Same
  #     "version unknown" label, and no chmod, so this case also runs as root.
  local m11="$TMP/m11.json" s11="$TMP/s11" err11 pkg11
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$m11" || die "write m11 failed"
  seed_pkg "$s11" "gAmUssA/coding-policy" "0.3.147"
  pkg11="$s11/plugins/gAmUssA/coding-policy/tessl-package.json"
  printf 'not json\n' > "$pkg11" || die "write $pkg11 failed"
  OUT="$(env "PATH=$STUBBIN:$PATH" TESSL_LATEST_MANIFEST="$m11" TESSL_STATE_DIR="$s11" bash "$SCRIPT" </dev/null 2>"$TMP/err11")"; RC=$?
  err11="$(cat "$TMP/err11")" || die "read $TMP/err11 failed"
  if [[ $RC -eq 0 ]] && printf '%s' "$err11" | grep -q "could not parse" \
     && has "version unknown" && ! has "install pending"; then
    pass; else fail "unparseable state file: expected parse warning + 'version unknown' label, got RC=$RC err=$err11 OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
