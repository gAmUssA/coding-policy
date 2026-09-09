#!/usr/bin/env bash
# Ask one worker for its standup, as a plain message.
#
# The prompt is fixed text, the reply shape is fixed, and the report path is
# the worker's only channel back — so the whole thing is one deterministic
# send (`rules/script-delegation.md`). What the lead does with the answers is
# the reasoning part, and that stays in the skill.
#
# The prompt goes out as a MESSAGE, never a slash command: a standup question
# is prose, and every slash-delivery quirk this fleet has hit (Grok reading a
# pasted `/usage` as chat, Codex swallowing the Enter behind its autocomplete)
# belongs to commands, not messages.
#
# Contract:
#   argv  : <agent-name> <report-path>
#           report-path must be absolute; the worker writes its four lines there.
#   stdout: one JSON object —
#           {"agent":"<n>","report_path":"<p>","state":"<s>","sent":true}
#   stderr: diagnostics only.
#   exit  : 0 the prompt was accepted by a worker that was idle or done,
#           1 precondition unmet (usage, relative or over-long path, not inside Herdr,
#             `herdr` or `jq` absent),
#           2 a herdr failure, or an unreadable `agent get` payload,
#           3 the worker is not idle or done — nothing was sent. A standup
#             never interrupts a turn (`rules/agent-team-operation.md`
#             Dispatch Safety).
#   env   : HERDR_BIN overrides the herdr binary; the tests point it at a fake.
#           STANDUP_REPORT_PATH_MAX_COLS overrides the report path length
#           limit; a non-integer or zero value is a precondition failure.
set -euo pipefail

HERDR_BIN="${HERDR_BIN:-herdr}"

# States that may receive a message. Anything else is left alone.
READY_STATES="idle done"
# Longest report path the prompt may name; the same limit compose-briefs.sh
# applies to a brief's REPORT, for the same reason: the worker's final
# `REPORT: <path>` line must fit one pane row for the wait to confirm it.
STANDUP_REPORT_PATH_MAX_COLS="${STANDUP_REPORT_PATH_MAX_COLS:-100}"

ERRFILE=""
AGENT=""
REPORT_PATH=""

warn() { printf 'standup-ask: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

# The standup question. One message, four lines back, each capped so the table
# stays readable and a worker cannot answer with an essay.
standup_prompt() { # <report-path>
  printf '%s' "\
Daily standup. Answer with EXACTLY four lines, nothing before or after, and \
write the same four lines to ${1} (create the parent directory if needed):

DONE: <what you finished since the last standup, at most 25 words>
PLAN: <what you are doing next, at most 20 words>
BLOCKED: <what is blocking you, or the single word none>
REPORT: ${1}

No preamble, no markdown, no bullet points. If you have done nothing since the \
last standup, say so in DONE. Do not start any new work: answer, write the \
file, and stop."
}

main() {
  if (( $# != 2 )); then
    warn "usage: standup-ask.sh <agent-name> <report-path>"
    return 1
  fi
  AGENT="$1"
  REPORT_PATH="$2"

  if [[ "$REPORT_PATH" != /* ]]; then
    warn "report path '${REPORT_PATH}' is relative — pass an absolute path; the worker resolves it in its own working directory, not yours"
    return 1
  fi
  case "$STANDUP_REPORT_PATH_MAX_COLS" in
    ''|*[!0-9]*)
      warn "STANDUP_REPORT_PATH_MAX_COLS must be a positive integer, got '${STANDUP_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
      return 1
      ;;
  esac
  if (( 10#$STANDUP_REPORT_PATH_MAX_COLS < 1 )); then
    warn "STANDUP_REPORT_PATH_MAX_COLS must be a positive integer, got '${STANDUP_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
    return 1
  fi
  # Normalize to decimal once, so a validated `08` is not reparsed as octal.
  STANDUP_REPORT_PATH_MAX_COLS=$(( 10#$STANDUP_REPORT_PATH_MAX_COLS ))
  if (( ${#REPORT_PATH} > STANDUP_REPORT_PATH_MAX_COLS )); then
    warn "report path is ${#REPORT_PATH} characters; the limit is ${STANDUP_REPORT_PATH_MAX_COLS} so the worker's \`REPORT: <path>\` line fits one pane row — use a shorter reports directory (e.g. one under \$HOME/.local/state)"
    return 1
  fi
  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run the standup from a pane Herdr manages"
    return 1
  fi
  local dep
  for dep in "$HERDR_BIN" jq; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      warn "'${dep}' not found on PATH — install it to run the standup"
      return 1
    fi
  done

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  local raw rc=0 state
  raw="$("$HERDR_BIN" agent get "$AGENT" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent get ${AGENT}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${HERDR_BIN} agent list\` to see the live names"
    return 2
  fi
  rc=0
  state="$(printf '%s' "$raw" | jq -r '
    if (.result.agent | type) != "object" then
      error("herdr agent get payload has no .result.agent object")
    else
      .result.agent.agent_status // "unknown"
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the state from the herdr agent get payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  # A standup is worth less than somebody's turn. A worker that is not ready
  # keeps working, and the lead fills its row from the round log instead.
  if [[ " $READY_STATES " != *" $state "* ]]; then
    warn "${AGENT} is '${state}' — not asking. Fill its row from the round log."
    jq -n --arg a "$AGENT" --arg p "$REPORT_PATH" --arg s "$state" \
      '{agent: $a, report_path: $p, state: $s, sent: false}'
    return 3
  fi

  rc=0
  "$HERDR_BIN" agent prompt "$AGENT" "$(standup_prompt "$REPORT_PATH")" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent prompt ${AGENT}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  jq -n --arg a "$AGENT" --arg p "$REPORT_PATH" --arg s "$state" \
    '{agent: $a, report_path: $p, state: $s, sent: true}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
