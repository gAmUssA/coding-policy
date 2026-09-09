#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/compose-briefs.sh.
#
# Templates and values are built in the test, so the assertions are about the
# script's own behavior rather than about the shipped templates' current text
# (rules/testing-standards.md — fixtures built in setup, no binary fixtures).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Substitution     -> shared + per-role values land in the right files.
#   2. Role wins        -> a role value overrides the same shared key.
#   3. Emitted paths    -> the JSON names COMMON.md and every brief.
#   4. Unfilled         -> a placeholder with no value is exit 2, nothing written.
#   5. Unknown key      -> a value no template uses is exit 2, nothing written.
#   6. Atomicity        -> a failing second role leaves the first unwritten.
#   7. Missing template -> exit 1 naming the path.
#   8. Bad JSON         -> exit 1.
#   9. Idempotent       -> a second identical run rewrites the same content.
#  10. Usage            -> exit 1 with a usage line.
#  11. Broken grep      -> a failing scan is exit 3, never "no placeholders".
#  12. Null value       -> exit 2, nothing written (a `null` would render as
#                          the literal text and leave no placeholder behind).
#  13. Object value     -> exit 2 for the same reason.
#  14. Number value     -> accepted; an issue number is legitimate text.
#
# Run: bash skills/herdr-teamlead/tests/test_compose_briefs.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_templates() { # <dir>
  mkdir -p "$1" || die "could not create $1"
  printf 'Checkout: {{SHARED_CHECKOUT}}\nAuthority: {{AUTHORITY_STATEMENT}}\n' > "$1/COMMON.md" \
    || die "could not write COMMON.md"
  printf 'Dev on {{BRANCH}} in {{WORKTREE}} for {{ISSUE}}\nReport: {{REPORT}}\n' > "$1/brief-developer.md" \
    || die "could not write brief-developer.md"
  printf 'Tester for {{ISSUE}}\nReport: {{REPORT}}\nPackage: {{REVIEW_PACKAGE}}\nRange: {{REVIEW_BASE}}..{{REVIEW_HEAD}}\n' > "$1/brief-tester.md" \
    || die "could not write brief-tester.md"
}

