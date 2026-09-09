#!/usr/bin/env bash
# Wait for one worker's round report to land, then report what was observed.
#
# Completion is TWO signals, never one: the report FILE exists on disk AND the
# worker's pane shows the `REPORT: ` marker line its brief ends with, carrying
# THIS report's exact absolute path on one unquoted, unfenced row. Known native
# decoration requires completed source-message proof; the source and display
# contracts live in teamlead/report_delivery.py. Each attempt uses a fresh
# report path, checked at brief composition. Herdr's
# lifecycle state alone does not decide it — a Claude Code pane reports `done`
# between tool calls while the turn is still running, and a Grok pane reports
# `working` while idle at startup, so a single idle/done observation would end
# the wait on a worker that has produced nothing.
#
# Contract:
#   argv  : [--once] <agent-name> <report-path>
#           --once checks current evidence without waiting for future work.
#                  Existing blocked/refusal confirmations still run. A present
#                  unconfirmed report gets the normal consecutive-read check.
#                  With --once, exit 1 means a pending checkpoint.
#           agent-name  a live Herdr agent name (or the pane id hosting it).
#           report-path absolute path the brief told that worker to write; a
#                       relative path is refused (exit 2).
#   stdout: one JSON object on every terminal outcome except exit 2, which
#           leaves stdout empty (its diagnostic is on stderr) —
#           {"agent":"<n>","state":"<s>","report_path":"<p>",
#            "found":<bool>,"elapsed_seconds":<int>}
#           plus "reason":"<why>" on exits 4 and 5.
#   stderr: diagnostics and per-attempt progress.
#   exit  : 0 report found (`found` true),
#           1 pending checkpoint with --once; otherwise wait budget exhausted
#             (`found` false, `state` last observed),
#           2 usage error, precondition unmet, or a herdr/tool failure,
#           3 the worker is blocked at an approval or question dialog,
#             confirmed by two reads and the pane
#             (`found` false) — inspect the dialog with
#             `herdr agent read <name> --source visible` before answering it,
#           4 the report FILE is present and the worker has read idle or done
#             on consecutive polls, yet the marker could not be confirmed
#             (`found` false, `reason` set) — never delivery: a marker the
#             pane wrapped cannot be told from a newline. The skill re-runs
#             for a blocked or working worker. Owner recover-report can append
#             source-evidenced delivery for a completed affected dispatch;
#             otherwise it records no report. Old receipts stay unchanged.
#           5 this attempt's report is unavailable after a terminal provider
#             refusal: two idle/done observations in the same pane, with an
#             unchanged terminal notice directly above an empty composer at
#             the live bottom of the same terminal session, and
#             no report file (`found` false, `reason` terminal_provider_refusal).
#             Report the unavailable attempt; never retry, rephrase, switch
#             providers/models, or synthesize the missing report automatically.
#   env   : HERDR_ENV must be 1. HERDR_BIN overrides the herdr binary.
#           Poll interval, give-up budget, and the pane-probe parameters are
#           the named constants below (rules/ci-safety.md Always Watch CI —
#           poll interval and budget are script-owned, never agent-chosen);
#           each is env-overridable for tests and for a longer-running round.
set -euo pipefail

# Seconds between attempts. One attempt always runs before the budget check,
# so a zero budget still probes once.
TEAMLEAD_WAIT_INTERVAL_SEC="${TEAMLEAD_WAIT_INTERVAL_SEC:-15}"
# Give-up budget in seconds. A worker round on a real task runs long; this is
# the point at which the lead inspects the pane by hand instead of waiting.
TEAMLEAD_WAIT_BUDGET_SEC="${TEAMLEAD_WAIT_BUDGET_SEC:-5400}"
# Consecutive polls on which the report file exists AND the worker reads idle
# or done AND the marker is still unconfirmed before the wait gives up with
# exit 4 instead of sitting on the budget. Two, so a `done` flicker between a
# worker's tool calls cannot end the wait by itself. Exit 4 is a diagnostic,
# never a completion.
TEAMLEAD_UNCONFIRMED_IDLE_READS="${TEAMLEAD_UNCONFIRMED_IDLE_READS:-2}"

