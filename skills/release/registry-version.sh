#!/usr/bin/env bash
# Read a tile's current latest published version from the authoritative
# versions API (`tessl api v1/tiles/<ws>/<tile>/versions`).
#
# Extracted from verify-publish-landed.sh's inline fetch (it did this same
# read to derive conjunct 2's `current`) so the read has ONE hardened home:
# scripts/assemble-release.sh needs the same "what does the registry say
# now" answer, and rules/script-delegation.md wants that deterministic read in
# a single script, not reproduced per caller.
#
# Why the versions API and NOT `tessl plugin info`: the plugin-info search
# listing is eventually-consistent and lags the version API by minutes, so a
# freshly-landed publish reads back as "not published yet" through it — a
# false negative exactly when the caller is asking "did my publish land"
# (#181). The versions API reflects a publish immediately. Both release-gate
# reads go through it: capture-registry-baseline.sh for the PRE-merge baseline
# and verify-publish-landed.sh for the POST question. Both source this file.
#
# Sourceable AND runnable, with TWO stdout contracts for the two entry points:
#   - the registry_version FUNCTION prints the BARE version string (empty when
#     never published) — capture-registry-baseline.sh and
#     verify-publish-landed.sh source this file and call it, so an in-process
#     `tessl` shell-mock (verify's unit suite) reaches the read.
#   - the SCRIPT's main() emits STRUCTURED JSON (rules/script-delegation.md —
#     deterministic scripts emit JSON, not prose) — scripts/assemble-release.sh
#     runs it as a subprocess and parses `.version`.
# The EXIT-trap cleanup is installed inside main() (not at top level) so
# sourcing this file never clobbers the caller's own trap.
#
# Usage: registry-version.sh <workspace> <plugin>
# Out:   one JSON object on stdout — {"version": "<major.minor.patch>"} for a
#        published tile, or {"version": null} when the tile has never published
#        (an empty registry is a valid first-publish baseline, not an error).
#        Never published covers two bodies: `{"data":[]}`, and the structured
#        not-found body the versions API answers for a plugin with no versions,
#        `{"error":{"status":404,"title":"Not Found"[,"message":...]}}` with no
#        other key, AND tessl's exit status 1 for it. The same body with any
#        other exit status is a failure. Any other error body — a 404 of another shape, 401, 5xx — a
#        transport failure, or an unparseable body stays a failure.
#        Callers parse `.version`: the gate's capture phase reads null as "no
#        baseline yet". Diagnostics go to stderr.
# Exit:  0 on a successful read (published version OR never-published null);
#        2 on tool/parse failure (tessl or jq absent, tessl unreachable,
#        non-JSON body, a .data entry whose version is not a numeric
#        MAJOR.MINOR.PATCH string — prereleases included, which this plugin
#        family never publishes — or a failed extraction). On exit 2 stdout is empty — no JSON is emitted.

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main() returns,
# when a main-local would be out of scope and `set -u` would turn cleanup into
# an unbound-variable failure.
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
  local versions_json call_rc=0
  versions_json=$(tessl api "$endpoint" 2>"$RV_ERR_FILE") || call_rc=$?

  # The versions endpoint answers a never-published plugin with a structured
  # 404 and tessl exits 1 for it. Both are required: the same body with any
  # other status (0, 2, 126, a 137 kill) is not that registry answer and
  # falls through to the failure paths below. Strict shape: the top level holds only `error`, which holds only
  # status/title/message, with the number 404 and the title "Not Found". A
  # looser match (any 404, any body naming "Not Found") would let a wrong-slug
  # route or a proxy error page fabricate a first-publish baseline.
  if (( call_rc == 1 )) && printf '%s' "$versions_json" | jq -e '
      type == "object" and (keys == ["error"])
      and (.error | type == "object")
      and ((.error | keys) - ["message", "status", "title"] | length == 0)
      and .error.status == 404 and .error.title == "Not Found"' >/dev/null 2>&1; then
    cleanup_rv_err_file
    return 0
  fi

  if (( call_rc != 0 )); then
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

  # Every entry must carry a numeric MAJOR.MINOR.PATCH .attributes.version.
  # A missing, null, non-string, empty or otherwise malformed version ("1.2",
  # "v1.2.3", a prerelease such as "1.2.3-rc1") is a malformed entry, never
  # skipped: skipping it would turn a page of malformed entries into an empty
  # (never-published) result, or hide it behind a valid version that sorts
  # later. Prereleases are rejected: this plugin family never publishes them,
  # and version_gt orders numeric versions only.
  # Only an empty .data array, or the exit-1 not-found body above, may yield
  # an empty successful read.
  if ! printf '%s' "$versions_json" | jq -e '
      all(.data[]; type == "object" and (.attributes | type == "object")
                   and (.attributes.version | type == "string"
                        and test("^[0-9]+\\.[0-9]+\\.[0-9]+$")))' >/dev/null 2>&1; then
    echo "error: 'tessl api ${endpoint}' has a .data entry whose .attributes.version string is not numeric MAJOR.MINOR.PATCH — an endpoint shape change or malformed entry, not an empty registry. Inspect it directly with 'tessl api ${endpoint}' (body was: ${versions_json})" >&2
    return 2
  fi

  # Max version across the page, ranked numerically. sort_by a per-element
  # parsed [major,minor,patch] key (jq compares arrays element-wise, so
  # [0,3,119] outranks [0,3,20]) and take the last. Every component is
  # numeric (checked above). `// empty` yields an empty string only for an empty .data array. The
  # extraction's own exit status is checked: a jq failure is exit 2.
  local current
  if ! current=$(printf '%s' "$versions_json" | jq -r '
      [.data[].attributes.version]
      | sort_by(split(".") | map(tonumber))
      | last // empty'); then
    echo "error: could not extract the latest version from 'tessl api ${endpoint}' — inspect it directly (body was: ${versions_json})" >&2
    return 2
  fi

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
