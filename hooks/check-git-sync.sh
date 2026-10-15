#!/usr/bin/env bash
# Sync the local default branch with origin at session start, or report why not.
#
# A SessionStart hook implementing rules/sync-before-work.md as a deterministic
# check: it fetches origin (throttled) and compares the local default branch
# with origin/<default>. A default branch strictly behind is fast-forwarded when
# that is mechanical; every other drift is reported via additionalContext. The
# rule says fetch + sync before reading/editing, but relied on the agent
# remembering; we hit stale-checkout ("main is behind") repeatedly.
#
# Design choices:
#   - It DOES something (fetches, compares refs, fast-forwards), it does not
#     re-state a rule.
#   - SessionStart fires once per session, not per turn — no per-turn tax.
#   - Throttled: the fetch runs at most once per SYNC_THROTTLE_HOURS (default 1h)
#     per repo, so rapid session churn doesn't hammer the network. Without a
#     fresh fetch this session the remote-tracking ref may be stale, so a
#     throttled (or failed) fetch reports "sync not verified" rather than a
#     definitive conclusion (rules/sync-before-work.md).
#   - The session role is decided BEFORE any fetch. A Herdr worker never
#     fetches: the fetch writes remote-tracking refs shared with the lead's
#     checkout (rules/agent-team-operation.md Workers). It reports drift as last
#     fetched. A session whose role cannot be proven (a failed probe with
#     HERDR_ENV set, or a linked worktree under SESSION_START_MODE=portable,
#     where tessl strips HERDR_ENV) neither fetches nor fast-forwards.
#   - Repo hooks never run: every git call that can update refs or the work tree
#     carries `-c core.hooksPath=/dev/null` (a ref update runs
#     `reference-transaction`, a merge runs `post-merge`).
#   - The fetch is time-bounded without depending on timeout/gtimeout: git's
#     HTTP low-speed abort and ssh ConnectTimeout/ServerAlive bound it always,
#     and timeout/gtimeout wraps it too when present.
#   - Fast-forward only for a proven non-worker in native mode, on the default
#     branch, with no tracked changes, strictly behind after a fresh fetch, via
#     `merge --ff-only` (git refuses anything unsafe). Diverged, off-default,
#     dirty, and unverified sessions are reported only.
#   - Never blocks (always exits 0), never exits 2.
#
# Contract:
#   stdin : consensus SessionStart JSON — not read (the script needs none of it).
#   stdout: once the repo's default branch is resolved, one JSON object
#           {"additionalContext": "<status>"} whose text begins with the
#           "Session-start status — " marker (rules/hook-action-reporting.md) —
#           reporting in-sync, ahead, fast-forwarded, behind (with why it was
#           not fast-forwarded), or diverged after a fresh fetch; "sync not
#           verified" when the fetch was throttled, failed, or the role is
#           unproven; or, for a Herdr worker, the unfetched drift with a
#           do-not-sync note.
#   exit  : always 0. Every best-effort failure emits an actionable stderr warning
#           and continues/no-ops (rules/error-handling.md Shell Error Handling).
#           Non-repo, no origin, and no local default branch are silent no-ops —
#           the sync check does not apply, so those sessions stay quiet.
#   state : $SYNC_STATE_DIR/sync-<repo-key> (default ${TMPDIR:-/tmp}/coding-policy-sync),
#           a per-repo throttle stamp (keyed by toplevel path). Schema documented
#           in hooks/state-schema.md: one line "<schema_version> <checked_at>".
#           A worker or unproven-role session never writes it.
#   env   : SYNC_THROTTLE_HOURS (default 1), SYNC_FETCH_TIMEOUT (seconds, default
#           10), SESSION_START_MODE (set to "portable" by hooks/session-start.sh
#           under `tessl hook run`), SYNC_STATE_DIR (tests), SYNC_NOW (test-only
#           injected clock; defaults to `date +%s`).
set -euo pipefail

warn() { printf 'check-git-sync: %s\n' "$1" >&2; }

# Does a ref exist? `git show-ref --verify --quiet` exits 0 (exists) or 1 (the
# expected "absent / malformed name" no-result); any other exit is a real git
# failure — surface it and treat the ref as absent so best-effort work continues
# visibly (rules/error-handling.md — distinguish a non-result from a tool error).
ref_exists() { # <fully-qualified-ref>
  local rc=0
  git show-ref --verify --quiet "$1" || rc=$?
  if (( rc == 0 )); then return 0; fi
  if (( rc != 1 )); then
    warn "git show-ref failed (exit ${rc}) checking $1 — treating the ref as absent"
  fi
  return 1
}

