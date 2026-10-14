#!/usr/bin/env bash
# Assemble pending changelog fragments into a release: assign the next
# version, write it into both manifests, head a CHANGELOG entry, and consume
# the fragments. Run by .github/workflows/publish.yml on every push to main;
# the workflow commits the result back and publishes when `publish` is true
# (docs/adr/0003-version-on-merge.md).
#
# Behavior:
#   - No fragments: change nothing; report the manifest version and whether
#     it is still ahead of the registry (a re-run after a failed publish).
#   - Fragments: validate EVERY fragment before writing anything (grammar:
#     scripts/check-changelog-fragment.sh, sourced here — one home).
#     base = max(registry latest, .tessl-plugin version, .codex-plugin version);
#     next = base bumped by the highest requested level (major > minor > patch).
#     Both manifests get `version = next` via jq (2-space indent, trailing
#     newline). CHANGELOG.md gets `## <next> — <date>` plus the fragments'
#     sections, inserted directly above its first `## ` heading (appended when
#     it has none). Sections merge by name in SECTION_ORDER, then any other
#     name alphabetically; within a section, entries follow fragment file name
#     order (LC_ALL=C). Consumed fragments are deleted; changelog.d/README.md
#     is never consumed. Every new file is staged beside its target first and
#     renamed into place only after all of them are built.
#
# Usage: assemble-release.sh [--root <dir>] [--date YYYY-MM-DD] [--registry-cmd <cmd>]
#   --root          plugin repo root (default: current directory)
#   --date          CHANGELOG heading date (default: `date -u +%F`)
#   --registry-cmd  command printing registry-version.sh JSON
#                   ({"version":"x.y.z"} or {"version":null}); default
#                   `bash skills/release/registry-version.sh <ws> <plugin>` from
#                   the manifest `name`. Tests inject a fake; no network.
# Out:   one JSON object on stdout:
#          {"assembled": bool, "version": "<manifest version after the run>",
#           "registry": "<registry latest, 0.0.0 when never published>",
#           "publish": bool (version strictly ahead of registry),
#           "bump": "patch|minor|major"|null, "fragments": ["changelog.d/x.md", ...]}
# Exit:  0 success (assembled or no-op); 1 a malformed fragment or a stray
#        file in changelog.d/ (nothing written, actionable stderr); 2 on
#        tool/setup error (jq missing, manifest or CHANGELOG missing,
#        registry lookup failed) with empty stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=scripts/check-changelog-fragment.sh
source "$SCRIPT_DIR/check-changelog-fragment.sh"
# shellcheck source=skills/release/version-compare.sh
source "$REPO_ROOT/skills/release/version-compare.sh"

SECTION_ORDER="Added Changed Deprecated Removed Fixed Security"
TESSL_MANIFEST=".tessl-plugin/plugin.json"
CODEX_MANIFEST=".codex-plugin/plugin.json"
CHANGELOG="CHANGELOG.md"
VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
DATE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'

STAGED=()
# EXIT-trap cleanup of staged files never renamed into place; `return 0`
# keeps a failed rm from rewriting the exit status.
cleanup_staged() {
  local f
  for f in "${STAGED[@]+"${STAGED[@]}"}"; do
    if [[ -e "$f" ]] && ! rm -f "$f"; then
      echo "warning: could not remove staged file ${f} — remove it by hand" >&2
    fi
  done
  return 0
}

# bump_version <x.y.z> <patch|minor|major>
bump_version() {
  local a b c
  IFS='.' read -r a b c <<< "$1"
  case "$2" in
    major) printf '%d.0.0\n' "$((a + 1))" ;;
    minor) printf '%d.%d.0\n' "$a" "$((b + 1))" ;;
    patch) printf '%d.%d.%d\n' "$a" "$b" "$((c + 1))" ;;
  esac
}

bump_rank() {
  case "$1" in major) echo 3 ;; minor) echo 2 ;; patch) echo 1 ;; *) echo 0 ;; esac
}

max_version() {
  local best="$1" v
  shift
  for v in "$@"; do
    if version_gt "$v" "$best"; then best="$v"; fi
  done
  printf '%s\n' "$best"
}

