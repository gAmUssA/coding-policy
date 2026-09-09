#!/usr/bin/env bash
# Compose one round's briefs from the packaged templates.
#
# Substitution is a pure function of (template, values), so it belongs in a
# script rather than in an agent's hands (`rules/script-delegation.md`): a
# placeholder the agent forgets to fill reaches a worker as the literal
# `{{WORKTREE}}`, and a worker with a cleared context has no way to notice.
#
# Contract:
#   argv  : <templates-dir> <values-json-file> <output-dir>
#   values: {"shared": {"KEY": "value", ...},
#            "roles":  {"<role>": {"KEY": "value", ...}, ...}}
#           `shared` fills COMMON.md and every brief; a role's own values win
#           on a collision. Roles map to `brief-<role>.md` in the templates dir.
#   stdout: one JSON object —
#           {"common":"<path>","briefs":{"<role>":"<path>", ...}}
#   stderr: diagnostics only.
#   exit  : 0 every file written with no placeholder left,
#           1 precondition unmet (usage, missing dir/file/template, no jq),
#           2 validation failed — an unfilled placeholder, a supplied key no
#             template uses, a value that is not text, an invalid/reused REPORT
#             path, a REPORT longer than TEAMLEAD_REPORT_PATH_MAX_COLS, or a reviewer/tester
#             REVIEW_PACKAGE that is not an absolute readable non-empty file,
#             or an unreadable POLICY_INDEX / RELEASE_SKILL artifact.
#             Nothing is written on a
#             validation failure,
#           3 a tool this depends on failed (the placeholder scan itself). The
#             answer is unknown, which is never reported as "no placeholders".
#   env   : TEAMLEAD_REPORT_PATH_MAX_COLS overrides the REPORT length limit
#           (tests, a fleet whose narrowest pane is wider); a non-integer or
#           zero value is a precondition failure (exit 1).
#
# Validation runs in both directions on purpose. An unfilled placeholder is a
# brief that lies to a worker; a supplied key nothing uses is a value the lead
# believes it sent and did not.
set -euo pipefail

# A placeholder is upper-case, digits, and underscores between double braces.
PLACEHOLDER_RE='\{\{[A-Z0-9_]+\}\}'
# Longest REPORT value a brief may carry. The worker's final message ends with
# `REPORT: <path>`, and the wait confirms the complete literal on one visible
# row. A TUI wraps that line at its own content width and
# a wrap cannot be told from a newline, so the only sound fix
# is a path that fits one row on every pane this fleet runs: the widest marker
# line is the prefix plus indentation plus this many characters.
TEAMLEAD_REPORT_PATH_MAX_COLS="${TEAMLEAD_REPORT_PATH_MAX_COLS:-100}"

warn() { printf 'compose-briefs: %s\n' "$1" >&2; }

# Echo every distinct placeholder name in <file>, one per line.
placeholders_in() { # <file>
  local found rc=0
  found="$(grep -oE "$PLACEHOLDER_RE" "$1")" || rc=$?
  if (( rc == 1 )); then return 0; fi
  if (( rc != 0 )); then
    warn "cannot scan placeholders in ${1} — check file permissions and the grep installation"
    return 2
  fi
  if ! printf '%s\n' "$found" | sed -e 's/^{{//' -e 's/}}$//' | sort -u; then
    warn "cannot normalize placeholders in ${1} — check the sed and sort installation"
    return 2
  fi
}

# Echo the placeholders still standing in <text>, space separated and sorted.
#
# Returns 0 when the scan RAN — empty output then means none are left — and 2
# when the scan itself failed. `grep` exits 1 on no-match and 2 on a real error
# (an unreadable input, a bad pattern), and collapsing those two into "nothing
# found" is what would let an unrendered brief pass validation
# (rules/error-handling.md Shell Error Handling).
leftover_placeholders() { # <text>
  local found rc=0 sorted
  found="$(printf '%s' "$1" | grep -oE "$PLACEHOLDER_RE")" || rc=$?
  if (( rc == 1 )); then
    printf ''
    return 0
  fi
  if (( rc != 0 )); then
    warn "the placeholder scan failed (grep exit ${rc}) — the rendered text could not be checked; re-run, and check that grep is a working GNU/BSD grep"
    return 2
  fi
  rc=0
  sorted="$(printf '%s' "$found" | sort -u | tr '\n' ' ')" || rc=$?
  if (( rc != 0 )); then
    warn "the placeholder scan failed while sorting its matches (exit ${rc}) — the rendered text could not be checked; re-run"
    return 2
  fi
  printf '%s' "$sorted"
  return 0
}

