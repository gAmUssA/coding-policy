#!/usr/bin/env bash
# Report the live Herdr team at session start.
#
# A SessionStart hook for the lead's own pane: when this session runs inside
# Herdr alongside other NAMED agents, it names them and their lifecycle state
# so the lead opens knowing who is on the roster and who is mid-task, instead
# of dispatching a round into a busy worker (rules/agent-team-operation.md
# Dispatch Safety).
#
# Design choices, shared with the other SessionStart hooks:
#   - It DOES something (reads the live roster), it does not re-state a rule.
#   - Silent in every session that is not a team session: outside Herdr, with
#     no herdr binary, or with no named worker but this pane.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#   - The roster read is skills/herdr-teamlead/roster.sh, the same script the skill
#     calls, so "who is on the team" has one implementation.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the hook needs none of it).
#   stdout: one JSON object {"additionalContext": "<status>"} whose text begins
#           with the "Session-start status — " marker
#           (rules/hook-action-reporting.md), listing each named worker as
#           "<name> (<kind>) <state>". Emitted only when at least one named
#           worker other than this pane is live; every other outcome is silent.
#   exit  : always 0. Every best-effort failure emits an actionable stderr
#           warning and no-ops (rules/error-handling.md Shell Error Handling).
#   state : none.
#   env   : HERDR_ENV must be 1. HERDR_BIN passes through to roster.sh; the
#           tests point it at a fake herdr.
set -euo pipefail

warn() { printf 'herdr-team-status: %s\n' "$1" >&2; }

main() {
  local hook_dir roster_script roster rc=0 line

  # Not a Herdr session => no team to report. Silent no-op.
  [[ "${HERDR_ENV:-}" == "1" ]] || return 0

  hook_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  roster_script="${hook_dir}/../skills/herdr-teamlead/roster.sh"
  if [[ ! -r "$roster_script" ]]; then
    warn "roster script not found at ${roster_script} — reinstall the plugin with \`tessl install gamussa/coding-policy\`; skipping the team status"
    return 0
  fi

  command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to emit the team status"; return 0; }

  roster="$(bash "$roster_script" 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "the roster read failed (exit ${rc}) — run \`bash ${roster_script}\` to see its diagnostic; skipping the team status"
    return 0
  fi

  # No named worker but this pane => nothing worth a session-start line.
  rc=0
  line="$(printf '%s' "$roster" | jq -r '
    if ((.agents | length) == 0) then
      ""
    else
      "Session-start status — herdr team: "
      + ([.agents[] | "\(.name) (\(.kind)) \(.state)"] | join(", "))
    end' 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the roster payload (jq exit ${rc}) — run \`bash ${roster_script}\` and inspect its JSON; skipping the team status"
    return 0
  fi
  [[ -n "$line" ]] || return 0

  jq -n --arg c "$line" '{additionalContext: $c}' ||
    warn "could not emit the team status as JSON — skipping the team status"
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the script can also be sourced to unit-test its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