# render_sections <fragment>...: print the merged `### <Section>` blocks.
render_sections() {
  LC_ALL=C awk -v order="$SECTION_ORDER" '
    function flush(  b) {
      if (cur != "") {
        b = buf
        sub(/^\n+/, "", b)
        sub(/\n+$/, "", b)
        if (b != "") {
          if (cur in content) { content[cur] = content[cur] "\n" b }
          else { content[cur] = b; names[++nn] = cur }
        }
      }
      cur = ""; buf = ""
    }
    FNR == 1 { flush(); fm = 0 }
    fm < 2 { if ($0 == "---") fm++; next }
    /^###[ \t]+/ {
      flush()
      cur = $0
      sub(/^###[ \t]+/, "", cur)
      sub(/[ \t]+$/, "", cur)
      next
    }
    { buf = buf "\n" $0 }
    END {
      flush()
      no = split(order, canon, " ")
      for (i = 1; i <= no; i++) {
        known[canon[i]] = 1
        if (canon[i] in content) printf "### %s\n\n%s\n\n", canon[i], content[canon[i]]
      }
      no = 0
      for (i = 1; i <= nn; i++) if (!(names[i] in known)) other[++no] = names[i]
      for (i = 2; i <= no; i++) {
        v = other[i]
        for (j = i - 1; j >= 1 && other[j] > v; j--) other[j + 1] = other[j]
        other[j + 1] = v
      }
      for (i = 1; i <= no; i++) printf "### %s\n\n%s\n\n", other[i], content[other[i]]
    }
  ' "$@"
}

# stage <target> <var>: create a staging file beside target (same filesystem,
# so the final mv is a rename), remember it for cleanup, and store its path in
# <var>. Not run in a command substitution: STAGED must reach the EXIT trap.
stage() {
  local s
  s=$(mktemp "${1}.assemble.XXXXXX") \
    || { echo "error: cannot create a staging file beside ${1} — check write permission on $(dirname "$1")" >&2; return 2; }
  STAGED+=("$s")
  # mktemp creates 0600; the tracked files are plain 0644.
  chmod 0644 "$s" || { echo "error: chmod on staging file ${s} failed" >&2; return 2; }
  printf -v "$2" '%s' "$s"
}

manifest_field() {
  jq -r "$2 // empty" "$1" 2>/dev/null \
    || { echo "error: ${1} is not valid JSON — fix the manifest" >&2; return 2; }
}