# Emit the sync notice as additionalContext JSON. jq is required only here; its
# absence is an expected environment condition, not a failure.
emit_notice() { # <notice-text>
  command -v jq >/dev/null 2>&1 || { warn "jq not found — install jq to emit the sync notice"; return 0; }
  jq -n --arg c "$1" '{additionalContext: $c}' ||
    warn "could not emit the sync notice as JSON — skipping sync check"
  return 0
}

# Which Herdr role does this session hold?
#
# The team rules reserve the shared checkout and every worktree operation for
# the lead: a worker "runs no git command against the shared checkout,
# mutating or otherwise" (rules/agent-team-operation.md Workers). A fetch from a
# worker writes the remote-tracking refs the lead's checkout shares, and a hook
# that tells a worker to fast-forward `main` instructs it to break that rule.
#
# Herdr exports no lead/worker flag, so the role is derived from where the
# session sits: the lead works in the shared checkout, every worker works in a
# linked worktree. In a linked worktree `--git-dir` and `--git-common-dir`
# resolve differently; in the main checkout they are the same. Under
# SESSION_START_MODE=portable tessl has stripped HERDR_ENV, so its absence
# proves nothing and a linked worktree may be a worker's.
#
# Return: 0 = a Herdr worker; 1 = the lead or a standalone agent (the only
# sessions that fetch); 2 = unproven — no fetch, no fast-forward. ROLE_WHY names
# the cause for the notice when 2.
herdr_role() {
  ROLE_WHY=""
  local portable=0
  if [[ -z "${HERDR_ENV:-}" ]]; then
    [[ "${SESSION_START_MODE:-}" == portable ]] || return 1
    portable=1
  fi

  local git_dir common_dir rc=0
  git_dir="$(git rev-parse --absolute-git-dir 2>/dev/null)" || rc=$?
  if (( rc == 0 )); then
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || rc=$?
  fi
  if (( rc != 0 )); then
    warn "git rev-parse failed (exit ${rc}) — cannot tell a Herdr worker from the lead; not fetching"
    ROLE_WHY="this hook could not tell a Herdr worker from the lead — run \`git rev-parse --absolute-git-dir --path-format=absolute --git-common-dir\` to diagnose."
    return 2
  fi

  [[ "$git_dir" != "$common_dir" ]] || return 1
  if (( portable )); then
    ROLE_WHY="SessionStart ran through tessl, which hides the session's environment, and this linked worktree may be a Herdr worker's."
    return 2
  fi
  return 0
}

# Count the local default branch's drift from its remote-tracking ref as the
# refs stand now (no fetch). --left-right --count on a three-dot range yields
# "<ahead>\t<behind>" — ahead = local-only commits, behind = origin-only commits
# — so a diverged branch (both > 0) is distinguished from one strictly behind.
# Sets DRIFT_AHEAD / DRIFT_BEHIND; a missing ref or a rev-list failure is warned
# and returns 1, never swallowed as "up to date".
drift_counts() { # <default-branch>
  local db="$1" counts
  if ! counts="$(git rev-list --left-right --count "refs/heads/${db}...refs/remotes/origin/${db}" 2>/dev/null)"; then
    warn "could not compare ${db} against origin/${db} — run \`git status\` to inspect; skipping sync check"
    return 1
  fi
  DRIFT_AHEAD="${counts%%[[:space:]]*}"
  DRIFT_BEHIND="${counts##*[[:space:]]}"
  if ! [[ "$DRIFT_AHEAD" =~ ^[0-9]+$ && "$DRIFT_BEHIND" =~ ^[0-9]+$ ]]; then
    warn "unexpected ahead/behind counts '${counts}' for ${db} — run \`git status\` to inspect; skipping sync check"
    return 1
  fi
  return 0
}

