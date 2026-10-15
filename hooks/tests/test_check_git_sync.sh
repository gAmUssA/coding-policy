#!/usr/bin/env bash
# Outcome-based tests for check-git-sync.sh.
#
# The hook shells out to real git, so tests build real local repos (a bare
# "origin" plus working clones) and drive git offline — no network, fully
# deterministic. The injected clock (SYNC_NOW) drives the throttle assertions.
#
# Each scenario builds its OWN bare origin + seed (mk_origin) so scenarios share
# no mutable state and run in any order (rules/testing-standards.md Independence).
# The harness drops `set -e` to aggregate results, so every fixture-setup command
# is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Behind        -> a clean lead on the default branch is fast-forwarded and
#                       the status says so; local main equals origin/main after.
#   1a. Behind+dirty / off-default -> reported "behind" with the reason, not moved.
#   1b. Worker       -> HERDR_ENV in a linked worktree: no fetch (origin/main
#                       ref unchanged), no throttle stamp, do-not-sync notice.
#   1c. Lead in Herdr -> the main checkout with HERDR_ENV set still fetches + ffs.
#   1d. Portable     -> SESSION_START_MODE=portable in a linked worktree: no
#                       fetch, "not verified"; in the main checkout: fetch, but
#                       report "behind" rather than fast-forward.
#   1e. Repo hooks   -> reference-transaction / post-index-change / post-merge
#                       hooks never run during the fetch, the status check
#                       (index refresh after a tracked file's mtime changes),
#                       or the fast-forward.
#   1e2. Ignored file -> origin starts tracking a path holding ignored local
#                       data: no fast-forward, a report, and the data intact.
#   1e3. fsmonitor   -> a repo-configured core.fsmonitor program never runs
#                       during the status check or the fast-forward.
#   1e4. Local filter -> a filter command in repo-local config refuses the
#                       fast-forward; the filter never runs and main stays put.
#   1e5. Included / worktree / global filter -> included and per-worktree
#                       config refuse like local config; a global filter
#                       fast-forwards.
#   1f. ssh bound    -> ConnectTimeout reaches the plain default ssh only; a
#                       custom ssh (GIT_SSH_COMMAND or core.sshCommand, simple
#                       variant) that rejects extra arguments still fetches.
#   1g. HTTP bound   -> with no timeout/gtimeout on PATH, a stalled HTTP remote
#                       still ends the fetch ("not verified", exit 0) within the guard.
#   1h. ssh deadline -> an ssh that connects and then hangs is killed at the
#                       fetch deadline ("not verified", exit 0), with no timeout on PATH.
#   1i. Nested deadline -> SESSION_START_CHILD_DEADLINE_SEC caps a 60s
#                       SYNC_FETCH_TIMEOUT, so the hung fetch ends before the
#                       watchdog (without the cap it would run 60s).
#   2. Up to date    -> emits a marker "in sync" status, exit 0.
#   3. Throttle      -> with a fixed injected clock: a call inside the window
#                       skips the fetch and reports "not verified" (never a
#                       definitive "in sync"/"behind" against the stale ref); a
#                       call past the window fetches and fast-forwards.
#   4. Not a repo    -> silent no-op, exit 0.
#   5. No origin     -> silent no-op, exit 0.
#   6. Fetch failure -> marker "not verified" (never a false "in sync" against a
#                       stale ref), exit 0 (offline/broken remote tolerated).
#   7. Bad clock     -> silent no-op, exit 0 (never aborts SessionStart).
#   8. Diverged      -> marker notice names divergence and recommends rebase, not
#                       a fast-forward (local both ahead and behind origin).
#   9. Future stamp  -> a schema_version > 1 record is not throttled on and is
#                       preserved (not downgraded to version 1).
#
# Run: bash hooks/tests/test_check_git_sync.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() {
  if [[ -n "${STALL_PID:-}" ]] && ! kill "$STALL_PID" 2>/dev/null; then
    echo "warn: stall server $STALL_PID already gone" >&2
  fi
  [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2
  return 0
}

#: Deadlines under test in the stall cases (1g, 1h, 1i): the fetch deadline,
#: and the aggregator's child deadline that caps it.
FETCH_DEADLINE_SEC=2
CHILD_DEADLINE_SEC=3
#: Upper bound, and the only elapsed-time check: the watchdog that ends a stall
#: case whose hook never returns (testing-standards Determinism, elapsed-time
#: carve-out: 4x the largest deadline under test plus fixed slack).
GUARD_SLACK_SEC=8
STALL_GUARD_SEC=$(( 4 * CHILD_DEADLINE_SEC + GUARD_SLACK_SEC ))

g() { git "$@"; }

