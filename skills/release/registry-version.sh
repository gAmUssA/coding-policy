#!/usr/bin/env bash
# Read a tile's current latest published version from the authoritative
# versions API (`tessl api v1/tiles/<ws>/<tile>/versions`).
#
# Extracted from verify-publish-landed.sh's inline fetch (it did this same
# read to derive conjunct 2's `current`) so the read has ONE hardened home:
# scripts/check-version-bump.sh needs the same "what does the registry say
# now" answer, and rules/script-delegation.md wants that deterministic read in
# a single script, not reproduced per caller.
#
# Why the versions API and NOT `tessl plugin info`: the plugin-info search
# listing is eventually-consistent and lags the version API by minutes, so a
# freshly-landed publish reads back as "not published yet" through it — a
# false negative exactly when the caller is asking "did my publish land"
# (#181). The versions API reflects a publish immediately. capture-registry-
# baseline.sh reads plugin info for the PRE-merge baseline (staleness is fine
# before the publish); this script reads the versions API for the POST
# question (staleness is not).
#
# Sourceable AND runnable, with TWO stdout contracts for the two entry points:
#   - the registry_version FUNCTION prints the BARE version string (empty when
#     never published) — verify-publish-landed.sh sources this file and calls
#     it so an in-process `tessl` shell-mock (its unit suite) reaches the read.
#   - the SCRIPT's main() emits STRUCTURED JSON (rules/script-delegation.md —
#     deterministic scripts emit JSON, not prose) — scripts/check-version-bump.sh
#     runs it as a subprocess and parses `.version`.
# The EXIT-trap cleanup is installed inside main() (not at top level) so
# sourcing this file never clobbers the caller's own trap.
#
# Usage: registry-version.sh <workspace> <plugin>
# Out:   one JSON object on stdout — {"version": "<major.minor.patch>"} for a
#        published tile, or {"version": null} when the tile has never published
#        (an empty registry is a valid first-publish baseline, not an error).
#        Callers parse `.version`: the gate's capture phase reads null as "no
#        baseline yet". Diagnostics go to stderr.
# Exit:  0 on a successful read (published version OR never-published null);
#        2 on tool/parse failure (tessl or jq absent, tessl unreachable,
#        non-JSON body). On exit 2 stdout is empty — no JSON is emitted.

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main() returns,
# when a main-local would be out of scope and `set -u` would turn cleanup into
# an unbound-variable failure (same reasoning as capture-registry-baseline.sh).
# registry_version() also clears it inline on every return path, so the
# main-level EXIT trap is only a backstop for an unexpected signal.
RV_ERR_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final command
# status becomes the script's exit status, so a failing `rm` here would rewrite
# a deliberate `exit 2` (rules/error-handling.md Shell Error Handling). `if !
# rm` rather than a bare `rm`: under `set -e` a failing rm would abort the
# handler before `return 0` runs, reintroducing the rewrite the handler exists
# to prevent; an `if` condition suspends `set -e`. Clears RV_ERR_FILE so a
# second call (inline cleanup then the backstop trap) is an idempotent no-op.
cleanup_rv_err_file() {
  if [[ -n "${RV_ERR_FILE:-}" ]]; then
    if ! rm -f "$RV_ERR_FILE"; then
      echo "registry-version.sh: warning: could not remove temp file ${RV_ERR_FILE} — remove it by hand" >&2
    fi
    RV_ERR_FILE=""
  fi
  return 0
}

