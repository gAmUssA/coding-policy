#!/usr/bin/env bash
# Name the lead's workspace and each worker's workspace and pane.
#
# A sidebar of `w1 w2 w3 w4` tells the operator nothing at 3am. Naming is one
# right answer per input — the lead's workspace becomes <lead-label>, a
# worker's workspace becomes its agent name, and its pane becomes its kind —
# so it lives here rather than in the lead's hands
# (`rules/script-delegation.md`). Run it once per team, not once per round.
#
# Contract:
#   argv  : <lead-label> [<agent>=<workspace-id>]...
#           With no pairs, every named worker in the roster is labelled and its
#           workspace comes from `herdr agent list`.
#   stdout: one JSON object —
#           {"lead":{"workspace_id":"<id>","label":"<l>","state":"renamed|unchanged|failed"},
#            "agents":[{"name","kind","workspace_id","pane_id",
#                       "workspace":"renamed|unchanged|failed",
#                       "pane":"renamed|unchanged|failed"}, ...]}
#   stderr: diagnostics only.
#   exit  : 0 every rename landed or was already in place,
#           1 precondition unmet (usage, not inside Herdr, `herdr`/`jq` absent,
#             no caller workspace id),
#           2 the roster or workspace list could not be read,
#           3 at least one rename failed — the JSON says which. Labels are
#             cosmetic, so a failure is reported rather than fatal to a round.
#   env   : HERDR_BIN overrides the herdr binary; the tests point it at a fake.
#
# Idempotent: a name already in place is reported `unchanged` and nothing is
# sent (`rules/file-hygiene.md` Idempotency).
set -euo pipefail

HERDR_BIN="${HERDR_BIN:-herdr}"

ERRFILE=""
WORKSPACE_LIST=""

warn() { printf 'label-workspaces: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

# Echo the current name of a workspace, empty when it has none.
workspace_name() { # <workspace-id>
  printf '%s' "$WORKSPACE_LIST" | jq -r --arg id "$1" '
    .[] | select(.workspace_id == $id) | .name'
}

# Rename one thing. Echoes renamed|unchanged|failed; never aborts the run.
rename_to() { # <kind: workspace|pane> <id> <label> <current>
  local kind="$1" id="$2" label="$3" current="$4" rc=0
  if [[ "$current" == "$label" ]]; then
    printf 'unchanged'
    return 0
  fi
  "$HERDR_BIN" "$kind" rename "$id" "$label" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} ${kind} rename ${id} ${label}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — the label is cosmetic, so the round continues"
    printf 'failed'
    return 1
  fi
  printf 'renamed'
  return 0
}

