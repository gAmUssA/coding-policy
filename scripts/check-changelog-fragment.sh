#!/usr/bin/env bash
# PR gate: a change to shipped content carries a changelog fragment, and no PR
# assigns a version itself.
#
# The version is assigned at merge (.github/workflows/publish.yml runs
# scripts/assemble-release.sh): it bumps both manifests from the fragments'
# requested semver level, writes the `## <version> — <date>` CHANGELOG heading,
# and commits back. A PR therefore never edits a manifest `version` or adds a
# version heading — two PRs doing so would conflict and serialize the merge
# queue, which is what fragments exist to remove (docs/adr/0003-version-on-merge.md).
#
# Fragment format (changelog.d/README.md is the human-facing copy):
#   changelog.d/<name>.md, <name> matching FRAGMENT_NAME_RE below:
#     ---
#     bump: patch|minor|major
#     ---
#     ### <Section>
#
#     - <bullet>
#   Front matter holds only `bump`. The body holds one or more `### <Section>`
#   headings, each followed by at least one `- ` bullet; no other `#` heading
#   level and no text before the first section. No CR characters.
#
# This file is also SOURCED by scripts/assemble-release.sh for fragment_check /
# fragment_name_ok, so the fragment grammar has one home
# (rules/script-delegation.md). Sourcing defines functions only.
#
# Usage: check-changelog-fragment.sh <base-ref> [head-ref]
#   base-ref  the PR's base (CI: origin/<base_ref>); the diff is taken from
#             the merge base, so commits landed on the base since the branch
#             was cut do not count against the PR
#   head-ref  default HEAD; committed state only — uncommitted edits are ignored
# Out:   one JSON object on stdout:
#          {"ok": bool, "base": "<ref>", "head": "<ref>",
#           "shipped_changed": bool, "shipped_files": [...],
#           "fragments": [{"path": "...", "bump": "..."}], "errors": [...]}
#        `fragments` lists the valid fragments the PR adds or modifies; an
#        empty list means the merge assigns no new version unless another
#        fragment is already pending on the base.
# Exit:  0 ok; 1 a gate failure (each named on stderr with what to do);
#        2 on tool/setup error (git or jq missing, unknown ref, no merge base)
#        with empty stdout.

set -euo pipefail

# Paths whose change alters the published plugin (rules/context-artifacts.md
# Versioning and CHANGELOG). Prefix match. .tesslignore decides what the
# published artifact holds, so a change to it ships too.
SHIPPED_PREFIXES=(rules/ skills/ hooks/ .tessl-plugin/ .codex-plugin/ .tesslignore)
# .tesslignore patterns inside shipped trees that are still not published.
UNSHIPPED_SUFFIXES=(.bak .tmp)
MANIFESTS=(.tessl-plugin/plugin.json .codex-plugin/plugin.json)
FRAGMENT_DIR="changelog.d"
FRAGMENT_README="changelog.d/README.md"
FRAGMENT_NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]*\.md$'
BUMP_LEVELS_RE='^(patch|minor|major)$'

# fragment_name_ok <basename>: 0 iff the name is a valid fragment file name.
fragment_name_ok() {
  [[ "$1" =~ $FRAGMENT_NAME_RE && "$1" != "README.md" ]]
}

