#!/usr/bin/env bash
# Herdr-compatible adapter over Rex. Point HERDR_BIN and TEAMLEAD_HERDR_BIN at
# this file to run the team-lead skills inside a Rex session
# (docs/adr/0015-rex-backend-through-herdr-compatible-adapter.md).
#
# Contract:
#   argv  : the Herdr subset listed in skills/herdr-teamlead/teamlead/rex_herdr.py,
#           forwarded verbatim to `python3 -m teamlead.rex_herdr`.
#   stdout: `{"result": ...}` JSON, or raw pane text for `agent read`/`pane read`.
#   stderr: `{"error":{"code","message"}}` on a Rex-side failure; a diagnostic
#           from this launcher otherwise.
#   exit  : 0 success; 1 Rex-side failure or a missing/too-old interpreter;
#           2 usage error.
#   env   : REX_SESSION (required), TEAMLEAD_REX_BIN (default `rex`), PY_BIN
#           (default python3). PYTHONPATH is prefixed, never replaced.
set -euo pipefail

PY_BIN="${PY_BIN:-python3}"

main() {
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    echo "rex-herdr: '${PY_BIN}' not found on PATH — install Python 3.11+ (\`brew install python@3.11\`) or point PY_BIN at the interpreter" >&2
    return 1
  fi
  if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "rex-herdr: '${PY_BIN}' is not Python 3.11+ — install Python 3.11+ (\`brew install python@3.11\`) or point PY_BIN at a compatible interpreter" >&2
    return 1
  fi
  if [[ ! -f "${skill_dir}/teamlead/rex_herdr.py" ]]; then
    echo "rex-herdr: teamlead/rex_herdr.py is missing from ${skill_dir} — reinstall the plugin with \`tessl install gamussa/coding-policy\`" >&2
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  exec "$PY_BIN" -m teamlead.rex_herdr "$@"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
