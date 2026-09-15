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
#   argv  : [--once] [--worktree <path>] [--base <revision>] [--since <iso8601>]
#           <agent-name> <report-path>
#           --worktree names the worker's own checkout. With it, a budget
#                  exhaustion classifies what the worker left behind and the
#                  JSON carries a "stall" object; without it the exhaustion
#                  reports the wait alone. The classification NEVER decides
#                  that the work is usable -- a stalled worker's output is
#                  unreviewed by construction (#418).
#           --base names the revision this dispatch started from, from the
#                  ledger. Without it a clean checkout cannot be told from one
#                  whose worker pushed and then stopped, so `no_work` is never
#                  claimed and the class reads `unknown`.
#           --since names when this dispatch was sent, from the ledger. A
#                  checkpoint (`--once`) carries no elapsed time of its own, so
#                  without it repeated checkpoints reset the clock and the
#                  stall outcome is never reached. With it the SAME
#                  script-owned budget applies, measured from the send (#418).
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
#             (`found` false, `state` last observed). A budget exhaustion with
#             the report absent and the worker terminal is a STALL: the wait
#             ends rather than continuing, and the JSON adds
#             {"stall":{"class":"<c>","evidence":{...}}}. Without --worktree
#             class is `unclassified`; with it, class is
#             partial_work (a worktree mid-operation, staged, modified or
#             carrying untracked files -- preserve it as evidence),
#             unpushed_commits (completed work with a failed transport; the
#             dispatch-recovery path), no_work (clean and unchanged; the
#             dispatch is a not_sent-equivalent and may be retried), or
#             unknown (the worktree could not be read). Never commit a stalled
#             worker's partial work: re-dispatch it with the observed state
#             described, or discard it,
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
#             Save this JSON and record it with `teamlead record-refusal`;
#             never rephrase or synthesize the missing report. The bounded
#             move to another provider is dispatch-recovery.md's Wait
#             outcomes, enforced by `apply`.
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
#: The worker's own checkout, when the caller named one. A stall classifies
#: what it holds; without it the exhaustion reports the wait alone (#418).
WORKTREE=""
#: This dispatch's recorded send time, when the caller named one. A checkpoint
#: measures the budget from it rather than from its own start (#418).
SINCE=""
#: The revision this dispatch started from, when the caller named one. Without
#: it a clean checkout cannot be told from one whose worker pushed (#418).
BASE=""
REFUSAL_STATE=""
REFUSAL_PANE=""

