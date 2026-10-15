#!/usr/bin/env bash
# Run every SessionStart hook and deliver all of their statuses in one payload.
#
# The plugin declares this script as its only SessionStart hook: native for
# Claude Code (`nativeHooks.claude-code`) and portable (`hooks`, run through
# `tessl hook run`) for every other agent Tessl configures. `tessl hook run`
# keeps only the LAST hook's output in a SessionStart group and hands a hook a
# stripped environment without HERDR_ENV (probed on tessl 0.115.0), so one entry
# that merges the statuses itself is the only way every status arrives.
#
# Modes, from TESSL_AGENT (set by `tessl hook run`, absent in a native entry):
#   - unset           : native. Emit Claude Code's hookSpecificOutput payload.
#   - claude-code     : the native entry already ran this session. Exit silent.
#   - any other agent : portable. Emit the consensus {"additionalContext"} that
#                       tessl translates, and export SESSION_START_MODE=portable
#                       so a child that would write reports instead.
#
# Contract:
#   stdin : SessionStart JSON — not read; each child gets /dev/null.
#   time  : each child runs under hooks/bounded-run.sh with CHILD_BUDGET_SEC,
#           capped by what is left of TOTAL_BUDGET_SEC; its process group is
#           killed at the deadline. A child past its deadline, or one never
#           started because the total budget was spent, gets its own status
#           line, and the list continues. The child sees its deadline as
#           SESSION_START_CHILD_DEADLINE_SEC.
#   stdout: one JSON object whose additionalContext holds two parts, each in
#           hooks/session-start-hooks.txt order and joined with a blank line:
#           first every status (a child payload beginning with STATUS_MARKER),
#           the block rules/hook-action-reporting.md relays; then every other
#           child payload (agent instructions such as the beans guide), each
#           under its own CONTEXT_HEADER line naming the hook, which ends the
#           status block. Nothing when no child reported. A child that exits non-zero or
#           prints something other than one additionalContext object becomes its
#           own "Session-start status — hook <name> ..." line naming the command
#           to rerun it, never dropped.
#   stderr: each child's stderr, passed through, plus this script's warnings.
#   exit  : always 0 — a failed child is reported, never a failed session start.
#   needs : jq to read and encode JSON. The children run either way; without
#           jq the payload is one fixed status naming the install step.
#   env   : SESSION_START_HOOKS (space-separated filenames; tests only) overrides
#           hooks/session-start-hooks.txt. SESSION_START_CHILD_BUDGET_SEC and
#           SESSION_START_BUDGET_SEC (tests only) override the budgets.
set -euo pipefail

warn() { printf 'session-start: %s\n' "$1" >&2; }

#: Seconds one child may run. Matches the Codex adapter's per-hook deadline.
CHILD_BUDGET_SEC=45
#: Seconds all children together may run.
TOTAL_BUDGET_SEC=120

#: A child payload starting with this is a status the agent relays to the user.
STATUS_MARKER="Session-start status — "
#: Opens each non-status payload, so it never reads as part of the status block.
CONTEXT_HEADER="Agent context, not a session-start status — do not relay it to the user"

#: Agents whose native SessionStart entry runs this script with the session's
#: environment; the portable run defers to it.
NATIVE_AGENTS=(claude-code)

# Print one child output's additionalContext. 0 = printed; 1 = the output is not
# one object with a string additionalContext; 2 = jq itself failed. The
# trailing "x" keeps a status's own trailing newlines through $(...).
context_of() { # <output>
  local out rc=0
  # `fromjson?` yields nothing for non-JSON, so -e exits 4: the expected
  # non-result. Any other non-zero exit is jq itself failing.
  out="$(jq -e -R -s -j 'fromjson? | select(type == "object" and (.additionalContext | type) == "string") | .additionalContext + "x"' <<<"$1")" || rc=$?
  case "$rc" in
    0) printf '%s' "${out%x}"; return 0 ;;
    4) return 1 ;;
  esac
  return 2
}

# Print the payload for <mode>. The payload is printed only once jq produced
# it, so a failed attempt never leaves partial output. 1 = jq failed.
encode() { # <mode> <text>
  local out
  out="$(jq -n --arg m "$1" --arg c "$2" \
    'if $m == "portable" then {additionalContext: $c} else {hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}} end')" || return 1
  printf '%s\n' "$out"
  return 0
}