main() {
  if (( $# < 1 )); then
    warn "usage: label-workspaces.sh <lead-label> [<agent>=<workspace-id>]..."
    return 1
  fi
  local lead_label="$1"; shift

  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    warn "not running inside Herdr (HERDR_ENV='${HERDR_ENV:-}') — run this from the lead's own pane"
    return 1
  fi
  local dep
  for dep in "$HERDR_BIN" jq; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      warn "'${dep}' not found on PATH — install it to label the layout"
      return 1
    fi
  done
  local lead_ws="${HERDR_WORKSPACE_ID:-}"
  if [[ -z "$lead_ws" ]]; then
    warn "HERDR_WORKSPACE_ID is empty — Herdr injects it into every managed pane; re-run from the lead's pane"
    return 1
  fi

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  local workspace_raw
  rc=0
  workspace_raw="$("$HERDR_BIN" workspace list 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} workspace list\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — restore the Herdr session or connection, then retry"
    return 2
  fi
  rc=0
  WORKSPACE_LIST="$(printf '%s' "$workspace_raw" | jq -ce '
    if (.result.workspaces | type) == "array"
       and all(.result.workspaces[];
         type == "object"
         and (.workspace_id | type) == "string"
         and ((.name // "") | type) == "string") then
      [.result.workspaces[] | {workspace_id, name: (.name // "")}]
    else
      error("herdr workspace list payload has no .result.workspaces array")
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the herdr workspace list payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — update or reinstall the Herdr CLI, then retry"
    return 2
  fi

  # Explicit pairs override the roster; with none, every named worker is taken
  # from `herdr agent list`.
  local overrides="{}" pair name value
  for pair in "$@"; do
    if [[ "$pair" != *=* ]]; then
      warn "'${pair}' is not <agent>=<workspace-id>"
      return 1
    fi
    name="${pair%%=*}"
    value="${pair#*=}"
    overrides="$(printf '%s' "$overrides" | jq -c --arg k "$name" --arg v "$value" '. + {($k): $v}')"
  done

  local list rc=0
  list="$("$HERDR_BIN" agent list 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${HERDR_BIN} agent list\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi
  rc=0
  local roster
  roster="$(printf '%s' "$list" | jq -c --arg caller "${HERDR_PANE_ID:-}" '
    if (.result.agents | type) != "array" then
      error("herdr agent list payload has no .result.agents array")
    else
      [ .result.agents[]
        | select((.name? | type) == "string" and (.name | length) > 0)
        | select(.pane_id != $caller)
        | {name: .name, kind: (.agent // "unknown"),
           workspace_id: (.workspace_id // ""), pane_id: (.pane_id // "")} ]
    end' 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "could not read the herdr agent list payload (jq exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  local failures=0 lead_state lead_current=""
  lead_current="$(workspace_name "$lead_ws")" || lead_current=""
  lead_state="$(rename_to workspace "$lead_ws" "$lead_label" "$lead_current")" || failures=$((failures + 1))

  local results="[]" idx count agent aname akind aws apane ws_state pane_state pane_label
  count="$(printf '%s' "$roster" | jq 'length')"
  for (( idx = 0; idx < count; idx++ )); do
    agent="$(printf '%s' "$roster" | jq -c --argjson i "$idx" '.[$i]')"
    aname="$(printf '%s' "$agent" | jq -r '.name')"
    akind="$(printf '%s' "$agent" | jq -r '.kind')"
    apane="$(printf '%s' "$agent" | jq -r '.pane_id')"
    aws="$(printf '%s' "$overrides" | jq -r --arg k "$aname" '.[$k] // ""')"
    if [[ -z "$aws" ]]; then
      aws="$(printf '%s' "$agent" | jq -r '.workspace_id')"
    fi

    ws_state="skipped"
    if [[ -n "$aws" ]]; then
      local current=""
      current="$(workspace_name "$aws")" || current=""
      ws_state="$(rename_to workspace "$aws" "$aname" "$current")" || failures=$((failures + 1))
    else
      warn "${aname} has no workspace id — its workspace keeps its current name"
    fi

    pane_state="skipped"
    # The kind alone: the workspace row above it already says which agent this
    # is, and the first dispatch relabels the pane with the role it took.
    pane_label="${akind}"
    if [[ -n "$apane" ]]; then
      # A pane carries no readable name in `agent list`, so a rename is always
      # sent; herdr treats setting the same title as a no-op.
      pane_state="$(rename_to pane "$apane" "$pane_label" "")" || failures=$((failures + 1))
    fi

    results="$(printf '%s' "$results" | jq -c \
      --arg n "$aname" --arg k "$akind" --arg w "$aws" --arg p "$apane" \
      --arg ws "$ws_state" --arg pn "$pane_state" \
      '. + [{name: $n, kind: $k, workspace_id: $w, pane_id: $p, workspace: $ws, pane: $pn}]')"
  done

  jq -n --arg ws "$lead_ws" --arg label "$lead_label" --arg state "$lead_state" \
    --argjson agents "$results" \
    '{lead: {workspace_id: $ws, label: $label, state: $state}, agents: $agents}'

  if (( failures > 0 )); then
    return 3
  fi
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
