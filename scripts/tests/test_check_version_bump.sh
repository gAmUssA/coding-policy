#!/usr/bin/env bash
# Outcome-based tests for check-version-bump.sh.
#
# The registry read is injected via CHECK_VERSION_REGISTRY_CMD (a printf of a
# fixture JSON), so no tessl, no network. Each case builds its own manifest +
# changelog in a fresh temp dir (rules/testing-standards.md Independence).
#
# Covers:
#   1. ahead + heading      -> ok, exit 0, JSON reports both versions.
#   2. equal to registry    -> exit 1, stderr names the bump.
#   3. behind registry      -> exit 1.
#   4. never published      -> registry null reads as 0.0.0, exit 0.
#   5. heading missing      -> exit 1 even when ahead; stderr names the heading.
#   6. registry lookup fail -> exit 2, empty stdout.
#   7. malformed manifest version -> exit 2.
#   8. missing manifest     -> exit 2.
#
# Run: bash scripts/tests/test_check_version_bump.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-version-bump.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# fixture <dir> <manifest-version> <changelog-heading-version|"">
fixture() {
  local d="$1" mv="$2" cv="$3"
  mkdir -p "$d/.tessl-plugin" || { echo "fatal: mkdir $d" >&2; exit 2; }
  printf '{"name":"gAmUssA/coding-policy","version":"%s"}\n' "$mv" > "$d/.tessl-plugin/plugin.json"
  if [[ -n "$cv" ]]; then
    printf '# Changelog\n\n## %s — 2026-01-15\n\n### Added\n\n- thing\n' "$cv" > "$d/CHANGELOG.md"
  else
    printf '# Changelog\n\n### Added\n\n- thing\n' > "$d/CHANGELOG.md"
  fi
}

# run <dir> <registry-json> -> OUT, ERR, RC
run() {
  local d="$1" rj="$2"
  OUT="$(cd "$d" && CHECK_VERSION_REGISTRY_CMD="printf '%s' '$rj'" bash "$SCRIPT" 2>"$d/err")"; RC=$?
  ERR="$(cat "$d/err")"
}

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT

  # 1. ahead + heading -> ok
  fixture "$TMP/1" 0.2.0 0.2.0
  run "$TMP/1" '{"version":"0.1.9"}'
  if [[ $RC -eq 0 ]] && jq -e '.ok == true and .manifest == "0.2.0" and .registry == "0.1.9"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "ahead: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. equal -> exit 1, names the bump
  fixture "$TMP/2" 0.2.0 0.2.0
  run "$TMP/2" '{"version":"0.2.0"}'
  if [[ $RC -eq 1 ]] && jq -e '.ok == false' <<<"$OUT" >/dev/null 2>&1 && grep -q 'bump version' <<<"$ERR"; then pass
  else fail "equal: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. behind -> exit 1
  fixture "$TMP/3" 0.1.0 0.1.0
  run "$TMP/3" '{"version":"0.2.0"}'
  if [[ $RC -eq 1 ]]; then pass; else fail "behind: RC=$RC OUT=$OUT"; fi

  # 4. never published -> 0.0.0 baseline, ok
  fixture "$TMP/4" 0.1.0 0.1.0
  run "$TMP/4" '{"version":null}'
  if [[ $RC -eq 0 ]] && jq -e '.registry == "0.0.0" and .ok == true' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "never published: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 5. heading missing -> exit 1 even when ahead
  fixture "$TMP/5" 0.3.0 ""
  run "$TMP/5" '{"version":"0.2.0"}'
  if [[ $RC -eq 1 ]] && grep -q "## 0.3.0" <<<"$ERR"; then pass; else fail "no heading: RC=$RC ERR=$ERR"; fi

  # 5b. heading for a DIFFERENT version does not satisfy
  fixture "$TMP/5b" 0.3.0 0.2.0
  run "$TMP/5b" '{"version":"0.2.0"}'
  if [[ $RC -eq 1 ]]; then pass; else fail "wrong heading: RC=$RC OUT=$OUT"; fi

  # 6. registry lookup fails -> exit 2, empty stdout
  fixture "$TMP/6" 0.3.0 0.3.0
  OUT="$(cd "$TMP/6" && CHECK_VERSION_REGISTRY_CMD="exit 2" bash "$SCRIPT" 2>/dev/null)"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]]; then pass; else fail "lookup fail: RC=$RC OUT=$OUT"; fi

  # 7. malformed manifest version -> exit 2
  fixture "$TMP/7" "v1" "v1"
  run "$TMP/7" '{"version":"0.1.0"}'
  if [[ $RC -eq 2 ]]; then pass; else fail "malformed version: RC=$RC"; fi

  # 8. missing manifest -> exit 2
  mkdir -p "$TMP/8"; printf '# Changelog\n' > "$TMP/8/CHANGELOG.md"
  run "$TMP/8" '{"version":"0.1.0"}'
  if [[ $RC -eq 2 ]]; then pass; else fail "missing manifest: RC=$RC"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

main "$@"