# Build the bounded, hook-free fetch command into FETCH_CMD.
#
# git's own limits bound it on every platform: http.lowSpeedLimit/lowSpeedTime
# abort a stalled HTTP transfer, and ssh ConnectTimeout/ServerAlive* abort a
# stalled ssh one. The ssh options extend whichever ssh command git would have
# used (GIT_SSH_COMMAND, then core.sshCommand), never replace it. A bare GIT_SSH
# program takes no options, so it keeps only the HTTP and timeout bounds.
# timeout/gtimeout wraps the whole fetch when present.
build_fetch() { # <seconds>
  local secs="$1" ssh_base="" rc=0
  local -a envs=()
  FETCH_CMD=()
  if [[ -n "${GIT_SSH_COMMAND:-}" ]]; then
    ssh_base="$GIT_SSH_COMMAND"
  else
    ssh_base="$(git config --get core.sshCommand)" || rc=$?
    if (( rc == 1 )); then
      ssh_base=""
    elif (( rc != 0 )); then
      warn "git config core.sshCommand failed (exit ${rc}) — the ssh connect bound is off for this fetch"
      ssh_base=""
    fi
    if [[ -z "$ssh_base" && -z "${GIT_SSH:-}" ]]; then ssh_base="ssh"; fi
  fi
  if [[ -n "$ssh_base" ]]; then
    envs=(env "GIT_SSH_COMMAND=${ssh_base} -o ConnectTimeout=${secs} -o ServerAliveInterval=5 -o ServerAliveCountMax=2")
  fi
  if command -v timeout >/dev/null 2>&1; then
    FETCH_CMD=(timeout "$secs")
  elif command -v gtimeout >/dev/null 2>&1; then
    FETCH_CMD=(gtimeout "$secs")
  fi
  FETCH_CMD+=(${envs[@]+"${envs[@]}"} git -c core.hooksPath=/dev/null -c http.lowSpeedLimit=1000 -c "http.lowSpeedTime=${secs}" fetch --quiet origin)
}

