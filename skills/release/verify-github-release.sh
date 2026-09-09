#!/usr/bin/env bash
# Answer ONE question, definitively: did run <run-id> publish a
# retrievable GitHub release at <tag> on <owner>/<repo>?
#
# This is the non-Tessl channel's published-artifact evidence
# (rules/ci-safety.md "Always Watch CI"). A package published by pushing
# a release tag — an ACR package published by `acr publish` from a
# tag-triggered workflow — has no registry to advance and no moderation
# state to clear, so the Tessl helpers say nothing about whether it
# landed. What answers that is the release itself: it exists at the
# exact tag the run published, it is not a draft, and its assets are
# retrievable.
#
# Two conjuncts, both required, neither substituting for the other —
# the tag/asset counterpart of verify-publish-landed.sh's pair:
#   1. The resolved run's `conclusion` is `success`
#   2. The release exists at the exact tag, is published, and every
#      asset is retrievable
# Conjunct 1 alone passes a run that uploaded assets and then failed a
# later step. Conjunct 2 alone passes an upload that never completed, a
# draft left unpublished, or a release created at a different tag.
#
# The run id comes from `resolve-publish-run.sh`; `gh run watch` on it
# is a timing precondition, never the gate. A run still in flight is
# INDETERMINATE (exit 2), the same reading verify-publish-landed.sh
# gives a `null` conclusion.
#
# Asset retrievability is read from GitHub's own `state` field: an asset
# mid-upload or failed reports something other than `uploaded`, and the
# download it fronts 404s. A release with zero assets is reported as a
# failed conjunct, never as a vacuous pass.
#
# Discriminating absent from indeterminate, the same way
# registry-has-version.sh does: `gh api` exits non-zero on a 404 AND on
# an auth or network failure, so a non-zero exit alone is not "absent".
# The `HTTP 404` in gh's diagnostic is the discriminator. Anything else
# is INDETERMINATE and exits 2 — a caller that cannot tell must never
# claim the artifact landed (fail closed).
#
# jq is deliberately NOT used: the field extraction runs inside gh's own
# `--jq`, so this script carries no system-jq dependency (same reasoning
# as resolve-publish-run.sh).
#
# Every string interpolated into the envelope goes through
# json_escape, so a tag carrying a quote or a backslash emits valid
# JSON rather than a broken object (rules/script-delegation.md Script
# Requirements, "JSON-producing"). Each definitive-no branch also writes
# an actionable stderr diagnostic before exiting, per that rule's
# "self-error-handling" and rules/error-handling.md Actionable Messages.
#
# Usage: verify-github-release.sh <owner> <repo> <tag> <run-id>
# Out:   one JSON object on stdout —
#          rc 0: {"ok":true,"tag":"...","assets":N,"url":"...","run_conclusion":"success"}
#          rc 1: {"ok":false,"reason":"...","tag":"...","assets":N,"run_conclusion":"..."}
#        A diagnostic accompanies every non-zero exit on stderr. On exit
#        2 stdout is empty.
# Exit:  0 the run succeeded, the release is published, and every asset
#          is retrievable;
#        1 a definitive no (run conclusion not `success`, or the release
#          is absent, draft, empty, or carries an asset not in the
#          `uploaded` state);
#        2 indeterminate (gh absent or unreachable, run still in flight,
#          unparseable body) or a usage error

set -euo pipefail

# Script-global, NOT a main() local: the EXIT trap fires after main()
# returns, when a main-local would be out of scope and `set -u` would
# turn cleanup into an unbound-variable failure.
VGR_ERR_FILE=""

# EXIT-trap cleanup. `return 0` is load-bearing: an EXIT trap's final
# command status becomes the script's exit status, so a failing `rm`
# would rewrite a deliberate `exit 1` (rules/error-handling.md Shell
# Error Handling).
cleanup_vgr_err_file() {
  if [[ -n "${VGR_ERR_FILE:-}" ]]; then
    if ! rm -f "$VGR_ERR_FILE"; then
      echo "verify-github-release.sh: warning: could not remove temp file ${VGR_ERR_FILE} — remove it by hand" >&2
    fi
    VGR_ERR_FILE=""
  fi
  return 0
}
trap cleanup_vgr_err_file EXIT