main() {
  local mode=native agent here src dir name out rc ctx joined="" i
  local child_budget="${SESSION_START_CHILD_BUDGET_SEC:-$CHILD_BUDGET_SEC}"
  local total_budget="${SESSION_START_BUDGET_SEC:-$TOTAL_BUDGET_SEC}"
  local start budget left has_runner=0
  local -a runner=()
  local -a hooks=() names=() codes=() outputs=() statuses=() contexts=()

  if [[ -n "${TESSL_AGENT:-}" ]]; then
    for agent in "${NATIVE_AGENTS[@]}"; do
      [[ "$TESSL_AGENT" != "$agent" ]] || return 0
    done
    mode=portable
  fi
  export SESSION_START_MODE="$mode"

  # Derive the hooks directory without `cd` output surprises: CDPATH is cleared
  # and a sentinel carries a trailing newline in the path through $(...).
  src="${BASH_SOURCE[0]}"
  case "$src" in
    */*) dir="${src%/*}" ;;
    *) dir=. ;;
  esac
  here="$(CDPATH='' cd -- "${dir:-/}" && pwd && printf x)" || { warn "cannot enter the hooks directory ${dir:-/} — reinstall the plugin"; return 0; }
  here="${here%x}"
  here="${here%$'\n'}"

  if [[ -n "${SESSION_START_HOOKS:-}" ]]; then
    read -r -a hooks <<<"$SESSION_START_HOOKS"
  elif [[ -r "${here}/session-start-hooks.txt" ]]; then
    while IFS= read -r name || [[ -n "$name" ]]; do
      if [[ -n "$name" ]]; then hooks+=("$name"); fi
    done < "${here}/session-start-hooks.txt"
  else
    warn "hook list ${here}/session-start-hooks.txt is missing or unreadable — reinstall the plugin"
    statuses+=("Session-start status — session-start could not read its hook list at ${here}/session-start-hooks.txt; reinstall the plugin.")
  fi

  [[ "$child_budget" =~ ^[1-9][0-9]*$ ]] || child_budget=$CHILD_BUDGET_SEC
  [[ "$total_budget" =~ ^[1-9][0-9]*$ ]] || total_budget=$TOTAL_BUDGET_SEC
  if [[ -r "${here}/bounded-run.sh" ]]; then
    has_runner=1
  else
    warn "deadline runner ${here}/bounded-run.sh is missing — reinstall the plugin; children run without a deadline"
  fi

  # Run every child first, so a missing JSON tool never stops one from acting.
  # Codes: the child's own exit, 124 past its deadline, -1 never started.
  start=$SECONDS
  for name in ${hooks[@]+"${hooks[@]}"}; do
    left=$(( total_budget - (SECONDS - start) ))
    if (( left <= 0 )); then
      names+=("$name"); codes+=(-1); outputs+=("")
      continue
    fi
    budget=$(( left < child_budget ? left : child_budget ))
    # A child with deadlines of its own keeps them inside this one.
    export SESSION_START_CHILD_DEADLINE_SEC="$budget"
    runner=()
    if (( has_runner )); then runner=(bash "${here}/bounded-run.sh" "$budget"); fi
    rc=0
    out="$(${runner[@]+"${runner[@]}"} bash "${here}/${name}" </dev/null)" || rc=$?
    names+=("$name"); codes+=("$rc"); outputs+=("$out")
  done

  # Without jq no status can be read or encoded. The fixed payload needs no
  # escaping, so it is printed as a literal.
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq is not on PATH — the hooks ran, but their statuses cannot be delivered; install jq"
    if [[ "$mode" == portable ]]; then
      printf '%s\n' '{"additionalContext":"Session-start status — session-start: jq is not installed, so no session-start status could be delivered. Install jq, then start a new session."}'
    else
      printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Session-start status — session-start: jq is not installed, so no session-start status could be delivered. Install jq, then start a new session."}}'
    fi
    return 0
  fi

  for (( i = 0; i < ${#names[@]}; i++ )); do
    name="${names[i]}"
    if [[ "${codes[i]}" == -1 ]]; then
      statuses+=("Session-start status — hook ${name} did not run: the ${total_budget}s session-start budget was spent; run \`bash ${here}/${name}\` from this repo.")
      continue
    fi
    if [[ "${codes[i]}" == 124 ]] && (( has_runner )); then
      statuses+=("Session-start status — hook ${name} ran past its deadline and was stopped; run \`bash ${here}/${name}\` from this repo to see where it waits.")
      continue
    fi
    if [[ "${codes[i]}" != 0 ]]; then
      statuses+=("Session-start status — hook ${name} exited ${codes[i]}; run \`bash ${here}/${name}\` from this repo to see why.")
      continue
    fi
    [[ -n "${outputs[i]//[[:space:]]/}" ]] || continue
    rc=0
    ctx="$(context_of "${outputs[i]}")" || rc=$?
    case "$rc" in
      0)
        if [[ "$ctx" == "$STATUS_MARKER"* ]]; then
          statuses+=("$ctx")
        else
          contexts+=("${CONTEXT_HEADER} (from ${name}):"$'\n'"${ctx}")
        fi ;;
      1) statuses+=("Session-start status — hook ${name} printed something other than one additionalContext object; run \`bash ${here}/${name}\` from this repo to see it.") ;;
      *) statuses+=("Session-start status — session-start could not parse hook ${name}'s output: jq failed. Repair jq, then start a new session.") ;;
    esac
  done

  local -a parts=(${statuses[@]+"${statuses[@]}"} ${contexts[@]+"${contexts[@]}"})
  (( ${#parts[@]} > 0 )) || return 0
  for (( i = 0; i < ${#parts[@]}; i++ )); do
    (( i == 0 )) || joined+=$'\n\n'
    joined+="${parts[i]}"
  done
  encode "$mode" "$joined" ||
    warn "jq could not encode the merged status — this session gets none; run \`bash ${here}/session-start.sh\` to see why"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