# Fast-forward the checked-out default branch to origin's, or say why not.
#
# Preconditions checked here: HEAD is the default branch and no tracked file
# differs from HEAD. `merge --ff-only` then refuses a non-fast-forward and any
# update that would overwrite an untracked file. Repo hooks are off
# (`post-merge`). 0 = moved; 1 = not moved, FF_WHY names the reason.
fast_forward() { # <default-branch>
  local db="$1" current="" out rc=0
  FF_WHY=""
  current="$(git symbolic-ref --quiet --short HEAD)" || rc=$?
  if (( rc > 1 )); then
    warn "git symbolic-ref HEAD failed (exit ${rc}) — ${db} was not fast-forwarded"
    FF_WHY="the checked-out branch could not be read"
    return 1
  fi
  if [[ "$current" != "$db" ]]; then
    FF_WHY="\`${db}\` is not the checked-out branch here"
    return 1
  fi
  rc=0
  out="$(git status --porcelain --untracked-files=no)" || rc=$?
  if (( rc != 0 )); then
    warn "git status failed (exit ${rc}) — ${db} was not fast-forwarded"
    FF_WHY="\`git status\` failed"
    return 1
  fi
  if [[ -n "$out" ]]; then
    FF_WHY="the work tree has uncommitted changes"
    return 1
  fi
  rc=0
  out="$(git -c core.hooksPath=/dev/null merge --ff-only --quiet "refs/remotes/origin/${db}" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    warn "fast-forward of ${db} refused (exit ${rc}): ${out//$'\n'/ }"
    FF_WHY="git refused the fast-forward (see the stderr warning)"
    return 1
  fi
  return 0
}

main() {
  local THROTTLE_HOURS="${SYNC_THROTTLE_HOURS:-1}"
  local FETCH_TIMEOUT="${SYNC_FETCH_TIMEOUT:-10}"
  local STATE_DIR="${SYNC_STATE_DIR:-${TMPDIR:-/tmp}/coding-policy-sync}"
  local rc db inside cand now top repo_key stamp sv ts should_fetch preserve_future fetch_failed ahead behind role ROLE_WHY FF_WHY
  local -a FETCH_CMD=()

  # git is required to produce a signal; its absence is an expected environment
  # condition, not a failure — warn and no-op.
  command -v git >/dev/null 2>&1 || { warn "git not found — install git to enable the sync check"; return 0; }

  # Outside a work tree there is nothing to sync. `--is-inside-work-tree` prints
  # true/false and exits 0 inside any repo; its only failure is exit 128 ("not a
  # git repository") — the expected non-repo session. Proceed only on a literal
  # "true"; a bare repo / gitdir ("false") and the non-repo case are silent
  # no-ops, and an unexpected value is surfaced.
  inside="$(git rev-parse --is-inside-work-tree 2>/dev/null)" || inside="__notrepo__"
  case "$inside" in
    true) : ;;
    false|__notrepo__) return 0 ;;
    *) warn "unexpected \`git rev-parse --is-inside-work-tree\` output '${inside}' — skipping sync check"; return 0 ;;
  esac

  # No origin remote => nothing to compare against. `git remote get-url` exits 2
  # for the documented "No such remote" (silent no-op); any other non-zero is a
  # real git failure and is surfaced (rules/error-handling.md).
  rc=0
  git remote get-url origin >/dev/null 2>&1 || rc=$?
  if (( rc != 0 )); then
    if (( rc == 2 )); then return 0; fi
    warn "git remote get-url origin failed (exit ${rc}) — run \`git remote -v\` to inspect; skipping sync check"
    return 0
  fi

  if ! [[ "$THROTTLE_HOURS" =~ ^[0-9]+$ ]]; then
    warn "SYNC_THROTTLE_HOURS='${THROTTLE_HOURS}' is not an integer — using 1"
    THROTTLE_HOURS=1
  fi

  # Resolve the remote default branch. Primary path: origin/HEAD's symbolic ref
  # (set at clone time). `git symbolic-ref --quiet` exits 0 when resolved and 1
  # for the expected no-symref case (origin/HEAD absent or not symbolic); any
  # other exit is a real git failure and is surfaced, not swallowed as "no
  # default" (rules/error-handling.md — distinguish an expected non-result from a
  # tool failure).
  db=""
  if db="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"; then
    db="${db#origin/}"
  else
    rc=$?
    db=""
    if (( rc != 1 )); then
      warn "git symbolic-ref failed (exit ${rc}) resolving origin/HEAD — falling back to name probe"
    fi
  fi
  # Fallback: probe the conventional names among the remote-tracking refs we have.
  if [[ -z "$db" ]]; then
    for cand in main master; do
      if ref_exists "refs/remotes/origin/$cand"; then db="$cand"; break; fi
    done
  fi
  if [[ -z "$db" ]]; then
    warn "cannot determine origin's default branch — set it with \`git remote set-head origin --auto\`; skipping sync check"
    return 0
  fi

  # No local default branch (e.g. only feature branches checked out) => nothing to
  # report as "behind". Silent no-op when absent; ref_exists surfaces a real
  # git failure before returning "absent".
  ref_exists "refs/heads/$db" || return 0

  # Decide the role before anything touches the network or the throttle stamp.
  role=0
  herdr_role || role=$?
  if (( role == 2 )); then
    emit_notice "Session-start status — git: \`${db}\` sync not verified, and nothing was fetched or fast-forwarded here — ${ROLE_WHY} If this is a Herdr worker session, do not sync the shared checkout (rules/agent-team-operation.md Workers); otherwise run \`git fetch origin\`, then \`git status\` (rules/sync-before-work.md)."
    return 0
  fi
  if (( role == 0 )); then
    drift_counts "$db" || return 0
    emit_notice "Session-start status — git: local \`${db}\` is ${DRIFT_BEHIND} behind / ${DRIFT_AHEAD} ahead of \`origin/${db}\` as last fetched (a Herdr worker session does not fetch). This is a Herdr worker session: the shared checkout is the lead's (rules/agent-team-operation.md Workers). Do not sync it — work in this worktree and report the drift."
    return 0
  fi

  # Resolve the clock. A test may inject SYNC_NOW; otherwise read the system clock
  # and handle its failure. Validate as an integer before any arithmetic so a
  # malformed value can't abort the hook under set -e.
  if [[ -n "${SYNC_NOW:-}" ]]; then
    now="$SYNC_NOW"
  elif ! now="$(date +%s)"; then
    warn "cannot read the system clock — skipping sync check"
    return 0
  fi
  if ! [[ "$now" =~ ^[0-9]+$ ]]; then
    warn "clock value '${now}' is not an integer — unset SYNC_NOW; skipping sync check"
    return 0
  fi

  # Per-repo throttle stamp, keyed by the toplevel path so sibling clones throttle
  # independently. cksum gives a stable filename-safe key for an arbitrary path.
  if ! top="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    warn "git rev-parse --show-toplevel failed — keying the throttle stamp on \$PWD instead"
    top="$PWD"
  fi
  # A cksum/cut failure would abort the assignment under set -e and break the
  # always-exit-0 contract, so handle it: fall back to an un-throttled run.
  if ! repo_key="$(printf '%s' "$top" | cksum | cut -d' ' -f1)"; then
    warn "could not derive a throttle key for ${top} — sync check will not throttle this run"
    repo_key=""
  fi
  stamp="${STATE_DIR}/sync-${repo_key}"

  # Throttle stamp schema (see hooks/state-schema.md): one line "<schema_version>
  # <checked_at-epoch>". Per rules/stateful-artifacts.md Migration Policy:
  #   - schema_version 1 within the window => throttle (skip the fetch).
  #   - a future version (sv > 1) => this hook is lagging: no usable prior state
  #     (fetch), and DO NOT downgrade the record — preserve it (preserve_future).
  #   - anything else (old bare-integer, corrupt, absent) => no prior state
  #     (fetch), safe to rewrite as version 1.
  # With no throttle key the fallback fetches unconditionally (never throttles).
  should_fetch=1
  preserve_future=0
  fetch_failed=0
  if [[ -n "$repo_key" && -r "$stamp" ]]; then
    sv=""; ts=""
    read -r sv ts < "$stamp" || { sv=""; ts=""; }
    if [[ "$sv" =~ ^[0-9]+$ ]] && (( sv > 1 )); then
      preserve_future=1
    elif [[ "$sv" == "1" && "$ts" =~ ^[0-9]+$ ]] && (( now - ts < THROTTLE_HOURS * 3600 )); then
      should_fetch=0
    fi
  fi

  if (( should_fetch )); then
    # Record the fetch up front so a slow/failed fetch still throttles the next
    # session rather than retrying the network on every start. Skipped when no
    # throttle key was derived, and when a future-version record must be
    # preserved rather than downgraded.
    if [[ -n "$repo_key" ]] && (( preserve_future == 0 )); then
      if mkdir -p "$STATE_DIR"; then
        printf '1 %s\n' "$now" > "$stamp" ||
          warn "cannot write throttle stamp ${stamp} — check permissions on ${STATE_DIR}; will re-fetch next session"
      else
        warn "cannot create state dir ${STATE_DIR} — check permissions or set SYNC_STATE_DIR; sync check will not throttle"
      fi
    fi

    # Zero or a non-number would switch the bound off; fall back to the default.
    [[ "$FETCH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || FETCH_TIMEOUT=10
    build_fetch "$FETCH_TIMEOUT"
    # A fetch failure (offline, auth, timeout) is a no-op, not a broken session —
    # warn and let the unverified-sync branch below report it. Comparing against
    # a remote-tracking ref no fetch refreshed would risk a false "in sync"
    # (rules/sync-before-work.md Staleness Poisons Conclusions).
    "${FETCH_CMD[@]}" 2>/dev/null || {
      warn "git fetch origin failed or timed out — check connectivity; cannot verify sync against a current origin/${db}"
      fetch_failed=1
    }
  fi

  # A definitive sync conclusion requires a fresh fetch this session. A throttled
  # or failed fetch leaves the remote-tracking ref possibly stale, and stale
  # state poisons conclusions (rules/sync-before-work.md). Report unverified
  # rather than a false "in sync".
  if (( should_fetch == 0 || fetch_failed )); then
    emit_notice "Session-start status — git: \`${db}\` sync not verified against a current \`origin/${db}\` this session — run \`git fetch origin\`, then \`git status\` (rules/sync-before-work.md)."
    return 0
  fi

  drift_counts "$db" || return 0
  ahead="$DRIFT_AHEAD"
  behind="$DRIFT_BEHIND"

  # Success path: nothing to pull from origin. Distinguish truly in-sync from
  # ahead-only — a branch ahead of origin (unpushed commits) is not "in sync".
  if (( behind == 0 )); then
    if (( ahead == 0 )); then
      emit_notice "Session-start status — git: local \`${db}\` is in sync with \`origin/${db}\`"
    else
      emit_notice "Session-start status — git: local \`${db}\` is ${ahead} commit(s) ahead of \`origin/${db}\` (unpushed), none behind"
    fi
    return 0
  fi

  # Diverged needs judgment (which side wins), so it stays a report.
  if (( ahead > 0 )); then
    emit_notice "Session-start status — Local \`${db}\` has diverged from \`origin/${db}\` (${behind} behind, ${ahead} ahead) — reconcile before working (rules/sync-before-work.md): \`git fetch origin\`, then rebase \`${db}\` onto \`origin/${db}\` (a fast-forward won't apply)."
    return 0
  fi

  # Strictly behind after a fresh fetch. Under tessl the environment is
  # stripped, so report rather than write.
  if [[ "${SESSION_START_MODE:-}" == portable ]]; then
    FF_WHY="SessionStart ran through tessl, which hides the session's environment"
  elif fast_forward "$db"; then
    emit_notice "Session-start status — git: fast-forwarded local \`${db}\` by ${behind} commit(s) to \`origin/${db}\`"
    return 0
  fi
  emit_notice "Session-start status — Local \`${db}\` is ${behind} commit(s) behind \`origin/${db}\` and was left unmoved (${FF_WHY}) — sync before working (rules/sync-before-work.md): fast-forward \`${db}\` to \`origin/${db}\`."
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): run only when
# executed, so the script can also be sourced to unit-test its functions.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