warn() { printf 'wait-report: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

emit() { # <state> <found-bool> <elapsed-seconds> [reason] [stall-json]
  # `reason` and `stall` appear only when set: the object stays the documented
  # shape on every outcome, with one extra field for an unavailable delivery
  # and one for a stall's classification.
  jq -n --arg a "$AGENT" --arg s "$1" --arg p "$REPORT_PATH" \
        --argjson f "$2" --argjson e "$3" --arg r "${4:-}" --argjson st "${5:-null}" \
    '{agent: $a, state: $s, report_path: $p, found: $f, elapsed_seconds: $e}
     + (if $r == "" then {} else {reason: $r} end)
     + (if $st == null then {} else {stall: $st} end)'
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

# Classify what a stalled worker left in its own checkout.
#
# A stalled worker's partial work can LOOK complete -- zero conflicts, a clean
# build -- and still be wrong, so this reports what is on disk and decides
# nothing about whether the work is usable (#418). Echoes one JSON object;
# never fails the wait, because an unreadable worktree is `unknown`, not a
# reason to lose the stall itself.
classify_worktree() { # <worktree-path> [base-revision]
  local tree="$1" base="${2:-}" status="" mid=false staged=0 modified=0 untracked=0 unpushed=0 own=-1 class
  if [[ -z "$tree" ]]; then
    # The stall is established without it; only the classification is missing.
    jq -n '{class: "unclassified", evidence: {worktree: null, readable: false}}'
    return 0
  fi
  if [[ ! -d "$tree" ]] || ! git -C "$tree" rev-parse --is-inside-work-tree >/dev/null 2>"$ERRFILE"; then
    local why
    why="$(tr '\n' ' ' < "$ERRFILE")"
    warn "\`git rev-parse --is-inside-work-tree\` found no work tree at ${tree}: ${why:-no such directory} — the stall is recorded, its classification is not; inspect that path by hand"
    jq -n --arg t "$tree" --arg e "${why:-no such directory}" \
      '{class: "unknown", evidence: {worktree: $t, readable: false, error: $e}}'
    return 0
  fi
  # `--absolute-git-dir`, never the relative `--git-dir`: the relative form is
  # resolved against the CALLER's cwd, so the lead's own repository mid-rebase
  # would mark every worker worktree as mid-operation.
  # A failed lookup is a tool failure, not "no operation in progress": skipping
  # the check would report a mid-merge tree as no_work
  # (rules/error-handling.md Shell Error Handling).
  local gitdir rc=0
  gitdir="$(git -C "$tree" rev-parse --absolute-git-dir 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$gitdir" ]]; then
    local detail
    detail="$(tr '\n' ' ' < "$ERRFILE")"
    warn "could not read the git directory of ${tree}: ${detail:-no --absolute-git-dir output} — the stall is recorded, its classification is not; inspect the worktree by hand"
    jq -n --arg t "$tree" --arg e "${detail:-no --absolute-git-dir output}" \
      '{class: "unknown", evidence: {worktree: $t, readable: false, error: $e}}'
    return 0
  fi
  # Marker files AND operation directories. A rebase paused at an `exec` or
  # `break` writes `rebase-merge/` with no REBASE_HEAD and can leave a clean
  # tree, which would classify as no_work or as completed commits.
  local marker
  for marker in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG \
                rebase-merge rebase-apply sequencer; do
    if [[ -e "${gitdir}/${marker}" ]]; then mid=true; break; fi
  done
  rc=0
  status="$(git -C "$tree" status --porcelain --untracked-files=all 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    local why
    why="$(tr '\n' ' ' < "$ERRFILE")"
    warn "\`git status\` failed in ${tree}: ${why:-no diagnostic} — the stall is recorded, its classification is not; inspect that checkout by hand"
    jq -n --arg t "$tree" --arg e "${why:-no diagnostic}" \
      '{class: "unknown", evidence: {worktree: $t, readable: false, error: $e}}'
    return 0
  fi
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    case "$line" in
      '??'*) untracked=$(( untracked + 1 )) ;;
      ' '*) modified=$(( modified + 1 )) ;;
      *) staged=$(( staged + 1 )) ;;
    esac
  done <<<"$status"
  # Commits the worker made that no remote-tracking ref holds. A repository
  # with no remote reports none, which is the honest answer for one.
  rc=0
  unpushed="$(git -C "$tree" rev-list --count HEAD --not --remotes 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ ! "$unpushed" =~ ^[0-9]+$ ]]; then
    # Unreadable history is not "nothing to push": reading it as zero would
    # classify a worker's committed work as a retryable no_work.
    local detail
    detail="$(tr '\n' ' ' < "$ERRFILE")"
    warn "could not count unpushed commits in ${tree}: ${detail:-unreadable rev-list output} — the stall is recorded, its classification is not; inspect the worktree by hand"
    jq -n --arg t "$tree" --arg e "${detail:-unreadable rev-list output}" --argjson m "$mid" \
          --argjson s "$staged" --argjson d "$modified" --argjson u "$untracked" \
      '{class: "unknown", evidence: {worktree: $t, readable: false, error: $e,
                                     mid_operation: $m, staged: $s, modified: $d, untracked: $u}}'
    return 0
  fi
  # Commits this dispatch produced, against the base its brief recorded. Zero
  # UNPUSHED commits never establishes "produced nothing": a worker can push
  # its work and stop before reporting, and calling that a retryable no_work
  # would discard completed work as evidence.
  if [[ -n "$base" ]]; then
    rc=0
    own="$(git -C "$tree" rev-list --count "${base}..HEAD" 2>"$ERRFILE")" || rc=$?
    if (( rc != 0 )) || [[ ! "$own" =~ ^[0-9]+$ ]]; then
      local why
      why="$(tr '\n' ' ' < "$ERRFILE")"
      warn "could not count this dispatch's commits in ${tree} against ${base}: ${why:-unreadable rev-list output} — the stall is recorded, its classification is not; inspect that checkout by hand"
      jq -n --arg t "$tree" --arg b "$base" --arg e "${why:-unreadable rev-list output}" \
        '{class: "unknown", evidence: {worktree: $t, base: $b, readable: false, error: $e}}'
      return 0
    fi
  fi
  # Order follows the recovery each class needs: partial work is preserved as
  # evidence before anything else is read off the tree.
  if [[ "$mid" == true ]] || (( staged > 0 || modified > 0 || untracked > 0 )); then
    class="partial_work"
  elif (( unpushed > 0 )); then
    class="unpushed_commits"
  elif (( own > 0 )); then
    # Pushed and unreported: completed work whose transport succeeded and whose
    # report did not. Recovery evidence, never a retryable dispatch.
    class="pushed_commits"
  elif (( own == 0 )); then
    class="no_work"
  else
    # No base to judge against: a clean tree could be an untouched checkout or
    # a worker that already pushed. Say so rather than guess the retryable one.
    class="unknown"
  fi
  jq -n --arg c "$class" --arg t "$tree" --arg b "$base" --argjson m "$mid" \
        --argjson s "$staged" --argjson d "$modified" --argjson u "$untracked" \
        --argjson p "$unpushed" --argjson o "$own" \
    '{class: $c, evidence: ({worktree: $t, readable: true, mid_operation: $m,
                             staged: $s, modified: $d, untracked: $u, unpushed_commits: $p}
               + (if $b == "" then {base: null, dispatch_commits: null}
                  else {base: $b, dispatch_commits: $o} end))}'
  return 0
}

