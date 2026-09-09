#!/usr/bin/env bash
# Outcome-based tests for stop-handoff-hygiene.sh.
#
# The hook shells out to real git, so tests build real local repos (a bare
# "origin" plus working clones) and drive git offline — no network, fully
# deterministic. Diagnostics scenarios stub shellcheck/pyright on PATH so the
# changed-set gate is exercised without depending on the real engines.
#
# Each scenario builds its OWN origin so scenarios share no mutable state and run
# in any order (rules/testing-standards.md Independence). The harness drops
# `set -e` to aggregate results, so every fixture-setup command is checked
# explicitly and aborts with a fatal diagnostic (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Covers:
#   1. Loop guard      -> stop_hook_active:true allows even with leftovers.
#   2. Clean repo      -> allow (no stdout), exit 0.
#   3. Gone branch     -> block; reason names the branch + `git branch -d`.
#   4. Orphaned wt     -> block; reason names the worktree + `git worktree remove`,
#                         and does NOT also list it as a leftover branch.
#   5. Dirty tree only -> allow (report-only, not a block).
#   6. Diag finding    -> block; changed uncommitted .sh with a failing engine.
#   7. Diag clean      -> changed uncommitted .sh, engines clean -> no diag block.
#   8. No jq           -> fail-open allow, exit 0.
#   9. Not a repo      -> allow, exit 0.
#  10. Engine absent   -> block with install guidance (changed .sh, no shellcheck).
#
# Run: bash hooks/tests/test_stop_handoff_hygiene.sh
set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/stop-handoff-hygiene.sh"

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
g() { git "$@"; }

# mk_origin <prefix>: sets globals BARE, SEED (fresh, independent origin with c1).
mk_origin() {
  local prefix="$1"
  BARE="$TMP/${prefix}.git"; SEED="$TMP/${prefix}-seed"
  g init -q --bare -b main "$BARE"                || die "mk_origin: init bare failed"
  g clone -q "$BARE" "$SEED" 2>/dev/null          || die "mk_origin: clone seed failed"
  g -C "$SEED" symbolic-ref HEAD refs/heads/main  || die "mk_origin: symbolic-ref failed"
  printf 'c1\n' > "$SEED/f"                        || die "mk_origin: write f failed"
  g -C "$SEED" add f                              || die "mk_origin: add failed"
  g -C "$SEED" commit -q -m c1                    || die "mk_origin: commit failed"
  g -C "$SEED" push -q origin main                || die "mk_origin: push failed"
}

clone_from() { g clone -q "$1" "$2" || die "clone_from: $1 -> $2 failed"; }

# Make a branch whose upstream is gone: push it, delete it on origin, prune.
# Run from the given worktree dir. <dir> <branch>
make_gone_branch() {
  local dir="$1" br="$2"
  g -C "$dir" push -q -u origin "$br"     || die "make_gone_branch: push $br failed"
  g -C "$dir" push -q origin --delete "$br" || die "make_gone_branch: delete $br failed"
  g -C "$dir" fetch -q --prune            || die "make_gone_branch: prune failed"
}

mk_stub_bin() { # <dir> <sc_rc> <py_rc>
  mkdir -p "$1" || die "mk_stub_bin: mkdir $1 failed"
  printf '#!/usr/bin/env bash\nexit %s\n' "$2" > "$1/shellcheck" || die "stub shellcheck failed"
  printf '#!/usr/bin/env bash\nexit %s\n' "$3" > "$1/pyright"    || die "stub pyright failed"
  chmod +x "$1/shellcheck" "$1/pyright" || die "chmod stubs failed"
}