commit_push() { # <clone-dir> <message>
  local dir="$1" msg="$2"
  printf '%s\n' "$msg" >> "$dir/f"                || die "commit_push: write to $dir/f failed"
  g -C "$dir" add f                               || die "commit_push: git add failed in $dir"
  g -C "$dir" commit -q -m "$msg"                 || die "commit_push: git commit failed in $dir"
  g -C "$dir" push -q origin main                 || die "commit_push: git push from $dir failed"
}

mk_origin() { # <prefix>: sets globals BARE, SEED to a fresh, independent origin
  local prefix="$1"
  BARE="$TMP/${prefix}.git"; SEED="$TMP/${prefix}-seed"
  g init -q --bare -b main "$BARE"                || die "mk_origin: git init --bare failed for $BARE"
  g clone -q "$BARE" "$SEED" 2>/dev/null          || die "mk_origin: git clone failed for $SEED"
  g -C "$SEED" symbolic-ref HEAD refs/heads/main  || die "mk_origin: git symbolic-ref failed in $SEED"
  commit_push "$SEED" "c1"
}

clone_from() { # <bare> <dest>: a working clone, checked explicitly
  g clone -q "$1" "$2"                            || die "clone_from: git clone $1 -> $2 failed"
}

# run <repo-dir> <state-dir> [extra env...] -> OUT, RC
run() {
  local repo="$1" state="$2"; shift 2
  OUT="$(cd "$repo" && env SYNC_STATE_DIR="$state" "$@" bash "$SCRIPT" </dev/null 2>/dev/null)"
  RC=$?
}

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-git-sync.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "hook not found/readable at $SCRIPT"
  command -v jq  >/dev/null 2>&1 || die "jq required for these tests"
  command -v git >/dev/null 2>&1 || die "git required for these tests"
  command -v python3 >/dev/null 2>&1 || die "python3 required for these tests"

  TMP="$(mktemp -d -t git-sync-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  # Isolate git from the operator's global/system config so identity and defaults
  # are deterministic across machines. This isolation is load-bearing — an
  # unchecked mkdir failure would silently defeat it, so guard it explicitly.
  export HOME="$TMP/home"
  mkdir -p "$HOME" || die "could not create isolated HOME at $HOME"
  export GIT_CONFIG_NOSYSTEM=1
  # The operator's session may itself run under Herdr or with a custom ssh;
  # every case sets these explicitly.
  unset HERDR_ENV SESSION_START_MODE GIT_SSH_COMMAND GIT_SSH
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

  FAIL=0; PASS=0

  # 1. behind, clean, on main -> fast-forwarded, and main really moved.
  mk_origin o1
  clone_from "$BARE" "$TMP/r1"
  commit_push "$SEED" "c2"
  run "$TMP/r1" "$TMP/s1"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("fast-forwarded") and test("main")' >/dev/null 2>&1 \
     && [[ "$(git -C "$TMP/r1" rev-parse main)" == "$(git -C "$TMP/r1" rev-parse origin/main)" ]]; then
    pass; else fail "behind: expected fast-forward to origin/main, got RC=$RC OUT=$OUT"; fi

  # 1a. behind with a tracked change -> reported, not moved.
  mk_origin o1a
  clone_from "$BARE" "$TMP/r1a"
  commit_push "$SEED" "c2"
  printf 'wip\n' >> "$TMP/r1a/f" || die "write r1a/f failed"
  local before1a; before1a="$(git -C "$TMP/r1a" rev-parse main)" || die "r1a rev-parse failed"
  run "$TMP/r1a" "$TMP/s1a"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind") and test("uncommitted changes") and (test("fast-forwarded") | not)' >/dev/null 2>&1 \
     && [[ "$(git -C "$TMP/r1a" rev-parse main)" == "$before1a" ]]; then
    pass; else fail "behind+dirty: expected a report and an unmoved main, got RC=$RC OUT=$OUT"; fi

  # 1a2. behind while a feature branch is checked out -> reported, not moved.
  mk_origin o1a2
  clone_from "$BARE" "$TMP/r1a2"
  g -C "$TMP/r1a2" checkout -q -b feat/x || die "r1a2 checkout failed"
  commit_push "$SEED" "c2"
  local before1a2; before1a2="$(git -C "$TMP/r1a2" rev-parse main)" || die "r1a2 rev-parse failed"
  run "$TMP/r1a2" "$TMP/s1a2"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind") and test("not the checked-out branch")' >/dev/null 2>&1 \
     && [[ "$(git -C "$TMP/r1a2" rev-parse main)" == "$before1a2" ]]; then
    pass; else fail "off-default: expected a report and an unmoved main, got RC=$RC OUT=$OUT"; fi

  # 1b. A Herdr worker (HERDR_ENV set, linked worktree) never fetches: origin
  # moves, the worker's run leaves refs/remotes/origin/main where it was, writes
  # no throttle stamp, and tells the worker not to sync the shared checkout
  # (rules/agent-team-operation.md Workers).
  mk_origin o1b
  clone_from "$BARE" "$TMP/r1b"
  git -C "$TMP/r1b" worktree add -q "$TMP/r1b-wt" -b feat/worker >/dev/null 2>&1 \
    || die "r1b worktree add failed"
  commit_push "$SEED" "c2"
  local ref1b; ref1b="$(git -C "$TMP/r1b" rev-parse refs/remotes/origin/main)" || die "r1b rev-parse failed"
  run "$TMP/r1b-wt" "$TMP/s1b" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Herdr worker session") and test("Do not sync") and test("as last fetched") and (test("fast-forwarded") | not)' >/dev/null 2>&1; then
    pass; else fail "worker session: expected a no-sync notice, got RC=$RC OUT=$OUT"; fi
  if [[ "$(git -C "$TMP/r1b" rev-parse refs/remotes/origin/main)" == "$ref1b" && ! -e "$TMP/s1b" ]]; then
    pass; else fail "worker session: expected no fetch and no throttle stamp"; fi

  # 1c. The same drift from the MAIN checkout with HERDR_ENV set is the lead:
  # it fetches and fast-forwards.
  run "$TMP/r1b" "$TMP/s1c" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
    pass; else fail "lead session: expected a fast-forward, got RC=$RC OUT=$OUT"; fi

  # 1d. Portable mode (tessl stripped HERDR_ENV). A linked worktree may be a
  # worker's: no fetch, "not verified". The main checkout fetches but only
  # reports, never fast-forwards.
  mk_origin o1d
  clone_from "$BARE" "$TMP/r1d"
  git -C "$TMP/r1d" worktree add -q "$TMP/r1d-wt" -b feat/maybe-worker >/dev/null 2>&1 \
    || die "r1d worktree add failed"
  commit_push "$SEED" "c2"
  local ref1d; ref1d="$(git -C "$TMP/r1d" rev-parse refs/remotes/origin/main)" || die "r1d rev-parse failed"
  run "$TMP/r1d-wt" "$TMP/s1d" SESSION_START_MODE=portable
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("not verified") and test("tessl")' >/dev/null 2>&1 \
     && [[ "$(git -C "$TMP/r1d" rev-parse refs/remotes/origin/main)" == "$ref1d" ]]; then
    pass; else fail "portable worktree: expected 'not verified' and no fetch, got RC=$RC OUT=$OUT"; fi
  local before1d; before1d="$(git -C "$TMP/r1d" rev-parse main)" || die "r1d rev-parse failed"
  run "$TMP/r1d" "$TMP/s1d2" SESSION_START_MODE=portable
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("behind") and test("tessl") and (test("fast-forwarded") | not)' >/dev/null 2>&1 \
     && [[ "$(git -C "$TMP/r1d" rev-parse main)" == "$before1d" ]]; then
    pass; else fail "portable main checkout: expected a behind report and an unmoved main, got RC=$RC OUT=$OUT"; fi

  # 1e. Repo hooks never run: the fetch updates refs (reference-transaction)
  # and the fast-forward is a merge (post-merge). Each hook drops a marker.
  mk_origin o1e
  clone_from "$BARE" "$TMP/r1e"
  commit_push "$SEED" "c2"
  local hk
  for hk in reference-transaction post-index-change post-merge; do
    printf '#!/bin/sh\ntouch "%s/ran-%s"\n' "$TMP" "$hk" > "$TMP/r1e/.git/hooks/$hk" || die "write $hk hook failed"
    chmod +x "$TMP/r1e/.git/hooks/$hk" || die "chmod $hk hook failed"
  done
  # A new mtime on a tracked file makes `git status` refresh and write the index.
  touch -t 202001010000 "$TMP/r1e/f" || die "touch r1e/f failed"
  run "$TMP/r1e" "$TMP/s1e"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1 \
     && [[ ! -e "$TMP/ran-reference-transaction" && ! -e "$TMP/ran-post-index-change" && ! -e "$TMP/ran-post-merge" ]]; then
    pass; else fail "repo hooks: expected fetch + fast-forward with no repo hook run, got RC=$RC OUT=$OUT"; fi

  # 1e2. Ignored local data where origin now tracks a file: git's default merge
  # would overwrite it; the hook must refuse and leave the data intact.
  mk_origin o1e2
  clone_from "$BARE" "$TMP/r1e2"
  printf 'precious\n' >> "$TMP/r1e2/.git/info/exclude" || die "r1e2 exclude failed"
  printf 'local data\n' > "$TMP/r1e2/precious" || die "r1e2 precious write failed"
  printf 'from origin\n' > "$SEED/precious" || die "seed precious write failed"
  g -C "$SEED" add precious || die "seed add precious failed"
  g -C "$SEED" commit -q -m "track precious" || die "seed commit failed"
  g -C "$SEED" push -q origin main || die "seed push failed"
  local before1e2; before1e2="$(git -C "$TMP/r1e2" rev-parse main)" || die "r1e2 rev-parse failed"
  run "$TMP/r1e2" "$TMP/s1e2"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("left unmoved") and test("refused")' >/dev/null 2>&1 \
     && [[ "$(cat "$TMP/r1e2/precious")" == "local data" && "$(git -C "$TMP/r1e2" rev-parse main)" == "$before1e2" ]]; then
    pass; else fail "ignored file: expected a refused fast-forward and intact local data, got RC=$RC OUT=$OUT data=$(cat "$TMP/r1e2/precious")"; fi

  # 1e3. core.fsmonitor names a program git runs on every index refresh.
  mk_origin o1e3
  clone_from "$BARE" "$TMP/r1e3"
  commit_push "$SEED" "c2"
  printf '#!/bin/sh\ntouch "%s/ran-fsmonitor"\nexit 1\n' "$TMP" > "$TMP/fsmonitor-hook" || die "write fsmonitor hook failed"
  chmod +x "$TMP/fsmonitor-hook" || die "chmod fsmonitor hook failed"
  g -C "$TMP/r1e3" config core.fsmonitor "$TMP/fsmonitor-hook" || die "r1e3 fsmonitor config failed"
  touch -t 202001010000 "$TMP/r1e3/f" || die "touch r1e3/f failed"
  run "$TMP/r1e3" "$TMP/s1e3"
  if [[ $RC -eq 0 && ! -e "$TMP/ran-fsmonitor" ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
    pass; else fail "fsmonitor: expected a fast-forward with the fsmonitor program never run, got RC=$RC ran=$([[ -e "$TMP/ran-fsmonitor" ]] && echo yes || echo no) OUT=$OUT"; fi

  # 1e4. A smudge filter from repo-local config would run on the fast-forward's
  # checkout of a file the new .gitattributes routes through it.
  mk_origin o1e4
  clone_from "$BARE" "$TMP/r1e4"
  printf '* filter=probe\n' > "$SEED/.gitattributes" || die "seed attributes write failed"
  printf 'payload\n' > "$SEED/data" || die "seed data write failed"
  g -C "$SEED" add .gitattributes data || die "seed add failed"
  g -C "$SEED" commit -q -m "route through probe" || die "seed commit failed"
  g -C "$SEED" push -q origin main || die "seed push failed"
  printf '#!/bin/sh\ntouch "%s/ran-filter"\ncat\n' "$TMP" > "$TMP/filter-probe" || die "write filter probe failed"
  chmod +x "$TMP/filter-probe" || die "chmod filter probe failed"
  g -C "$TMP/r1e4" config filter.probe.smudge "$TMP/filter-probe" || die "r1e4 smudge config failed"
  g -C "$TMP/r1e4" config filter.probe.clean "$TMP/filter-probe" || die "r1e4 clean config failed"
  local before1e4; before1e4="$(git -C "$TMP/r1e4" rev-parse main)" || die "r1e4 rev-parse failed"
  run "$TMP/r1e4" "$TMP/s1e4"
  if [[ $RC -eq 0 && ! -e "$TMP/ran-filter" && "$(git -C "$TMP/r1e4" rev-parse main)" == "$before1e4" ]] \
     && printf '%s' "$OUT" | jq -e '.additionalContext | test("left unmoved") and test("filter command")' >/dev/null 2>&1; then
    pass; else fail "local filter: expected a refused fast-forward with the filter never run, got RC=$RC ran=$([[ -e "$TMP/ran-filter" ]] && echo yes || echo no) OUT=$OUT"; fi

  # 1e5. A filter in a file the repository config includes, or in the
  # per-worktree config, is repository config too and refuses the same way.
  # A filter from the user's own global config is trusted and fast-forwards.
  local variant
  for variant in include worktree global; do
    mk_origin "o1e5$variant"
    clone_from "$BARE" "$TMP/r1e5$variant"
    printf '* filter=probe\n' > "$SEED/.gitattributes" || die "seed attributes write failed"
    printf 'payload\n' > "$SEED/data" || die "seed data write failed"
    g -C "$SEED" add .gitattributes data || die "seed add failed"
    g -C "$SEED" commit -q -m "route through probe" || die "seed commit failed"
    g -C "$SEED" push -q origin main || die "seed push failed"
    rm -f "$TMP/ran-filter" || die "could not clear the filter marker"
    printf '[filter "probe"]\n\tsmudge = %s\n\tclean = %s\n' "$TMP/filter-probe" "$TMP/filter-probe" \
      > "$TMP/filter-$variant.cfg" || die "write $variant filter config failed"
    local extra=()
    case "$variant" in
      include)  g -C "$TMP/r1e5$variant" config include.path "$TMP/filter-$variant.cfg" || die "r1e5 include config failed" ;;
      worktree) g -C "$TMP/r1e5$variant" config extensions.worktreeConfig true || die "r1e5 worktreeConfig failed"
                g -C "$TMP/r1e5$variant" config --worktree filter.probe.smudge "$TMP/filter-probe" || die "r1e5 worktree smudge failed"
                g -C "$TMP/r1e5$variant" config --worktree filter.probe.clean "$TMP/filter-probe" || die "r1e5 worktree clean failed" ;;
      global)   extra=(GIT_CONFIG_GLOBAL="$TMP/filter-$variant.cfg") ;;
    esac
    local before1e5; before1e5="$(git -C "$TMP/r1e5$variant" rev-parse main)" || die "r1e5 rev-parse failed"
    run "$TMP/r1e5$variant" "$TMP/s1e5$variant" ${extra[@]+"${extra[@]}"}
    if [[ "$variant" == global ]]; then
      if [[ $RC -eq 0 && "$(git -C "$TMP/r1e5$variant" rev-parse main)" != "$before1e5" ]] \
         && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
        pass; else fail "global filter: expected a fast-forward, got RC=$RC OUT=$OUT"; fi
    elif [[ $RC -eq 0 && ! -e "$TMP/ran-filter" && "$(git -C "$TMP/r1e5$variant" rev-parse main)" == "$before1e5" ]] \
       && printf '%s' "$OUT" | jq -e '.additionalContext | test("left unmoved") and test("filter command")' >/dev/null 2>&1; then
      pass; else fail "$variant filter: expected a refused fast-forward with the filter never run, got RC=$RC ran=$([[ -e "$TMP/ran-filter" ]] && echo yes || echo no) OUT=$OUT"; fi
  done

  # 1f. The OpenSSH connect bound goes only to the plain default `ssh`. A
  # recording "ssh" first on PATH logs its argv and fails ("not verified").
  mk_origin o1f
  clone_from "$BARE" "$TMP/r1f"
  g -C "$TMP/r1f" remote set-url origin "ssh://fake.invalid/repo.git" || die "r1f set-url failed"
  mkdir -p "$TMP/sshbin" || die "could not create $TMP/sshbin"
  # shellcheck disable=SC2016 # the $* and $0 belong to the generated script
  printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "$0.log"\nexit 255\n' > "$TMP/sshbin/ssh" || die "write fake ssh failed"
  chmod +x "$TMP/sshbin/ssh" || die "chmod fake ssh failed"
  run "$TMP/r1f" "$TMP/s1f" PATH="$TMP/sshbin:$PATH"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("not verified")' >/dev/null 2>&1 \
     && grep -q 'ConnectTimeout=' "$TMP/sshbin/ssh.log"; then
    pass; else fail "ssh bound (default ssh): expected ConnectTimeout on the ssh argv, got RC=$RC OUT=$OUT"; fi

  # 1f2. A custom ssh runs unchanged. This "simple"-variant wrapper rejects
  # anything but <host> <command> and serves the repo locally, so the fetch
  # succeeds (and fast-forwards) only if no option was added. Covered for
  # GIT_SSH_COMMAND + GIT_SSH_VARIANT and for core.sshCommand + ssh.variant.
  # shellcheck disable=SC2016 # the $# $1 $2 $0 belong to the generated script
  printf '#!/bin/sh\nif [ "$#" -ne 2 ]; then echo "strict-ssh: rejected $*" >> "$0.log"; exit 2; fi\nexec sh -c "git ${2#git-}"\n' > "$TMP/strict-ssh" \
    || die "write strict-ssh failed"
  chmod +x "$TMP/strict-ssh" || die "chmod strict-ssh failed"
  local how
  for how in env config; do
    mk_origin "o1f2$how"
    clone_from "$BARE" "$TMP/r1f2$how"
    commit_push "$SEED" "c2"
    g -C "$TMP/r1f2$how" remote set-url origin "ssh://fake.invalid${BARE}" || die "r1f2$how set-url failed"
    rm -f "$TMP/strict-ssh.log" || die "could not reset strict-ssh.log"
    if [[ "$how" == env ]]; then
      run "$TMP/r1f2$how" "$TMP/s1f2$how" GIT_SSH_COMMAND="$TMP/strict-ssh" GIT_SSH_VARIANT=simple
    else
      g -C "$TMP/r1f2$how" config core.sshCommand "$TMP/strict-ssh" || die "r1f2 config sshCommand failed"
      g -C "$TMP/r1f2$how" config ssh.variant simple || die "r1f2 config variant failed"
      run "$TMP/r1f2$how" "$TMP/s1f2$how"
    fi
    if [[ $RC -eq 0 && ! -e "$TMP/strict-ssh.log" ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
      pass; else fail "custom ssh ($how): expected an unchanged ssh command and a fetch, got RC=$RC log=$(cat "$TMP/strict-ssh.log" 2>/dev/null) OUT=$OUT"; fi
  done

  # 1g. HTTP stall with no timeout/gtimeout on PATH: a server that accepts and
  # never answers. git's low-speed abort must end the fetch; the harness kills
  # the hook after STALL_GUARD_SEC and fails the case if it is still running.
  mk_origin o1g
  clone_from "$BARE" "$TMP/r1g"
  python3 -c '
import socket, sys, time
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
open(sys.argv[1], "w").write(str(s.getsockname()[1]))
held = []
while True:
    c, _ = s.accept(); held.append(c)
' "$TMP/stall.port" &
  STALL_PID=$!
  local i port=""
  for (( i = 0; i < 50; i++ )); do
    [[ -s "$TMP/stall.port" ]] && { port="$(cat "$TMP/stall.port")"; break; }
    sleep 0.1
  done
  [[ -n "$port" ]] || die "stall server did not start"
  g -C "$TMP/r1g" remote set-url origin "http://127.0.0.1:${port}/repo.git" || die "r1g set-url failed"
  local bin tool
  bin="$TMP/notimeout-bin"
  mkdir -p "$bin" || die "could not create $bin"
  for tool in bash git jq cksum cut mkdir date env dirname sleep ps awk; do
    ln -sf "$(command -v "$tool")" "$bin/$tool" || die "could not link $tool"
  done
  ( cd "$TMP/r1g" && env SYNC_STATE_DIR="$TMP/s1g" SYNC_FETCH_TIMEOUT="$FETCH_DEADLINE_SEC" PATH="$bin" bash "$SCRIPT" </dev/null >"$TMP/r1g.out" 2>/dev/null ) &
  local hook_pid=$! done1g=0 rc1g=0
  for (( i = 0; i < STALL_GUARD_SEC * 10; i++ )); do
    if ! kill -0 "$hook_pid" 2>/dev/null; then done1g=1; break; fi
    sleep 0.1
  done
  if (( ! done1g )); then
    kill "$hook_pid" || echo "warn: could not kill the stalled hook $hook_pid" >&2
  fi
  wait "$hook_pid" || rc1g=$?
  OUT="$(cat "$TMP/r1g.out")"
  if (( done1g )) && [[ $rc1g -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("not verified")' >/dev/null 2>&1; then
    pass; else fail "http bound: expected the stalled fetch to end within ${STALL_GUARD_SEC}s as 'not verified' with exit 0, got done=$done1g rc=$rc1g OUT=$OUT"; fi
  kill "$STALL_PID" || echo "warn: could not stop the stall server $STALL_PID" >&2
  local stall_rc=0
  wait "$STALL_PID" || stall_rc=$?
  STALL_PID=""
  # SIGTERM (143) is the expected end; SIGKILL (137) is tolerated. Anything
  # else means the server died on its own and the case proved nothing.
  case "$stall_rc" in
    143|137) pass ;;
    *) fail "stall server: expected termination by signal (143/137), got exit $stall_rc" ;;
  esac

  # 1h. An ssh that connects and then never answers: ConnectTimeout cannot
  # help, so only the fetch deadline ends it. Same no-timeout PATH as 1g.
  mk_origin o1h
  clone_from "$BARE" "$TMP/r1h"
  g -C "$TMP/r1h" remote set-url origin "ssh://fake.invalid/repo.git" || die "r1h set-url failed"
  # shellcheck disable=SC2016 # $$ and $0 belong to the generated script
  printf '#!/bin/sh\necho $$ > "$0.pid"\nexec sleep 60\n' > "$TMP/hang-ssh" || die "write hang-ssh failed"
  chmod +x "$TMP/hang-ssh" || die "chmod hang-ssh failed"
  ( cd "$TMP/r1h" && env SYNC_STATE_DIR="$TMP/s1h" SYNC_FETCH_TIMEOUT="$FETCH_DEADLINE_SEC" GIT_SSH_COMMAND="$TMP/hang-ssh" PATH="$bin" bash "$SCRIPT" </dev/null >"$TMP/r1h.out" 2>/dev/null ) &
  hook_pid=$!; local done1h=0 rc1h=0
  for (( i = 0; i < STALL_GUARD_SEC * 10; i++ )); do
    if ! kill -0 "$hook_pid" 2>/dev/null; then done1h=1; break; fi
    sleep 0.1
  done
  if (( ! done1h )); then
    kill "$hook_pid" || echo "warn: could not kill the stalled hook $hook_pid" >&2
  fi
  wait "$hook_pid" || rc1h=$?
  OUT="$(cat "$TMP/r1h.out")"
  local hang_pid=""
  [[ -r "$TMP/hang-ssh.pid" ]] && hang_pid="$(cat "$TMP/hang-ssh.pid")"
  if (( done1h )) && [[ $rc1h -eq 0 && -n "$hang_pid" ]] && ! kill -0 "$hang_pid" 2>/dev/null \
     && printf '%s' "$OUT" | jq -e '.additionalContext | test("not verified")' >/dev/null 2>&1; then
    pass; else fail "ssh deadline: expected the hung ssh killed and 'not verified' with exit 0 within ${STALL_GUARD_SEC}s, got done=$done1h rc=$rc1h hang=$hang_pid OUT=$OUT"; fi
  if [[ -n "$hang_pid" ]] && kill -0 "$hang_pid" 2>/dev/null; then
    kill "$hang_pid" || echo "warn: could not stop the hung ssh $hang_pid" >&2
  fi

  # 1i. The aggregator's child deadline caps the fetch deadline: with 3s for
  # the whole hook, a 60s fetch deadline must not apply. Same hung ssh as 1h.
  mk_origin o1i
  clone_from "$BARE" "$TMP/r1i"
  g -C "$TMP/r1i" remote set-url origin "ssh://fake.invalid/repo.git" || die "r1i set-url failed"
  rm -f "$TMP/hang-ssh.pid" || die "could not reset hang-ssh.pid"
  local rc1i=0
  ( cd "$TMP/r1i" && env SYNC_STATE_DIR="$TMP/s1i" SYNC_FETCH_TIMEOUT=60 SESSION_START_CHILD_DEADLINE_SEC="$CHILD_DEADLINE_SEC" GIT_SSH_COMMAND="$TMP/hang-ssh" PATH="$bin" bash "$SCRIPT" </dev/null >"$TMP/r1i.out" 2>/dev/null ) &
  hook_pid=$!; local done1i=0
  for (( i = 0; i < STALL_GUARD_SEC * 10; i++ )); do
    if ! kill -0 "$hook_pid" 2>/dev/null; then done1i=1; break; fi
    sleep 0.1
  done
  if (( ! done1i )); then
    kill "$hook_pid" || echo "warn: could not kill the stalled hook $hook_pid" >&2
  fi
  wait "$hook_pid" || rc1i=$?
  OUT="$(cat "$TMP/r1i.out")"
  if (( done1i )) && [[ $rc1i -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("not verified")' >/dev/null 2>&1; then
    pass; else fail "nested deadline: expected the fetch capped below the ${CHILD_DEADLINE_SEC}s child deadline and 'not verified' with exit 0, got done=$done1i rc=$rc1i OUT=$OUT"; fi
  hang_pid=""
  [[ -r "$TMP/hang-ssh.pid" ]] && hang_pid="$(cat "$TMP/hang-ssh.pid")"
  if [[ -n "$hang_pid" ]] && kill -0 "$hang_pid" 2>/dev/null; then
    kill "$hang_pid" || echo "warn: could not stop the hung ssh $hang_pid" >&2
  fi

  # 2. up to date -> marker "in sync" status.
  mk_origin o2
  clone_from "$BARE" "$TMP/r2"
  run "$TMP/r2" "$TMP/s2"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("in sync")' >/dev/null 2>&1; then
    pass; else fail "up-to-date: expected in-sync status, got RC=$RC OUT=$OUT"; fi

  # 2b. ahead only -> "ahead ... unpushed", never "in sync" (ahead>0, behind==0).
  #     Clone up to date, add a local commit without pushing; origin unchanged.
  mk_origin o2b
  clone_from "$BARE" "$TMP/r2b"
  g -C "$TMP/r2b" commit -q --allow-empty -m "local ahead"  || die "git commit in r2b failed"
  run "$TMP/r2b" "$TMP/s2b"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("ahead") and (test("in sync") | not)' >/dev/null 2>&1; then
    pass; else fail "ahead-only: expected \"ahead\" (not \"in sync\"), got RC=$RC OUT=$OUT"; fi

  # 3. throttle. Clone up to date. First call fetches, stamps, and reports a
  #    definitive "in sync". Move origin ahead WITHOUT the hook fetching: a call
  #    +60s is throttled, so it does not fetch and reports "not verified" (never
  #    a definitive "in sync"/"behind" against a stale ref). A call past the 1h
  #    window fetches the new commit and fires "behind".
  mk_origin o3
  clone_from "$BARE" "$TMP/r3"
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2000000               # fetch, stamp, up to date -> in sync
  # This establishes the throttle stamp the next two assertions depend on, so a
  # failure here must abort, not merely tally (aggregate-reporting carve-out:
  # later checks may not depend on an earlier one merely having incremented FAIL).
  { [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("in sync")' >/dev/null 2>&1; } \
    || die "throttle setup: first call should report in sync, got RC=$RC OUT=$OUT"
  commit_push "$SEED" "c3"                                # origin moves; r3's tracking ref still old
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2000060               # +60s: throttled -> no fetch -> not verified
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("not verified") and (test("in sync") | not) and (test("behind") | not)' >/dev/null 2>&1; then
    pass; else fail "throttle active: inside window should report 'not verified' (not 'in sync'/'behind'), got OUT=$OUT"; fi
  run "$TMP/r3" "$TMP/s3" SYNC_NOW=2003601               # +>1h: fetch -> behind -> fast-forwarded
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
    pass; else fail "throttle expired: past window should fetch and fire, got OUT=$OUT"; fi

  # 4. not a repo -> silent no-op.
  mkdir -p "$TMP/notrepo" || die "could not create $TMP/notrepo"
  run "$TMP/notrepo" "$TMP/s4"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "not a repo: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 5. no origin remote -> silent no-op.
  g init -q -b main "$TMP/noorigin" || die "git init noorigin failed"
  printf 'x\n' > "$TMP/noorigin/f"  || die "write noorigin/f failed"
  g -C "$TMP/noorigin" add f        || die "git add in noorigin failed"
  g -C "$TMP/noorigin" commit -q -m x || die "git commit in noorigin failed"
  run "$TMP/noorigin" "$TMP/s5"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "no origin: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 6. fetch failure -> "not verified" (broken remote tolerated, no crash, and
  #    never a false "in sync" against a stale ref — sync-before-work). Clone,
  #    then delete the bare origin so the fetch fails; the hook reports
  #    not-verified and exits 0 without crashing.
  mk_origin o6
  clone_from "$BARE" "$TMP/r6"
  rm -rf "$BARE" || die "could not remove $BARE"
  run "$TMP/r6" "$TMP/s6"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("not verified") and (test("in sync") | not)' >/dev/null 2>&1; then
    pass; else fail "fetch failure: expected 'not verified' (not 'in sync') exit 0, got RC=$RC OUT=$OUT"; fi

  # 7. malformed clock -> no-op, exit 0.
  mk_origin o7
  clone_from "$BARE" "$TMP/r7"
  run "$TMP/r7" "$TMP/s7" SYNC_NOW="not-a-number"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "bad clock: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 8. diverged -> divergence notice (rebase, not fast-forward). Clone, add a
  #    local commit (ahead by 1), and push a different commit to origin (behind
  #    by 1); the hook fetches and reports the diverged state.
  mk_origin o8
  clone_from "$BARE" "$TMP/r8"
  printf 'local\n' >> "$TMP/r8/f"        || die "write r8/f failed"
  g -C "$TMP/r8" add f                    || die "git add in r8 failed"
  g -C "$TMP/r8" commit -q -m local       || die "git commit in r8 failed"   # ahead by 1
  commit_push "$SEED" "c2"                                                    # origin moves -> behind by 1
  run "$TMP/r8" "$TMP/s8"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("diverged") and test("rebase")' >/dev/null 2>&1; then
    pass; else fail "diverged: expected marker divergence notice, got RC=$RC OUT=$OUT"; fi

  # 9. future-version throttle stamp -> not throttled on, and preserved (never
  #    downgraded). Pre-seed a "2 <recent>" record at the stamp path the hook
  #    derives (cksum of the repo toplevel), move origin ahead, and run inside
  #    the window: a v1 stamp would throttle to silence, but the future record
  #    must be ignored (the hook fires) and left untouched.
  mk_origin o9
  clone_from "$BARE" "$TMP/r9"
  commit_push "$SEED" "c2"                                # origin ahead -> a non-throttled run fires
  local top9 key9 stampdir9 sv9
  top9="$(cd "$TMP/r9" && git rev-parse --show-toplevel)" || die "r9 toplevel failed"
  key9="$(printf '%s' "$top9" | cksum | cut -d' ' -f1)"   || die "r9 key derivation failed"
  stampdir9="$TMP/s9"
  mkdir -p "$stampdir9" || die "could not create $stampdir9"
  printf '2 %s\n' 2000000 > "$stampdir9/sync-$key9" || die "could not seed future stamp"
  run "$TMP/r9" "$stampdir9" SYNC_NOW=2000060            # within window, but future schema
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("fast-forwarded")' >/dev/null 2>&1; then
    pass; else fail "future stamp: expected fire (not throttled), got RC=$RC OUT=$OUT"; fi
  sv9=""; read -r sv9 _ < "$stampdir9/sync-$key9" || sv9=""
  if [[ "$sv9" == "2" ]]; then pass; else fail "future stamp: expected preserved version 2, got '$sv9'"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
