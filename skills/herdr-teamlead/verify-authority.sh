#!/usr/bin/env bash
# Answer whether the operator owns the repo a round is about to write in.
#
# `rules/external-repo-contributions.md` Default Deny turns on ownership, and
# ownership is a fact about the namespace, not a feeling about the task: the
# operator is the repo's namespace owner (their user account, or an org they
# administer). A collaborator with write permission is NOT an owner, so
# `viewer_permission` is reported and never decides.
#
# Contract:
#   argv  : <owner/repo>
#   stdout: one JSON object —
#           {"repo":"<owner/repo>",
#            "viewer_login":"<login>",
#            "owner_login":"<login>",
#            "owner_type":"User|Organization",
#            "viewer_permission":"admin|maintain|write|triage|read|none",
#            "namespace_owner":<bool>,
#            "authorized":<bool>}
#           `authorized` mirrors `namespace_owner`. Permission never sets it.
#   stderr: diagnostics only.
#   exit  : 0 verdict emitted (authorized either way),
#           1 precondition unmet (usage, `gh` or `jq` absent, `gh` not logged
#             in — checked up front with `gh auth status`, so an auth problem
#             is never reported as an API fault),
#           2 the GitHub API could not answer — never a verdict.
#   env   : GH_BIN overrides the gh binary; the tests point it at a fake.
set -euo pipefail

GH_BIN="${GH_BIN:-gh}"

ERRFILE=""

warn() { printf 'verify-authority: %s\n' "$1" >&2; }

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

# Echo the JSON body of one gh api path, or return 2 with a diagnostic.
api() { # <path>
  local rc=0 out
  out="$("$GH_BIN" api "$1" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    warn "\`${GH_BIN} api $1\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${GH_BIN} auth status\` to check the session"
    return 2
  fi
  printf '%s' "$out"
  return 0
}

main() {
  if (( $# != 1 )); then
    warn "usage: verify-authority.sh <owner/repo>"
    return 1
  fi
  local slug="$1"
  if [[ ! "$slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    warn "'${slug}' is not an <owner>/<repo> slug — pass the full slug, e.g. gamussa/coding-policy"
    return 1
  fi

  local dep
  for dep in "$GH_BIN" jq; do
    if ! command -v "$dep" >/dev/null 2>&1; then
      warn "'${dep}' not found on PATH — install it to verify repo authority"
      return 1
    fi
  done

  ERRFILE="$(mktemp)"
  trap cleanup EXIT

  # Logged-in is a precondition, not an API answer: check it first so a missing
  # session exits 1 with the fix, instead of surfacing as exit 2 from the
  # first `gh api` call and reading as "GitHub could not answer".
  local auth_rc=0
  "$GH_BIN" auth status >/dev/null 2>"$ERRFILE" || auth_rc=$?
  if (( auth_rc != 0 )); then
    warn "not logged in to GitHub (\`${GH_BIN} auth status\` exit ${auth_rc}): $(tr '\n' ' ' < "$ERRFILE") — run \`${GH_BIN} auth login\` and retry"
    return 1
  fi

  local rc=0 viewer repo
  viewer="$(api user)" || rc=$?
  if (( rc != 0 )); then return 2; fi
  repo="$(api "repos/${slug}")" || rc=$?
  if (( rc != 0 )); then return 2; fi

  local viewer_login owner_login owner_type permission
  rc=0
  viewer_login="$(printf '%s' "$viewer" | jq -r '.login // ""')" || rc=$?
  owner_login="$(printf '%s' "$repo" | jq -r '.owner.login // ""')" || rc=$?
  owner_type="$(printf '%s' "$repo" | jq -r '.owner.type // "unknown"')" || rc=$?
  # The permission block is present only for an authenticated viewer with any
  # access; its absence is "none", never an error.
  permission="$(printf '%s' "$repo" | jq -r '
    if (.permissions | type) != "object" then "none"
    elif .permissions.admin then "admin"
    elif .permissions.maintain then "maintain"
    elif .permissions.push then "write"
    elif .permissions.triage then "triage"
    elif .permissions.pull then "read"
    else "none" end')" || rc=$?
  if (( rc != 0 )) || [[ -z "$viewer_login" || -z "$owner_login" ]]; then
    warn "could not read the login/permission fields from the GitHub payloads — run \`${GH_BIN} api repos/${slug}\` and inspect them"
    return 2
  fi

  # Personal namespace: the repo's owner IS the viewer. GitHub logins are
  # case-insensitive, so compare case-folded.
  local owner=false
  if [[ "$(printf '%s' "$viewer_login" | tr '[:upper:]' '[:lower:]')" == "$(printf '%s' "$owner_login" | tr '[:upper:]' '[:lower:]')" ]]; then
    owner=true
  elif [[ "$owner_type" == "Organization" ]]; then
    # Org namespace: ownership means administering the org. A 404 here is the
    # documented "not a member" answer, not a failure.
    local role="" membership_rc=0
    role="$("$GH_BIN" api "orgs/${owner_login}/memberships/${viewer_login}" --jq '.role // ""' 2>"$ERRFILE")" || membership_rc=$?
    if (( membership_rc == 0 )) && [[ "$role" == "admin" ]]; then
      owner=true
    elif (( membership_rc != 0 )); then
      # A 404 IS the answer for a non-member, and it is the common case for
      # any org the operator does not belong to. Warning on it would cry wolf
      # on every ordinary run. Anything else means the API did not answer,
      # and an unanswered question is exit 2, never a false verdict: a
      # transient fault must not read as "the operator does not own this".
      if grep -q '404' "$ERRFILE"; then
        : # not a member — not an owner, nothing to report
      else
        warn "could not read org membership for ${viewer_login} in ${owner_login} (exit ${membership_rc}): $(tr '\n' ' ' < "$ERRFILE") — no verdict; check \`gh auth status\` and the network, then rerun"
        return 2
      fi
    fi
  fi

  jq -n \
    --arg repo "$slug" \
    --arg viewer "$viewer_login" \
    --arg owner_login "$owner_login" \
    --arg owner_type "$owner_type" \
    --arg permission "$permission" \
    --argjson namespace_owner "$owner" \
    '{repo: $repo,
      viewer_login: $viewer,
      owner_login: $owner_login,
      owner_type: $owner_type,
      viewer_permission: $permission,
      namespace_owner: $namespace_owner,
      authorized: $namespace_owner}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