mk_pyright_probe() { # <path>
  cat > "$1" <<'PROBE' || die "could not write Pyright environment probe"
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$0" "$@" > "${PYRIGHT_PROBE_LOG:?}"
if [[ -n "${PYRIGHT_REQUIRED_INTERPRETER:-}" ]]; then
  if [[ "${1:-}" != --pythonpath || "${2:-}" != "$PYRIGHT_REQUIRED_INTERPRETER" ]]; then
    echo 'reportMissingImports: interpreter does not expose the project dependency'
    exit 1
  fi
  shift 2
elif [[ "${1:-}" == --pythonpath ]]; then
  echo 'unexpected override of default/config interpreter'
  exit 1
fi
if [[ $# != 1 || "$1" != 'changed file.py' || ! -f "$1" ]]; then
  echo 'changed Python file was not resolved from the repository root'
  exit 1
fi
if [[ -n "${PYRIGHT_PROBE_FINDING:-}" ]]; then
  printf '%s\n' "$PYRIGHT_PROBE_FINDING"
  exit 1
fi
PROBE
  chmod +x "$1" || die "could not enable Pyright environment probe"
}

mk_python_probe() { # <path>
  mkdir -p "${1%/*}" || die "could not create environment directory"
  printf '#!/bin/sh\nexit 0\n' > "$1" || die "could not write interpreter fixture"
  chmod +x "$1" || die "could not enable interpreter fixture"
}

# run_hook <repo> <stop-json> [path] -> OUT, RC
run_hook() {
  local repo="$1" json="$2" pathspec="${3:-$PATH}"
  OUT="$(cd "$repo" && printf '%s' "$json" | PATH="$pathspec" bash "$HOOK" 2>/dev/null)"
  RC=$?
}

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
reason_has() { printf '%s' "$OUT" | jq -e --arg re "$1" '.reason | test($re)' >/dev/null 2>&1; }

main() {
  command -v jq  >/dev/null 2>&1 || die "jq required for these tests"
  command -v git >/dev/null 2>&1 || die "git required for these tests"
  [[ -f "$HOOK" && -r "$HOOK" ]] || die "hook not found/readable at $HOOK"

  TMP="$(mktemp -d -t stop-hygiene-test.XXXXXX)" || die "mktemp failed"
  TMP="$(cd "$TMP" && pwd -P)" || die "could not resolve fixture root"
  trap cleanup EXIT
  export HOME="$TMP/home"; mkdir -p "$HOME" || die "could not create isolated HOME"
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

  FAIL=0; PASS=0

  # 1. loop guard: active:true allows even with a gone branch present.
  mk_origin o1; clone_from "$BARE" "$TMP/r1"
  g -C "$TMP/r1" switch -qc feat/foo || die "r1 branch failed"
  make_gone_branch "$TMP/r1" feat/foo
  g -C "$TMP/r1" switch -q main || die "r1 switch main failed"
  run_hook "$TMP/r1" '{"stop_hook_active":true}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "loop guard: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 2. clean repo -> allow.
  mk_origin o2; clone_from "$BARE" "$TMP/r2"
  run_hook "$TMP/r2" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "clean: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 3. gone branch -> block naming the branch + delete command.
  mk_origin o3; clone_from "$BARE" "$TMP/r3"
  g -C "$TMP/r3" switch -qc feat/bar || die "r3 branch failed"
  make_gone_branch "$TMP/r3" feat/bar
  g -C "$TMP/r3" switch -q main || die "r3 switch main failed"
  run_hook "$TMP/r3" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 ]] && reason_has "feat/bar" && reason_has "git branch -d" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]]; then
    pass; else fail "gone branch: expected block naming feat/bar, got RC=$RC OUT=$OUT"; fi

  # 4. orphaned worktree -> block naming the worktree, not as a leftover branch.
  mk_origin o4; clone_from "$BARE" "$TMP/r4"
  g -C "$TMP/r4" worktree add -q "$TMP/r4-wt" -b feat/wt || die "r4 worktree add failed"
  make_gone_branch "$TMP/r4-wt" feat/wt
  g -C "$TMP/r4" fetch -q --prune || die "r4 prune failed"
  run_hook "$TMP/r4" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 ]] && reason_has "Orphaned worktrees" && reason_has "worktree remove" \
     && ! reason_has "Leftover local branches"; then
    pass; else fail "orphaned worktree: expected worktree block only, got RC=$RC OUT=$OUT"; fi

  # 4b. The same orphaned worktree, seen from INSIDE a linked worktree with
  # HERDR_ENV set: that is a worker session, and removing a worktree is the
  # lead's job (rules/agent-team-operation.md). Blocking here would force the
  # worker to either disobey the rule or fail to hand off.
  OUT="$(cd "$TMP/r4-wt" && printf '%s' '{"stop_hook_active":false}' \
    | HERDR_ENV=1 bash "$HOOK" 2>/dev/null)"; RC=$?
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "worker session: expected silence, got RC=$RC OUT=$OUT"; fi

  # 4b-ii. A worker's OWN changed files still gate: only branch/worktree
  # cleanup is suppressed, never the diagnostics its handoff depends on.
  # shellcheck disable=SC2016  # The literal `$x` IS the fixture: the hook's
  # own shellcheck run has to find something to report.
  printf 'if [ $x = 1 ]; then :; fi\n' > "$TMP/r4-wt/bad.sh" || die "r4-wt bad.sh failed"
  OUT="$(cd "$TMP/r4-wt" && printf '%s' '{"stop_hook_active":false}' \
    | HERDR_ENV=1 bash "$HOOK" 2>/dev/null)"; RC=$?
  if [[ $RC -eq 0 ]] && reason_has "shellcheck findings" && ! reason_has "Orphaned worktrees"; then
    pass; else fail "worker diagnostics: expected a diagnostics block without the worktree finding, got RC=$RC OUT=$OUT"; fi
  rm -f "$TMP/r4-wt/bad.sh" || die "r4-wt cleanup failed"

  # 4c. The lead's own session (main checkout) still blocks with HERDR_ENV set:
  # the suppression keys on being in a linked worktree, not on Herdr alone.
  OUT="$(cd "$TMP/r4" && printf '%s' '{"stop_hook_active":false}' \
    | HERDR_ENV=1 bash "$HOOK" 2>/dev/null)"; RC=$?
  if [[ $RC -eq 0 ]] && reason_has "Orphaned worktrees"; then
    pass; else fail "lead session: expected the worktree block, got RC=$RC OUT=$OUT"; fi

  # 5. dirty tree only -> allow (report-only).
  mk_origin o5; clone_from "$BARE" "$TMP/r5"
  printf 'dirty\n' >> "$TMP/r5/f" || die "r5 dirty failed"
  run_hook "$TMP/r5" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "dirty only: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 6. changed-set diagnostics finding -> block. Uncommitted .sh + failing engine.
  mk_origin o6; clone_from "$BARE" "$TMP/r6"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r6/new.sh" || die "r6 new.sh failed"
  mk_stub_bin "$TMP/r6-bin" 1 0     # stub engine exits 1 (a finding)
  run_hook "$TMP/r6" '{"stop_hook_active":false}' "$TMP/r6-bin:$PATH"
  if [[ $RC -eq 0 ]] && reason_has "shellcheck findings" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]]; then
    pass; else fail "diag finding: expected block, got RC=$RC OUT=$OUT"; fi

  # 7. changed-set diagnostics clean -> no diagnostics block (dirty tree is only
  #    report-only, so allow). Proves the changed set was linted and passed.
  mk_origin o7; clone_from "$BARE" "$TMP/r7"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r7/new.sh" || die "r7 new.sh failed"
  mk_stub_bin "$TMP/r7-bin" 0 0     # engines clean
  run_hook "$TMP/r7" '{"stop_hook_active":false}' "$TMP/r7-bin:$PATH"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "diag clean: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 8. no jq -> fail-open allow. Minimal PATH with git/bash but no jq.
  mk_origin o8; clone_from "$BARE" "$TMP/r8"
  local minbin="$TMP/minbin"; mkdir -p "$minbin" || die "minbin mkdir failed"
  local t
  for t in bash git cat env mkdir; do
    local p; p="$(command -v "$t")" || die "no $t on PATH"
    ln -s "$p" "$minbin/$t" || die "symlink $t failed"
  done
  run_hook "$TMP/r8" '{"stop_hook_active":false}' "$minbin"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no jq: expected fail-open allow, got RC=$RC OUT=$OUT"; fi

  # 9. not a repo -> allow.
  mkdir -p "$TMP/notrepo" || die "notrepo mkdir failed"
  run_hook "$TMP/notrepo" '{"stop_hook_active":false}'
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "not a repo: expected allow/silence, got RC=$RC OUT=$OUT"; fi

  # 10. engine unavailable for a present type -> block with install guidance
  #     (rules/language-diagnostics.md: the gate can't clear findings without the
  #     engine). PATH has git/jq/cat/bash but no shellcheck; a changed .sh forces
  #     the check.
  mk_origin o10; clone_from "$BARE" "$TMP/r10"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMP/r10/new.sh" || die "r10 new.sh failed"
  local engbin="$TMP/engbin"; mkdir -p "$engbin" || die "engbin mkdir failed"
  local u p2
  for u in bash git jq cat mktemp rm; do
    p2="$(command -v "$u")" || die "no $u on PATH"
    ln -s "$p2" "$engbin/$u" || die "symlink $u failed"
  done
  run_hook "$TMP/r10" '{"stop_hook_active":false}' "$engbin"
  if [[ $RC -eq 0 ]] && reason_has "shellcheck is not installed" \
     && [[ "$(printf '%s' "$OUT" | jq -r '.decision')" == "block" ]]; then
    pass; else fail "engine unavailable: expected block with install guidance, got RC=$RC OUT=$OUT"; fi

  # 11. Environment selection drives the public hook outcome. Each fixture
  # has a dependency that the probe resolves only with its intended Python.
  local shape repo interpreter engine active_env probe_log
  for shape in active dotvenv venv windows default; do
    mk_origin "py-$shape"
    repo="$TMP/python $shape"; clone_from "$BARE" "$repo"
    printf 'import project_dependency\n' > "$repo/changed file.py" || die "could not write Python fixture"
    mkdir "$repo/nested" "$TMP/bin-$shape" || die "could not create probe directories"
    mk_pyright_probe "$TMP/bin-$shape/pyright"
    active_env=""; interpreter=""; engine="$TMP/bin-$shape/pyright"
    case "$shape" in
      active)
        active_env="$TMP/active environment"
        interpreter="$active_env/bin/python"
        mk_python_probe "$repo/.venv/bin/python"
        mk_python_probe "$repo/venv/bin/python"
        ;;
      dotvenv)
        active_env="$TMP/missing environment"
        interpreter="$repo/.venv/bin/python"
        mk_python_probe "$repo/venv/bin/python"
        ;;
      venv) interpreter="$repo/venv/bin/python" ;;
      windows) interpreter="$repo/.venv/Scripts/python.exe" ;;
      default) ;;
    esac
    if [[ -n "$interpreter" ]]; then mk_python_probe "$interpreter"; fi
    if [[ "$shape" == active || "$shape" == dotvenv || "$shape" == windows ]]; then
      engine="${interpreter%/*}/pyright"
      if [[ "$shape" == windows ]]; then engine+=.exe; fi
      mk_pyright_probe "$engine"
    fi
    probe_log="$TMP/probe-$shape.log"
    VIRTUAL_ENV="$active_env" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" \
      run_hook "$repo/nested" '{"stop_hook_active":false}' "$TMP/bin-$shape:$PATH"
    if [[ $RC -eq 0 && -z "$OUT" && -r "$probe_log" ]] && [[ "$(head -1 "$probe_log")" == "$engine" ]]; then
      pass; else fail "$shape environment must resolve the dependency through the intended engine: RC=$RC OUT=$OUT"; fi

    if [[ "$shape" == dotvenv ]]; then
      # Local engine is sufficient even when PATH contains no Pyright.
      VIRTUAL_ENV="" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" \
        run_hook "$repo" '{"stop_hook_active":false}' "$engbin"
      if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "local Pyright must work without a global installation: OUT=$OUT"; fi
      local finding
      for finding in reportArgumentType reportMissingImports; do
        VIRTUAL_ENV="" PYRIGHT_REQUIRED_INTERPRETER="$interpreter" PYRIGHT_PROBE_LOG="$probe_log" PYRIGHT_PROBE_FINDING="$finding" \
          run_hook "$repo" '{"stop_hook_active":false}' "$TMP/bin-$shape:$PATH"
        if [[ $RC -eq 0 ]] && reason_has "$finding" && reason_has 'pyright findings'; then
          pass; else fail "environment selection must preserve blocking $finding diagnostics: OUT=$OUT"; fi
      done
    fi
  done

  # No engine in either environment or PATH retains the explicit install gate.
  mk_origin py-missing; repo="$TMP/python missing"; clone_from "$BARE" "$repo"
  printf 'value = 1\n' > "$repo/changed file.py" || die "could not write missing-engine fixture"
  mk_python_probe "$repo/.venv/bin/python"
  VIRTUAL_ENV="" run_hook "$repo" '{"stop_hook_active":false}' "$engbin"
  if [[ $RC -eq 0 ]] && reason_has 'pyright is not installed'; then
    pass; else fail "missing Pyright must retain install guidance: OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
