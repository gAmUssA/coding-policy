#!/usr/bin/env bash
# Copy the policy-reviewer files from the installed plugin into the consumer:
#   .github/workflows/review-codex.yml        — the PR-time Codex policy review
#   .github/copilot-instructions.md           — the Copilot complementary lane
#   .github/codex-review/prompt.md            — the reviewer prompt
#   .github/codex-review/schema.json          — the reviewer output schema
#   .github/codex-review/post-review.sh       — posts the verdict as a PR review
#   .github/codex-review/mask-secrets.sh      — log-masks the credential
#   .github/codex-review/assert-no-secret-leak.sh — refuses a leaking review
#
# Templates live under the plugin mount. The workflow template carries a `.md`
# shim extension: tessl packaging ships only .md/.sh/.json/.py, so a bare
# `.yml` never reaches the installed plugin. The shim is stripped on write.
#
# Every target is snapshotted before any write and restored if a later write
# fails, so a partial run never leaves a half-written reviewer.
#
# Usage: scaffold.sh [--override]
#   --override   overwrite existing targets (upgrade). Install mode refuses
#                when any target already exists.
# Env:   PLUGIN_MOUNT   installed plugin root (default
#                       .tessl/plugins/gamussa/coding-policy; tests override)
# Out:   one JSON object on stdout:
#          {"state":"scaffolded|no-op","override":bool,
#           "files":[{"target":"...","action":"created|overwritten|unchanged"}]}
# Exit:  0 on success (including no-op); 1 on a refused install or a write
#        failure (targets restored first); 2 on a bad argument or missing tool.

set -euo pipefail

OVERRIDE_MODE=0
for arg in "$@"; do
  case "$arg" in
    --override) OVERRIDE_MODE=1 ;;
    *) echo "error: unknown argument '$arg' (only --override is recognized)" >&2; exit 2 ;;
  esac
done

command -v jq >/dev/null 2>&1 \
  || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

PLUGIN_MOUNT="${PLUGIN_MOUNT:-.tessl/plugins/gamussa/coding-policy}"
TEMPLATE_DIR="${PLUGIN_MOUNT}/skills/onboard-repo/templates"
# "<source under TEMPLATE_DIR>:<target in the consumer repo>"
MANIFEST=(
  "review-codex.yml.md:.github/workflows/review-codex.yml"
  "copilot-instructions.md:.github/copilot-instructions.md"
  "prompt.md:.github/codex-review/prompt.md"
  "schema.json:.github/codex-review/schema.json"
  "post-review.sh:.github/codex-review/post-review.sh"
  "mask-secrets.sh:.github/codex-review/mask-secrets.sh"
  "assert-no-secret-leak.sh:.github/codex-review/assert-no-secret-leak.sh"
)

SNAP_DIR=""
cleanup() {
  if [[ -n "$SNAP_DIR" ]] && ! rm -rf "$SNAP_DIR"; then
    echo "scaffold.sh: warning: could not remove snapshot dir ${SNAP_DIR} — remove it by hand" >&2
  fi
  return 0
}

# Restore every snapshotted target (absent-before targets are removed).
restore() {
  local entry target snap
  for entry in "${MANIFEST[@]}"; do
    target="${entry#*:}"; snap="${SNAP_DIR}/${target//\//__}"
    if [[ -f "$snap" ]]; then cp "$snap" "$target" || echo "scaffold.sh: warning: could not restore ${target}" >&2
    elif [[ -f "${snap}.absent" ]]; then rm -f "$target" || echo "scaffold.sh: warning: could not remove ${target}" >&2
    fi
  done
}

main() {
  local root
  root=$(git rev-parse --show-toplevel 2>/dev/null) \
    || { echo "error: not inside a git worktree — run from within the consumer repo" >&2; exit 1; }
  cd "$root"

  local entry src target present=() missing=()
  for entry in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${entry%%:*}"; target="${entry#*:}"
    [[ -f "$src" ]] || missing+=("$src")
    [[ -e "$target" ]] && present+=("$target")
  done
  if (( ${#missing[@]} > 0 )); then
    echo "error: template(s) not found: ${missing[*]} — run 'tessl install gamussa/coding-policy' first" >&2
    exit 1
  fi
  if (( OVERRIDE_MODE == 0 && ${#present[@]} > 0 )); then
    echo "error: reviewer file(s) already exist: ${present[*]} — re-run with --override to refresh them" >&2
    exit 1
  fi

  SNAP_DIR=$(mktemp -d) || { echo "error: mktemp failed — check TMPDIR is writable" >&2; exit 1; }
  trap cleanup EXIT

  local -a files=()
  local action changed=0
  for entry in "${MANIFEST[@]}"; do
    src="${TEMPLATE_DIR}/${entry%%:*}"; target="${entry#*:}"
    local snap="${SNAP_DIR}/${target//\//__}"
    if [[ -f "$target" ]]; then
      cp "$target" "$snap" || { echo "error: could not snapshot ${target}" >&2; exit 1; }
      if cmp -s "$src" "$target"; then
        action="unchanged"
      else
        action="overwritten"
      fi
    else
      : > "${snap}.absent"
      action="created"
    fi
    if [[ "$action" != "unchanged" ]]; then
      if ! { mkdir -p "$(dirname "$target")" && cp "$src" "$target"; }; then
        echo "error: could not write ${target} — restoring prior targets" >&2
        restore
        exit 1
      fi
      changed=1
    fi
    files+=("$(jq -nc --arg t "$target" --arg a "$action" '{target: $t, action: $a}')")
  done

  local state="no-op"
  (( changed == 0 )) || state="scaffolded"
  local override_json="false"; (( OVERRIDE_MODE == 1 )) && override_json="true"
  printf '%s\n' "${files[@]}" | jq -sc --arg s "$state" --argjson o "$override_json" '{state: $s, override: $o, files: .}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