# Echo <iso8601> as epoch seconds, or return 1 with a diagnostic. jq owns the
# parse (it is already a hard dependency); a timezone-qualified ledger stamp
# and a bare `Z` both reach `fromdateiso8601` through the same normalization.
epoch_of() { # <iso8601>
  local out rc=0
  # The offset is SUBTRACTED, never rewritten as `Z`: rewriting it moves the
  # instant (00:00-05:00 would read as midnight UTC, five hours older) and the
  # budget would be declared spent before it was.
  out="$(jq -rn --arg t "$1" '
    ($t | capture("^(?<stamp>.*?)(?<zone>Z|[+-][0-9]{2}:?[0-9]{2})$")) as $parts
    | (($parts.stamp | sub("\\.[0-9]+$"; "")) + "Z" | fromdateiso8601) as $naive
    | (if $parts.zone == "Z" then 0
       else ($parts.zone
             | capture("^(?<sign>[+-])(?<h>[0-9]{2}):?(?<m>[0-9]{2})$")
             | (((.h | tonumber) * 3600) + ((.m | tonumber) * 60))
               * (if .sign == "-" then -1 else 1 end))
       end) as $offset
    | $naive - $offset' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ ! "$out" =~ ^-?[0-9]+$ ]]; then
    warn "could not read --since '${1}' as an ISO-8601 timestamp: $(tr '\n' ' ' < "$ERRFILE") — pass the dispatch's recorded send time"
    return 1
  fi
  printf '%s' "$out"
}

