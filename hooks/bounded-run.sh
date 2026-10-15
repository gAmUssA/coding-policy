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
#   stderr: the command's own stderr, plus one diagnostic line when the
#           deadline passed.
#   exit  : the command's own exit code; 124 when the deadline passed (the
#           `timeout` convention); 125 on a usage error.
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

#: The managed process group; empty until the command starts.
PGID=""

main() {
  if (( $# < 2 )) || [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
    printf 'bounded-run: usage: bounded-run.sh <seconds> <command> [args...]\n' >&2
    return 125
  fi
  local budget="$1" rc=0 i
  shift

  # A runner stopped from outside (an outer runner's deadline, a cancelled
  # session) must not leave its group running with stdout open.
  trap 'on_stop 143' TERM
  trap 'on_stop 130' INT

  # Job control gives the background command its own process group (pgid ==
  # its pid), so `kill -- -<pgid>` reaches every descendant that stays in it.
  set -m
  "$@" &
  PGID=$!
  set +m

  for (( i = 0; i < budget * POLLS_PER_SEC; i++ )); do
    if ! kill -0 "$PGID" 2>/dev/null; then
      wait "$PGID" || rc=$?
      return "$rc"
    fi
    sleep "$POLL_SEC"
  done
  if ! kill -0 "$PGID" 2>/dev/null; then
    wait "$PGID" || rc=$?
    return "$rc"
  fi
  stop_group "$KILL_GRACE_SEC"
  printf 'bounded-run: %s ran past its %ss deadline and was stopped — run it by hand to see where it waits\n' "$*" "$budget" >&2
  return 124
}

# Is any process of the managed group still alive? The leader alone is not
# enough: it can exit on SIGTERM while a descendant ignores it.
group_alive() {
  kill -0 -- "-$PGID" 2>/dev/null
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
