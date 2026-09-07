#!/usr/bin/env bash
# Run every onboard-repo precondition and report them as ONE JSON result, so
# the skill surfaces all failures together instead of one per run.
#
# Checks: git worktree, origin remote, GitHub CLI installed + authenticated,
# the Codex subscription credential (`~/.codex/auth.json` with auth_mode
# "chatgpt" and has_refresh_token true — the reviewer workflow runs on it),
# the installed plugin's templates, and (install mode only) that no reviewer
# target already exists. In --override mode an existing target is expected;
# the check becomes "no uncommitted edits on a target the upgrade would
# clobber".
#
# Usage: preflight.sh [--override]
# Env:   CODEX_AUTH_FILE   path of the Codex credential (default ~/.codex/auth.json;
#                          tests point it at a fixture)
#        PLUGIN_MOUNT      installed plugin root (default
#                          .tessl/plugins/gAmUssA/coding-policy; tests override)
# Out:   one JSON object on stdout:
#          {"ok": bool, "override": bool,
#           "failures": [{"check": "<name>", "reason": "<text>"}, ...],
#           "warnings": [{"check": "<name>", "reason": "<text>"}, ...]}
#        Every failure carries a concrete recovery command. Warnings never
#        change `ok` or the exit code.
# Exit:  0 if ok; 1 if any check fails; 2 on a bad argument.

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done
OVERRIDE_JSON="false"; (( OVERRIDE_MODE == 1 )) && OVERRIDE_JSON="true"

# jq emits the contract; without it, hand-roll the one failure envelope.
if ! command -v jq >/dev/null 2>&1; then
  printf '{"ok": false, "override": %s, "failures": [{"check": "jq-installed", "reason": "jq is not installed; install with '"'"'brew install jq'"'"' (macOS) or '"'"'apt install jq'"'"' (Debian/Ubuntu) and re-run"}], "warnings": []}\n' "$OVERRIDE_JSON"
  exit 1
fi

PLUGIN_MOUNT="${PLUGIN_MOUNT:-.tessl/plugins/gAmUssA/coding-policy}"
CODEX_AUTH_FILE="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
TARGETS=(
  ".github/workflows/review-codex.yml"
  ".github/copilot-instructions.md"
  ".github/codex-review/prompt.md"
  ".github/codex-review/schema.json"
  ".github/codex-review/post-review.sh"
  ".github/codex-review/mask-secrets.sh"
  ".github/codex-review/assert-no-secret-leak.sh"
)

declare -a failures=() warnings=()
push_failure() { failures+=("$(jq -nc --arg c "$1" --arg r "$2" '{check: $c, reason: $r}')"); }
push_warning() { warnings+=("$(jq -nc --arg c "$1" --arg r "$2" '{check: $c, reason: $r}')"); }

check_git_worktree() {
  local root rc=0
  root=$(git rev-parse --show-toplevel 2>/dev/null) || rc=$?
  if (( rc != 0 )); then
    push_failure "in-git-worktree" "Not inside a git worktree — run the skill from the root of the consumer repo's checkout"
    return 0
  fi
  cd "$root"
}

check_origin_remote() {
  git remote get-url origin >/dev/null 2>&1 \
    || push_failure "origin-remote" "No git remote named 'origin' — add one with 'git remote add origin <url>' before re-running"
}

check_gh() {
  if ! command -v gh >/dev/null 2>&1; then
    push_failure "gh-installed" "GitHub CLI not found on PATH — install from https://cli.github.com/"
    return 0
  fi
  gh auth status >/dev/null 2>&1 \
    || push_failure "gh-authenticated" "GitHub CLI not authenticated — run 'gh auth login' (or 'gh auth refresh -h github.com')"
}

check_codex_auth() {
  if [[ ! -f "$CODEX_AUTH_FILE" ]]; then
    push_failure "codex-auth" "Codex credential not found at ${CODEX_AUTH_FILE} — run 'codex login' (Sign in with ChatGPT) on this machine first"
    return 0
  fi
  local mode refresh
  mode=$(jq -r '.auth_mode // empty' "$CODEX_AUTH_FILE" 2>/dev/null) || mode=""
  refresh=$(jq -r '.has_refresh_token // false' "$CODEX_AUTH_FILE" 2>/dev/null) || refresh="false"
  if [[ "$mode" != "chatgpt" || "$refresh" != "true" ]]; then
    push_failure "codex-auth" "${CODEX_AUTH_FILE} is not a ChatGPT-subscription credential with a refresh token (auth_mode='${mode:-?}', has_refresh_token=${refresh}) — run 'codex login' and pick Sign in with ChatGPT"
  fi
}

check_templates_present() {
  local t missing=()
  for t in "${PLUGIN_MOUNT}/skills/onboard-repo/templates/review-codex.yml.md" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/copilot-instructions.md" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/prompt.md" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/schema.json" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/post-review.sh" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/mask-secrets.sh" \
           "${PLUGIN_MOUNT}/skills/onboard-repo/templates/assert-no-secret-leak.sh"; do
    [[ -f "$t" ]] || missing+=("$t")
  done
  (( ${#missing[@]} == 0 )) \
    || push_failure "templates-present" "Template(s) not found: ${missing[*]} — run 'tessl install gAmUssA/coding-policy' first"
}

check_targets() {
  local t present=() dirty=()
  for t in "${TARGETS[@]}"; do
    if [[ -e "$t" ]]; then
      present+=("$t")
      # Uncommitted edits (tracked-modified or untracked) would be clobbered by
      # an upgrade; `git status --porcelain <path>` is empty when clean.
      [[ -z "$(git status --porcelain -- "$t" 2>/dev/null)" ]] || dirty+=("$t")
    fi
  done
  if (( OVERRIDE_MODE == 0 )); then
    (( ${#present[@]} == 0 )) \
      || push_failure "targets-absent" "Reviewer file(s) already present: ${present[*]} — re-run the skill in upgrade mode (--override) to refresh them"
  else
    (( ${#dirty[@]} == 0 )) \
      || push_failure "targets-clean" "Uncommitted changes on: ${dirty[*]} — commit, stash, or discard them before the upgrade overwrites these files"
    (( ${#present[@]} > 0 )) \
      || push_warning "targets-absent" "No prior reviewer files found; --override will behave like a fresh install"
  fi
}

main() {
  check_git_worktree
  check_origin_remote
  check_gh
  check_codex_auth
  check_templates_present
  check_targets

  local fj wj ok="true"
  fj=$(printf '%s\n' "${failures[@]:-}" | jq -sc 'map(select(. != ""))')
  wj=$(printf '%s\n' "${warnings[@]:-}" | jq -sc 'map(select(. != ""))')
  (( ${#failures[@]} == 0 )) || ok="false"
  jq -nc --argjson ok "$ok" --argjson override "$OVERRIDE_JSON" --argjson failures "$fj" --argjson warnings "$wj" \
    '{ok: $ok, override: $override, failures: $failures, warnings: $warnings}'
  [[ "$ok" == "true" ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
