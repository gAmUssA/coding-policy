#!/usr/bin/env bash
# Outcome-based tests for hooks/bounded-run.sh.
#
# Covers:
#   1. Fast command      -> its stdout and exit code pass through unchanged.
#   2. Deadline passes   -> exit 124, a stderr diagnostic, and a grandchild in
#                           the group killed.
#   3. TERM ignored      -> SIGKILL follows the grace period; exit 124.
#   4. Usage errors      -> a non-positive or missing deadline exits 125.
#   5. Group escalation  -> the leader exits on SIGTERM while a descendant
#                           ignores it: the descendant is SIGKILLed anyway.
#   6. Nested runners    -> an outer deadline that fires first stops the inner
#                           runner, which reaps its own group: the innermost
#                           process is killed.
#   7. Leader exits early -> the leader exits 0 while a background descendant
#                           holds stdout past the deadline: 124, descendant killed.
#   8. Group finishes    -> a leader exiting 3 whose descendant ends before the
#                           deadline returns 3 after the descendant ends.
#   9. Zombie member     -> the group's only other member is defunct under a
#                           non-reaping parent: the leader's exit 3 comes back,
#                           not 124. A python3 helper outside the group (same
#                           session) forks a child that joins the runner's group
#                           with setpgid and exits; the helper never reaps it.
#                           (macOS kill -0 already skips zombies; Linux does not.)
#  10. Liveness helper   -> group_alive against a fake `ps` (the process
#                           boundary) on a live group: a group whose only member
#                           reads Z is not running, one reading S is, and a
#                           failing ps counts as running. Platform-independent.
#  11. kill -0 refused   -> an exported `kill` function answers every `-0`
#                           probe EPERM, as macOS does mid-exec: the runner
#                           still enforces the deadline (124) and kills the
#                           command, never reading EPERM as "group gone".
#
# Run: bash hooks/tests/test_bounded_run.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bounded-run.sh"
  [[ -r "$SCRIPT" ]] || die "runner not found at $SCRIPT"
  TMP="$(mktemp -d -t bounded-run-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  local out rc

  # Outcomes are deterministic, never elapsed time: each sleeper outlives every
  # deadline under test and touches a "finished" marker only if nothing killed
  # it. A case passes only on the runner's exit code, the sleeper dead, and no
  # marker.
  # killed <pid-file> <marker>: the recorded process is gone and never finished.
  killed() {
    local pid
    pid="$(cat "$1")" || die "pid not recorded at $1"
    ! kill -0 "$pid" 2>/dev/null && [[ ! -e "$2" ]]
  }
  # reap <pid-file>: stop a survivor so a failed case cannot leak a process.
  reap() {
    local pid
    pid="$(cat "$1")" || return 0
    if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" || echo "warn: could not stop $pid" >&2; fi
    return 0
  }

  # 1. Pass-through: stdout, stderr and the exit code arrive unmodified.
  rc=0; out="$(bash "$SCRIPT" 5 bash -c 'echo hello; echo oops >&2; exit 7' </dev/null 2>"$TMP/err1")" || rc=$?
  if [[ $rc -eq 7 && "$out" == "hello" && "$(cat "$TMP/err1")" == "oops" ]]; then
    pass; else fail "pass-through: expected stdout 'hello', stderr 'oops' and exit 7, got rc=$rc out=$out err=$(cat "$TMP/err1")"; fi

  # 2. Deadline: the command waits on a grandchild sleeper.
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shell, not here
  bash "$SCRIPT" 1 bash -c '(sleep 8; touch "$2") & echo $! > "$1"; wait' _ "$TMP/c2.pid" "$TMP/c2.done" </dev/null 2>"$TMP/err2" || rc=$?
  if [[ $rc -eq 124 ]] && grep -q 'deadline' "$TMP/err2" && killed "$TMP/c2.pid" "$TMP/c2.done"; then
    pass; else fail "deadline: expected 124, a diagnostic, and the grandchild killed; got rc=$rc"; fi
  reap "$TMP/c2.pid"

  # 3. A command that ignores SIGTERM is killed after the grace period.
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shell, not here
  bash "$SCRIPT" 1 bash -c 'trap "" TERM; echo $$ > "$1"; sleep 8; touch "$2"' _ "$TMP/c3.pid" "$TMP/c3.done" </dev/null 2>/dev/null || rc=$?
  if [[ $rc -eq 124 ]] && killed "$TMP/c3.pid" "$TMP/c3.done"; then pass; else fail "TERM ignored: expected 124 and the command killed, got rc=$rc"; fi
  reap "$TMP/c3.pid"

  # 4. Usage errors.
  rc=0; bash "$SCRIPT" 0 true 2>/dev/null || rc=$?
  if [[ $rc -eq 125 ]]; then pass; else fail "usage: deadline 0 should exit 125, got $rc"; fi
  rc=0; bash "$SCRIPT" 5 2>/dev/null || rc=$?
  if [[ $rc -eq 125 ]]; then pass; else fail "usage: missing command should exit 125, got $rc"; fi

  # 5. Leader dies on TERM, a descendant ignores TERM (an ignored disposition
  # is inherited).
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shells, not here
  bash "$SCRIPT" 1 bash -c 'bash -c "trap \"\" TERM; echo \$\$ > \"\$1\"; sleep 8; touch \"\$2\"" _ "$1" "$2" & wait' _ "$TMP/c5.pid" "$TMP/c5.done" </dev/null 2>/dev/null || rc=$?
  if [[ $rc -eq 124 ]] && killed "$TMP/c5.pid" "$TMP/c5.done"; then
    pass; else fail "escalation: expected 124 and the TERM-ignoring descendant killed, got rc=$rc"; fi
  reap "$TMP/c5.pid"

  # 6. Nested runners, outer deadline first. Command substitution waits for
  # every holder of stdout, so a surviving innermost process would finish.
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shell, not here
  out="$(bash "$SCRIPT" 1 bash "$SCRIPT" 30 bash -c 'echo $$ > "$1"; sleep 8; touch "$2"' _ "$TMP/c6.pid" "$TMP/c6.done" </dev/null 2>/dev/null)" || rc=$?
  if [[ $rc -eq 124 ]] && killed "$TMP/c6.pid" "$TMP/c6.done"; then
    pass; else fail "nested: expected 124 and the innermost process killed, got rc=$rc"; fi
  reap "$TMP/c6.pid"

  # 7. The leader exits 0 at once; its background descendant holds stdout.
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shell, not here
  out="$(bash "$SCRIPT" 1 bash -c '(sleep 8; touch "$2") & echo $! > "$1"; exit 0' _ "$TMP/c7.pid" "$TMP/c7.done" </dev/null 2>/dev/null)" || rc=$?
  if [[ $rc -eq 124 ]] && killed "$TMP/c7.pid" "$TMP/c7.done"; then
    pass; else fail "leader exits early: expected 124 and the descendant killed, got rc=$rc"; fi
  reap "$TMP/c7.pid"

  # 8. The group empties before the deadline: the leader's own code comes back.
  rc=0
  out="$(bash "$SCRIPT" 5 bash -c 'sleep 1 & exit 3' </dev/null 2>/dev/null)" || rc=$?
  if [[ $rc -eq 3 ]]; then pass; else fail "group finishes: expected the leader's exit 3, got rc=$rc"; fi

  # 9. A zombie in the group must not hold the deadline open.
  cat > "$TMP/zombie.py" <<'ZOMBIE'
