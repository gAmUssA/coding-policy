#!/usr/bin/env bash
# Outcome-based tests for check-changelog-fragment.sh.
#
# Each case builds its own throwaway git repo: a base commit on `main`, then a
# `pr` branch commit carrying the case's change. The gate runs as
# `check-changelog-fragment.sh main pr` — no network, no registry
# (rules/testing-standards.md Independence).
#
# Covers:
#   1. shipped change + valid fragment       -> exit 0, fragment + bump reported
#   2. shipped change, no fragment           -> exit 1, stderr names the fragment
#   3. invalid bump value                    -> exit 1
#   4. fragment missing front matter         -> exit 1
#   5. fragment section with no bullet       -> exit 1
#   6. tessl manifest version edited         -> exit 1, stderr says revert
#   7. codex manifest version edited         -> exit 1
#   8. CHANGELOG `## x.y.z` heading added    -> exit 1
#   9. docs/ + scripts/ only, no fragment    -> exit 0, shipped_changed false
#  10. CHANGELOG history edit (no heading)   -> exit 0 with a fragment
#  11. fragment outside the name contract    -> exit 1
#  12. base commits after the branch point   -> not counted against the PR
#  13. unknown base ref                      -> exit 2, empty stdout
#  14. changelog.d/README.md edit only       -> exit 0, not a fragment
#
# Run: bash scripts/tests/test_check_changelog_fragment.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-changelog-fragment.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "fatal: git required" >&2; exit 2; }

# Hermetic git: no user/system config, fixed identity.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

VALID_FRAGMENT=$'---\nbump: patch\n---\n### Fixed\n\n- **A fix**. Details.\n'

# repo <dir>: base commit on main, then check out a `pr` branch.
repo() {
  local d="$1"
  mkdir -p "$d/.tessl-plugin" "$d/.codex-plugin" "$d/rules" "$d/docs" "$d/scripts" "$d/changelog.d" \
    || { echo "fatal: mkdir $d" >&2; exit 2; }
  printf '{\n  "name": "gamussa/coding-policy",\n  "version": "0.8.5"\n}\n' > "$d/.tessl-plugin/plugin.json"
  printf '{\n  "name": "gamussa-coding-policy",\n  "version": "0.8.5"\n}\n' > "$d/.codex-plugin/plugin.json"
  printf '# Changelog\n\n## 0.8.5 — 2026-10-14\n\n### Changed\n\n- old entry\n' > "$d/CHANGELOG.md"
  printf '# Rule\n\n- one\n' > "$d/rules/a.md"
  printf 'doc\n' > "$d/docs/a.md"
  printf 'echo hi\n' > "$d/scripts/a.sh"
  printf '# Changelog fragments\n' > "$d/changelog.d/README.md"
  if ! { git -C "$d" init -q -b main && git -C "$d" add -A && git -C "$d" commit -qm base && git -C "$d" checkout -qb pr; }; then
    echo "fatal: git setup in $d" >&2; exit 2
  fi
}

commit() {
  if ! { git -C "$1" add -A && git -C "$1" commit -qm change; }; then
    echo "fatal: commit in $1" >&2; exit 2
  fi
}

