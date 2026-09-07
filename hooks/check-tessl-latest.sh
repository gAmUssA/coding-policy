#!/usr/bin/env bash
# Report gAmUssA/* dependency versions at session start, after running an update.
#
# A SessionStart hook. It is the deterministic enforcement for the
# Runtime-Managed Manifest Carve-Out (rules/dependency-management.md): tessl.json
# is a runtime-managed manifest — tessl rewrites the resolved state into the
# gitignored .tessl/ — so gAmUssA/*-owned deps use the floating "latest"
# specifier. Each session this hook runs `tessl update --yes` (best-effort) and
# reports every gAmUssA/* dependency's version transition, so a stale consumer repo
# surfaces the moment a session opens and a disallowed pin is flagged without a
# per-consumer deploy-time check. Third-party pins (tessl-labs/*, tessl/npm-*)
# are out of scope — they pin normally.
#
# The status text begins with the "Session-start status — " marker so the agent
# surfaces it to the user (rules/hook-action-reporting.md).
#
# Design choices, shared with the other SessionStart hooks:
#   - It DOES something (updates + reads resolved state), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#   - No throttle state: running `tessl update` is the point, so it runs every session.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read.
#   stdout: on a consumer repo, one JSON object {"additionalContext": "<status>"}
#           whose text begins with "Session-start status — versions: ".
#           Three cases emit nothing at all: no manifest (not a consumer), no
#           gAmUssA/* dependency, and an unparseable manifest (warned to stderr,
#           no status — there is no dependency list to report on).
#   exit  : always 0. A best-effort failure that still leaves something to
#           report — a failed `tessl update`, a missing jq, an unreadable
#           resolved-state file — warns to stderr AND emits the status, which
#           names the degradation (rules/error-handling.md Shell Error Handling).
#   env   : TESSL_LATEST_MANIFEST (manifest path, default tessl.json),
#           TESSL_STATE_DIR (resolved-state dir, default .tessl).
set -euo pipefail

warn() { printf 'check-tessl-latest: %s\n' "$1" >&2; }

# Print a gAmUssA/* dependency's installed version from its resolved-state
# tessl-package.json, or nothing when it cannot be determined. An absent file is
# an expected non-result (the dep is not yet resolved into .tessl/ — reported as
# install-pending). An existing but unreadable or unparseable file is a tool
# failure: warn, never swallow it as an absent-file non-result
# (rules/error-handling.md — distinguish a non-result from a tool failure).
#
# The exit status separates the two empty-output cases so the caller can label
# them differently: 0 = read it, or the dep is simply not resolved yet;
# 3 = the file is there and broken. Both print nothing, and a status line
# reading "(install pending)" for a broken file would report a tool failure as
# a routine not-installed-yet state.
installed_version() { # <state-dir> <dep-name>
  local pkg="$1/plugins/$2/tessl-package.json" v
  [[ -e "$pkg" ]] || return 0
  if [[ ! -r "$pkg" ]]; then
    warn "resolved-state file ${pkg} is unreadable — check permissions on the .tessl/ tree"
    return 3
  fi
  if ! v="$(jq -r '.version // empty' "$pkg" 2>/dev/null)"; then
    warn "could not parse ${pkg} — check it is valid JSON"
    return 3
  fi
  printf '%s' "$v"
}

