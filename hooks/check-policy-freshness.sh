#!/usr/bin/env bash
# Warn at session start when installed Tessl plugins are behind the registry.
#
# A SessionStart hook: runs `tessl outdated --json`, and if anything is behind,
# injects a short notice via additionalContext so the agent/user sees it and can
# `tessl update`. Consumer repos silently drift to stale policy versions with
# nothing flagging it (observed: consumers running an old coding-policy while a
# newer one is published); this surfaces that the moment a session opens.
#
# Design choices, learned from the reverted resurface hook:
#   - It DOES something (checks registry state), it does not re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Throttled: a registry check runs at most once per FRESHNESS_THROTTLE_HOURS
#     (default 24h) across sessions, so most session starts skip the network call.
#   - Informative only. Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: when the registry check runs, one JSON object
#           {"additionalContext": "<status>"} whose text begins with the
#           "Session-start status — " marker (rules/hook-action-reporting.md) —
#           "policy: fresh" (success) or the available updates (a header plus one
#           line per plugin). Throttled and tool-missing sessions stay silent.
#   exit  : always 0. Every best-effort failure emits an actionable stderr warning
#           and continues/no-ops (rules/error-handling.md Shell Error Handling).
#   state : $FRESHNESS_STATE_DIR/last-check (default ${TMPDIR:-/tmp}/coding-policy-freshness),
#           a throttle stamp. Schema documented in hooks/state-schema.md:
#           one line "<schema_version> <checked_at>".
#   env   : FRESHNESS_THROTTLE_HOURS (default 24), FRESHNESS_STATE_DIR (tests),
#           FRESHNESS_NOW (test-only injected clock; defaults to `date +%s`).
set -euo pipefail

warn() { printf 'check-policy-freshness: %s\n' "$1" >&2; }

main() {
  local THROTTLE_HOURS="${FRESHNESS_THROTTLE_HOURS:-24}"
  local STATE_DIR="${FRESHNESS_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-freshness}"
  local now stamp sv ts preserve_future out notice

  # jq and tessl are both required to produce a signal; a missing optional tool is
  # an expected environment condition, not a failure — warn and no-op.
  command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to enable the freshness check"; return 0; }
  command -v tessl >/dev/null 2>&1 || { warn "tessl not found — install the Tessl CLI to enable the freshness check"; return 0; }

  if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
    warn "FRESHNESS_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 24"
    THROTTLE_HOURS=24
  fi

  # Resolve the clock. A test may inject FRESHNESS_NOW; otherwise read the system
  # clock and handle its failure. Validate the result as an integer before any
  # arithmetic so a malformed value can't abort the hook under set -e.
  if [[ -n "${FRESHNESS_NOW:-}" ]]; then
    now="$FRESHNESS_NOW"
  elif ! now="$(date +%s)"; then
    warn "cannot read the system clock — skipping freshness check"
    return 0
  fi
  if ! [[ "$now" =~ ^[0-9]+$ ]]; then
    warn "clock value '${now}' is not an integer — unset FRESHNESS_NOW; skipping freshness check"
    return 0
  fi

  stamp="${STATE_DIR}/last-check"

  # Throttle stamp schema (see hooks/state-schema.md): one line "<schema_version>
  # <checked_at-epoch>". Per hooks/state-schema.md Migration:
  #   - schema_version 1 within the window => throttle (return silently).
  #   - a future version (sv > 1) => this hook is lagging: no usable prior state
  #     (re-check), and DO NOT downgrade the record — preserve it.
  #   - anything else (old bare-integer, corrupt, absent) => no prior state
  #     (re-check), safe to rewrite as version 1.
  preserve_future=0
  if [[ -r "$stamp" ]]; then
    sv=""; ts=""
    read -r sv ts < "$stamp" || { sv=""; ts=""; }
    if [[ "$sv" =~ ^[0-9]+$ ]] && (( sv > 1 )); then
      preserve_future=1
    elif [[ "$sv" == "1" && "$ts" =~ ^[0-9]+$ ]] && (( now - ts < THROTTLE_HOURS * 3600 )); then
      return 0
    fi
  fi

  # Record the check up front so a slow/failed registry call still throttles the
  # next session rather than hammering the registry on every start. Skipped when
  # a future-version record must be preserved rather than downgraded.
  if (( preserve_future == 0 )); then
    if mkdir -p "$STATE_DIR"; then
      printf '1 %s\n' "$now" > "$stamp" ||
        warn "cannot write throttle stamp ${stamp} — check permissions on ${STATE_DIR}; will re-check next session"
    else
      warn "cannot create state dir ${STATE_DIR} — check permissions or set FRESHNESS_STATE_DIR; freshness check will not throttle"
    fi
  fi

  # Silence tessl's own diagnostic but explicitly handle the failure and warn —
  # an offline/registry error is a no-op, not a broken session.
  if ! out="$(tessl outdated --json 2>/dev/null)"; then
    warn "tessl outdated failed — check connectivity and retry \`tessl outdated\`; skipping freshness check"
    return 0
  fi

  # Build a notice line per outdated plugin. Empty list => silent. A parse failure
  # is surfaced, not swallowed as "nothing outdated".
  if ! notice="$(printf '%s' "$out" | jq -r '
    (.outdated // [])
    | map("- \(.current.tile.workspaceName)/\(.current.tile.tileName) \(.current.tile.version) -> \(.update.version)")
    | if length == 0 then empty else
        "Session-start status — policy: plugin updates available (run `tessl update`):\n" + (. | join("\n"))
      end
  ' 2>/dev/null)"; then
    warn "could not parse \`tessl outdated --json\` output — run it manually to inspect; skipping freshness check"
    return 0
  fi

  # Success path: nothing outdated. Emit a positive marker status so the agent
  # surfaces it (rules/hook-action-reporting.md). The problem path's notice
  # already carries the marker (set in the jq program above).
  if [[ -z "$notice" ]]; then
    jq -n --arg c "Session-start status — policy: fresh" '{additionalContext: $c}' ||
      warn "could not emit the freshness status as JSON — skipping freshness check"
    return 0
  fi

  jq -n --arg c "$notice" '{additionalContext: $c}' ||
    warn "could not emit the update notice as JSON — skipping freshness check"
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the script can also be sourced to unit-test its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