# registry_version <workspace> <plugin>
# Prints the numeric latest version (empty when never published) to stdout;
# returns 0 on a successful read, 2 on tool/parse failure. Assumes tessl and
# jq are present — main() checks that once up front; the sourcing caller
# (verify-publish-landed.sh) guards jq itself and surfaces a tessl outage
# through this function's runtime failure branch.
registry_version() {
  local workspace="$1" tile="$2"
  local endpoint="v1/tiles/${workspace}/${tile}/versions"

  RV_ERR_FILE=$(mktemp) \
    || { echo "error: mktemp failed — cannot run registry-version.sh without a writable TMPDIR" >&2; return 2; }

  # Capture stdout (the JSON body) and stderr (a tessl warning) SEPARATELY so a
  # warning line can't merge into the parsed payload and be misread as a
  # version — same capture discipline as verify-moderation-cleared.sh. One page
  # suffices: the endpoint returns newest-first and a publish only ever adds a
  # new highest version, so the maximum is always on page 1.
  local versions_json
  if ! versions_json=$(tessl api "$endpoint" 2>"$RV_ERR_FILE"); then
    local err; err=$(cat "$RV_ERR_FILE")
    cleanup_rv_err_file
    echo "error: 'tessl api ${endpoint}' failed: ${err} — verify (1) tessl CLI is installed and on PATH ('command -v tessl'), (2) the workspace/plugin slug is correct, (3) you have network access to the registry, then re-run 'tessl api ${endpoint}' directly to inspect the failure" >&2
    return 2
  fi
  cleanup_rv_err_file

  # `jq empty`, not `jq -e .`: -e sets the exit from the last output's
  # truthiness, so a valid body evaluating to false/null would misreport as
  # "not JSON". `jq empty` parses and produces no output — exit reflects parse
  # validity alone (same reasoning as verify-moderation-cleared.sh).
  if ! printf '%s' "$versions_json" | jq empty >/dev/null 2>&1; then
    echo "error: 'tessl api ${endpoint}' returned a body that is not valid JSON — the registry may be returning an error page or the endpoint shape changed; inspect it directly with 'tessl api ${endpoint}' before retrying (body was: ${versions_json})" >&2
    return 2
  fi

  # Guard the response SHAPE: .data MUST be an array. A valid-JSON body with no
  # .data array (e.g. `{}` or an error object) would otherwise fall through the
  # optional `.data[]?` below as an empty (never-published) result, fabricating
  # a first-publish baseline that lets the gate pass a publish that never
  # landed. Reject it as a tool/parse failure — reserve empty for a real
  # `{"data":[]}` (rules/error-handling.md — a schema/error response is a tool
  # failure, not a non-result).
  if ! printf '%s' "$versions_json" | jq -e '.data | type == "array"' >/dev/null 2>&1; then
    echo "error: 'tessl api ${endpoint}' response has no .data array — a registry error body or an endpoint shape change, not an empty registry. Inspect it directly with 'tessl api ${endpoint}' (body was: ${versions_json})" >&2
    return 2
  fi

  # Max version across the page, ranked numerically. sort_by a per-element
  # parsed [major,minor,patch] key (jq compares arrays element-wise, so
  # [0,3,119] outranks [0,3,20]) and take the last — depends only on the
  # `.version` string, not the endpoint's split-out int fields, so a shape
  # change there can't silently mis-rank. `tonumber? // 0` neutralises a
  # non-numeric component rather than aborting the whole rank. `// empty`
  # yields an empty string when the tile has never published.
  local current
  current=$(printf '%s' "$versions_json" | jq -r '
    [.data[]?.attributes.version | select(. != null)]
    | sort_by(split(".") | map(tonumber? // 0))
    | last // empty')

  # A non-empty result must be numeric MAJOR.MINOR.PATCH — the shape version_gt
  # and the numeric sort assume. A malformed max (a schema change slipping a
  # non-numeric version through) is a tool/parse failure, not a version.
  if [[ -n "$current" ]]; then
    if [[ ! "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "error: 'tessl api ${endpoint}' latest version '${current}' is not numeric MAJOR.MINOR.PATCH — an endpoint shape change or malformed entry; inspect it directly with 'tessl api ${endpoint}'" >&2
      return 2
    fi
    printf '%s\n' "$current"
  fi
  return 0
}

main() {
  if [[ $# -ne 2 ]]; then
    echo "usage: $0 <workspace> <plugin>" >&2
    exit 2
  fi

  command -v tessl >/dev/null 2>&1 \
    || { echo "error: tessl CLI not found on PATH — install it ('npm i -g @tessl/cli') or add it to PATH, then re-run" >&2; exit 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; exit 2; }

  # Backstop cleanup for an unexpected signal; registry_version() already
  # clears RV_ERR_FILE on its own return paths (and does so inside the command
  # substitution's subshell below, so no temp file leaks).
  trap cleanup_rv_err_file EXIT

  # Capture the BARE version through the function, then wrap it in the script's
  # JSON contract. A tool/parse failure returns 2 with the actionable
  # diagnostic already on stderr; propagate that exit code and emit NO JSON
  # (stdout stays empty on failure, per the header contract).
  local version rc=0
  version=$(registry_version "$1" "$2") || rc=$?
  if [[ $rc -ne 0 ]]; then
    exit "$rc"
  fi

  # Empty -> JSON null (never published). A present version is numeric
  # MAJOR.MINOR.PATCH (registry_version validated the shape), so it carries no
  # character needing JSON string-escaping.
  if [[ -z "$version" ]]; then
    printf '{"version":null}\n'
  else
    printf '{"version":"%s"}\n' "$version"
  fi
}

# `if` form, not `[[ ]] && main`: this file is SOURCED by
# verify-publish-landed.sh under an `if ! source ...` guard, and `[[ false ]]
# && main` would leave the sourced file's exit status at 1 (the false test),
# tripping that guard. An `if` whose condition is false returns 0, so sourcing
# succeeds cleanly while a direct run still invokes main.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