# Has this dispatch reached the stall conjunction? Report absent, worker
# terminal, and the SAME script-owned budget spent -- measured from `--since`
# when the caller named it, since a checkpoint carries no elapsed time of its
# own and repeated checkpoints would otherwise reset the clock (#418).
# 0 = stalled, 1 = not (yet), 2 = `--since` is unreadable.
stalled_now() { # <state> <elapsed-seconds>
  [[ ! -f "$REPORT_PATH" ]] || return 1
  [[ "$1" == "idle" || "$1" == "done" ]] || return 1
  local elapsed="$2" started now
  if [[ -n "$SINCE" ]]; then
    started="$(epoch_of "$SINCE")" || return 2
    # TEAMLEAD_NOW_EPOCH is the test seam: a budget case needs a fixed clock,
    # never the run date (rules/testing-standards.md Determinism).
    now="${TEAMLEAD_NOW_EPOCH:-$(date +%s)}"
    if [[ ! "$now" =~ ^-?[0-9]+$ ]]; then
      warn "TEAMLEAD_NOW_EPOCH='${now}' is not epoch seconds"
      return 2
    fi
    elapsed=$(( now - started ))
  fi
  (( elapsed >= TEAMLEAD_WAIT_BUDGET_SEC )) || return 1
  return 0
}

main() {
  local once=0
  while (( $# )); do
    case "${1:-}" in
      --once) once=1; shift ;;
      --worktree)
        if (( $# < 2 )) || [[ -z "$2" ]]; then
          warn "--worktree needs the worker's checkout path"
          return 2
        fi
        WORKTREE="$2"; shift 2 ;;
      --since)
        if (( $# < 2 )) || [[ -z "$2" ]]; then
          warn "--since needs this dispatch's recorded send time"
          return 2
        fi
        SINCE="$2"; shift 2 ;;
      --base)
        if (( $# < 2 )) || [[ -z "$2" ]]; then
          warn "--base needs the revision this dispatch started from"
          return 2
        fi
        BASE="$2"; shift 2 ;;
      --) shift; break ;;
      -*) warn "unknown flag '${1}'"; warn "usage: wait-report.sh [--once] [--worktree <path>] [--base <revision>] [--since <iso8601>] <agent-name> <report-path>"; return 2 ;;
      *) break ;;
    esac
  done
  if (( $# != 2 )); then
    warn "usage: wait-report.sh [--once] [--worktree <path>] [--base <revision>] [--since <iso8601>] <agent-name> <report-path>"
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
        warn "${AGENT}: report unavailable after a confirmed terminal provider refusal — save this JSON and record it with teamlead record-refusal; keep review/release gates unsatisfied, with no rephrasing, no resend to the same provider, and no synthesized report; one move of the unchanged brief to another provider goes through plan and apply (dispatch-recovery.md Wait outcomes)"
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
    # The stall conjunction is read on EVERY interval. With `--since` the budget
    # is measured from the dispatch, so one already past it stalls here rather
    # than after another full budget of this invocation's own (#418). Without
    # `--since` this fires exactly at the budget boundary, as before.
    local stall_rc=0
    stalled_now "$state" "$elapsed" || stall_rc=$?
    if (( stall_rc == 2 )); then return 2; fi
    if (( stall_rc == 0 )); then
      local stall
      stall="$(classify_worktree "$WORKTREE" "$BASE")"
      if [[ -z "$stall" ]]; then
        warn "the stall classifier produced no output for worktree '${WORKTREE}' — reporting the stall unclassified"
        stall='{"class": "unknown", "evidence": {"worktree": null, "readable": false, "error": "classifier produced no output"}}'
      fi
      emit "$state" false "$elapsed" "" "$stall"
      warn "${AGENT} stalled: no report, worker reads ${state}, and the ${TEAMLEAD_WAIT_BUDGET_SEC}s budget${SINCE:+ from ${SINCE}} is spent — read the pane with \`${HERDR_BIN} agent read ${AGENT} --source visible\`, record a user-attention obligation, and preserve any partial work as evidence; never commit it on the strength of the tree building"
      return 1
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
      # Exhaustion that is NOT a stall: the report landed, or the worker is
      # still working. The stall conjunction returned above.
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
