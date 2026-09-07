#!/usr/bin/env bash
# Language-diagnostics gate (rules/language-diagnostics.md): run the
# headless engines over the first-party trees and fail on any finding so
# CI gates at zero. shellcheck is the engine behind bash-language-server;
# pyright is the Python engine (scope: pyrightconfig.json). Mirrors
# scripts/run-tests.sh — CI calls it on every PR (tests.yml) before the
# unit suites.
#
# Both engines always run (explicit rc capture, not error suppression per
# rules/error-handling.md) so one invocation surfaces every finding.
#
# `set -uo pipefail`, not `set -euo pipefail`: this script takes
# rules/error-handling.md Shell Error Handling's carve-out for a script
# running independent checks and reporting an aggregate. All four
# preconditions hold — shellcheck and pyright are independent; each rc is
# captured explicitly and aggregated into a non-zero exit; only `-e` is
# dropped; and the two setup steps that gate the checks — base-dir presence
# and engine presence — carry their own explicit checks below (per the
# carve-out's silent-corruption clause; the engines' own runs need no such
# guard, since their exit codes are exactly what this script captures).
#
# Output:
#   stderr: each engine's native findings plus per-engine progress.
# Usage: scripts/run-diagnostics.sh [base-dir]
#   base-dir  Tree whose skills/, scripts/, hooks/, .github/actions/, and
#             .github/codex-review/ are scanned for shell scripts.
#             Defaults to the repo root. The
#             optional arg exists so the runner's own test can point it at
#             a fixture tree.
#             pyright always runs against pyrightconfig.json at the cwd,
#             independent of base-dir.
# Exit: 0 if both engines report zero findings, 1 on any finding, 2 on
#       setup error (base dir missing, no scripts found, engine absent).

set -uo pipefail

# Best-effort temp cleanup. Without `set -e` a failing bare `rm` here would
# leave the tmpfile with no signal at all; warn, never silence
# (rules/error-handling.md Shell Error Handling).
discard() {
  local f="$1"
  if [[ -n "$f" ]] && ! rm -f "$f"; then
    echo "run-diagnostics: warning: could not remove temp file ${f} — remove it by hand" >&2
  fi
}

main() {
  local base="${1:-}"
  if [[ -z "$base" ]]; then
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  if [[ ! -d "$base" ]]; then
    echo "run-diagnostics: base dir not found: $base" >&2
    return 2
  fi

  local engine
  for engine in shellcheck pyright; do
    if ! command -v "$engine" >/dev/null 2>&1; then
      echo "run-diagnostics: $engine not installed — install it in CI (see .github/workflows/tests.yml)" >&2
      return 2
    fi
  done

  local roots=()
  [[ -d "$base/skills" ]] && roots+=("$base/skills")
  [[ -d "$base/scripts" ]] && roots+=("$base/scripts")
  # First-party hook scripts (hooks/*.sh + their tests) — gate them at the
  # same zero-findings bar as every other shipped shell script.
  [[ -d "$base/hooks" ]] && roots+=("$base/hooks")
  # Composite-action bodies extracted to real scripts per
  # rules/script-delegation.md — gate them too so an action script is held
  # to the same zero-findings bar.
  [[ -d "$base/.github/actions" ]] && roots+=("$base/.github/actions")
  # The policy-reviewer driver scripts (post-review.sh, mask-secrets.sh,
  # assert-no-secret-leak.sh + their tests) are deterministic shell held to
  # the same bar.
  [[ -d "$base/.github/codex-review" ]] && roots+=("$base/.github/codex-review")
  if [[ ${#roots[@]} -eq 0 ]]; then
    echo "run-diagnostics: none of skills/, scripts/, hooks/, .github/actions/, .github/codex-review/ found under $base" >&2
    return 2
  fi

  # NUL-safe discovery (a Unix path may contain spaces or newlines). A temp
  # file keeps find's exit observable through the pipe (see run-tests.sh).
  local tmplist; tmplist="$(mktemp)"
  if ! find "${roots[@]}" -type f -name '*.sh' -print0 | sort -z > "$tmplist"; then
    discard "$tmplist"
    echo "run-diagnostics: shell-script discovery (find) failed under $base" >&2
    return 2
  fi
  local scripts=()
  local f
  while IFS= read -r -d '' f; do
    [[ -n "$f" ]] && scripts+=("$f")
  done < "$tmplist"
  discard "$tmplist"

  if [[ ${#scripts[@]} -eq 0 ]]; then
    echo "run-diagnostics: no shell scripts found under ${base}/{skills,scripts,.github/actions,.github/codex-review}" >&2
    return 2
  fi

  local rc=0

  echo "▶ shellcheck (${#scripts[@]} script(s))" >&2
  if shellcheck "${scripts[@]}"; then
    echo "  ✓ shellcheck clean" >&2
  else
    echo "  ✗ shellcheck findings" >&2
    rc=1
  fi
  echo "" >&2

  echo "▶ pyright" >&2
  if pyright; then
    echo "  ✓ pyright clean" >&2
  else
    echo "  ✗ pyright findings" >&2
    rc=1
  fi
  echo "" >&2

  echo "─────────────────────────────────────────────" >&2
  if [[ $rc -eq 0 ]]; then
    echo "PASSED: diagnostics clean." >&2
  else
    echo "FAILED: diagnostics findings above." >&2
  fi

  return $rc
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