main() {
  local root="." date="" registry_cmd=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --root) root="${2:?--root needs a directory}"; shift 2 ;;
      --date) date="${2:?--date needs YYYY-MM-DD}"; shift 2 ;;
      --registry-cmd) registry_cmd="${2:?--registry-cmd needs a command}"; shift 2 ;;
      *) echo "usage: $0 [--root <dir>] [--date YYYY-MM-DD] [--registry-cmd <cmd>]" >&2; return 2 ;;
    esac
  done

  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; return 2; }
  cd "$root" || { echo "error: cannot enter --root ${root}" >&2; return 2; }

  local f
  for f in "$TESSL_MANIFEST" "$CODEX_MANIFEST" "$CHANGELOG"; do
    [[ -f "$f" ]] || { echo "error: ${f} not found under $(pwd) — run from the plugin repo root or pass --root" >&2; return 2; }
  done
  if [[ -z "$date" ]]; then date=$(date -u +%F); fi
  [[ "$date" =~ $DATE_RE ]] || { echo "error: --date '${date}' is not YYYY-MM-DD" >&2; return 2; }

  local name tv cv
  name=$(manifest_field "$TESSL_MANIFEST" .name) || return 2
  tv=$(manifest_field "$TESSL_MANIFEST" .version) || return 2
  cv=$(manifest_field "$CODEX_MANIFEST" .version) || return 2
  for f in "$tv" "$cv"; do
    [[ "$f" =~ $VERSION_RE ]] \
      || { echo "error: manifest version '${f}' is not numeric MAJOR.MINOR.PATCH — fix ${TESSL_MANIFEST} / ${CODEX_MANIFEST}" >&2; return 2; }
  done

  # Collect and validate fragments before any read of the registry or write.
  local frags=() entry base_name
  if [[ -d "$FRAGMENT_DIR" ]]; then
    # A checked substitution, not a process substitution: a failed scan must
    # stop the release, never read as "no fragments".
    local listing
    listing=$(find "$FRAGMENT_DIR" -mindepth 1 -maxdepth 1 | LC_ALL=C sort) \
      || { echo "error: cannot list ${FRAGMENT_DIR}/ — check the checkout's permissions and re-run the publish workflow; nothing was written" >&2; return 2; }
    while IFS= read -r entry; do
      [[ -n "$entry" ]] || continue
      base_name="${entry#"$FRAGMENT_DIR"/}"
      [[ "$base_name" == "README.md" ]] && continue
      if [[ "$base_name" == */* || ! -f "$entry" ]] || ! fragment_name_ok "$base_name"; then
        echo "error: ${entry} is not a fragment — ${FRAGMENT_DIR}/ holds only README.md and <name>.md fragment files; move or remove it" >&2
        return 1
      fi
      frags+=("$entry")
    done <<< "$listing"
  fi

  local top=0 bump="" rank
  for f in "${frags[@]+"${frags[@]}"}"; do
    if ! fragment_check "$f"; then
      echo "error: ${f}: ${FRAGMENT_ERROR} — nothing was written; fix the fragment on main and re-run the publish workflow" >&2
      return 1
    fi
    rank=$(bump_rank "$FRAGMENT_BUMP")
    if (( rank > top )); then top=$rank; bump="$FRAGMENT_BUMP"; fi
  done

  local rjson rc=0 rversion
  if [[ -n "$registry_cmd" ]]; then
    # Test-only override; the publish workflow never passes it.
    rjson=$(bash -c "$registry_cmd") || rc=$?
  else
    # The name comes from a PR-editable manifest: hold it to the registry's
    # name grammar and pass it as argv, never through a shell string.
    [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
      || { echo "error: manifest name '${name}' is not <workspace>/<plugin> (letters, digits, '.', '_', '-'); fix .tessl-plugin/plugin.json 'name'" >&2; return 2; }
    rjson=$(bash "${REPO_ROOT}/skills/release/registry-version.sh" "${name%%/*}" "${name#*/}") || rc=$?
  fi
  if (( rc != 0 )) || [[ -z "$rjson" ]]; then
    echo "error: registry lookup failed (exit ${rc}) — check 'tessl' auth/connectivity and re-run" >&2
    return 2
  fi
  rversion=$(printf '%s' "$rjson" | jq -r '.version // empty' 2>/dev/null) \
    || { echo "error: registry lookup returned non-JSON: ${rjson}" >&2; return 2; }
  [[ -n "$rversion" ]] || rversion="0.0.0"
  [[ "$rversion" =~ $VERSION_RE ]] \
    || { echo "error: registry version '${rversion}' is not numeric MAJOR.MINOR.PATCH" >&2; return 2; }

  if (( ${#frags[@]} == 0 )); then
    local publish=false
    if version_gt "$tv" "$rversion"; then publish=true; fi
    jq -n --arg v "$tv" --arg r "$rversion" --argjson p "$publish" \
      '{assembled: false, version: $v, registry: $r, publish: $p, bump: null, fragments: []}'
    return 0
  fi

  local base next
  base=$(max_version "$rversion" "$tv" "$cv")
  next=$(bump_version "$base" "$bump")

  trap cleanup_staged EXIT
  local s_tessl s_codex s_cl sections
  stage "$TESSL_MANIFEST" s_tessl || return 2
  stage "$CODEX_MANIFEST" s_codex || return 2
  stage "$CHANGELOG" s_cl || return 2
  jq --arg v "$next" '.version = $v' "$TESSL_MANIFEST" > "$s_tessl" \
    || { echo "error: writing the version into ${TESSL_MANIFEST} failed" >&2; return 2; }
  jq --arg v "$next" '.version = $v' "$CODEX_MANIFEST" > "$s_codex" \
    || { echo "error: writing the version into ${CODEX_MANIFEST} failed" >&2; return 2; }
  sections=$(render_sections "${frags[@]}") \
    || { echo "error: rendering the fragment sections failed" >&2; return 2; }
  HEADING="## ${next} — ${date}" SECTIONS="$sections" awk '
    BEGIN { block = ENVIRON["HEADING"] "\n\n" ENVIRON["SECTIONS"] "\n" }
    !done && /^## / { printf "%s\n", block; done = 1 }
    { print }
    END { if (!done) printf "\n%s", block }
  ' "$CHANGELOG" > "$s_cl" \
    || { echo "error: writing ${CHANGELOG} failed" >&2; return 2; }

  if ! { mv "$s_tessl" "$TESSL_MANIFEST" && mv "$s_codex" "$CODEX_MANIFEST" && mv "$s_cl" "$CHANGELOG"; }; then
    echo "error: renaming staged files into place failed — inspect 'git status' and restore with 'git checkout -- .'" >&2
    return 2
  fi
  rm -f "${frags[@]}" \
    || { echo "error: deleting consumed fragments failed — remove ${frags[*]} by hand before committing" >&2; return 2; }

  jq -n --arg v "$next" --arg r "$rversion" --arg b "$bump" \
    --argjson fr "$(printf '%s\n' "${frags[@]}" | jq -R . | jq -s .)" \
    '{assembled: true, version: $v, registry: $r, publish: true, bump: $b, fragments: $fr}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
