#!/usr/bin/env bash
# Outcome-based tests for assemble-release.sh.
#
# The registry read is injected via --registry-cmd (a printf of a fixture
# JSON), so no tessl, no network; --date pins the heading date. Each case
# builds its own plugin tree in a fresh temp dir (rules/testing-standards.md
# Independence).
#
# Covers:
#   1. no fragments                    -> no-op JSON, files byte-identical
#   2. no fragments, manifest ahead    -> publish true (re-run after a failed publish)
#   3. one patch fragment              -> registry+patch, heading above the old top,
#                                         both manifests written, fragment deleted
#   4. patch + minor                   -> minor bump
#   5. major                           -> major bump
#   6. two fragments' sections merge   -> Added, Changed, Fixed, Security, then
#                                         other names alphabetically; entries in
#                                         file name order
#   7. manifest ahead of registry      -> bump from the manifest
#   8. README.md in changelog.d        -> never consumed
#   9. malformed fragment              -> exit 1, no file changed, no staging left
#  10. stray file in changelog.d       -> exit 1, no file changed
#  11. registry lookup fails           -> exit 2, empty stdout, no file changed
#  12. CHANGELOG without a `## `       -> entry appended after the preamble
#  13. second run after assembly       -> no-op
#
# Run: bash scripts/tests/test_assemble_release.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assemble-release.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

RSQUO=$'\xe2\x80\x99'  # U+2019, a non-ASCII character the manifest must keep as UTF-8
OLD_CHANGELOG=$'# Changelog\n\nPreamble line.\n\n## 0.8.5 — 2026-10-14\n\n### Changed\n\n- old entry\n'

# tree <dir> [version]: a plugin tree at <version> (default 0.8.5).
tree() {
  local d="$1" v="${2:-0.8.5}"
  mkdir -p "$d/.tessl-plugin" "$d/.codex-plugin" "$d/changelog.d" || { echo "fatal: mkdir $d" >&2; exit 2; }
  printf '{\n  "name": "gamussa/coding-policy",\n  "version": "%s",\n  "rules": [\n    "rules/a.md"\n  ]\n}\n' "$v" > "$d/.tessl-plugin/plugin.json"
  printf '{\n  "name": "gamussa-coding-policy",\n  "version": "%s",\n  "interface": {\n    "shortDescription": "Viktor Gamov%ss rules"\n  }\n}\n' "$v" "$RSQUO" > "$d/.codex-plugin/plugin.json"
  printf '%s' "$OLD_CHANGELOG" > "$d/CHANGELOG.md"
  printf '# Changelog fragments\n' > "$d/changelog.d/README.md"
}

# frag <dir> <name> <bump> <body>
frag() { printf -- '---\nbump: %s\n---\n%s' "$3" "$4" > "$1/changelog.d/$2"; }

# snapshot <dir>: a listing + content digest of every file in the tree.
snapshot() { (cd "$1" && find . -type f | LC_ALL=C sort | while IFS= read -r f; do printf '%s ' "$f"; cksum < "$f"; done); }

# run <dir> <registry-version|null> -> OUT, ERR, RC
run() {
  local d="$1" rv="$2" rj
  if [[ "$rv" == null ]]; then rj='{"version":null}'; else rj="{\"version\":\"${rv}\"}"; fi
  OUT="$(bash "$SCRIPT" --root "$d" --date 2026-10-15 --registry-cmd "printf '%s' '$rj'" 2>"$d.err")"; RC=$?
  ERR="$(cat "$d.err")"
}