# Escape one string for embedding in a JSON string literal. A control
# character has no place in any value this script emits, and encoding it
# would need \uXXXX, so it is refused rather than silently mangled.
#
# It RETURNS 2 rather than exiting: callers run it in a command
# substitution, where an `exit` would end only that subshell and leave
# the caller printing an envelope built from an empty string. Every
# caller checks the status and propagates it (rules/error-handling.md
# Shell Error Handling — a discarded exit status is suppression).
json_escape() {
  local value="$1"
  if [[ "$value" == *[[:cntrl:]]* ]]; then
    echo "error: value contains a control character and cannot be emitted as JSON: '${value}' — pass a tag and repository free of control characters" >&2
    return 2
  fi
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

# Encode a tag as one URL path component. Git permits URL delimiters and
# percent signs in refs; sending those raw can request a different tag.
# Byte-wise encoding keeps UTF-8 intact without a runtime jq dependency.
url_encode_tag() {
  local LC_ALL=C value="$1" encoded="" char byte hex i
  for (( i=0; i<${#value}; i++ )); do
    char="${value:i:1}"
    case "$char" in
      [a-zA-Z0-9.~_-]) encoded+="$char" ;;
      *)
        # Bash 3.2 sign-extends high bytes; normalize the ordinal to 0..255.
        if ! printf -v byte '%d' "'$char" ||
           ! printf -v hex '%%%02X' "$(( byte & 0xFF ))"; then
          echo "error: could not encode tag — check Bash printf support and re-run this check" >&2
          return 2
        fi
        encoded+="$hex"
        ;;
    esac
  done
  printf '%s' "$encoded"
}

# One definitive no: the actionable stderr diagnostic first, then the
# structured envelope on stdout, then exit 1. Both surfaces carry the
# finding — a wrapper parsing stdout and an operator reading stderr each
# get an answer. An escaping failure preempts both and exits 2 with
# stdout untouched, the contract's indeterminate answer.
deny() {
  local reason="$1" tag="$2" assets="$3" conclusion="$4" hint="$5"
  echo "verify-github-release.sh: ${reason} — ${hint}" >&2
  local esc_reason esc_tag esc_conclusion rc=0
  esc_reason=$(json_escape "$reason") || rc=$?
  (( rc == 0 )) || exit "$rc"
  esc_tag=$(json_escape "$tag") || rc=$?
  (( rc == 0 )) || exit "$rc"
  esc_conclusion=$(json_escape "$conclusion") || rc=$?
  (( rc == 0 )) || exit "$rc"
  printf '{"ok":false,"reason":"%s","tag":"%s","assets":%s,"run_conclusion":"%s"}\n' \
    "$esc_reason" "$esc_tag" "$assets" "$esc_conclusion"
  exit 1
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <owner> <repo> <tag> <run-id>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" tag="$3" run_id="$4"
  if [[ -z "$owner" || -z "$repo" || -z "$tag" ]]; then
    echo "error: owner, repo and tag must all be non-empty — got '${owner}' '${repo}' '${tag}'" >&2
    exit 2
  fi
  # Same positive-integer shape resolve-publish-run.sh emits and
  # verify-publish-landed.sh accepts; a bare zero is not a run id.
  if ! [[ "$run_id" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: run-id must be a positive integer, got: '${run_id}' — take it from 'skills/release/resolve-publish-run.sh ... | jq -r .database_id'" >&2
    exit 2
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "error: gh not found on PATH — install the GitHub CLI (https://cli.github.com) and run 'gh auth login'" >&2
    exit 2
  fi

  VGR_ERR_FILE=$(mktemp -t verify-github-release.XXXXXX)

  # Conjunct 1 — the resolved run's own conclusion.
  local conclusion rc=0
  conclusion=$(gh run view "$run_id" --repo "${owner}/${repo}" --json conclusion --jq '.conclusion' 2>"$VGR_ERR_FILE") || rc=$?
  if (( rc != 0 )); then
    echo "error: could not read run ${run_id} in ${owner}/${repo} — $(cat "$VGR_ERR_FILE")" >&2
    echo "check 'gh auth status', then retry 'gh run view ${run_id} --repo ${owner}/${repo} --json conclusion'" >&2
    exit 2
  fi
  # `gh run view --jq .conclusion` prints the literal `null` for a run
  # that has not finished. Treating that as "not success" would report a
  # failed publish against a run still in flight.
  if [[ -z "$conclusion" || "$conclusion" == "null" ]]; then
    echo "error: run ${run_id} in ${owner}/${repo} has not reached a terminal state — run 'gh run watch ${run_id}' first, then re-run this check" >&2
    exit 2
  fi
  if [[ "$conclusion" != "success" ]]; then
    deny "publish run ${run_id} concluded ${conclusion}" "$tag" 0 "$conclusion" \
      "inspect 'gh run view ${run_id} --repo ${owner}/${repo} --log-failed' and fix the failing step before re-releasing"
  fi

  # Conjunct 2 — the release the run was supposed to create.
  local row encoded_tag
  encoded_tag=$(url_encode_tag "$tag") || return $?
  rc=0
  row=$(gh api "repos/${owner}/${repo}/releases/tags/${encoded_tag}" \
    --jq '[.tag_name, (.draft|tostring), ([.assets[]|select(.state == "uploaded")]|length|tostring), (.assets|length|tostring), .html_url] | @tsv' \
    2>"$VGR_ERR_FILE") || rc=$?

  if (( rc != 0 )); then
    local gh_err
    gh_err=$(cat "$VGR_ERR_FILE")
    if [[ "$gh_err" == *"HTTP 404"* ]]; then
      deny "no release exists at tag ${tag} in ${owner}/${repo}" "$tag" 0 "$conclusion" \
        "the publish run succeeded without creating the release — inspect 'gh run view ${run_id} --repo ${owner}/${repo} --log' for the publish step's own output"
    fi
    echo "error: could not read repos/${owner}/${repo}/releases/tags/${tag} — ${gh_err}" >&2
    echo "check 'gh auth status', then retry 'gh api repos/${owner}/${repo}/releases/tags/${tag}'" >&2
    exit 2
  fi

  local actual_tag draft uploaded total url
  IFS=$'\t' read -r actual_tag draft uploaded total url <<<"$row"

  if [[ -z "$actual_tag" || -z "$draft" || -z "$uploaded" || -z "$total" ]]; then
    echo "error: unparseable release payload for tag ${tag} in ${owner}/${repo}, got: '${row}' — inspect 'gh api repos/${owner}/${repo}/releases/tags/${tag}' to diagnose" >&2
    exit 2
  fi

  # A caller that asked for a version must be told about the version it
  # got, never about a near-miss the API resolved on its behalf.
  if [[ "$actual_tag" != "$tag" ]]; then
    deny "release at ${tag} reports tag ${actual_tag}" "$tag" "$total" "$conclusion" \
      "confirm which tag the publish run pushed, then re-check against that tag"
  fi
  if [[ "$draft" == "true" ]]; then
    deny "release ${tag} is still a draft and is not publicly retrievable" "$tag" "$total" "$conclusion" \
      "publish the draft, or fix the publish step so it creates a published release"
  fi
  if [[ "$total" == "0" ]]; then
    deny "release ${tag} carries no assets" "$tag" 0 "$conclusion" \
      "inspect the publish step's upload output in 'gh run view ${run_id} --repo ${owner}/${repo} --log'"
  fi
  if [[ "$uploaded" != "$total" ]]; then
    deny "${uploaded} of ${total} assets on release ${tag} are in the uploaded state" "$tag" "$total" "$conclusion" \
      "re-check shortly if an upload is still finishing, otherwise re-run the publish workflow"
  fi

  local esc_tag esc_url esc_conclusion
  esc_tag=$(json_escape "$tag") || return $?
  esc_url=$(json_escape "$url") || return $?
  esc_conclusion=$(json_escape "$conclusion") || return $?
  printf '{"ok":true,"tag":"%s","assets":%s,"url":"%s","run_conclusion":"%s"}\n' \
    "$esc_tag" "$total" "$esc_url" "$esc_conclusion"
  return 0
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