# Refuse a value that is not text before it reaches a brief.
#
# `jq -r` prints a JSON null as the four characters `null`, which substitutes
# cleanly and leaves NO placeholder behind — the brief then reads as fully
# rendered while telling a worker its worktree is at `null`. Objects and arrays
# arrive as JSON fragments the same way.
validate_values() { # <values-json> <label>
  local offenders
  offenders="$(printf '%s' "$1" | jq -r '
    to_entries
    | map(select((.value | type) as $t | $t != "string" and $t != "number"))
    | map("\(.key) (\(.value | type))")
    | join(", ")')" || {
    warn "could not inspect the values for ${2} — check that they are a JSON object"
    return 2
  }
  if [[ -n "$offenders" ]]; then
    warn "${2} carries values that are not text: ${offenders} — give each one a string (a JSON null renders as the literal 'null' in a brief)"
    return 2
  fi
  return 0
}

validate_review_package() { # <merged-values-json> <role>
  case "$2" in reviewer|tester) ;; *) return 0 ;; esac
  local package ref key
  for key in REVIEW_BASE REVIEW_HEAD; do
    ref="$(printf '%s' "$1" | jq -r --arg k "$key" '.[$k] // ""')" || return 2
    if [[ ! "$ref" =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]]; then
      warn "${key} for role '${2}' must be a full lowercase commit SHA — resolve the recorded range with git rev-parse before composing"
      return 2
    fi
  done
  package="$(printf '%s' "$1" | jq -r '.REVIEW_PACKAGE // ""')" || return 2
  if [[ "$package" != /* || "$package" == *[[:cntrl:]]* \
        || ! -f "$package" || ! -r "$package" || ! -s "$package" ]]; then
    warn "REVIEW_PACKAGE for role '${2}' must name an absolute readable non-empty file — run review-package.sh for the recorded range and pass its output path"
    return 2
  fi
}

# Echo <template> with every KEY=VALUE pair in the given JSON object applied.
substitute() { # <template-file> <values-json>
  local content key value
  content="$(cat "$1")"
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    value="$(printf '%s' "$2" | jq -r --arg k "$key" '.[$k]')"
    content="${content//\{\{$key\}\}/$value}"
  done < <(printf '%s' "$2" | jq -r 'keys[]')
  printf '%s\n' "$content"
}

main() {
  if (( $# != 3 )); then
    warn "usage: compose-briefs.sh <templates-dir> <values-json-file> <output-dir>"
    return 1
  fi
  local templates="$1" values_file="$2" outdir="$3"

  case "$TEAMLEAD_REPORT_PATH_MAX_COLS" in
    ''|*[!0-9]*)
      warn "TEAMLEAD_REPORT_PATH_MAX_COLS must be a positive integer, got '${TEAMLEAD_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
      return 1
      ;;
  esac
  if (( 10#$TEAMLEAD_REPORT_PATH_MAX_COLS < 1 )); then
    warn "TEAMLEAD_REPORT_PATH_MAX_COLS must be a positive integer, got '${TEAMLEAD_REPORT_PATH_MAX_COLS}' — unset it to use the script's default"
    return 1
  fi
  # Normalize to decimal once, so a validated `08` is not reparsed as octal.
  TEAMLEAD_REPORT_PATH_MAX_COLS=$(( 10#$TEAMLEAD_REPORT_PATH_MAX_COLS ))

  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to compose briefs"
    return 1
  fi
  if [[ ! -d "$templates" ]]; then
    warn "templates dir not found: ${templates} — point at skills/herdr-teamlead/templates"
    return 1
  fi
  if [[ ! -r "$values_file" ]]; then
    warn "values file not readable: ${values_file}"
    return 1
  fi

  local values rc=0
  values="$(jq -e '.' < "$values_file" 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "values file ${values_file} is not valid JSON — fix it and re-run"
    return 1
  fi
  local roles
  roles="$(printf '%s' "$values" | jq -r '.roles | keys[]' 2>/dev/null)" || {
    warn "values file ${values_file} has no .roles object — see the contract at the top of this script"
    return 1
  }
  if [[ -z "$roles" ]]; then
    warn "values file ${values_file} names no roles — nothing to compose"
    return 1
  fi

  local shared
  shared="$(printf '%s' "$values" | jq -c '.shared // {}')"

  # Every source file must exist before anything is written.
  local common_tpl="${templates}/COMMON.md" role
  if [[ ! -r "$common_tpl" ]]; then
    warn "template not found: ${common_tpl}"
    return 1
  fi
  while IFS= read -r role; do
    if [[ ! -r "${templates}/brief-${role}.md" ]]; then
      warn "template not found: ${templates}/brief-${role}.md — roles are named by their template"
      return 1
    fi
  done <<< "$roles"

  # Compose into memory first: a validation failure must leave no half-written
  # round behind, and no output directory either (`rules/file-hygiene.md`
  # Idempotency); the directory is created only once every check has passed.
  local -a out_paths=() out_bodies=() report_paths=()
  local merged rendered leftovers supplied known common_known unused key report
  local common_body scan_rc=0
  validate_values "$shared" "the shared values" || return 2
  # Resolver-produced policy paths are explicit brief inputs. Custom templates
  # need not carry them; any supplied artifact must remain readable at compose.
  local policy_key policy_path policy_present
  for policy_key in POLICY_INDEX RELEASE_SKILL; do
    policy_present="$(printf '%s' "$shared" | jq -r --arg k "$policy_key" 'has($k)')" || return 2
    if [[ "$policy_present" == true ]]; then
      if ! printf '%s' "$shared" | jq -e --arg k "$policy_key" '.[$k] | if type == "string" then explode | all(. >= 32 and . != 127) else false end' >/dev/null; then
        warn "${policy_key} must be a file path without control characters — use resolve-policy-paths.sh output"
        return 2
      fi
      policy_path="$(printf '%s' "$shared" | jq -r --arg k "$policy_key" '.[$k]')" || return 2
      if [[ "$policy_path" != /* || "$policy_path" == *[[:cntrl:]]* || ! -f "$policy_path" || ! -r "$policy_path" || ! -s "$policy_path" ]]; then
        warn "${policy_key} must name an absolute readable non-empty file — run resolve-policy-paths.sh and supply its output before composing"
        return 2
      fi
    fi
  done
  common_body="$(substitute "$common_tpl" "$shared")"
  leftovers="$(leftover_placeholders "$common_body")" || scan_rc=$?
  if (( scan_rc != 0 )); then return 3; fi
  if [[ -n "${leftovers// /}" ]]; then
    warn "COMMON.md still holds unfilled placeholders: ${leftovers}— add them to .shared"
    return 2
  fi
  out_paths+=("${outdir}/COMMON.md")
  out_bodies+=("$common_body")

  while IFS= read -r role; do
    local policy_override
    policy_override="$(printf '%s' "$values" | jq -r --arg r "$role" '.roles[$r] | has("POLICY_INDEX") or has("RELEASE_SKILL")')" || return 2
    if [[ "$policy_override" == true ]]; then
      warn "policy artifact paths for role '${role}' belong only in .shared — remove per-role POLICY_INDEX and RELEASE_SKILL keys"
      return 2
    fi
    merged="$(jq -c -n --argjson a "$shared" --argjson b "$(printf '%s' "$values" | jq -c --arg r "$role" '.roles[$r]')" '$a * $b')"
    validate_values "$merged" "the values for role '${role}'" || return 2
    # Validate in JSON before command substitution can strip trailing newlines
    # or discard a NUL byte from the path.
    if ! printf '%s' "$merged" | jq -e '.REPORT | if type == "string" then explode | all(. >= 32 and . != 127) else false end' >/dev/null; then
      warn "REPORT for role '${role}' must be a string without control characters — choose a fresh absolute file path on one line"
      return 2
    fi
    report="$(printf '%s' "$merged" | jq -r '.REPORT // ""')"
    if [[ "$report" != /* || "$report" == *[[:cntrl:]]* || "$report" == */ ]]; then
      warn "REPORT for role '${role}' must be an absolute file path on one line — choose a fresh path for this attempt"
      return 2
    fi
    if [[ -e "$report" || -L "$report" ]]; then
      warn "REPORT for role '${role}' already exists at ${report} — preserve it and choose a fresh path for this attempt"
      return 2
    fi
    local prior_report
    for prior_report in ${report_paths[@]+"${report_paths[@]}"}; do
      if [[ "$report" == "$prior_report" ]]; then
        warn "REPORT for role '${role}' duplicates another role's destination — give each assignment a distinct report path"
        return 2
      fi
    done
    report_paths+=("$report")
    if (( ${#report} > TEAMLEAD_REPORT_PATH_MAX_COLS )); then
      warn "REPORT for role '${role}' is ${#report} characters; the limit is ${TEAMLEAD_REPORT_PATH_MAX_COLS} so the worker's \`REPORT: <path>\` line fits one pane row and the wait can confirm it — use a shorter reports directory (e.g. one under \$HOME/.local/state) and re-run"
      return 2
    fi
    rendered="$(substitute "${templates}/brief-${role}.md" "$merged")"
    scan_rc=0
    leftovers="$(leftover_placeholders "$rendered")" || scan_rc=$?
    if (( scan_rc != 0 )); then return 3; fi
    if [[ -n "${leftovers// /}" ]]; then
      warn "brief-${role}.md still holds unfilled placeholders: ${leftovers}— add them to .roles.${role} or .shared"
      return 2
    fi
    # A supplied key no template uses is a value the lead believes it sent.
    # The known set is collected ONCE into a string and membership-tested with
    # a glob: piping into `grep -q` under `set -o pipefail` reports failure
    # whenever grep exits early on a match and SIGPIPEs the producer, which
    # reads as "not found" for every key that IS found.
    supplied="$(printf '%s' "$merged" | jq -r 'keys[]')"
    known="$(placeholders_in "${templates}/brief-${role}.md")" || return 3
    common_known="$(placeholders_in "$common_tpl")" || return 3
    known+=$'\n'"$common_known"
    unused=""
    while IFS= read -r key; do
      [[ -n "$key" ]] || continue
      if [[ $'\n'"${known}"$'\n' != *$'\n'"${key}"$'\n'* ]]; then
        unused+="${key} "
      fi
    done <<< "$supplied"
    if [[ -n "$unused" ]]; then
      warn "values for role '${role}' carry keys no template uses: ${unused}— remove them or fix the name"
      return 2
    fi
    validate_review_package "$merged" "$role" || return 2
    out_paths+=("${outdir}/brief-${role}.md")
    out_bodies+=("$rendered")
  done <<< "$roles"

  local output_path
  for report in "${report_paths[@]}"; do
    for output_path in "${out_paths[@]}"; do
      if [[ "$report" == "$output_path" ]]; then
        warn "REPORT overlaps a generated brief at ${report} — choose a separate report destination"
        return 2
      fi
    done
  done

  if ! mkdir -p "$outdir"; then
    warn "cannot create the output dir ${outdir} — check permissions"
    return 1
  fi
  local i
  for i in "${!out_paths[@]}"; do
    if ! printf '%s\n' "${out_bodies[$i]}" > "${out_paths[$i]}"; then
      warn "cannot write ${out_paths[$i]} — check permissions on ${outdir}"
      return 1
    fi
  done

  local briefs_json="{}"
  while IFS= read -r role; do
    briefs_json="$(printf '%s' "$briefs_json" | jq -c --arg r "$role" --arg p "${outdir}/brief-${role}.md" '. + {($r): $p}')"
  done <<< "$roles"
  jq -n --arg common "${outdir}/COMMON.md" --argjson briefs "$briefs_json" \
    '{common: $common, briefs: $briefs}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
