#!/usr/bin/env bash
# Launcher for the team-lead Python utility (`measure`, `plan`, `apply`,
# `state`). It resolves the skill directory, puts it on PYTHONPATH so the
# packaged `teamlead` module imports without an install step, and execs the
# module with every argument forwarded unchanged.
#
# Contract:
#   argv  : forwarded verbatim to `python3 -m teamlead`.
#   stdout: whatever the module emits — one JSON object per subcommand.
#   stderr: diagnostics from this launcher and from the module.
#   exit  : the module's own exit status, or 1 when the interpreter is absent,
#           its version probe fails or is unreadable, it is older than 3.11,
#           or the packaged module is missing.
#   env   : PY_BIN overrides the interpreter (default python3); the tests and
#           a venv shim point it elsewhere. PYTHONPATH is prefixed, never
#           replaced. Every other variable the module reads is documented in
#           skills/herdr-teamlead/state-schema.md.
set -euo pipefail

PY_BIN="${PY_BIN:-python3}"

main() {
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if ! command -v "$PY_BIN" >/dev/null 2>&1; then
    echo "teamlead: '${PY_BIN}' not found on PATH — install Python 3.11+ (\`brew install python@3.11\`) or point PY_BIN at the interpreter" >&2
    return 1
  fi

  local py_version py_rc=0 py_major py_minor
  py_version="$("$PY_BIN" -c 'import sys; sys.stdout.write("%s.%s\n" % sys.version_info[:2])' 2>&1)" || py_rc=$?
  if (( py_rc != 0 )); then
    echo "teamlead: '${PY_BIN}' failed while checking its version (exit ${py_rc}): ${py_version} — repair that interpreter or point PY_BIN at Python 3.11+" >&2
    return 1
  fi
  if [[ ! "$py_version" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "teamlead: '${PY_BIN}' returned an unreadable version '${py_version}' — repair that interpreter or point PY_BIN at Python 3.11+" >&2
    return 1
  fi
  IFS=. read -r py_major py_minor <<<"$py_version"
  if (( py_major < 3 || (py_major == 3 && py_minor < 11) )); then
    echo "teamlead: '${PY_BIN}' is older than Python 3.11 — install Python 3.11+ (\`brew install python@3.11\`) or point PY_BIN at a compatible interpreter" >&2
    return 1
  fi

  if [[ ! -d "${skill_dir}/teamlead" ]]; then
    echo "teamlead: the teamlead package is missing from ${skill_dir} — reinstall the plugin with \`tessl install gamussa/coding-policy\`" >&2
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  exec "$PY_BIN" -m teamlead "$@"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
