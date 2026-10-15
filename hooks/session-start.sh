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
#   stdout: one JSON object whose additionalContext joins every child's
#           additionalContext with a blank line, in hooks/session-start-hooks.txt
#           order. Nothing when no child reported. A child that exits non-zero or
#           prints something other than one additionalContext object becomes its
#           own "Session-start status — hook <name> ..." line naming the command
#           to rerun it, never dropped.
#   stderr: each child's stderr, passed through, plus this script's warnings.
#   exit  : always 0 — a failed child is reported, never a failed session start.
#   needs : jq or python3 to read and encode JSON (jq first). The children run
#           either way; with neither, a warning says the statuses were lost.
#   env   : SESSION_START_HOOKS (space-separated filenames; tests only) overrides
#           hooks/session-start-hooks.txt.
set -euo pipefail

warn() { printf 'session-start: %s\n' "$1" >&2; }

#: Agents whose native SessionStart entry runs this script with the session's
#: environment; the portable run defers to it.
NATIVE_AGENTS=(claude-code)

# Print one child output's additionalContext. 0 = printed; 1 = the output is not
# one object with a string additionalContext; 2 = no parser could answer. The
# trailing "x" keeps a status's own trailing newlines through $(...).
context_of() { # <output>
  local out rc=0
  if command -v jq >/dev/null 2>&1; then
    # `fromjson?` yields nothing for non-JSON, so -e exits 4: the expected
    # non-result. Any other non-zero exit is jq itself failing.
    out="$(jq -e -R -s -j 'fromjson? | select(type == "object" and (.additionalContext | type) == "string") | .additionalContext + "x"' <<<"$1")" || rc=$?
    case "$rc" in
      0) printf '%s' "${out%x}"; return 0 ;;
      4) return 1 ;;
    esac
  fi
  if command -v python3 >/dev/null 2>&1; then
    rc=0
    out="$(python3 -c '
import json, sys
try:
    doc = json.loads(sys.argv[1])
except ValueError:
    sys.exit(3)
ctx = doc.get("additionalContext") if isinstance(doc, dict) else None
if not isinstance(ctx, str):
    sys.exit(3)
sys.stdout.write(ctx + "x")
' "$1")" || rc=$?
    case "$rc" in
      0) printf '%s' "${out%x}"; return 0 ;;
      3) return 1 ;;
    esac
  fi
  return 2
}

# Print the payload for <mode>. The payload is printed only once a tool produced
# it, so a failed attempt never leaves partial output. 1 = both tools failed.
encode() { # <mode> <text>
  local out
  if command -v jq >/dev/null 2>&1 && out="$(jq -n --arg m "$1" --arg c "$2" \
      'if $m == "portable" then {additionalContext: $c} else {hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}} end')"; then
    printf '%s\n' "$out"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 && out="$(python3 -c '
import json, sys
mode, text = sys.argv[1], sys.argv[2]
doc = {"additionalContext": text} if mode == "portable" else {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
print(json.dumps(doc))
' "$1" "$2")"; then
    printf '%s\n' "$out"
    return 0
  fi
  return 1
}

main() {
  local mode=native agent here src dir name out rc ctx joined="" i
  local -a hooks=() names=() codes=() outputs=() statuses=()

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

  # Run every child first, so a missing JSON tool never stops one from acting.
  for name in ${hooks[@]+"${hooks[@]}"}; do
    rc=0
    out="$(bash "${here}/${name}" </dev/null)" || rc=$?
    names+=("$name"); codes+=("$rc"); outputs+=("$out")
  done

  if ! command -v jq >/dev/null 2>&1 && ! command -v python3 >/dev/null 2>&1; then
    warn "neither jq nor python3 is on PATH — the hooks ran, but their statuses cannot be delivered; install jq"
    return 0
  fi

  for (( i = 0; i < ${#names[@]}; i++ )); do
    name="${names[i]}"
    if [[ "${codes[i]}" != 0 ]]; then
      statuses+=("Session-start status — hook ${name} exited ${codes[i]}; run \`bash ${here}/${name}\` from this repo to see why.")
      continue
    fi
    [[ -n "${outputs[i]//[[:space:]]/}" ]] || continue
    rc=0
    ctx="$(context_of "${outputs[i]}")" || rc=$?
    case "$rc" in
      0) statuses+=("$ctx") ;;
      1) statuses+=("Session-start status — hook ${name} printed something other than one additionalContext object; run \`bash ${here}/${name}\` from this repo to see it.") ;;
      *) statuses+=("Session-start status — session-start could not parse hook ${name}'s output: jq and python3 both failed. Repair one of them, then start a new session.") ;;
    esac
  done

  (( ${#statuses[@]} > 0 )) || return 0
  for (( i = 0; i < ${#statuses[@]}; i++ )); do
    (( i == 0 )) || joined+=$'\n\n'
    joined+="${statuses[i]}"
  done
  encode "$mode" "$joined" ||
    warn "neither jq nor python3 could encode the merged status — this session gets none; run \`bash ${here}/session-start.sh\` to see why"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