versions() { printf '%s %s' "$(jq -r .version "$1/.tessl-plugin/plugin.json")" "$(jq -r .version "$1/.codex-plugin/plugin.json")"; }

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  local before after

  # 1. no fragments -> no-op
  tree "$TMP/1"; before=$(snapshot "$TMP/1")
  run "$TMP/1" 0.8.5; after=$(snapshot "$TMP/1")
  if [[ $RC -eq 0 && "$before" == "$after" ]] && jq -e '.assembled == false and .publish == false and .version == "0.8.5" and .fragments == [] and .bump == null' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "no fragments: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. no fragments, manifest ahead of the registry -> publish true
  tree "$TMP/2" 0.8.6
  run "$TMP/2" 0.8.5
  if [[ $RC -eq 0 ]] && jq -e '.assembled == false and .publish == true and .version == "0.8.6" and .registry == "0.8.5"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "republish: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. one patch fragment
  tree "$TMP/3"; frag "$TMP/3" fix.md patch $'### Fixed\n\n- **A fix**. Details.\n'
  run "$TMP/3" 0.8.5
  local want3=$'# Changelog\n\nPreamble line.\n\n## 0.8.6 — 2026-10-15\n\n### Fixed\n\n- **A fix**. Details.\n\n## 0.8.5 — 2026-10-14\n\n### Changed\n\n- old entry'
  if [[ $RC -eq 0 && "$(cat "$TMP/3/CHANGELOG.md")" == "$want3" && "$(versions "$TMP/3")" == "0.8.6 0.8.6" && ! -e "$TMP/3/changelog.d/fix.md" ]] \
    && jq -e '.assembled and .version == "0.8.6" and .bump == "patch" and .publish and .fragments == ["changelog.d/fix.md"]' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "patch: RC=$RC OUT=$OUT ERR=$ERR CHANGELOG=$(cat "$TMP/3/CHANGELOG.md")"; fi
  # Manifest formatting: 2-space indent, other keys and UTF-8 kept, trailing newline.
  local want3m=$'{\n  "name": "gamussa-coding-policy",\n  "version": "0.8.6",\n  "interface": {\n    "shortDescription": "Viktor Gamov\xe2\x80\x99s rules"\n  }\n}'
  if [[ "$(cat "$TMP/3/.codex-plugin/plugin.json")" == "$want3m" && "$(tail -c 1 "$TMP/3/.codex-plugin/plugin.json" | od -An -c | tr -d ' ')" == '\n' ]]; then pass
  else fail "manifest format: $(cat "$TMP/3/.codex-plugin/plugin.json")"; fi

  # 4. patch + minor -> minor
  tree "$TMP/4"; frag "$TMP/4" a.md patch $'### Fixed\n\n- a\n'; frag "$TMP/4" b.md minor $'### Added\n\n- b\n'
  run "$TMP/4" 0.8.5
  if [[ $RC -eq 0 && "$(versions "$TMP/4")" == "0.9.0 0.9.0" ]] && jq -e '.bump == "minor"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "minor: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 5. major
  tree "$TMP/5"; frag "$TMP/5" a.md minor $'### Added\n\n- a\n'; frag "$TMP/5" b.md major $'### Removed\n\n- b\n'
  run "$TMP/5" 0.8.5
  if [[ $RC -eq 0 && "$(versions "$TMP/5")" == "1.0.0 1.0.0" ]] && jq -e '.bump == "major"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "major: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 6. sections from two fragments merge in the defined order
  tree "$TMP/6"
  frag "$TMP/6" a-first.md patch $'### Zeta\n\n- a zeta\n\n### Fixed\n\n- a fixed\n  - a nested\n\n### Security\n\n- a security\n'
  frag "$TMP/6" b-second.md patch $'\n### Fixed\n\n- b fixed\n\n### Added\n\n- b added\n\n### Alpha\n\n- b alpha\n\n### Changed\n\n- b changed\n'
  run "$TMP/6" 0.8.5
  local want6=$'## 0.8.6 — 2026-10-15\n\n### Added\n\n- b added\n\n### Changed\n\n- b changed\n\n### Fixed\n\n- a fixed\n  - a nested\n- b fixed\n\n### Security\n\n- a security\n\n### Alpha\n\n- b alpha\n\n### Zeta\n\n- a zeta\n\n## 0.8.5 — 2026-10-14'
  if [[ $RC -eq 0 && "$(cat "$TMP/6/CHANGELOG.md")" == *"$want6"* ]]; then pass
  else fail "section merge: RC=$RC ERR=$ERR CHANGELOG=$(cat "$TMP/6/CHANGELOG.md")"; fi

  # 7. manifest ahead of the registry -> bump from the manifest
  tree "$TMP/7" 0.9.2; frag "$TMP/7" a.md patch $'### Fixed\n\n- a\n'
  run "$TMP/7" 0.8.5
  if [[ $RC -eq 0 && "$(versions "$TMP/7")" == "0.9.3 0.9.3" ]] && jq -e '.registry == "0.8.5" and .version == "0.9.3"' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "manifest ahead: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 7b. registry ahead of the manifest -> bump from the registry; never published reads 0.0.0
  tree "$TMP/7b" 0.8.5; frag "$TMP/7b" a.md patch $'### Fixed\n\n- a\n'
  run "$TMP/7b" 0.8.9
  if [[ $RC -eq 0 && "$(versions "$TMP/7b")" == "0.8.10 0.8.10" ]]; then pass
  else fail "registry ahead: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 8. README.md is never consumed
  tree "$TMP/8"; frag "$TMP/8" a.md patch $'### Fixed\n\n- a\n'
  run "$TMP/8" 0.8.5
  if [[ $RC -eq 0 && -f "$TMP/8/changelog.d/README.md" ]] && jq -e '.fragments == ["changelog.d/a.md"]' <<<"$OUT" >/dev/null 2>&1 \
    && ! grep -q 'Changelog fragments' "$TMP/8/CHANGELOG.md"; then pass
  else fail "readme: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 9. malformed fragment -> exit 1, nothing written (valid sibling untouched)
  tree "$TMP/9"; frag "$TMP/9" a.md patch $'### Fixed\n\n- a\n'; frag "$TMP/9" b.md huge $'### Fixed\n\n- b\n'
  before=$(snapshot "$TMP/9"); run "$TMP/9" 0.8.5; after=$(snapshot "$TMP/9")
  if [[ $RC -eq 1 && -z "$OUT" && "$before" == "$after" ]] && grep -q "changelog.d/b.md: line 2: bump 'huge'" <<<"$ERR" && grep -q 'nothing was written' <<<"$ERR"; then pass
  else fail "malformed: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 10. stray file in changelog.d -> exit 1, nothing written
  tree "$TMP/10"; frag "$TMP/10" a.md patch $'### Fixed\n\n- a\n'; printf 'x\n' > "$TMP/10/changelog.d/notes.txt"
  before=$(snapshot "$TMP/10"); run "$TMP/10" 0.8.5; after=$(snapshot "$TMP/10")
  if [[ $RC -eq 1 && "$before" == "$after" ]] && grep -q 'changelog.d/notes.txt is not a fragment' <<<"$ERR"; then pass
  else fail "stray file: RC=$RC ERR=$ERR"; fi

  # 11. registry lookup fails -> exit 2, empty stdout, nothing written
  tree "$TMP/11"; frag "$TMP/11" a.md patch $'### Fixed\n\n- a\n'
  before=$(snapshot "$TMP/11")
  OUT="$(bash "$SCRIPT" --root "$TMP/11" --date 2026-10-15 --registry-cmd "exit 2" 2>/dev/null)"; RC=$?
  after=$(snapshot "$TMP/11")
  if [[ $RC -eq 2 && -z "$OUT" && "$before" == "$after" ]]; then pass
  else fail "registry fail: RC=$RC OUT=$OUT"; fi

  # 12. CHANGELOG with no `## ` heading -> appended after the preamble
  tree "$TMP/12"; printf '# Changelog\n\nPreamble line.\n' > "$TMP/12/CHANGELOG.md"; frag "$TMP/12" a.md patch $'### Fixed\n\n- a\n'
  run "$TMP/12" null
  if [[ $RC -eq 0 && "$(cat "$TMP/12/CHANGELOG.md")" == $'# Changelog\n\nPreamble line.\n\n## 0.8.6 — 2026-10-15\n\n### Fixed\n\n- a' ]]; then pass
  else fail "no heading: RC=$RC ERR=$ERR CHANGELOG=$(cat "$TMP/12/CHANGELOG.md")"; fi

  # 13. a second run after assembly is a no-op
  before=$(snapshot "$TMP/3"); run "$TMP/3" 0.8.6; after=$(snapshot "$TMP/3")
  if [[ $RC -eq 0 && "$before" == "$after" ]] && jq -e '.assembled == false and .publish == false' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "second run: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
