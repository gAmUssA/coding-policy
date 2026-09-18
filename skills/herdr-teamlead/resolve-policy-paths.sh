#!/usr/bin/env bash
# Resolve readable policy artifacts before composing a worker brief.
# argv: <absolute-shared-checkout> [absolute-global-tessl-root]
#       Global root defaults to $HOME/.tessl; the optional argument supports
#       nonstandard installations and isolated test fixtures.
# stdout: {"POLICY_INDEX":"<absolute-path>","RELEASE_SKILL":"<absolute-path>"}
# stderr: actionable diagnostic on failure; stdout stays empty.
# exit: 0 both artifacts resolved, 1 usage/precondition, 2 lookup/tool failure.
# Each artifact independently prefers <shared-checkout>/.tessl, then the
# global root, then this package's native Codex artifacts when its Codex manifest
# exists. Only readable non-empty regular files qualify. No writes or network calls.
set -euo pipefail

resolve_artifact() { # <relative-artifact> <local-root> <global-root> <codex-fallback>
  local candidate directory
  for candidate in "$2/$1" "$3/$1" "$4"; do
    if [[ -f "$candidate" && -r "$candidate" && -s "$candidate" ]]; then
      if ! directory="$(cd "${candidate%/*}" && pwd -P)"; then
        printf 'resolve-policy-paths: cannot resolve %s — restore directory access and retry\n' "$candidate" >&2
        return 2
      fi
      printf '%s/%s\n' "$directory" "${candidate##*/}"
      return 0
    fi
  done
  printf 'resolve-policy-paths: no readable %s under %s or %s or the bundled Codex fallback — install or repair the policy plugin before composing briefs\n' "$1" "$2" "$3" >&2
  return 2
}

main() {
  if (( $# < 1 || $# > 2 )); then
    echo 'resolve-policy-paths: usage: resolve-policy-paths.sh <absolute-shared-checkout> [absolute-global-tessl-root]' >&2
    return 1
  fi
  local shared="$1" global_root="${2:-$HOME/.tessl}" policy release output
  if [[ "$shared" != /* || ! -d "$shared" || "$global_root" != /* || "$shared$global_root" == *[[:cntrl:]]* ]]; then
    echo 'resolve-policy-paths: pass an existing absolute shared checkout and an absolute global Tessl root' >&2
    return 1
  fi
  if ! command -v jq >/dev/null 2>&1; then
    echo 'resolve-policy-paths: install jq before resolving brief inputs' >&2
    return 1
  fi
  local plugin_root codex_policy="" codex_release=""
  if ! plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"; then
    echo 'resolve-policy-paths: cannot locate the installed plugin — restore directory access and retry' >&2
    return 2
  fi
  if [[ -f "$plugin_root/.codex-plugin/plugin.json" ]]; then
    codex_policy="$plugin_root/.codex-plugin/RULES.md"
    codex_release="$plugin_root/skills/release/SKILL.md"
  fi
  policy="$(resolve_artifact RULES.md "$shared/.tessl" "$global_root" "$codex_policy")" || return 2
  release="$(resolve_artifact plugins/gamussa/coding-policy/skills/release/SKILL.md "$shared/.tessl" "$global_root" "$codex_release")" || return 2
  if ! output="$(jq -n --arg policy "$policy" --arg release "$release" '{POLICY_INDEX:$policy, RELEASE_SKILL:$release}')"; then
    echo 'resolve-policy-paths: cannot emit paths — check jq and retry before dispatch' >&2
    return 2
  fi
  printf '%s\n' "$output"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
