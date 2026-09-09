#!/usr/bin/env bash
# Wait for one worker's standup answer, with the standup's own give-up budget.
#
# A standup answer is four lines, not a task, so the wait for it is short. The
# budget is THIS script's constant, never a number the skill picks per run
# (rules/script-delegation.md Black Box, rules/ci-safety.md Always Watch CI); the
# skill invokes this wrapper and nothing else. Everything besides the budget —
# the two-signal completion (report file on disk AND the `REPORT: ` marker in
# the pane), the poll interval, the exit codes — belongs to wait-report.sh in
# the sibling herdr-teamlead skill, which this script execs.
#
# Contract:
#   argv  : <agent-name> <report-path>   forwarded verbatim to wait-report.sh
#   stdout: wait-report.sh's one JSON object on exits 0, 1, 3, and 4. Empty on
#           any exit 2 — wait-report.sh's own, or this wrapper's preflight
#           (bad budget override, sibling script missing) — with the
#           diagnostic on stderr.
#   stderr: diagnostics.
#   exit  : wait-report.sh's code — 0 report found, 1 budget exhausted,
#           2 usage error or tool failure, 3 blocked at a dialog, 4 file
#           present but marker unconfirmed (read the pane); also 2 when
#           wait-report.sh is not installed beside this skill or the budget
#           override is not an integer. In both of those cases nothing is run.
#   env   : STANDUP_WAIT_BUDGET_SEC overrides the budget (tests, a slow fleet).
#           HERDR_ENV and HERDR_BIN pass through to wait-report.sh.
set -euo pipefail

# Give-up budget for a standup answer, in seconds. Four lines take a worker
# well under a minute; past this point the lead writes that worker's row from
# the round log instead of chasing it.
STANDUP_WAIT_BUDGET_SEC="${STANDUP_WAIT_BUDGET_SEC:-180}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WAIT_REPORT="${SKILL_DIR}/../herdr-teamlead/wait-report.sh"

case "$STANDUP_WAIT_BUDGET_SEC" in
  ''|*[!0-9]*)
    echo "standup-wait: STANDUP_WAIT_BUDGET_SEC must be a whole number of seconds, got '${STANDUP_WAIT_BUDGET_SEC}' — unset it to use the script's default" >&2
    exit 2
    ;;
esac
if [[ ! -f "$WAIT_REPORT" ]]; then
  echo "standup-wait: wait-report.sh not found at ${WAIT_REPORT} — herdr-standup runs beside the herdr-teamlead skill; install both with \`tessl install gamussa/coding-policy\`" >&2
  exit 2
fi

TEAMLEAD_WAIT_BUDGET_SEC="$STANDUP_WAIT_BUDGET_SEC" exec bash "$WAIT_REPORT" "$@"