# Every numeric override is validated before it reaches arithmetic, `sleep`,
# or a herdr argument: a bad override must fail as exit 2 with a diagnostic,
# never as a bash arithmetic abort or a tool error with no JSON and no named
# cause. Counts that must be at least one use the positive form; seconds may
# be zero (the tests run with zero intervals and budgets).
validate_nonneg_int() { # <name> <value>
  case "$2" in
    ''|*[!0-9]*)
      warn "$1 must be a non-negative integer, got '${2}' — unset it to use the script's default"
      return 2
      ;;
  esac
  return 0
}
validate_positive_int() { # <name> <value>
  case "$2" in
    ''|*[!0-9]*)
      warn "$1 must be a positive integer, got '${2}' — unset it to use the script's default"
      return 2
      ;;
  esac
  # Digits only from here; compare in base 10 so `00` and `000` read as zero
  # rather than slipping past a literal-"0" test.
  if (( 10#$2 < 1 )); then
    warn "$1 must be a positive integer, got '${2}' — unset it to use the script's default"
    return 2
  fi
  return 0
}
# Per-attempt pane-probe timeout in milliseconds. `herdr pane wait-output`
# searches the existing snapshot first, so this bounds one probe, not the wait.
TEAMLEAD_PROBE_TIMEOUT_MS="${TEAMLEAD_PROBE_TIMEOUT_MS:-2000}"
# Rows of the visible snapshot searched for the marker. Claude Code and Grok
# render on the alternate screen, so rows that scrolled off are unrecoverable;
# the marker is the LAST line of the final message and stays on screen.
TEAMLEAD_PROBE_LINES="${TEAMLEAD_PROBE_LINES:-40}"
# Seconds between the two reads that a `blocked` verdict has to survive. Herdr
# flickered `blocked` for a single read on a Codex pane running in Full Access,
# where a permission prompt resolves itself before anything can see it; the
# script reported a dialog that was never on screen, with elapsed_seconds 0.
TEAMLEAD_BLOCKED_CONFIRM_SEC="${TEAMLEAD_BLOCKED_CONFIRM_SEC:-5}"
# A terminal provider notice must survive a separate live-state and viewport
# read. This confirmation is separate from human-dialog handling.
TEAMLEAD_REFUSAL_CONFIRM_SEC="${TEAMLEAD_REFUSAL_CONFIRM_SEC:-5}"

# Literal rows that mean a dialog really is waiting for a human, matched
# case-insensitively against the visible pane. One per line, any kind's markers
# accepted for any worker: a marker list keyed by kind would need the kind at
# every call site, and a false MATCH here only costs a second read that already
# said `blocked`.
#   Codex   `Press enter to continue`, `Allow`, numbered choices (`1.` / `2.`)
#   Claude  `Do you want to`
#   Grok    bracketed choice rows (`[Opt in]`, `[Yes]`, `[No]`)
TEAMLEAD_DIALOG_MARKERS="${TEAMLEAD_DIALOG_MARKERS:-Press enter to continue
Do you want to
Allow
[Opt in]
[Yes]
[No]}"

# The literal the brief requires at the head of the final message's last line.
# Matched with `--match`, never `--regex`: it is a literal, and a regex engine
# would only add a second opinion about what its space means.
#
# The prefix ALONE is not proof. A hit only triggers a confirming read. That
# read must contain the complete expected marker on one row; names elsewhere
# in the window, quoted examples and wrapped fragments are not delivery.
REPORT_MARKER='REPORT: '

# Fleet checkpoints do not block on the prefix wait or the long round budget.
# Positive milliseconds keep Herdr's timeout contract intact.
CHECK_PROBE_TIMEOUT_MS=1
CHECK_CONFIRM_SEC=1

HERDR_BIN="${HERDR_BIN:-herdr}"

ERRFILE=""

# Set by main from argv. Initialized here rather than only there so every
# function that reads them is safe under `set -u` whatever the call order --
# an unset read aborts the script mid-flight, after output has already gone out
# (rules/error-handling.md: fail visibly, never half-way).
AGENT=""
REPORT_PATH=""
REFUSAL_STATE=""
REFUSAL_PANE=""