run() { # <templates> <values-file> <outdir>
  RUN_SEQ=$((RUN_SEQ+1))
  local values="$2"
  # Generic rendering fixtures supply a valid package. The dedicated package
  # suite exercises missing/invalid paths through the unwrapped production CLI.
  if [[ "$1" == "$TPL" ]] && jq -e . "$values" >/dev/null 2>&1; then
    values="$TMP/values.$RUN_SEQ.json"
    jq --arg p "$TMP/package.diff" \
      'if .roles | has("tester") then .roles.tester += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"} else . end' \
      "$2" > "$values" || die "could not prepare rendering fixture"
  fi
  OUT="$(bash "$SCRIPT" "$1" "$values" "$3" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compose-briefs.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "compose-briefs.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t teamlead-compose-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  printf 'Review fixture\n' > "$TMP/package.diff" || die "could not write package fixture"
  TPL="$TMP/templates"; mk_templates "$TPL"
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1 + 3. A complete round.
  local v1="$TMP/v1.json" o1="$TMP/out1"
  cat > "$v1" <<'JSON' || die "could not write $v1"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "owner of jbaruch/x"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt/dev", "ISSUE": "#7", "REPORT": "/r/dev.md"},
    "tester": {"ISSUE": "#7", "REPORT": "/r/test.md"}
  }
}
JSON
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] \
     && grep -q "Checkout: /repo" "$o1/COMMON.md" \
     && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md" \
     && grep -q "Report: /r/test.md" "$o1/brief-tester.md"; then
    pass; else fail "substitution: got RC=$RC ERR=$ERRTEXT"; fi
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg o "$o1" '
      .common == ($o + "/COMMON.md")
      and .briefs.developer == ($o + "/brief-developer.md")
      and .briefs.tester == ($o + "/brief-tester.md")' >/dev/null 2>&1; then
    pass; else fail "emitted paths: got OUT=$OUT"; fi

  # 2. A role value beats the shared one.
  local v2="$TMP/v2.json" o2="$TMP/out2"
  cat > "$v2" <<'JSON' || die "could not write $v2"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a", "ISSUE": "#1", "REPORT": "/shared.md"},
  "roles": {"tester": {"ISSUE": "#9"}}
}
JSON
  run "$TPL" "$v2" "$o2"
  if [[ $RC -eq 0 ]] && grep -q "Tester for #9" "$o2/brief-tester.md"; then
    pass; else fail "role override: got RC=$RC ERR=$ERRTEXT"; fi

  # 4. A placeholder with no value is a brief that lies to a worker.
  local v4="$TMP/v4.json" o4="$TMP/out4"
  cat > "$v4" <<'JSON' || die "could not write $v4"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"developer": {"BRANCH": "feat/x", "ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v4" "$o4"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "WORKTREE"; then
    pass; else fail "unfilled: expected exit 2 naming WORKTREE, got RC=$RC ERR=$ERRTEXT"; fi
  if [[ ! -e "$o4/brief-developer.md" ]]; then
    pass; else fail "unfilled: nothing may be written on a validation failure"; fi

  # 5. A key no template uses is a value the lead believes it sent.
  local v5="$TMP/v5.json" o5="$TMP/out5"
  cat > "$v5" <<'JSON' || die "could not write $v5"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "/r.md", "REPORTS_DIRR": "/typo"}}
}
JSON
  run "$TPL" "$v5" "$o5"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "REPORTS_DIRR"; then
    pass; else fail "unknown key: expected exit 2 naming the typo, got RC=$RC ERR=$ERRTEXT"; fi

  # 6. A round is all-or-nothing: a later role failing leaves no earlier file.
  local v6="$TMP/v6.json" o6="$TMP/out6"
  cat > "$v6" <<'JSON' || die "could not write $v6"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {
    "developer": {"BRANCH": "feat/x", "WORKTREE": "/wt", "ISSUE": "#7", "REPORT": "/r.md"},
    "tester": {"ISSUE": "#7"}
  }
}
JSON
  run "$TPL" "$v6" "$o6"
  if [[ $RC -eq 2 ]] && [[ ! -e "$o6/brief-developer.md" ]] && [[ ! -e "$o6/COMMON.md" ]]; then
    pass; else fail "atomicity: a failed round left files behind (RC=$RC)"; fi

  # 6b. The PACKAGED templates compose from the values Step 7 documents — for
  #     every shipped role, the release brief included. A template that grows
  #     a placeholder nobody documented fails here before it fails a worker.
  local v6b="$TMP/v6b.json" o6b="$TMP/out6b" PKG
  PKG="$(dirname "$SCRIPT")/templates"
  cat > "$v6b" <<'JSON' || die "could not write $v6b"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "owner of jbaruch/x",
             "EXTERNAL_PERMISSION": "none", "TASK_AUTHORIZATION": "Operator request: ship issue #7 in jbaruch/x",
             "AUTHORIZED_ACTIONS": "jbaruch/x: implement, push, create PR, request reviews, reply to review threads, merge, publish, clean up the released branch",
             "ISSUE": "#7", "BRANCH": "feat/x"},
  "roles": {
    "developer": {"WORKTREE": "/wt/dev", "REPORTS_DIR": "/r", "REPORT": "/r/dev.md"},
    "reviewer": {"REPORT": "/r/review.md"},
    "tester": {"WORKTREE": "/wt/test", "REPORTS_DIR": "/r", "REPORT": "/r/test.md"},
    "release": {"WORKTREE": "/wt/dev", "REPORTS_DIR": "/r", "REPORT": "/r/release.md"}
  }
}
JSON
  jq --arg p "$TMP/package.diff" \
    '.shared += {POLICY_INDEX: $p, RELEASE_SKILL: $p}
     | .roles.reviewer += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
     | .roles.tester += {REVIEW_PACKAGE: $p, REVIEW_BASE: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", REVIEW_HEAD: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}' \
    "$v6b" > "$TMP/packaged-values.json" || die "could not add packaged review paths"
  v6b="$TMP/packaged-values.json"
  run "$PKG" "$v6b" "$o6b"
  if [[ $RC -eq 0 ]] && grep -q 'cd /wt/dev && pwd' "$o6b/brief-release.md" \
     && grep -q 'Skill(skill: "release")' "$o6b/brief-release.md"; then
    pass; else fail "packaged templates: expected exit 0 and a filled release brief, got RC=$RC ERR=$ERRTEXT"; fi

  # The actual packaged artifacts must distinguish the owner's shipping
  # authorization from additional permission needed in a non-owned repo.
  if [[ $RC -eq 0 ]] \
     && grep -Fq 'Verified repo ownership: **owner of jbaruch/x**' "$o6b/COMMON.md" \
     && grep -Fq 'Operator request: ship issue #7 in jbaruch/x' "$o6b/COMMON.md" \
     && grep -Fq 'jbaruch/x: implement, push, create PR, request reviews, reply to review threads, merge, publish, clean up the released branch' "$o6b/COMMON.md" \
     && grep -Fq 'An owned repository requires no additional non-owner permission;' "$o6b/COMMON.md" \
     && grep -Fq 'its authorized task actions still bind you.' "$o6b/COMMON.md"; then
    pass; else fail "owner release must retain its actual task authorization with no additional non-owner permission"; fi

  local ownership authority_values authority_output
  for ownership in 'owner of jbaruch/x' 'not owner of jbaruch/x'; do
    authority_values="$TMP/authority-${ownership%% *}.json"
    authority_output="$TMP/authority-${ownership%% *}"
    jq --arg ownership "$ownership" \
      '.shared.AUTHORITY_STATEMENT = $ownership
       | .shared.TASK_AUTHORIZATION = "Operator request: inspect issue #7; do not change the repository"
       | .shared.AUTHORIZED_ACTIONS = "none" | .shared.EXTERNAL_PERMISSION = "none"' \
      "$v6b" > "$authority_values" || die "could not build read-only authority fixture"
    run "$PKG" "$authority_values" "$authority_output"
    if [[ $RC -eq 0 ]] \
       && grep -Fq "Verified repo ownership: **$ownership**" "$authority_output/COMMON.md" \
       && grep -Fq 'Authorized task actions and target repo this round: **none**' "$authority_output/COMMON.md" \
       && grep -Fq 'make no repository changes or GitHub writes' "$authority_output/COMMON.md" \
       && grep -Fq 'In a non-owned repository, every write also requires' "$authority_output/COMMON.md" \
       && grep -Fq 'This role grants no additional permission.' "$authority_output/brief-release.md" \
       && grep -Fq 'report BLOCKED' "$authority_output/brief-release.md"; then
      pass; else fail "read-only $ownership must deny writes even in an accidentally selected release role: RC=$RC ERR=$ERRTEXT"; fi
  done

  # A lead using the old values cannot silently omit task authorization.
  local missing_authority
  for missing_authority in TASK_AUTHORIZATION AUTHORIZED_ACTIONS; do
    jq --arg key "$missing_authority" 'del(.shared[$key])' "$v6b" > "$TMP/authority-missing.json" \
      || die "could not build missing authority fixture"
    run "$PKG" "$TMP/authority-missing.json" "$TMP/missing-$missing_authority"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/missing-$missing_authority" && "$ERRTEXT" == *"$missing_authority"* ]]; then
      pass; else fail "missing $missing_authority must refuse before emitting any brief"; fi
  done

  local missing_policy
  for missing_policy in POLICY_INDEX RELEASE_SKILL; do
    jq --arg key "$missing_policy" '.shared[$key] = "/absent/policy-artifact.md"' "$v6b" > "$TMP/policy-missing.json" \
      || die "could not build absent policy fixture"
    run "$PKG" "$TMP/policy-missing.json" "$TMP/missing-$missing_policy"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/missing-$missing_policy" && "$ERRTEXT" == *"$missing_policy"* ]]; then
      pass; else fail "unreadable $missing_policy must refuse before emitting any brief"; fi
  done

  # Policy artifacts are shared inputs; role overrides cannot bypass their
  # validation, even when the override is readable or the shared key is absent.
  local override_case override_value
  for missing_policy in POLICY_INDEX RELEASE_SKILL; do
    for override_case in invalid readable role-only; do
      override_value="/absent/role-policy.md"
      if [[ "$override_case" == readable ]]; then override_value="$TMP/package.diff"; fi
      jq --arg key "$missing_policy" --arg value "$override_value" --arg shape "$override_case" \
        '.roles.tester[$key] = $value | if $shape == "role-only" then del(.shared[$key]) else . end' \
        "$v6b" > "$TMP/policy-override.json" || die "could not build role policy override"
      run "$PKG" "$TMP/policy-override.json" "$TMP/override-$missing_policy-$override_case"
      if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/override-$missing_policy-$override_case" && "$ERRTEXT" == *"$missing_policy"* ]]; then
        pass; else fail "$override_case role $missing_policy must refuse before emitting any brief: $ERRTEXT"; fi
    done
  done

  # 6c. A REPORT path that would wrap the worker's marker line is refused
  #     before any file is written: the wait confirms the complete marker on
  #     one visible row, and a wrap anywhere in it cannot be confirmed.
  local v6c="$TMP/v6c.json" o6c="$TMP/out6c" long_report
  long_report="/very/long/reports/directory/that/keeps/going/and/going/round-3/reports/developer-report-for-issue-12.md"
  cat > "$v6c" <<JSON || die "could not write $v6c"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "${long_report}"}}
}
JSON
  run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "REPORT for role 'tester'" && [[ ! -e "$o6c" ]]; then
    pass; else fail "long REPORT: expected exit 2 naming the role and no output dir at all, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  TEAMLEAD_REPORT_PATH_MAX_COLS=200 run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 0 ]]; then
    pass; else fail "long REPORT under a raised limit: expected exit 0, got RC=$RC ERR=$ERRTEXT"; fi
  # 6e. A leading-zero override is decimal downstream: `0200` raises the limit
  #     like `200` does, with no octal reparse.
  TEAMLEAD_REPORT_PATH_MAX_COLS=0200 run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 0 ]] && ! printf '%s' "$ERRTEXT" | grep -q "value too great"; then
    pass; else fail "leading-zero limit: expected exit 0 and no octal error, got RC=$RC ERR=$ERRTEXT"; fi

  # 6d. A bad limit override is a precondition failure, never an arithmetic abort.
  TEAMLEAD_REPORT_PATH_MAX_COLS=soon run "$TPL" "$v6c" "$o6c"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "TEAMLEAD_REPORT_PATH_MAX_COLS must be a positive integer"; then
    pass; else fail "bad limit override: expected exit 1 naming it, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 7. A role with no template is named, not guessed at.
  local v7="$TMP/v7.json" o7="$TMP/out7"
  cat > "$v7" <<'JSON' || die "could not write $v7"
{"shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"}, "roles": {"scribe": {}}}
JSON
  run "$TPL" "$v7" "$o7"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "brief-scribe.md"; then
    pass; else fail "missing template: expected exit 1 naming it, got RC=$RC ERR=$ERRTEXT"; fi

  # 8. Malformed values.
  local v8="$TMP/v8.json"
  printf '{broken' > "$v8" || die "could not write $v8"
  run "$TPL" "$v8" "$TMP/out8"
  if [[ $RC -eq 1 && -z "$OUT" ]]; then
    pass; else fail "bad JSON: expected exit 1, got RC=$RC"; fi

  # 9. Re-running a round rewrites the same content (idempotent).
  run "$TPL" "$v1" "$o1"
  if [[ $RC -eq 0 ]] && grep -q "Dev on feat/x in /wt/dev for #7" "$o1/brief-developer.md"; then
    pass; else fail "idempotent: second run changed the output (RC=$RC)"; fi

  # 10. Usage.
  OUT="$(bash "$SCRIPT" 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e10"; then
    pass; else fail "usage: expected exit 1 with a usage line, got RC=$RC"; fi

  # 11. A scan that cannot run must never report a clean render. A grep that
  #     exits 2 (unreadable input, bad pattern) is a tool failure, and
  #     collapsing it into "no placeholders left" is what would ship an
  #     unrendered brief to a worker.
  local bin="$TMP/brokenbin"
  mkdir -p "$bin" || die "could not create $bin"
  printf '#!/usr/bin/env bash\nexit 2\n' > "$bin/grep" || die "could not write the failing grep"
  chmod +x "$bin/grep" || die "could not chmod the failing grep"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(PATH="$bin:$PATH" bash "$SCRIPT" "$TPL" "$v1" "$TMP/out11" 2>"$TMP/e11")"; RC=$?
  if [[ $RC -eq 3 && -z "$OUT" ]] && grep -q "placeholder scan failed" "$TMP/e11"; then
    pass; else fail "broken grep: expected exit 3 naming the scan, got RC=$RC ERR=$(cat "$TMP/e11")"; fi
  if [[ ! -e "$TMP/out11/COMMON.md" ]]; then
    pass; else fail "broken grep: nothing may be written when the scan failed"; fi

  # 12. `jq -r` prints a JSON null as the four characters `null`, which
  #     substitutes cleanly and leaves no placeholder behind — the brief reads
  #     as fully rendered while telling a worker its worktree is at `null`.
  local v12="$TMP/v12.json" o12="$TMP/out12"
  cat > "$v12" <<'JSON' || die "could not write $v12"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"developer": {"BRANCH": "feat/x", "WORKTREE": null, "ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v12" "$o12"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "WORKTREE (null)"; then
    pass; else fail "null value: expected exit 2 naming it, got RC=$RC ERR=$ERRTEXT"; fi
  if [[ ! -e "$o12/brief-developer.md" && ! -e "$o12/COMMON.md" ]]; then
    pass; else fail "null value: nothing may be written on a validation failure"; fi

  # 13. An object arrives as a JSON fragment the same way.
  local v13="$TMP/v13.json" o13="$TMP/out13"
  cat > "$v13" <<'JSON' || die "could not write $v13"
{
  "shared": {"SHARED_CHECKOUT": {"path": "/repo"}, "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": "#7", "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v13" "$o13"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "SHARED_CHECKOUT (object)"; then
    pass; else fail "object value: expected exit 2 naming it, got RC=$RC ERR=$ERRTEXT"; fi

  # 14. A number is legitimate text — an issue number reads the same either way.
  local v14="$TMP/v14.json" o14="$TMP/out14"
  cat > "$v14" <<'JSON' || die "could not write $v14"
{
  "shared": {"SHARED_CHECKOUT": "/repo", "AUTHORITY_STATEMENT": "a"},
  "roles": {"tester": {"ISSUE": 42, "REPORT": "/r.md"}}
}
JSON
  run "$TPL" "$v14" "$o14"
  if [[ $RC -eq 0 ]] && grep -q "Tester for 42" "$o14/brief-tester.md"; then
    pass; else fail "number value: expected it to render, got RC=$RC ERR=$ERRTEXT"; fi

  # 15. A new attempt cannot reuse a prior report, or point two roles at one
  #     destination. Refusal preserves the old file and writes no new briefs.
  local old_report="$TMP/old-report.md" invalid_report v15="$TMP/v15.json"
  printf 'Prior attempt\n' > "$old_report" || die "could not write prior report"
  for invalid_report in "$old_report" "relative/report.md" "/r/report.md"$'\nextra' "/r/report.md"$'\n' "/r/"; do
    jq --arg p "$invalid_report" '.roles.developer.REPORT = $p' "$v1" > "$v15" \
      || die "could not build invalid report fixture"
    run "$TPL" "$v15" "$TMP/out15"
    if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
       && grep -q 'Prior attempt' "$old_report"; then
      pass; else fail "invalid/reused report: RC=$RC OUT=$OUT ERR=$ERRTEXT path=$invalid_report"; fi
  done
  jq '.roles.tester.REPORT = .roles.developer.REPORT' "$v1" > "$v15" \
    || die "could not build duplicate report fixture"
  run "$TPL" "$v15" "$TMP/out15"
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'distinct report path'; then
    pass; else fail "shared report destination: RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # A generated briefing artifact cannot stand in for a worker's report.
  jq --arg p "$TMP/out15/brief-developer.md" '.roles.developer.REPORT = $p' "$v1" > "$v15" \
    || die "could not build overlapping report fixture"
  TEAMLEAD_REPORT_PATH_MAX_COLS=500 run "$TPL" "$v15" "$TMP/out15"
  if [[ $RC -eq 2 && -z "$OUT" && ! -e "$TMP/out15" ]] \
     && printf '%s' "$ERRTEXT" | grep -q 'overlaps a generated brief'; then
    pass; else fail "report/brief overlap: RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
