#!/usr/bin/env bash
# Gate: the manifest version must be ahead of the registry, and the CHANGELOG
# must carry a heading for it.
#
# This plugin publishes the manifest version AS-IS on every merge to main (no
# auto-bump, no commit-back — .github/workflows/publish.yml). The author bumps
# `.tessl-plugin/plugin.json` and writes the `## <version> — <date>` CHANGELOG
# heading in the PR itself (rules/context-artifacts.md CHANGELOG Hygiene). A PR
# that forgets either would red the publish AFTER merge; this script fails the
# PR instead, before merge (rules/ci-safety.md).
#
# Usage: check-version-bump.sh [manifest] [changelog]
#   manifest   default .tessl-plugin/plugin.json
#   changelog  default CHANGELOG.md
# Env:
#   CHECK_VERSION_REGISTRY_CMD  command printing the registry-version.sh JSON
#                               ({"version":"x.y.z"} or {"version":null});
#                               default `bash skills/release/registry-version.sh
#                               <workspace> <plugin>` with workspace/plugin
#                               taken from the manifest `name`. Tests inject a
#                               fake here so no network is touched.
# Out:   one JSON object on stdout: {"manifest":"x.y.z","registry":"x.y.z","ok":bool}
#        A never-published plugin reads registry "0.0.0" (first publish passes).
# Exit:  0 ok; 1 the manifest is not ahead of the registry OR the CHANGELOG
#        heading is missing (actionable stderr); 2 on tool/parse error.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=skills/release/version-compare.sh
source "$REPO_ROOT/skills/release/version-compare.sh"

main() {
  local manifest="${1:-.tessl-plugin/plugin.json}" changelog="${2:-CHANGELOG.md}"

  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; return 2; }
  [[ -f "$manifest" ]] || { echo "error: manifest not found at ${manifest} — run from the plugin repo root or pass the path" >&2; return 2; }
  [[ -f "$changelog" ]] || { echo "error: changelog not found at ${changelog} — create it with a '## <version> — <date>' heading" >&2; return 2; }

  local name mversion
  name=$(jq -r '.name // empty' "$manifest") \
    || { echo "error: ${manifest} is not valid JSON" >&2; return 2; }
  mversion=$(jq -r '.version // empty' "$manifest") \
    || { echo "error: ${manifest} is not valid JSON" >&2; return 2; }
  [[ "$mversion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || { echo "error: manifest version '${mversion}' is not numeric MAJOR.MINOR.PATCH" >&2; return 2; }

  local registry_cmd="${CHECK_VERSION_REGISTRY_CMD:-}"
  if [[ -z "$registry_cmd" ]]; then
    [[ "$name" == */* ]] \
      || { echo "error: manifest name '${name}' is not <workspace>/<plugin>; set CHECK_VERSION_REGISTRY_CMD or fix the name" >&2; return 2; }
    registry_cmd="bash '${REPO_ROOT}/skills/release/registry-version.sh' '${name%%/*}' '${name#*/}'"
  fi

  # registry-version.sh exits 2 with empty stdout on a tool failure; a
  # never-published plugin is {"version":null} on exit 0 (a valid baseline).
  local rjson rc=0
  rjson=$(bash -c "$registry_cmd") || rc=$?
  if (( rc != 0 )) || [[ -z "$rjson" ]]; then
    echo "error: registry lookup failed (exit ${rc}) — check 'tessl' auth/connectivity and re-run" >&2
    return 2
  fi
  local rversion
  rversion=$(printf '%s' "$rjson" | jq -r '.version // empty') \
    || { echo "error: registry lookup returned non-JSON: ${rjson}" >&2; return 2; }
  [[ -n "$rversion" ]] || rversion="0.0.0"
  [[ "$rversion" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || { echo "error: registry version '${rversion}' is not numeric MAJOR.MINOR.PATCH" >&2; return 2; }

  local ok=true
  if ! version_gt "$mversion" "$rversion"; then
    ok=false
    echo "error: manifest version ${mversion} is not ahead of the registry (${rversion}) — bump version in ${manifest} and add a '## <version> — <date>' CHANGELOG heading" >&2
  fi

  # `## <version> — ` heading present? grep rc 0/1/>1 classified explicitly
  # (rules/error-handling.md): 1 is the expected "absent", >1 a read error.
  local grc=0
  grep -qF -- "## ${mversion} — " "$changelog" || grc=$?
  if (( grc == 1 )); then
    ok=false
    echo "error: ${changelog} has no '## ${mversion} — <date>' heading — add one above this release's entries" >&2
  elif (( grc > 1 )); then
    echo "error: reading ${changelog} failed (grep exit ${grc})" >&2
    return 2
  fi

  jq -n --arg m "$mversion" --arg r "$rversion" --argjson ok "$ok" '{manifest: $m, registry: $r, ok: $ok}'
  [[ "$ok" == "true" ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
