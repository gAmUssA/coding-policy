#!/usr/bin/env bash
# Capture a tile's current registry version as the pre-merge baseline for
# the release contract's conjunct 2 (registry advanced past the baseline —
# see rules/ci-safety.md "Always Watch CI").
#
# Why this is a script and not a pasted pipeline: skills/release/SKILL.md
# previously had the operator paste
#   PRE=$(tessl plugin info <w>/<p> | grep "Latest Version" | awk '{print $NF}')
# which shares its parse with verify-publish-landed.sh but none of its
# hardening. Two failure modes it could not see:
#   1. A tessl warning on stderr merged into the parsed text and misread
#      the version.
#   2. A parse miss (the "Latest Version" line absent, e.g. the registry
#      changed its output shape) silently yielded an empty PRE, which then
#      went on to verify-publish-landed.sh AS the baseline — every version
#      compares as "advanced past empty", so conjunct 2 passes vacuously
#      and an unpublished release reports as confirmed.
# Same parse in two places, one unguarded, feeding the gate that is
# supposed to catch exactly this (rules/script-delegation.md — deterministic
# work belongs in a script, not a code block for the agent to reproduce).
#
# Usage: capture-registry-baseline.sh <workspace> <plugin>
# Out:   one JSON object on stdout: {"version": "<major.minor.patch>"}
#        Numeric only — deliberately NARROWER than semver. The sole
#        consumer is verify-publish-landed.sh's version_gt(), which orders
#        baselines with bash arithmetic; a prerelease/build value arrives at
#        (( )) as `3-rc.1+build.5`, and the arithmetic error makes the
#        comparison return "not greater" rather than raising — so a landed
#        publish reports as failed against an innocent registry. Widening
#        this shape requires teaching version_gt() semver precedence first:
#        the accepted shape and the comparator move together or not at all.
# Exit:  0 on a successful parse; 2 on tool-state error (tessl unreachable
#        or absent, unparseable output, or a version outside the shape
#        above). There is no exit 1: an absent
#        baseline is never a valid input to the conjunction, so this script
#        fails loud rather than emitting a value the caller might use.

set -euo pipefail

json_str() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  printf '"%s"' "$s"
}

# Script-global, NOT a main() local: the EXIT trap fires after main()
# returns on the happy path, and a local would be out of scope by then —
# `set -u` turns the cleanup into an unbound-variable failure that reddens
# a successful run. (verify-publish-landed.sh survives the same pattern
# only because every one of its paths exits from inside main().)
ERR_FILE=""
# The EXIT trap's final status leaks into the script's exit status, so
# cleanup must never speak for the script's outcome. Two distinct ways it
# used to:
#   1. A bare `[[ -n "$ERR_FILE" ]]` guard returning 1 (path never set, e.g.
#      the usage-error path) rewrote a deliberate `exit 2` into `exit 1`.
#      `return 0` fixes that one.
#   2. `return 0` alone does NOT fix the other: under `set -e` a failing
#      `rm` aborts the handler before `return 0` ever runs, and the trap's
#      status still rewrites the verdict. Reproduced with an unwritable
#      TMPDIR — `exit 2` became `exit 1` and the handler never reached its
#      last line. An `if` condition suspends `set -e`, so the rm failure is
#      reported rather than escaping.
cleanup() {
  if [[ -n "$ERR_FILE" ]]; then
    if ! rm -f "$ERR_FILE"; then
      echo "capture-registry-baseline.sh: warning: could not remove temp file ${ERR_FILE} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup EXIT

main() {
  if [[ $# -ne 2 ]]; then
    echo "usage: capture-registry-baseline.sh <workspace> <plugin>" >&2
    exit 2
  fi
  local workspace="$1" tile="$2"

  command -v tessl >/dev/null \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }

  ERR_FILE=$(mktemp) \
    || { echo "error: mktemp failed — cannot run capture-registry-baseline.sh without a writable TMPDIR" >&2; exit 2; }

  # Capture stdout and stderr separately, then parse stdout on its own.
  # A mixed `2>&1` capture would let a tessl warning line reach the parse
  # and be misread as the version.
  local tessl_output
  tessl_output=$(tessl plugin info "${workspace}/${tile}" 2>"$ERR_FILE") \
    || { local err; err=$(cat "$ERR_FILE"); echo "error: 'tessl plugin info ${workspace}/${tile}' failed: ${err} — verify (1) the workspace/plugin slug is correct, (2) you have network access to the registry, (3) 'tessl status' shows you are logged in, then re-run 'tessl plugin info ${workspace}/${tile}' directly to inspect the failure" >&2; exit 2; }

  # Parse in its own step so a miss is distinguishable from a tool failure.
  # `grep` exits 1 on no-match, which `set -e` + `pipefail` would turn into
  # a bare exit before the actionable diagnostic below fires; an explicit
  # `if` around the pipeline keeps the failure observable without
  # suppressing it.
  local version=""
  if ! version=$(printf '%s\n' "$tessl_output" | grep "Latest Version" | awk '{print $NF}'); then
    version=""
  fi
  if [[ -z "$version" ]]; then
    echo "error: could not parse 'Latest Version' from 'tessl plugin info ${workspace}/${tile}' output — the registry output shape may have changed; inspect it directly and update this parse (output was: ${tessl_output})" >&2
    exit 2
  fi

  # Validate the shape this script's contract promises. Taking the last
  # field of the "Latest Version" line on faith accepts whatever the
  # registry prints there — `Latest Version unavailable` would emit
  # {"version":"unavailable"} and hand verify-publish-landed.sh a baseline
  # its comparator cannot order, converting a registry-side outage into a
  # bogus conjunct-2 verdict.
  #
  # Numeric major.minor.patch ONLY — deliberately narrower than semver.
  # The sole consumer of this baseline is verify-publish-landed.sh's
  # version_gt(), which splits on '.' and feeds the fields to bash
  # arithmetic. A prerelease/build value reaches `(( ))` as `3-rc.1+build.5`,
  # and the resulting arithmetic error makes the comparison return "not
  # greater" rather than raising: a landed publish is then reported as
  # "Latest Version is not greater than baseline — investigate the registry
  # state", sending the operator after a registry that did nothing wrong.
  # This script must not accept a baseline shape its consumer cannot order.
  # Widening here requires teaching version_gt() full semver precedence
  # first (see verify-publish-landed.sh) — the accepted shape and the
  # comparator move together or not at all.
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "error: parsed 'Latest Version' token '${version}' from 'tessl plugin info ${workspace}/${tile}' is not a numeric major.minor.patch version — the registry may be reporting an outage, or is publishing a prerelease/build version that verify-publish-landed.sh's comparator cannot order; inspect it directly before retrying the release (output was: ${tessl_output})" >&2
    exit 2
  fi

  printf '{"version":%s}\n' "$(json_str "$version")"
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
