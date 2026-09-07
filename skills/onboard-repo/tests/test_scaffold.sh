#!/usr/bin/env bash
# Outcome-based tests for scaffold.sh. Each case runs in a throwaway git repo
# with a fixture plugin mount (PLUGIN_MOUNT) holding every template — no
# network, no shared state.
#
# Covers:
#   1. install creates all 7 targets, workflow lands WITHOUT the .md shim.
#   2. install refuses when a target exists (exit 1, no other file written).
#   3. override overwrites a changed target and reports "overwritten".
#   4. override re-run with identical content is a no-op ("unchanged").
#   5. missing template -> exit 1 naming tessl install; nothing written.
#   6. write failure mid-run restores prior targets (read-only dir).
#   7. bad argument -> exit 2.
#
# Run: bash skills/onboard-repo/tests/test_scaffold.sh
set -uo pipefail

# Isolate git from the operator's global/system config (a global gitignore
# that ignores .github/ would make an untracked target read as clean).
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${SKILL_DIR}/scaffold.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: scaffold.sh not found at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && { chmod -R u+w "$TMP" 2>/dev/null; ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; }; return 0; }

TARGETS=(.github/workflows/review-codex.yml .github/copilot-instructions.md .github/codex-review/prompt.md .github/codex-review/schema.json .github/codex-review/post-review.sh .github/codex-review/mask-secrets.sh .github/codex-review/assert-no-secret-leak.sh)
TEMPLATES=(review-codex.yml.md copilot-instructions.md prompt.md schema.json post-review.sh mask-secrets.sh assert-no-secret-leak.sh)

mkrepo() { git -c init.defaultBranch=main init -q "$1" && git -C "$1" -c user.email=t@t -c user.name=t commit --allow-empty -q -m init; }
mkmount() { # <dir> <content-tag>
  local t="$1/skills/onboard-repo/templates" f; mkdir -p "$t" \
    && for f in "${TEMPLATES[@]}"; do printf '# %s %s\n' "$f" "$2" > "$t/$f"; done
}
run() { # <repo> <mount> [args...]
  local repo="$1" mount="$2"; shift 2
  OUT="$(cd "$repo" && PLUGIN_MOUNT="$mount" bash "$SCRIPT" "$@" 2>/dev/null)"; RC=$?
}
all_present() { local t; for t in "${TARGETS[@]}"; do [[ -f "$1/$t" ]] || return 1; done; }
none_present() { local t; for t in "${TARGETS[@]}"; do [[ -e "$1/$t" ]] && return 1; done; return 0; }

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  MOUNT_A="$TMP/mount-a"; mkmount "$MOUNT_A" v1
  MOUNT_B="$TMP/mount-b"; mkmount "$MOUNT_B" v2

  # 1. install creates all targets, no .md shim on the workflow
  mkrepo "$TMP/r1"; run "$TMP/r1" "$MOUNT_A"
  if [[ $RC -eq 0 ]] && jq -e '.state == "scaffolded" and (.files | length == 7) and all(.files[]; .action == "created")' <<<"$OUT" >/dev/null 2>&1 \
     && all_present "$TMP/r1" && [[ ! -e "$TMP/r1/.github/workflows/review-codex.yml.md" ]] \
     && grep -q 'review-codex.yml.md v1' "$TMP/r1/.github/workflows/review-codex.yml"; then pass; else fail "install: RC=$RC OUT=$OUT"; fi

  # 2. install refuses an existing target; nothing else written
  mkrepo "$TMP/r2"; mkdir -p "$TMP/r2/.github"; printf 'mine\n' > "$TMP/r2/.github/copilot-instructions.md"
  run "$TMP/r2" "$MOUNT_A"
  if [[ $RC -eq 1 && -z "$OUT" && ! -e "$TMP/r2/.github/workflows/review-codex.yml" ]] && grep -q mine "$TMP/r2/.github/copilot-instructions.md"; then pass; else fail "refuse: RC=$RC OUT=$OUT"; fi

  # 3. override overwrites with newer template content
  run "$TMP/r1" "$MOUNT_B" --override
  if [[ $RC -eq 0 ]] && jq -e '.state == "scaffolded" and .override == true and all(.files[]; .action == "overwritten")' <<<"$OUT" >/dev/null 2>&1 \
     && grep -q 'v2' "$TMP/r1/.github/codex-review/prompt.md"; then pass; else fail "override: RC=$RC OUT=$OUT"; fi

  # 4. override re-run identical -> no-op
  run "$TMP/r1" "$MOUNT_B" --override
  if [[ $RC -eq 0 ]] && jq -e '.state == "no-op" and all(.files[]; .action == "unchanged")' <<<"$OUT" >/dev/null 2>&1; then pass; else fail "no-op: RC=$RC OUT=$OUT"; fi

  # 5. missing template -> exit 1, nothing written
  MOUNT_C="$TMP/mount-c"; mkmount "$MOUNT_C" v3; rm "$MOUNT_C/skills/onboard-repo/templates/schema.json"
  mkrepo "$TMP/r5"; ERR="$(cd "$TMP/r5" && PLUGIN_MOUNT="$MOUNT_C" bash "$SCRIPT" 2>&1 >/dev/null)"; RC=$?
  if [[ $RC -eq 1 ]] && grep -q 'tessl install gamussa/coding-policy' <<<"$ERR" && none_present "$TMP/r5"; then pass; else fail "missing template: RC=$RC ERR=$ERR"; fi

  # 6. write failure restores prior targets: make a LATER target read-only so
  #    the earlier ones are already overwritten when the write fails.
  mkrepo "$TMP/r6"; run "$TMP/r6" "$MOUNT_A"
  chmod a-w "$TMP/r6/.github/codex-review/schema.json"
  if [[ "$(id -u)" == "0" ]]; then
    pass  # root ignores mode bits; the restore path cannot be provoked this way
  else
    run "$TMP/r6" "$MOUNT_B" --override
    if [[ $RC -eq 1 ]] && grep -q 'v1' "$TMP/r6/.github/workflows/review-codex.yml" && grep -q 'v1' "$TMP/r6/.github/copilot-instructions.md"; then pass
    else fail "restore: RC=$RC OUT=$OUT wf=$(cat "$TMP/r6/.github/workflows/review-codex.yml")"; fi
  fi
  chmod u+w "$TMP/r6/.github/codex-review/schema.json"

  # 7. bad argument
  mkrepo "$TMP/r7"; run "$TMP/r7" "$MOUNT_A" --bogus
  if [[ $RC -eq 2 ]]; then pass; else fail "bad arg: RC=$RC"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

main "$@"