import os, subprocess, sys, time
pgid_file, ready, confirmed = sys.argv[1:4]
for _ in range(200):
    if os.path.exists(pgid_file) and open(pgid_file).read().strip():
        break
    time.sleep(0.05)
pgid = int(open(pgid_file).read())
pid = os.fork()
if pid == 0:
    try:
        os.setpgid(0, pgid)
    finally:
        os._exit(0)
# The child exits and is never reaped, so it stays a zombie in the group.
# Poll ps (os.waitid is missing from older macOS Pythons) until it shows Z.
for _ in range(100):
    rows = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "pgid=", "-o", "stat="],
                          capture_output=True, text=True, check=True).stdout.splitlines()
    if any(r.split()[:2] == [str(pid), str(pgid)] and r.split()[2].startswith("Z")
           for r in rows if len(r.split()) >= 3):
        open(confirmed, "w").close()
        break
    time.sleep(0.05)
open(ready, "w").close()
time.sleep(30)
ZOMBIE
  python3 "$TMP/zombie.py" "$TMP/c9.pgid" "$TMP/c9.ready" "$TMP/c9.zombie" &
  local helper=$! hrc=0
  rc=0
  # shellcheck disable=SC2016 # expands inside the child shell, not here
  bash "$SCRIPT" 5 bash -c 'echo $$ > "$1"; for i in $(seq 100); do [ -e "$2" ] && exit 3; sleep 0.05; done; exit 9' _ "$TMP/c9.pgid" "$TMP/c9.ready" </dev/null 2>/dev/null || rc=$?
  if [[ -e "$TMP/c9.zombie" && $rc -eq 3 ]]; then
    pass; else fail "zombie member: expected the leader's exit 3 with a confirmed zombie in the group, got rc=$rc zombie=$([[ -e "$TMP/c9.zombie" ]] && echo yes || echo no)"; fi
  kill "$helper" || echo "warn: could not stop the zombie helper $helper" >&2
  wait "$helper" || hrc=$?
  # SIGTERM (143) is the helper's expected end.
  [[ $hrc -eq 143 ]] || echo "warn: zombie helper ended with exit $hrc" >&2

  # 10. group_alive reads states from ps. PGID is this harness's own group,
  # so `kill -0 -- -PGID` succeeds; only the fake ps decides.
  local mypg fakeps alive
  mypg="$(ps -o pgid= -p $$)" || die "cannot read this harness's process group"
  mypg="${mypg//[[:space:]]/}"
  fakeps="$TMP/fakeps"
  mkdir -p "$fakeps" || die "could not create $fakeps"
  for state in Z S fail; do
    if [[ "$state" == fail ]]; then
      printf '#!/bin/sh\nexit 1\n' > "$fakeps/ps" || die "fake ps write failed"
    else
      printf '#!/bin/sh\necho " %s %s+"\necho " 999999 S"\n' "$mypg" "$state" > "$fakeps/ps" || die "fake ps write failed"
    fi
    chmod +x "$fakeps/ps" || die "fake ps chmod failed"
    alive="$( (
      set +e
      # shellcheck source=/dev/null # the runner under test
      source "$SCRIPT"
      # shellcheck disable=SC2034 # read by the sourced group_alive
      PGID="$mypg"
      PATH="$fakeps:$PATH"
      if group_alive 2>/dev/null; then echo yes; else echo no; fi
    ) )"
    case "$state:$alive" in
      Z:no|S:yes|fail:yes) pass ;;
      *) fail "liveness helper: a group reading '$state' should be $([[ $state == Z ]] && echo 'not running' || echo running), got alive=$alive" ;;
    esac
  done

  # 11. Liveness never trusts kill -0: a probe refused with EPERM must not
  # read as an exited leader or an empty group.
  rc=0
  (
    # shellcheck disable=SC2329 # exported to the runner, which invokes it
    kill() {
      if [[ "${1:-}" == "-0" ]]; then echo "kill: Operation not permitted" >&2; return 1; fi
      builtin kill "$@"
    }
    export -f kill
    # shellcheck disable=SC2016 # expands inside the child shell, not here
    bash "$SCRIPT" 1 bash -c 'echo $$ > "$1"; sleep 8; touch "$2"' _ "$TMP/c11.pid" "$TMP/c11.done" </dev/null 2>/dev/null
  ) || rc=$?
  if [[ $rc -eq 124 ]] && killed "$TMP/c11.pid" "$TMP/c11.done"; then
    pass; else fail "kill -0 refused: expected 124 and the command killed, got rc=$rc"; fi
  reap "$TMP/c11.pid"

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
