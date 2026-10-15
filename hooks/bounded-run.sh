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
#: Seconds between liveness checks.
POLL_SEC=0.1

main() {
  if (( $# < 2 )) || [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
    printf 'bounded-run: usage: bounded-run.sh <seconds> <command> [args...]\n' >&2
    return 125
  fi
  local budget="$1" pid rc=0 start
  shift

  # Job control gives the background command its own process group (pgid ==
  # its pid), so `kill -- -<pid>` reaches every descendant that stays in it.
  start=$SECONDS
  set -m
  "$@" &
  pid=$!
  set +m

  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS - start >= budget )); then
      signal_group TERM "$pid"
      local grace_start=$SECONDS
      while kill -0 "$pid" 2>/dev/null && (( SECONDS - grace_start < KILL_GRACE_SEC )); do
        sleep "$POLL_SEC"
      done
      if kill -0 "$pid" 2>/dev/null; then signal_group KILL "$pid"; fi
      # The shell's own "Terminated" job report is replaced by the line below.
      wait "$pid" 2>/dev/null || rc=$?
      printf 'bounded-run: %s ran past its %ss deadline and was stopped — run it by hand to see where it waits\n' "$*" "$budget" >&2
      return 124
    fi
    sleep "$POLL_SEC"
  done
  wait "$pid" || rc=$?
  return "$rc"
}

# Signal the command's process group. A group that already exited is the
# expected race, not a failure.
signal_group() { # <signal> <pgid>
  if ! kill "-$1" -- "-$2" 2>/dev/null; then
    kill -0 "$2" 2>/dev/null || return 0
    printf 'bounded-run: could not send SIG%s to process group %s\n' "$1" "$2" >&2
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