warn() { printf 'wait-report: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

emit() { # <state> <found-bool> <elapsed-seconds> [reason]
  # `reason` appears only when set: the object stays the documented shape on
  # every outcome, with one extra field for an unavailable delivery.
  jq -n --arg a "$AGENT" --arg s "$1" --arg p "$REPORT_PATH" \
        --argjson f "$2" --argjson e "$3" --arg r "${4:-}" \
    '{agent: $a, state: $s, report_path: $p, found: $f, elapsed_seconds: $e}
     + (if $r == "" then {} else {reason: $r} end)'
}

# Echo "<state> <pane_id>" for the agent, or return 2 on a herdr failure.
agent_info() { # <agent-name>
  local raw rc=0 parsed
  raw="$("$HERDR_BIN" agent get "$1" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent get $1\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${HERDR_BIN} agent list\` to see the live names"
    return 2
  fi
  rc=0
  parsed="$(printf '%s' "$raw" | jq -r '
    if (.result.agent | type) != "object" then
      error("herdr agent get payload has no .result.agent object")
    else
      "\(.result.agent.agent_status // "unknown") \(.result.agent.pane_id // "unknown")"
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the herdr agent get payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi
  printf '%s\n' "$parsed"
  return 0
}

# 0 = this worker's marker is on screen, 1 = not yet (the expected no-result),
# 2 = tool failure.
#
# Two steps, and both must hold. `pane wait-output` waits on the prefix, which
# is event-driven and cheap; a hit is then confirmed by reading the same window
# and requiring the report's exact marker in it, so the previous round's line or
# another worker's cannot complete this wait. A no-match is exit 1 with an
# {"error":{"code":"timeout"}} payload on stderr; every other error code is a
# real failure and must not read as "the worker is still working"
# (rules/error-handling.md — distinguish an expected non-result from a fault).
marker_seen() { # <pane-id> <absolute-report-path>
  local rc=0 code text
  # Argument order follows herdr's own usage line -- `pane wait-output
  # [OPTIONS] <--match|--regex> <PANE_ID>` -- and the builder in
  # skills/herdr-teamlead/teamlead/herdr.py, so the two surfaces cannot drift.
  "$HERDR_BIN" pane wait-output \
    --match "$REPORT_MARKER" \
    --source visible \
    --lines "$TEAMLEAD_PROBE_LINES" \
    --timeout "$TEAMLEAD_PROBE_TIMEOUT_MS" \
    "$1" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    code="$(jq -r '.error.code // "unparseable"' < "$ERRFILE" 2>/dev/null)" || code="unparseable"
    if [[ "$code" == "timeout" ]]; then return 1; fi
    warn "\`${HERDR_BIN} pane wait-output $1\` failed (exit ${rc}, code ${code}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  # The prefix is on screen. Read the same window and confirm whose report it
  # announces. `pane read` is used rather than `agent read`, which refuses with
  # `agent_not_idle` on exactly the working pane this has to inspect.
  rc=0
  text="$("$HERDR_BIN" pane read "$1" \
    --source visible \
    --lines "$TEAMLEAD_PROBE_LINES" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} pane read $1\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — the marker was seen but could not be confirmed"
    return 2
  fi
  # Visible rows cannot prove that a newline was a soft wrap. Never join them
  # or independently match the prefix and a filename somewhere in the pane.
  if report_marker_on_screen "$text" "$2"; then return 0; fi
  # UI decoration is accepted only when the completed native source proves
  # the authored final row was bare. The helper owns source/UI allowlists.
  local result skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  rc=0
  result="$(printf '%s' "$text" | bash "$skill_dir/teamlead.sh" probe-report \
    --herdr-bin "$HERDR_BIN" --agent "$AGENT" --pane "$1" --report "$2" \
    --lines "$TEAMLEAD_PROBE_LINES")" || rc=$?
  if (( rc != 0 )); then
    warn "native report-source verification failed — restore the named evidence/tool before deciding delivery"
    return 2
  fi
  if [[ "$(printf '%s' "$result" | jq -r '.found')" == true ]]; then return 0; fi
  return 1
}

# Does the visible pane show a dialog waiting on a human?
#
# 0 = a marker is on screen, 1 = none, 2 = the pane could not be read. A pane
# this cannot read is NOT a dialog: an unreadable pane must never promote a
# flickered `blocked` into a terminal one.
dialog_on_screen() { # <pane-id>
  local rc=0 text marker
  text="$("$HERDR_BIN" pane read "$1" \
    --source visible \
    --lines "$TEAMLEAD_PROBE_LINES" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} pane read $1\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — cannot confirm whether a dialog is on screen"
    return 2
  fi
  # Lowercased through tr, not `${var,,}`: that expansion is bash 4+, and this
  # runs under macOS's stock bash 3.2 as well.
  local lower_text lower_marker
  lower_text="$(printf '%s' "$text" | tr '[:upper:]' '[:lower:]')"
  while IFS= read -r marker; do
    [[ -n "$marker" ]] || continue
    lower_marker="$(printf '%s' "$marker" | tr '[:upper:]' '[:lower:]')"
    if [[ "$lower_text" == *"$lower_marker"* ]]; then return 0; fi
  done <<< "$TEAMLEAD_DIALOG_MARKERS"
  return 1
}

# Accept only a complete bare marker with up to three spaces of indentation.
# Quoted, bulleted, indented-code and fenced examples never announce delivery.
# An unmatched fence keeps following rows unconfirmed; a shorter fence or one
# with trailing content cannot close it.
report_marker_on_screen() { # <pane-text> <absolute-report-path>
  local row trimmed fence="" fence_length=0 found=1 run tail container=0
  local fence_pattern='^(`{3,}|~{3,})'
  local container_pattern='^(>|[-+*•][[:blank:]]|[0-9]{1,9}[.)][[:blank:]])'
  while IFS= read -r row; do
    trimmed="${row#"${row%%[![:blank:]]*}"}"
    if [[ -z "$fence" && ! "$trimmed" =~ $fence_pattern ]]; then
      if [[ -z "$trimmed" ]]; then container=0; fi
      if [[ "$row" != '    '* && "$trimmed" =~ $container_pattern ]]; then container=1; fi
    fi
    [[ "$row" != '    '* && "$row" != *$'\t'* ]] || continue
    if [[ "$trimmed" =~ $fence_pattern ]]; then
      run="${BASH_REMATCH[1]}"
      tail="${trimmed#"$run"}"
      if [[ -z "$fence" ]]; then
        fence="${run:0:1}"
        fence_length=${#run}
        container=0
      elif [[ "${run:0:1}" == "$fence" && -z "${tail//[[:blank:]]/}" ]] \
           && (( ${#run} >= fence_length )); then
        fence=""
      fi
      continue
    fi
    [[ -z "$fence" && "$container" == 0 ]] || continue
    if [[ "$trimmed" == "${REPORT_MARKER}${2}" ]]; then found=0; fi
  done <<< "$1"
  return "$found"
}

# A bare provider notice must be the last content before an empty native
# composer. Quoted/fenced examples, occupied composers, later messages, and
# working footers cannot establish a terminal refusal. Unknown UI shapes keep
# the ordinary wait; this parser never guesses at a provider's hidden output.
terminal_refusal_on_screen() { # <visible-pane-text>
  local row trimmed run tail content fence="" fence_length=0 notice=0 composer=0
  local fence_pattern='^(`{3,}|~{3,})'
  local border_pattern='^[─━╭╮╰╯┌┐└┘│[:blank:]]+$'
  while IFS= read -r row; do
    trimmed="${row#"${row%%[![:blank:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:blank:]]}"}"
    [[ -n "$trimmed" ]] || continue
    if [[ "$row" == '    '* || "$row" == *$'\t'* ]]; then
      notice=0; composer=0
      continue
    fi
    if [[ "$trimmed" =~ $fence_pattern ]]; then
      run="${BASH_REMATCH[1]}"; tail="${trimmed#"$run"}"
      if [[ -z "$fence" ]]; then
        fence="${run:0:1}"; fence_length=${#run}
      elif [[ "${run:0:1}" == "$fence" && -z "${tail//[[:blank:]]/}" ]] && (( ${#run} >= fence_length )); then
        fence=""
      fi
      notice=0; composer=0
      continue
    fi
    [[ -z "$fence" ]] || continue
    case "$trimmed" in
      "This content can't be shown"|"This content can't be shown.")
        notice=1; composer=0
        continue
        ;;
      '›'|'❯'|'› Ask Codex to do anything')
        composer=$notice
        continue
        ;;
      '│ ❯'*'│')
        content="${trimmed#'│ ❯'}"; content="${content%'│'}"
        if [[ -z "${content//[[:blank:]]/}" ]]; then
          composer=$notice
          continue
        fi
        ;;
      '? for shortcuts'|'Shift+Tab:mode  │  Ctrl+.:shortcuts')
        if (( composer == 1 )); then continue; fi
        ;;
    esac
    if [[ "$trimmed" =~ $border_pattern ]]; then continue; fi
    notice=0; composer=0
  done <<< "$1"
  (( notice == 1 && composer == 1 ))
}

read_refusal_view() { # <pane-id>
  local text rc=0
  text="$("$HERDR_BIN" pane read "$1" --source visible --lines "$TEAMLEAD_PROBE_LINES" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "cannot inspect ${1} for a terminal provider notice (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — restore the pane connection before deciding this attempt's outcome"
    return 2
  fi
  printf '%s\n' "$text"
}

# 0 = pinned live terminal context, 1 = absent/nonterminal evidence, 2 = tool
# failure. Scroll position prevents a historical composer from qualifying;
# terminal/session identity and revision reject replacement or intervening UI
# activity. Missing metrics on older integrations keep the ordinary wait.
refusal_context() { # <pane-id>
  local raw parsed rc=0
  raw="$("$HERDR_BIN" pane get "$1" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "cannot verify the live terminal for ${1} (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — restore the pane connection before deciding this attempt's outcome"
    return 2
  fi
  parsed="$(printf '%s' "$raw" | jq -c --arg pane "$1" '
    if (.result.pane | type) != "object" then error("missing result.pane")
    else .result.pane |
      if .pane_id == $pane and (.terminal_id | type) == "string" and .terminal_id != ""
        and (.revision | type) == "number" and .revision >= 0
        and .scroll.offset_from_bottom == 0
        and (.agent_status == "idle" or .agent_status == "done")
      then {terminal_id, agent_session, revision}
      else null end
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "cannot parse the live terminal for ${1}: $(tr '\n' ' ' < "$ERRFILE") — check the Herdr pane get response before deciding this attempt's outcome"
    return 2
  fi
  [[ "$parsed" != null ]] || return 1
  printf '%s\n' "$parsed"
}

# 0 = confirmed terminal refusal, 1 = no terminal evidence, 2 = tool failure.
# Compare complete visible snapshots so new prompt/output activity invalidates
# an old notice even when its literal text remains somewhere on screen.
confirmed_provider_refusal() { # <pane-id>
  local before after context_before context_after info state pane rc=0
  REFUSAL_STATE=""; REFUSAL_PANE=""
  before="$(read_refusal_view "$1")" || return 2
  terminal_refusal_on_screen "$before" || return 1
  context_before="$(refusal_context "$1")" || return $?
  if ! sleep "$TEAMLEAD_REFUSAL_CONFIRM_SEC"; then
    warn "terminal-refusal confirmation wait failed — restore the sleep utility before deciding this attempt's outcome"
    return 2
  fi
  info="$(agent_info "$AGENT")" || return 2
  state="${info%% *}"; pane="${info##* }"
  REFUSAL_STATE="$state"; REFUSAL_PANE="$pane"
  if [[ -z "$pane" || "$pane" == "unknown" ]]; then
    warn "${AGENT} lost its pane during refusal confirmation — restore its live pane before deciding the report outcome"
    return 2
  fi
  if [[ "$pane" != "$1" || ( "$state" != "idle" && "$state" != "done" ) ]]; then return 1; fi
  after="$(read_refusal_view "$pane")" || return 2
  context_after="$(refusal_context "$pane")" || return $?
  [[ "$context_before" == "$context_after" ]] || return 1
  if [[ "$before" != "$after" || -f "$REPORT_PATH" ]]; then return 1; fi
  terminal_refusal_on_screen "$after" || rc=$?
  if (( rc != 0 )); then return 1; fi
  REFUSAL_STATE="$state"
  return 0
}

main() {
  local once=0
  if [[ "${1:-}" == "--once" ]]; then once=1; shift; fi
  if (( $# != 2 )); then
    warn "usage: wait-report.sh [--once] <agent-name> <report-path>"
    return 2
  fi
  AGENT="$1"
  REPORT_PATH="$2"

  # The contract says absolute, and the -f test below resolves a relative path
  # against whatever cwd the caller happens to be in -- a different directory
  # per round would report a present report as missing, or an unrelated file as
  # present.
  if [[ "$REPORT_PATH" != /* ]]; then
    warn "report path '${REPORT_PATH}' is relative — pass the absolute path the brief gave the worker (e.g. \"\$PWD/${REPORT_PATH#./}\")"
    return 2
  fi
  if [[ "$REPORT_PATH" == *[[:cntrl:]]* || "$REPORT_PATH" == */ ]]; then
    warn "report path must name a file on one line — pass the exact absolute REPORT value from the brief"
    return 2
  fi

  validate_positive_int TEAMLEAD_UNCONFIRMED_IDLE_READS "$TEAMLEAD_UNCONFIRMED_IDLE_READS" || return 2
  validate_nonneg_int TEAMLEAD_WAIT_INTERVAL_SEC "$TEAMLEAD_WAIT_INTERVAL_SEC" || return 2
  validate_nonneg_int TEAMLEAD_WAIT_BUDGET_SEC "$TEAMLEAD_WAIT_BUDGET_SEC" || return 2
  validate_nonneg_int TEAMLEAD_BLOCKED_CONFIRM_SEC "$TEAMLEAD_BLOCKED_CONFIRM_SEC" || return 2
  validate_nonneg_int TEAMLEAD_REFUSAL_CONFIRM_SEC "$TEAMLEAD_REFUSAL_CONFIRM_SEC" || return 2
  validate_positive_int TEAMLEAD_PROBE_TIMEOUT_MS "$TEAMLEAD_PROBE_TIMEOUT_MS" || return 2
  validate_positive_int TEAMLEAD_PROBE_LINES "$TEAMLEAD_PROBE_LINES" || return 2
  # Normalize to decimal once: a validated `08` would otherwise be reparsed as
  # octal by every later bare arithmetic expansion.
  TEAMLEAD_UNCONFIRMED_IDLE_READS=$(( 10#$TEAMLEAD_UNCONFIRMED_IDLE_READS ))
  TEAMLEAD_WAIT_INTERVAL_SEC=$(( 10#$TEAMLEAD_WAIT_INTERVAL_SEC ))
  TEAMLEAD_WAIT_BUDGET_SEC=$(( 10#$TEAMLEAD_WAIT_BUDGET_SEC ))
  TEAMLEAD_BLOCKED_CONFIRM_SEC=$(( 10#$TEAMLEAD_BLOCKED_CONFIRM_SEC ))
  TEAMLEAD_REFUSAL_CONFIRM_SEC=$(( 10#$TEAMLEAD_REFUSAL_CONFIRM_SEC ))
  TEAMLEAD_PROBE_TIMEOUT_MS=$(( 10#$TEAMLEAD_PROBE_TIMEOUT_MS ))
  TEAMLEAD_PROBE_LINES=$(( 10#$TEAMLEAD_PROBE_LINES ))
  if (( once )); then TEAMLEAD_PROBE_TIMEOUT_MS=$CHECK_PROBE_TIMEOUT_MS; fi

  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run the team round from a pane Herdr manages"
    return 2
  fi
  if ! command -v "$HERDR_BIN" >/dev/null 2>&1; then
    warn "'${HERDR_BIN}' not found on PATH — install the herdr CLI (https://herdr.dev) or point HERDR_BIN at the binary"
    return 2
  fi
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to parse the herdr payload"
    return 2
  fi

  # Initialized, not just declared: `local pane` alone leaves an UNSET
  # variable, and under `set -u` any path that reads it before the assignment
  # aborts the run. Giving each one a value makes that class impossible rather
  # than making it depend on statement order holding forever.
  local start=0 now=0 elapsed=0 info="" state="unknown" pane="" rc=0 marker=0
  local unconfirmed_idle=0
  if ! start="$(date +%s)"; then
    warn "cannot read the system clock — the wait cannot be bounded"
    return 2
  fi

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  while :; do
    rc=0
    info="$(agent_info "$AGENT")" || rc=$?
    if (( rc != 0 )); then return 2; fi
    state="${info%% *}"
    pane="${info##* }"
    # jq fills an absent pane_id with the literal "unknown" rather than failing,
    # and probing a pane id that does not exist would surface as a generic herdr
    # error on every attempt. Name the real cause once instead.
    if [[ -z "$pane" || "$pane" == "unknown" ]]; then
      warn "\`${HERDR_BIN} agent get ${AGENT}\` reported no pane id — the agent may have exited; run \`${HERDR_BIN} agent list\` to see the live panes"
      return 2
    fi

    # A blocked worker is waiting on a human, not producing a report -- once
    # that is actually true. A single `blocked` read is the same
    # one-observation trap as a single `done` read, so it has to survive a
    # second read TEAMLEAD_BLOCKED_CONFIRM_SEC later AND a dialog on the pane.
    # A lone `blocked` just keeps polling.
    if [[ "$state" == "blocked" ]]; then
      sleep "$TEAMLEAD_BLOCKED_CONFIRM_SEC"
      rc=0
      info="$(agent_info "$AGENT")" || rc=$?
      if (( rc != 0 )); then return 2; fi
      if [[ "${info##* }" != "$pane" ]]; then
        warn "${AGENT} changed panes during dialog confirmation — reconcile its live identity before deciding the report outcome"
        return 2
      fi
      state="${info%% *}"
      if [[ "$state" == "blocked" ]]; then
        rc=0
        dialog_on_screen "$pane" || rc=$?
        if (( rc == 2 )); then return 2; fi
        if (( rc == 0 )); then
          now="$(date +%s)"
          emit "$state" false "$(( now - start ))"
          warn "${AGENT} is blocked at an approval or question dialog — inspect it with \`${HERDR_BIN} pane read ${pane} --source visible\`, relay it to the operator, and let them answer it"
          return 3
        fi
      fi
      warn "${AGENT} read \`blocked\` once with no dialog on screen — treating it as a flicker and continuing to wait"
    fi

    rc=0
    marker_seen "$pane" "$REPORT_PATH" || rc=$?
    if (( rc == 2 )); then return 2; fi
    marker=$(( rc == 0 ? 1 : 0 ))

    if (( marker == 1 )) && [[ -f "$REPORT_PATH" ]]; then
      now="$(date +%s)"
      emit "$state" true "$(( now - start ))"
      return 0
    fi

    if [[ ! -f "$REPORT_PATH" && ( "$state" == "idle" || "$state" == "done" ) ]]; then
      rc=0
      confirmed_provider_refusal "$pane" || rc=$?
      if (( rc == 2 )); then return 2; fi
      state="${REFUSAL_STATE:-$state}"; pane="${REFUSAL_PANE:-$pane}"
      if (( rc == 0 )); then
        now="$(date +%s)"
        emit "$REFUSAL_STATE" false "$(( now - start ))" "terminal_provider_refusal"
        warn "${AGENT}: report unavailable after a confirmed terminal provider refusal — record this attempt as unavailable and tell the operator; keep review/release gates unsatisfied, with no automatic retry, rephrasing, model/provider switch, or synthesized report"
        return 5
      fi
    fi

    now="$(date +%s)"
    elapsed=$(( now - start ))

    # The file is there and the worker looks finished, but the marker did not
    # confirm. That is a probe blind spot, not a worker still working, and
    # sitting on the budget hides it for an hour. Two consecutive reads, so a
    # `done` flicker mid-turn cannot trip it alone.
    if (( marker == 0 )) && [[ -f "$REPORT_PATH" ]] && [[ "$state" == "idle" || "$state" == "done" ]]; then
      unconfirmed_idle=$(( unconfirmed_idle + 1 ))
      if (( unconfirmed_idle >= TEAMLEAD_UNCONFIRMED_IDLE_READS )); then
        emit "$state" false "$elapsed" "report file present, worker ${state} on ${unconfirmed_idle} consecutive reads, marker unconfirmed"
        warn "${AGENT}: the report file exists and the worker reads ${state}, but \`${REPORT_MARKER}${REPORT_PATH}\` is still unconfirmed after ${unconfirmed_idle} consecutive reads — re-run this wait once if the worker is blocked or working; for a completed native-decoration failure preserve this receipt and use owner recover-report with archived evidence, otherwise record no report"
        return 4
      fi
    else
      unconfirmed_idle=0
    fi
    if (( once )); then
      if (( unconfirmed_idle > 0 )); then
        sleep "$CHECK_CONFIRM_SEC"
        continue
      fi
      emit "$state" false "$elapsed" "checkpoint_pending"
      return 1
    fi
    if (( elapsed >= TEAMLEAD_WAIT_BUDGET_SEC )); then
      emit "$state" false "$elapsed"
      warn "${AGENT} produced no report within ${TEAMLEAD_WAIT_BUDGET_SEC}s (marker seen: ${marker}, file present: $([[ -f "$REPORT_PATH" ]] && echo 1 || echo 0)) — read the pane with \`${HERDR_BIN} agent read ${AGENT} --source visible\` before re-dispatching"
      return 1
    fi

    sleep "$TEAMLEAD_WAIT_INTERVAL_SEC"
  done
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