# fragment_check <file>: validate one fragment's content.
# Sets FRAGMENT_BUMP (patch|minor|major) on success; sets FRAGMENT_ERROR to an
# actionable message and returns 1 on the first violation.
fragment_check() {
  local file="$1" line n=0 state="open" bump="" section="" bullets=0 sections=0
  FRAGMENT_BUMP=""
  FRAGMENT_ERROR=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    if [[ "$line" == *$'\r'* ]]; then
      FRAGMENT_ERROR="line ${n} contains a CR character — save the fragment with LF line endings"
      return 1
    fi
    case "$state" in
      open)
        if [[ "$line" != "---" ]]; then
          FRAGMENT_ERROR="line 1 must be '---' — start the fragment with front matter: '---', 'bump: patch|minor|major', '---'"
          return 1
        fi
        state="front"
        ;;
      front)
        if [[ "$line" == "---" ]]; then
          state="body"
        elif [[ "$line" =~ ^bump:[[:space:]]*([^[:space:]]*)[[:space:]]*$ ]]; then
          if [[ -n "$bump" ]]; then
            FRAGMENT_ERROR="line ${n}: 'bump' appears twice — keep one 'bump: patch|minor|major' line"
            return 1
          fi
          bump="${BASH_REMATCH[1]}"
          if [[ ! "$bump" =~ $BUMP_LEVELS_RE ]]; then
            FRAGMENT_ERROR="line ${n}: bump '${bump}' is not one of patch, minor, major — patch for fixes and rule tightening, minor for a new rule/skill/hook or process change, major for a removed or contract-breaking one"
            return 1
          fi
        else
          FRAGMENT_ERROR="line ${n}: front matter holds only 'bump: patch|minor|major' — remove '${line}'"
          return 1
        fi
        ;;
      body)
        if [[ "$line" =~ ^###[[:space:]]+[^[:space:]] ]]; then
          if [[ -n "$section" && $bullets -eq 0 ]]; then
            FRAGMENT_ERROR="section '${section}' has no '- ' bullet — add one or remove the heading"
            return 1
          fi
          section="${line#'###'}"
          section="${section#"${section%%[![:space:]]*}"}"
          sections=$((sections + 1))
          bullets=0
        elif [[ "$line" == \#* ]]; then
          FRAGMENT_ERROR="line ${n}: only '### <Section>' headings are allowed in a fragment body — the publish workflow writes the '## <version>' heading on merge"
          return 1
        elif [[ -z "$section" && "$line" =~ [^[:space:]] ]]; then
          FRAGMENT_ERROR="line ${n}: text before the first '### <Section>' heading — open the body with a section such as '### Fixed'"
          return 1
        elif [[ "$line" =~ ^-[[:space:]]+[^[:space:]] ]]; then
          bullets=$((bullets + 1))
        fi
        ;;
    esac
  done < "$file"

  if [[ "$state" == "open" ]]; then
    FRAGMENT_ERROR="file is empty — write front matter ('---', 'bump: patch|minor|major', '---') and a '### <Section>' with a '- ' bullet"
    return 1
  fi
  if [[ "$state" == "front" ]]; then
    FRAGMENT_ERROR="front matter is not closed — add a '---' line after 'bump: ...'"
    return 1
  fi
  if [[ -z "$bump" ]]; then
    FRAGMENT_ERROR="front matter has no 'bump' — add 'bump: patch|minor|major'"
    return 1
  fi
  if (( sections == 0 )); then
    FRAGMENT_ERROR="body has no '### <Section>' heading — add one (Added, Changed, Deprecated, Removed, Fixed, Security) with a '- ' bullet"
    return 1
  fi
  if (( bullets == 0 )); then
    FRAGMENT_ERROR="section '${section}' has no '- ' bullet — add one or remove the heading"
    return 1
  fi
  FRAGMENT_BUMP="$bump"
  return 0
}

# EXIT-trap cleanup; `return 0` keeps a failed rm from rewriting the exit
# status (rules/error-handling.md Shell Error Handling).
WORK_DIR=""
cleanup_work_dir() {
  if [[ -n "$WORK_DIR" ]] && ! rm -rf "$WORK_DIR"; then
    echo "warning: could not remove temp dir ${WORK_DIR} — remove it by hand" >&2
  fi
  return 0
}

is_shipped() {
  local path="$1" p s
  for s in "${UNSHIPPED_SUFFIXES[@]}"; do
    [[ "$path" == *"$s" ]] && return 1
  done
  for p in "${SHIPPED_PREFIXES[@]}"; do
    [[ "$path" == "$p"* ]] && return 0
  done
  return 1
}

# manifest_version <ref> <path>: print the manifest's version at ref; empty
# when the file is absent there. Returns 2 when the file is present but not JSON.
manifest_version() {
  local ref="$1" path="$2" body
  if ! git cat-file -e "${ref}:${path}" 2>/dev/null; then
    return 0
  fi
  body=$(git show "${ref}:${path}") \
    || { echo "error: cannot read ${path} at ${ref}" >&2; return 2; }
  printf '%s' "$body" | jq -r '.version // empty' 2>/dev/null \
    || { echo "error: ${path} at ${ref} is not valid JSON — fix the manifest" >&2; return 2; }
}

main() {
  if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 <base-ref> [head-ref]" >&2
    return 2
  fi
  local base="$1" head="${2:-HEAD}"

  command -v git >/dev/null 2>&1 || { echo "error: git is not installed or not on PATH" >&2; return 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; return 2; }

  local ref
  for ref in "$base" "$head"; do
    git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null \
      || { echo "error: '${ref}' is not a commit in this repository — fetch it first (CI: actions/checkout with fetch-depth: 0; locally: git fetch origin)" >&2; return 2; }
  done
  local mb
  mb=$(git merge-base "$base" "$head") \
    || { echo "error: no merge base between ${base} and ${head} — fetch full history (fetch-depth: 0) and re-run" >&2; return 2; }

  WORK_DIR=$(mktemp -d) || { echo "error: mktemp failed — set TMPDIR to a writable directory" >&2; return 2; }
  trap cleanup_work_dir EXIT
  local work="$WORK_DIR"

  local errors=() shipped=() frag_json=() path

  local changed
  changed=$(git diff --name-only --no-renames "$mb" "$head") \
    || { echo "error: git diff ${mb}..${head} failed" >&2; return 2; }
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if is_shipped "$path"; then shipped+=("$path"); fi
  done <<< "$changed"

  # Fragments the PR adds or modifies (deletions are not authored entries).
  local frags
  frags=$(git diff --name-only --no-renames --diff-filter=AM "$mb" "$head" -- "$FRAGMENT_DIR") \
    || { echo "error: git diff over ${FRAGMENT_DIR}/ failed" >&2; return 2; }
  local valid=0
  while IFS= read -r path; do
    [[ -n "$path" && "$path" != "$FRAGMENT_README" ]] || continue
    local name="${path#"$FRAGMENT_DIR"/}"
    if [[ "$name" == */* ]] || ! fragment_name_ok "$name"; then
      errors+=("${path}: not a fragment name — fragments are ${FRAGMENT_DIR}/<name>.md directly under ${FRAGMENT_DIR}/, <name> of letters, digits, '.', '_', '-'; move or remove it")
      continue
    fi
    git show "${head}:${path}" > "${work}/frag" \
      || { echo "error: cannot read ${path} at ${head}" >&2; return 2; }
    if fragment_check "${work}/frag"; then
      valid=$((valid + 1))
      frag_json+=("$(jq -cn --arg p "$path" --arg b "$FRAGMENT_BUMP" '{path: $p, bump: $b}')")
    else
      errors+=("${path}: ${FRAGMENT_ERROR}")
    fi
  done <<< "$frags"

  if (( ${#shipped[@]} > 0 && valid == 0 )); then
    errors+=("shipped content changed (${shipped[*]}) but the PR adds no valid changelog fragment — add ${FRAGMENT_DIR}/<name>.md with 'bump: patch|minor|major' front matter and a '### <Section>' body (format: ${FRAGMENT_README})")
  fi

  local m old new
  for m in "${MANIFESTS[@]}"; do
    old=$(manifest_version "$mb" "$m") || return 2
    new=$(manifest_version "$head" "$m") || return 2
    # A removed or nulled version reads as empty; it is a change like any
    # other, and the post-merge assembler would fail on it.
    if [[ -n "$old" && "$old" != "$new" ]]; then
      errors+=("${m}: version changed ${old} -> ${new:-<removed>} — restore it to ${old}; the publish workflow assigns the version on merge from the ${FRAGMENT_DIR}/ fragments")
    fi
  done

  local cl_diff line
  cl_diff=$(git diff --no-renames "$mb" "$head" -- CHANGELOG.md) \
    || { echo "error: git diff over CHANGELOG.md failed" >&2; return 2; }
  while IFS= read -r line; do
    [[ "$line" == "+## "* ]] || continue
    errors+=("CHANGELOG.md: the PR adds version heading '${line#+}' — remove it and move the entry into a ${FRAGMENT_DIR}/ fragment; the publish workflow writes the heading on merge")
  done <<< "$cl_diff"

  local e
  for e in "${errors[@]+"${errors[@]}"}"; do
    echo "error: ${e}" >&2
  done

  local ok=true
  (( ${#errors[@]} == 0 )) || ok=false
  jq -n \
    --argjson ok "$ok" --arg base "$base" --arg head "$head" \
    --argjson shipped "$(printf '%s\n' "${shipped[@]+"${shipped[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')" \
    --argjson frags "$(printf '%s\n' "${frag_json[@]+"${frag_json[@]}"}" | jq -s .)" \
    --argjson errors "$(printf '%s\n' "${errors[@]+"${errors[@]}"}" | jq -R . | jq -s 'map(select(. != ""))')" \
    '{ok: $ok, base: $base, head: $head, shipped_changed: ($shipped | length > 0),
      shipped_files: $shipped, fragments: $frags, errors: $errors}'
  [[ "$ok" == "true" ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
