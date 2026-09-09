#!/usr/bin/env bash
# Native Claude/Codex Stop gate; only an exact persisted Herdr lead binding
# activates it. stdin/stdout/exit contract: teamlead/supervision_hook.py.
set -euo pipefail

main() {
  local plugin_root
  plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if ! command -v python3 >/dev/null 2>&1; then
    echo 'herdr-supervision-stop: python3 is missing — restore Python to enable the native supervision gate' >&2
    return 0
  fi
  local rc=0
  PYTHONPATH="${plugin_root}/skills/herdr-teamlead${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 -m teamlead.supervision_hook || rc=$?
  if (( rc != 0 )); then
    echo "herdr-supervision-stop: Python hook failed (exit ${rc}) before completing its contract — restore the hook installation; native gating requires its structured JSON result" >&2
  fi
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
