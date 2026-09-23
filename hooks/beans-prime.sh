#!/usr/bin/env bash
# Load the beans CLI's usage guide at session start in a repo that tracks work
# with beans.
#
# A SessionStart hook implementing rules/work-tracking.md as a deterministic
# step: when the session's directory (or an ancestor) carries `.beans.yml`, run
# `beans prime` and inject its output — the CLI's own agent-facing usage guide —
# as additionalContext. Consumers wired this by hand three different ways (a
# native settings.json hook, a CLAUDE.md "run beans prime first" line, nothing);
# the plugin does it once for every agent Tessl configures.
#
# Design choices, shared with the other SessionStart hooks:
#   - It DOES something (runs the CLI), it does not re-state a rule.
#   - Silent outside a beans repo: no `.beans.yml` on the path means nothing to
#     load, and no stderr either.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#   - No `Session-start status —` marker: the payload is agent instructions, not
#     a status the agent relays to the user (rules/hook-action-reporting.md).
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: in a beans repo, one JSON object {"additionalContext": "<guide>"}
#           carrying `beans prime`'s stdout verbatim. Nothing otherwise.
#   exit  : always 0. A beans repo whose CLI is missing, fails, or prints nothing
#           warns to stderr with the install/inspect step and emits nothing
#           (rules/error-handling.md Shell Error Handling).
#   state : none.
#   env   : BEANS_PRIME_START (test-only starting directory; defaults to $PWD).
set -euo pipefail

warn() { printf 'beans-prime: %s\n' "$1" >&2; }

# Echo the directory holding `.beans.yml`, searching upward from <start>, or
# return 1 when none is found. Mirrors the CLI's own config search.
find_beans_root() { # <start>
  local dir="$1"
  while :; do
    [[ -f "$dir/.beans.yml" ]] && { printf '%s' "$dir"; return 0; }
    [[ "$dir" == "/" || -z "$dir" ]] && return 1
    dir="$(dirname "$dir")"
  done
}

main() {
  local start root out rc=0
  start="${BEANS_PRIME_START:-$PWD}"
  root="$(find_beans_root "$start")" || return 0

  command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to load the beans guide"; return 0; }
  command -v beans >/dev/null 2>&1 || {
    warn "${root}/.beans.yml is present but the beans CLI is not on PATH — install it (brew install beans) so the work tracker's guide loads (rules/work-tracking.md)"
    return 0
  }

  # Run from the beans root so the CLI's upward config search matches ours.
  out="$(cd "$root" && beans prime 2>"${TMPDIR:-/tmp}/beans-prime.$$")" || rc=$?
  local err=""
  [[ -r "${TMPDIR:-/tmp}/beans-prime.$$" ]] && err="$(tr '\n' ' ' < "${TMPDIR:-/tmp}/beans-prime.$$")"
  rm -f "${TMPDIR:-/tmp}/beans-prime.$$" || warn "could not remove temp file ${TMPDIR:-/tmp}/beans-prime.$$"
  if (( rc != 0 )); then
    warn "beans prime failed (exit ${rc}) in ${root}: ${err:-no diagnostic} — run \`beans check\` there; the guide was not loaded"
    return 0
  fi
  if [[ -z "$out" ]]; then
    warn "beans prime printed nothing in ${root} — run \`beans check\` there; the guide was not loaded"
    return 0
  fi

  jq -n --arg c "$out" '{additionalContext: $c}' ||
    warn "could not emit the beans guide as JSON — the guide was not loaded"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