main() {
  local manifest="${TESSL_LATEST_MANIFEST:-tessl.json}"
  local state_dir="${TESSL_STATE_DIR:-.tessl}"

  # No tessl.json => not a tessl consumer; nothing to report. Silent no-op.
  [[ -f "$manifest" ]] || return 0

  # jq builds the JSON and reads the resolved state. Its absence is an expected
  # environment condition — surface the gap as a marker status without jq (a
  # literal, no interpolation, so no escaping is needed).
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found — cannot read tessl.json or the resolved state"
    printf '%s\n' '{"additionalContext":"Session-start status — versions: unavailable — jq is not installed, cannot read tessl.json or the resolved state. Install jq (and the Tessl CLI) so the Runtime-Managed Manifest Carve-Out enforcement (gAmUssA/* deps must be \"latest\") can run."}'
    return 0
  fi

  # Collect gAmUssA/* deps as "<name>\t<specifier>" lines, sorted by name for a
  # deterministic status (gAmUssA/coding-policy is pinned first below). A parse
  # failure is surfaced, not swallowed as "no deps".
  local deps_raw
  if ! deps_raw="$(jq -r '
      (.dependencies // {} | to_entries | sort_by(.key)[])
      | select(.key | startswith("gAmUssA/"))
      | "\(.key)\t\(.value.version // "")"' "$manifest" 2>/dev/null)"; then
    warn "could not parse ${manifest} — check it is valid JSON; skipping the versions status"
    return 0
  fi

  local -a names=() specs=()
  local name spec
  while IFS=$'\t' read -r name spec; do
    [[ -n "$name" ]] || continue
    names+=("$name"); specs+=("$spec")
  done <<< "$deps_raw"

  # No gAmUssA/* deps => nothing first-party to report. Silent no-op.
  (( ${#names[@]} > 0 )) || return 0

  # Installed versions BEFORE the update. A broken state file (rc 3) reads the
  # same as an absent one here — it only changes the label of the CURRENT state.
  local -a before=() after=() after_broken=()
  local i rc
  for (( i = 0; i < ${#names[@]}; i++ )); do
    rc=0
    before[i]="$(installed_version "$state_dir" "${names[i]}")" || rc=$?
  done

  # Run `tessl update --yes` best-effort. A missing CLI or a non-zero exit never
  # aborts the hook — it degrades the status to "update failed".
  local update_failed=0 update_reason="" out=""
  if ! command -v tessl >/dev/null 2>&1; then
    update_failed=1
    update_reason="tessl not installed — install the Tessl CLI to enable auto-update"
  elif ! out="$(tessl update --yes </dev/null 2>&1)"; then
    update_failed=1
    update_reason="${out%%$'\n'*}"
    update_reason="${update_reason:0:200}"
    [[ -n "$update_reason" ]] || update_reason="tessl update exited non-zero"
  fi

  # Installed versions AFTER the update. Here rc 3 is retained: a state file that
  # exists but cannot be read leaves the version genuinely unknown, which the
  # status must not report as "install pending".
  for (( i = 0; i < ${#names[@]}; i++ )); do
    rc=0
    after[i]="$(installed_version "$state_dir" "${names[i]}")" || rc=$?
    if (( rc == 3 )); then after_broken[i]=1; else after_broken[i]=0; fi
  done

  # Report order: gAmUssA/coding-policy first, then the rest in manifest order.
  local -a order=()
  for (( i = 0; i < ${#names[@]}; i++ )); do
    if [[ "${names[i]}" == "gAmUssA/coding-policy" ]]; then order+=("$i"); fi
  done
  for (( i = 0; i < ${#names[@]}; i++ )); do
    if [[ "${names[i]}" != "gAmUssA/coding-policy" ]]; then order+=("$i"); fi
  done

  # One segment per dep; collect any pins for the trailing NOTE.
  local -a segments=() pinned=()
  local idx b a seg
  for idx in "${order[@]}"; do
    b="${before[idx]}"; a="${after[idx]}"
    if (( after_broken[idx] )); then
      seg="${names[idx]} (version unknown — .tessl/ state file unreadable or invalid; check permissions and JSON validity, then re-run \`tessl install\`)"
    elif [[ -z "$a" ]]; then
      seg="${names[idx]} (install pending)"
    elif [[ "$b" == "$a" ]]; then
      # A failed update never verified freshness — do not claim "latest".
      if (( update_failed )); then
        seg="${names[idx]} ${a} (installed)"
      else
        seg="${names[idx]} ${a} (latest)"
      fi
    elif [[ -z "$b" ]]; then
      seg="${names[idx]} unknown → ${a} (updated)"
    else
      seg="${names[idx]} ${b} → ${a} (updated)"
    fi
    segments+=("$seg")
    if [[ "${specs[idx]}" != "latest" ]]; then pinned+=("${names[idx]}@${specs[idx]}"); fi
  done

  # Assemble the status line, marker first.
  local status="Session-start status — versions: "
  local first=1
  for seg in "${segments[@]}"; do
    if (( first )); then status+="$seg"; first=0; else status+=", ${seg}"; fi
  done

  if (( update_failed )); then status+="; update failed: ${update_reason}"; fi

  if (( ${#pinned[@]} > 0 )); then
    local pins="" firstp=1 p
    for p in "${pinned[@]}"; do
      if (( firstp )); then pins+="$p"; firstp=0; else pins+=", ${p}"; fi
    done
    status+=$'\n'"NOTE: tessl.json pins gAmUssA/* dependencies that should float to \`latest\` (Runtime-Managed Manifest Carve-Out, rules/dependency-management.md): ${pins}. Set them to \`\"version\": \"latest\"\` — the resolved state lives in the gitignored \`.tessl/\`, so a pin only re-introduces the auto-update churn."
  fi

  jq -n --arg c "$status" '{additionalContext: $c}' \
    || warn "could not emit the versions status as JSON — skipping"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
