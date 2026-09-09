#!/usr/bin/env bash
# Emit the Herdr worker roster the team-lead skill dispatches a round to.
#
# The roster lists only NAMED live agents whose pane is not the caller's. A
# name is the dispatch handle: `herdr agent prompt` takes a unique live agent
# name or the pane id hosting it, and a name follows the pane occupant, so an
# unnamed pane has nothing stable to address. Name one with
# `herdr agent rename <pane-id> <name>` before the round. The caller's own
# pane is excluded so a lead never dispatches a brief to itself.
#
# Contract:
#   argv  : none.
#   stdout: one JSON object —
#           {"caller":{"pane_id":"<id>"},
#            "agents":[{"name":"<n>","kind":"<k>","pane_id":"<p>","state":"<s>"}, ...]}
#           `state` is Herdr's agent lifecycle state (idle|working|blocked|
#           done|unknown) at the moment of the call, a hint the caller
#           re-checks before sending input, never a completion verdict.
#   stderr: diagnostics only.
#   exit  : 0 roster emitted — an empty `agents` array is a valid roster,
#           1 precondition unmet (not inside Herdr, no caller pane id,
#             `herdr` or `jq` absent),
#           2 herdr error, or an `herdr agent list` payload this cannot parse.
#   env   : HERDR_ENV must be 1 (Herdr sets it inside a managed pane).
#           HERDR_PANE_ID identifies the caller's pane (Herdr sets it too).
#           HERDR_BIN overrides the herdr binary; the tests point it at a fake.
set -euo pipefail

HERDR_BIN="${HERDR_BIN:-herdr}"

ERRFILE=""

warn() { printf 'roster: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

main() {
  local caller list roster rc=0

  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run the team round from a pane Herdr manages, or start this agent with \`herdr agent start\`"
    return 1
  fi
  if ! command -v "$HERDR_BIN" >/dev/null 2>&1; then
    warn "'${HERDR_BIN}' not found on PATH — install the herdr CLI (https://herdr.dev) or point HERDR_BIN at the binary"
    return 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to parse the herdr payload"
    return 1
  fi

  caller="${HERDR_PANE_ID:-}"
  if [[ -z "$caller" ]]; then
    warn "HERDR_PANE_ID is empty — Herdr injects it into every managed pane; re-run from the lead's own pane"
    return 1
  fi

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  list="$("$HERDR_BIN" agent list 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent list\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run it by hand to inspect the session"
    return 2
  fi

  # `.result.agents` is the documented array; anything else is a payload shape
  # this script cannot read, which is a tool failure, never an empty roster.
  rc=0
  roster="$(printf '%s' "$list" | jq --arg caller "$caller" '
    if (.result.agents | type) != "array" then
      error("herdr agent list payload has no .result.agents array")
    else
      { caller: { pane_id: $caller },
        agents: [ .result.agents[]
                  | select((.name? | type) == "string" and (.name | length) > 0)
                  | select(.pane_id != $caller)
                  | { name: .name,
                      kind: (.agent // "unknown"),
                      pane_id: (.pane_id // "unknown"),
                      state: (.agent_status // "unknown") } ] }
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the herdr agent list payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${HERDR_BIN} agent list\` and inspect its JSON"
    return 2
  fi

  printf '%s\n' "$roster"
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the tests can source the file to exercise its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
