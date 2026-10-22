#!/usr/bin/env bash
# Run one command under a wall-clock deadline, in pure bash.
#
# Session-start work that waits on the network or a registry can wait as long
# as the far side likes. `timeout` is GNU coreutils, absent from a stock macOS,
# so the deadline is enforced here with bash job control: the command runs in
# its own process group, and at the deadline the whole group gets SIGTERM, then
# SIGKILL after KILL_GRACE_SEC, so nothing it spawned (ssh, a registry client)
# outlives it.
#
# Contract:
#   argv  : <seconds> <command> [args...] — seconds is a positive integer.
#   stdin : passed to the command.
#   stdout: the command's own stdout, unchanged.
#   stderr: the command's own stderr, unchanged, plus this runner's own
#           diagnostics: one line when the deadline passed, and one warning
#           when process states cannot be read. stdout is never JSON: this is
#           the transparent-wrapper exception in rules/script-delegation.md.
#   exit  : the command's own exit code once its whole process group has
#           exited; 124 when the deadline passed with any group member still
#           running (the `timeout` convention), including a leader that
#           already exited 0 but left a descendant behind; 125 on a usage
#           error. A descendant that leaves the group (setsid) is not waited on.
set -euo pipefail

#: Seconds between SIGTERM and SIGKILL once the deadline has passed.
KILL_GRACE_SEC=2
#: Seconds between SIGTERM and SIGKILL when this runner is itself stopped.
#: Below KILL_GRACE_SEC, so a runner nested inside another runner reaps its
#: own group before the outer runner escalates to SIGKILL.
TRAP_GRACE_SEC=1
#: Seconds between liveness checks, and how many checks make one second.
POLL_SEC=0.1
POLLS_PER_SEC=10
# A poll costs its sleep plus the ps/awk forks, so on a loaded machine
# POLLS_PER_SEC polls take longer than a second. The deadline therefore also
# ends once the wall clock ($SECONDS, whole seconds) shows more than its
# seconds elapsed: never early, and under two seconds plus one poll late.
# The SIGTERM graces stay counted in polls and stretch with the load, so a
# nested runner's TRAP_GRACE_SEC still ends before its outer KILL_GRACE_SEC.

#: The managed process group; empty until the command starts.
PGID=""

main() {
  if (( $# < 2 )) || [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
    printf 'bounded-run: usage: bounded-run.sh <seconds> <command> [args...]\n' >&2
    return 125
  fi
  local budget="$1" rc=0 i leader_done=0 start
  shift

  # A runner stopped from outside (an outer runner's deadline, a cancelled
  # session) must not leave its group running with stdout open.
  trap 'on_stop 143' TERM
  trap 'on_stop 130' INT

  # Job control gives the background command its own process group (pgid ==
  # its pid), so `kill -- -<pgid>` reaches every descendant that stays in it.
  start=$SECONDS
  set -m
  "$@" &
  PGID=$!
  set +m

  # Done means the whole group is gone, not just the leader: a background
  # descendant can hold stdout open after the leader exits.
  for (( i = 0; i <= budget * POLLS_PER_SEC; i++ )); do
    if (( ! leader_done )) && ! leader_running; then
      wait "$PGID" || rc=$?
      leader_done=1
    fi
    if (( leader_done )) && ! group_alive; then
      return "$rc"
    fi
    (( SECONDS - start > budget )) && break
    (( i == budget * POLLS_PER_SEC )) || sleep "$POLL_SEC"
  done
  stop_group "$KILL_GRACE_SEC"
  printf 'bounded-run: %s ran past its %ss deadline and was stopped — run it by hand to see where it waits\n' "$*" "$budget" >&2
  return 124
}

# Is any process of the managed group still running? The leader alone is not
# enough: it can exit on SIGTERM while a descendant ignores it. A defunct
# (zombie) member does not count: it has already exited and holds no pipe,
# and under a non-reaping parent it would otherwise keep the group "alive"
# until the deadline. `ps -A -o pgid= -o stat=` reads states on macOS and
# Linux procps alike; when ps cannot answer, any member counts as running, so
# the deadline still holds. Liveness never comes from `kill -0`: its EPERM
# (macOS, a member mid-exec or a zombie) is indistinguishable from ESRCH once
# the diagnostic is discarded.
group_alive() {
  local table rc=0
  if ! table="$(ps -A -o pgid= -o stat=)"; then
    warn_once "ps could not list process states — counting every group member as running"
    return 0
  fi
  awk -v g="$PGID" '$1 == g && $2 !~ /^Z/ { found = 1 } END { exit !found }' <<<"$table" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    *) warn_once "awk failed (exit ${rc}) reading process states — counting every group member as running"; return 0 ;;
  esac
}

# Is the leader still running? `ps -p` exits 1 when no such process exists;
# a defunct leader has exited and only awaits `wait`. Any other ps failure
# counts as running, so the deadline still holds.
leader_running() {
  local st rc=0
  st="$(ps -p "$PGID" -o stat=)" || rc=$?
  case "$rc" in
    0) [[ "$st" != *Z* ]] ;;
    1) return 1 ;;
    *) warn_once "ps could not read the leader's state (exit ${rc}) — counting it as running"; return 0 ;;
  esac
}

#: Set once a degraded-liveness warning has been printed.
WARNED=0
warn_once() { # <message>
  (( WARNED )) && return 0
  WARNED=1
  printf 'bounded-run: %s\n' "$1" >&2
}

# SIGTERM the group, wait up to <grace> seconds for every member, then SIGKILL
# whatever is left, and reap the leader.
stop_group() { # <grace-seconds>
  local i
  signal_group TERM
  for (( i = 0; i < $1 * POLLS_PER_SEC; i++ )); do
    group_alive || break
    sleep "$POLL_SEC"
  done
  if group_alive; then signal_group KILL; fi
  # The shell's own "Terminated" job report is replaced by the caller's line.
  wait "$PGID" 2>/dev/null || return 0
  return 0
}

on_stop() { # <exit-code>
  trap - TERM INT
  if [[ -n "$PGID" ]]; then stop_group "$TRAP_GRACE_SEC"; fi
  exit "$1"
}

# Signal the managed process group. A group that already exited is the
# expected race, not a failure.
signal_group() { # <signal>
  if ! kill "-$1" -- "-$PGID" 2>/dev/null; then
    group_alive || return 0
    printf 'bounded-run: could not send SIG%s to process group %s\n' "$1" "$PGID" >&2
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
