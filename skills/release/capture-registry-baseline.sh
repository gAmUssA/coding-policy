#!/usr/bin/env bash
# Capture a tile's current registry version as the pre-merge baseline for
# the release contract's conjunct 2 (registry advanced past the baseline —
# see rules/ci-safety.md "Always Watch CI").
#
# Why this is a script and not a pasted pipeline: skills/release/SKILL.md
# previously had the operator paste
#   PRE=$(tessl plugin info <w>/<p> | grep "Latest Version" | awk '{print $NF}')
# which could not see two failure modes:
#   1. A tessl warning on stderr merged into the parsed text and misread
#      the version.
#   2. A parse miss silently yielded an empty PRE, which then went on to
#      verify-publish-landed.sh AS the baseline — every version compares as
#      "advanced past empty", so conjunct 2 passes vacuously and an
#      unpublished release reports as confirmed.
#
# The read goes through registry-version.sh's registry_version (sourced), the
# one hardened home for "what does the versions API say now"
# (rules/script-delegation.md). It replaced a `tessl plugin info` parse: that
# command answers a never-published plugin with prose and a non-zero exit,
# indistinguishable from an outage, so a new plugin's first release could
# never capture a baseline. The versions API answers it with a structured
# 404 that registry_version classifies strictly as never published.
#
# Usage: capture-registry-baseline.sh <workspace> <plugin>
# Out:   one JSON object on stdout, one of:
#          {"version": "<major.minor.patch>"}  the latest published version
#          {"version": "unpublished"}          a VERIFIED never-published
#                                              plugin: the versions API
#                                              returned `{"data":[]}` or its
#                                              structured not-found body
#        The version is numeric only — deliberately NARROWER than semver.
#        The sole consumer is verify-publish-landed.sh's version_gt(), which
#        orders baselines with bash arithmetic; a prerelease/build value would
#        make a landed publish report as failed. Widening this shape requires
#        teaching version_gt() semver precedence first.
#        `unpublished` is a sentinel verify-publish-landed.sh accepts in place
#        of a version; it then requires the registry to show any published
#        version. An empty or guessed baseline (e.g. 0.0.0) is never emitted.
#        Callers capture this JSON in its own guarded assignment, then parse
#        `.version` with a guarded `jq -e` (skills/release/SKILL.md Step 7).
#        Never pipe this script into jq: the pipeline reports jq's status, jq
#        accepts the empty stdout of exit 2, and the baseline comes out empty.
# Exit:  0 on a verified read; 2 on tool-state error (tessl or jq absent, a
#        non-404 or non-structured error body, a transport failure, an
#        unparseable body, or a version outside the shape above). Stdout is
#        empty on exit 2. There is no exit 1: an unverified baseline is never
#        a valid input to the conjunction.

set -euo pipefail

_crb_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=skills/release/registry-version.sh
if ! source "${_crb_dir}/registry-version.sh"; then
  echo "error: cannot source ${_crb_dir}/registry-version.sh — the release skill tree is incomplete; re-clone the repo or re-install the plugin, then re-run" >&2
  exit 2
fi

main() {
  if [[ $# -ne 2 ]]; then
    echo "usage: capture-registry-baseline.sh <workspace> <plugin>" >&2
    exit 2
  fi
  local workspace="$1" tile="$2"

  command -v tessl >/dev/null 2>&1 \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

  # Backstop for registry_version's temp file on an unexpected signal; the
  # function clears it on its own return paths.
  trap cleanup_rv_err_file EXIT

  # registry_version validates the body, the .data shape and the numeric
  # MAJOR.MINOR.PATCH shape, and writes an actionable diagnostic on failure.
  local version rc=0
  version=$(registry_version "$workspace" "$tile") || rc=$?
  if [[ $rc -ne 0 ]]; then
    exit 2
  fi

  if [[ -z "$version" ]]; then
    printf '{"version":"unpublished"}\n'
  else
    printf '{"version":"%s"}\n' "$version"
  fi
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