# run <dir> [base] -> OUT, ERR, RC
run() {
  local d="$1" base="${2:-main}"
  OUT="$(cd "$d" && bash "$SCRIPT" "$base" pr 2>"$d.err")"; RC=$?
  ERR="$(cat "$d.err")"
}

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT

  # 1. shipped change + valid fragment -> ok
  repo "$TMP/1"; printf -- '- two\n' >> "$TMP/1/rules/a.md"; printf '%s' "$VALID_FRAGMENT" > "$TMP/1/changelog.d/fix-a.md"; commit "$TMP/1"
  run "$TMP/1"
  if [[ $RC -eq 0 ]] && jq -e '.ok and .shipped_changed and .fragments == [{"path":"changelog.d/fix-a.md","bump":"patch"}] and .errors == []' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "valid fragment: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. shipped change, no fragment -> fail
  repo "$TMP/2"; printf -- '- two\n' >> "$TMP/2/rules/a.md"; commit "$TMP/2"
  run "$TMP/2"
  if [[ $RC -eq 1 ]] && jq -e '.ok == false and (.shipped_files == ["rules/a.md"])' <<<"$OUT" >/dev/null 2>&1 && grep -q 'no valid changelog fragment' <<<"$ERR"; then pass
  else fail "no fragment: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. invalid bump value -> fail
  repo "$TMP/3"; printf -- '- two\n' >> "$TMP/3/rules/a.md"; printf -- '---\nbump: huge\n---\n### Fixed\n\n- x\n' > "$TMP/3/changelog.d/x.md"; commit "$TMP/3"
  run "$TMP/3"
  if [[ $RC -eq 1 ]] && grep -q "bump 'huge' is not one of patch, minor, major" <<<"$ERR"; then pass
  else fail "invalid bump: RC=$RC ERR=$ERR"; fi

  # 4. fragment missing front matter -> fail
  repo "$TMP/4"; printf -- '- two\n' >> "$TMP/4/rules/a.md"; printf -- '### Fixed\n\n- x\n' > "$TMP/4/changelog.d/x.md"; commit "$TMP/4"
  run "$TMP/4"
  if [[ $RC -eq 1 ]] && grep -q "line 1 must be '---'" <<<"$ERR"; then pass
  else fail "no front matter: RC=$RC ERR=$ERR"; fi

  # 5. section with no bullet -> fail
  repo "$TMP/5"; printf -- '- two\n' >> "$TMP/5/rules/a.md"; printf -- '---\nbump: patch\n---\n### Fixed\n\nprose only\n' > "$TMP/5/changelog.d/x.md"; commit "$TMP/5"
  run "$TMP/5"
  if [[ $RC -eq 1 ]] && grep -q "section 'Fixed' has no '- ' bullet" <<<"$ERR"; then pass
  else fail "no bullet: RC=$RC ERR=$ERR"; fi

  # 6. tessl manifest version edited -> fail (even with a valid fragment)
  repo "$TMP/6"; printf '%s' "$VALID_FRAGMENT" > "$TMP/6/changelog.d/x.md"
  printf '{\n  "name": "gamussa/coding-policy",\n  "version": "0.8.6"\n}\n' > "$TMP/6/.tessl-plugin/plugin.json"; commit "$TMP/6"
  run "$TMP/6"
  if [[ $RC -eq 1 ]] && grep -q '.tessl-plugin/plugin.json: version changed 0.8.5 -> 0.8.6 — restore it' <<<"$ERR"; then pass
  else fail "tessl version edit: RC=$RC ERR=$ERR"; fi

  # 7. codex manifest version edited -> fail
  repo "$TMP/7"; printf '%s' "$VALID_FRAGMENT" > "$TMP/7/changelog.d/x.md"
  printf '{\n  "name": "gamussa-coding-policy",\n  "version": "0.9.0"\n}\n' > "$TMP/7/.codex-plugin/plugin.json"; commit "$TMP/7"
  run "$TMP/7"
  if [[ $RC -eq 1 ]] && grep -q '.codex-plugin/plugin.json: version changed' <<<"$ERR"; then pass
  else fail "codex version edit: RC=$RC ERR=$ERR"; fi

  # 7b. manifest version removed or nulled -> fail, not deferred to the assembler
  repo "$TMP/7b"; printf '%s' "$VALID_FRAGMENT" > "$TMP/7b/changelog.d/x.md"
  printf '{\n  "name": "gamussa/coding-policy"\n}\n' > "$TMP/7b/.tessl-plugin/plugin.json"
  printf '{\n  "name": "gamussa-coding-policy",\n  "version": null\n}\n' > "$TMP/7b/.codex-plugin/plugin.json"; commit "$TMP/7b"
  run "$TMP/7b"
  if [[ $RC -eq 1 ]] && grep -q '.tessl-plugin/plugin.json: version changed 0.8.5 -> <removed>' <<<"$ERR" \
     && grep -q '.codex-plugin/plugin.json: version changed .* -> <removed>' <<<"$ERR"; then pass
  else fail "version removed: RC=$RC ERR=$ERR"; fi

  # 8. CHANGELOG version heading added -> fail
  repo "$TMP/8"; printf -- '- two\n' >> "$TMP/8/rules/a.md"; printf '%s' "$VALID_FRAGMENT" > "$TMP/8/changelog.d/x.md"
  printf '# Changelog\n\n## 0.8.6 — 2026-10-15\n\n### Fixed\n\n- new\n\n## 0.8.5 — 2026-10-14\n\n### Changed\n\n- old entry\n' > "$TMP/8/CHANGELOG.md"; commit "$TMP/8"
  run "$TMP/8"
  if [[ $RC -eq 1 ]] && grep -q "adds version heading '## 0.8.6 — 2026-10-15'" <<<"$ERR"; then pass
  else fail "changelog heading: RC=$RC ERR=$ERR"; fi

  # 8b. .tesslignore-only change -> fail without a fragment: it changes the artifact
  repo "$TMP/8b"; printf 'docs/\n' >> "$TMP/8b/.tesslignore"; commit "$TMP/8b"
  run "$TMP/8b"
  if [[ $RC -eq 1 ]] && grep -q 'shipped content changed (.tesslignore)' <<<"$ERR"; then pass
  else fail "tesslignore change: RC=$RC ERR=$ERR"; fi

  # 9. docs/ + scripts/ only -> ok without a fragment
  repo "$TMP/9"; printf 'more\n' >> "$TMP/9/docs/a.md"; printf 'echo bye\n' >> "$TMP/9/scripts/a.sh"; commit "$TMP/9"
  run "$TMP/9"
  if [[ $RC -eq 0 ]] && jq -e '.ok and .shipped_changed == false and .fragments == []' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "docs only: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 10. CHANGELOG history edit without a new heading -> ok
  repo "$TMP/10"; printf -- '- two\n' >> "$TMP/10/rules/a.md"; printf '%s' "$VALID_FRAGMENT" > "$TMP/10/changelog.d/x.md"
  printf '# Changelog\n\n## 0.8.5 — 2026-10-14\n\n### Changed\n\n- old entry, typo fixed\n' > "$TMP/10/CHANGELOG.md"; commit "$TMP/10"
  run "$TMP/10"
  if [[ $RC -eq 0 ]]; then pass; else fail "history edit: RC=$RC ERR=$ERR"; fi

  # 11. fragment outside the name contract -> fail
  repo "$TMP/11"; printf -- '- two\n' >> "$TMP/11/rules/a.md"; mkdir -p "$TMP/11/changelog.d/sub"
  printf '%s' "$VALID_FRAGMENT" > "$TMP/11/changelog.d/sub/x.md"; commit "$TMP/11"
  run "$TMP/11"
  if [[ $RC -eq 1 ]] && grep -q 'changelog.d/sub/x.md: not a fragment name' <<<"$ERR"; then pass
  else fail "nested fragment: RC=$RC ERR=$ERR"; fi

  # 12. base advanced after the branch point (a release commit on main) -> the
  #     PR is judged on its own diff from the merge base only
  repo "$TMP/12"; printf 'more\n' >> "$TMP/12/docs/a.md"; commit "$TMP/12"
  git -C "$TMP/12" checkout -q main || { echo "fatal: checkout main in $TMP/12" >&2; exit 2; }
  printf '{\n  "name": "gamussa/coding-policy",\n  "version": "0.9.0"\n}\n' > "$TMP/12/.tessl-plugin/plugin.json"
  printf '# Changelog\n\n## 0.9.0 — 2026-10-15\n\n- x\n' > "$TMP/12/CHANGELOG.md"; commit "$TMP/12"
  git -C "$TMP/12" checkout -q pr || { echo "fatal: checkout pr in $TMP/12" >&2; exit 2; }
  run "$TMP/12"
  if [[ $RC -eq 0 ]] && jq -e '.ok and .shipped_changed == false' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "advanced base: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 13. unknown base ref -> exit 2, empty stdout
  repo "$TMP/13"; printf 'more\n' >> "$TMP/13/docs/a.md"; commit "$TMP/13"
  run "$TMP/13" origin/nope
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q 'fetch it first' <<<"$ERR"; then pass
  else fail "unknown base: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 14. README-only edit inside changelog.d -> ok, not a fragment
  repo "$TMP/14"; printf 'more\n' >> "$TMP/14/changelog.d/README.md"; commit "$TMP/14"
  run "$TMP/14"
  if [[ $RC -eq 0 ]] && jq -e '.fragments == []' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "readme edit: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
