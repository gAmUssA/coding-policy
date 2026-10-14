#!/usr/bin/env bash
# Outcome-based tests for push-release.sh.
#
# Each case builds a bare "origin" plus a clone in a fresh temp dir; a second
# clone stands in for a merge that lands after the release checkout. No
# network (rules/testing-standards.md Independence).
#
# Covers:
#   1. clean push              -> exit 0, origin/main is the release commit,
#                                 message `chore(release): <v>`, only the
#                                 release paths in it (a stray file stays out)
#   2. non-fast-forward        -> one rebase, exit 0, retried true, both
#                                 commits on origin/main
#   3. conflicting rebase      -> exit 1, rebase aborted, origin unchanged
#   4. nothing staged          -> exit 2
#   5. malformed version       -> exit 2
#
# Run: bash scripts/tests/test_push_release.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/push-release.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "fatal: git required" >&2; exit 2; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# setup <dir>: bare origin with one commit; clone at <dir>/work.
setup() {
  local d="$1"
  mkdir -p "$d/seed/.tessl-plugin" "$d/seed/.codex-plugin" "$d/seed/changelog.d" || { echo "fatal: mkdir" >&2; exit 2; }
  printf '{\n  "version": "0.8.5"\n}\n' > "$d/seed/.tessl-plugin/plugin.json"
  printf '{\n  "version": "0.8.5"\n}\n' > "$d/seed/.codex-plugin/plugin.json"
  printf '# Changelog\n\n## 0.8.5 — 2026-10-14\n' > "$d/seed/CHANGELOG.md"
  printf -- '---\nbump: patch\n---\n### Fixed\n\n- a\n' > "$d/seed/changelog.d/a.md"
  printf 'readme\n' > "$d/seed/README.md"
  { git -C "$d/seed" init -q -b main && git -C "$d/seed" add -A && git -C "$d/seed" commit -qm base \
    && git clone -q --bare "$d/seed" "$d/origin.git" && git clone -q "$d/origin.git" "$d/work"; } \
    || { echo "fatal: git setup in $d" >&2; exit 2; }
}

# assemble <work>: the file changes an assembly makes, plus a stray edit.
assemble() {
  printf '{\n  "version": "0.8.6"\n}\n' > "$1/.tessl-plugin/plugin.json"
  printf '{\n  "version": "0.8.6"\n}\n' > "$1/.codex-plugin/plugin.json"
  printf '# Changelog\n\n## 0.8.6 — 2026-10-15\n\n### Fixed\n\n- a\n\n## 0.8.5 — 2026-10-14\n' > "$1/CHANGELOG.md"
  rm "$1/changelog.d/a.md"
  printf 'stray\n' >> "$1/README.md"
}

# other_merge <dir> <file> <content>: a second clone pushes a commit first.
other_merge() {
  { git clone -q "$1/origin.git" "$1/other" && printf '%s' "$3" > "$1/other/$2" \
    && git -C "$1/other" add -A && git -C "$1/other" commit -qm "merge other" && git -C "$1/other" push -q origin main; } \
    || { echo "fatal: other merge in $1" >&2; exit 2; }
}

run() { OUT="$(cd "$1/work" && bash "$SCRIPT" "$2" 2>"$1/err")"; RC=$?; ERR="$(cat "$1/err")"; }

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  local files

  # 1. clean push
  setup "$TMP/1"; assemble "$TMP/1/work"
  run "$TMP/1" 0.8.6
  files=$(git -C "$TMP/1/origin.git" show --name-only --format= main | LC_ALL=C sort | tr '\n' ' ')
  if [[ $RC -eq 0 && "$(git -C "$TMP/1/origin.git" log -1 --format='%s|%an' main)" == "chore(release): 0.8.6|github-actions[bot]" \
    && "$files" == ".codex-plugin/plugin.json .tessl-plugin/plugin.json CHANGELOG.md changelog.d/a.md " ]] \
    && jq -e --arg c "$(git -C "$TMP/1/origin.git" rev-parse main)" '.pushed and .retried == false and .commit == $c' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "clean push: RC=$RC OUT=$OUT ERR=$ERR files=$files"; fi

  # 2. non-fast-forward -> rebase once, push
  setup "$TMP/2"; other_merge "$TMP/2" changelog.d/b.md $'---\nbump: minor\n---\n### Added\n\n- b\n'; assemble "$TMP/2/work"
  run "$TMP/2" 0.8.6
  if [[ $RC -eq 0 && "$(git -C "$TMP/2/origin.git" log -2 --format=%s main | tr '\n' '|')" == "chore(release): 0.8.6|merge other|" ]] \
    && jq -e '.retried == true' <<<"$OUT" >/dev/null 2>&1; then pass
  else fail "non-ff: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. conflicting rebase -> exit 1, aborted, origin unchanged
  setup "$TMP/3"; other_merge "$TMP/3" CHANGELOG.md $'# Changelog\n\n## 0.9.0 — 2026-10-15\n'; assemble "$TMP/3/work"
  local before; before=$(git -C "$TMP/3/origin.git" rev-parse main)
  run "$TMP/3" 0.8.6
  if [[ $RC -eq 1 && -z "$OUT" && "$(git -C "$TMP/3/origin.git" rev-parse main)" == "$before" && ! -d "$TMP/3/work/.git/rebase-merge" && ! -d "$TMP/3/work/.git/rebase-apply" ]] \
    && grep -q 're-run the publish workflow' <<<"$ERR"; then pass
  else fail "conflict: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 4. nothing staged -> exit 2
  setup "$TMP/4"
  run "$TMP/4" 0.8.6
  if [[ $RC -eq 2 ]] && grep -q 'nothing staged' <<<"$ERR"; then pass; else fail "nothing staged: RC=$RC ERR=$ERR"; fi

  # 5. malformed version -> exit 2
  setup "$TMP/5"; assemble "$TMP/5/work"
  run "$TMP/5" v0.8.6
  if [[ $RC -eq 2 ]]; then pass; else fail "bad version: RC=$RC ERR=$ERR"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

main "$@"
