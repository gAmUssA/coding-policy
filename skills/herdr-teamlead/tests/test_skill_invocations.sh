#!/usr/bin/env bash
# Guard the SKILL.md conventions a consumer agent depends on.
#
# Invocation conventions, checked against EVERY skill in SKILLS below:
# 1. Every script invocation carries `bash` or an explicit Python interpreter
#    selected by `uv run`. tessl packaging
#    normalizes plugin files to 0644, so a bare path is a permission-denied on
#    every consumer, and `chmod +x` in this repo does not survive publish.
#    Deterministic because the failure is invisible here: the scripts run fine
#    from a clone and break only once installed.
# 2. The `bash ` convention is present, so a rewrite that drops every
#    invocation cannot pass check 1 vacuously.
# 3. Every `$CP` use sits in a fenced block that resolved `CP=` first. The
#    plugin root differs between a project-local and a global install, so each
#    block carries its own resolver — an agent's shell state does not survive
#    between tool calls.
#    After the script path, a pipe, redirection, background `&`, or `;` is
#    rejected. Quoted strings, `<word>` placeholders with no whitespace, and
#    `[a | b]` bracketed alternatives are documentation syntax and are
#    stripped before the operator scan.
#
# Mode-gate conventions, checked against MODE_GATE_SKILL only:
# 4. The first step gates on HERDR_ENV before any script, and the gate turns a
#    standalone agent away by reading rather than by running a script.
# 5. Inside a Herdr round, a Step 1 branch the lead finishes itself never
#    covers an edit or a code review, and an edit routes on to Step 2. The
#    lead never edits the shared checkout (references/team-operation.md
#    Writers and Checkouts); a direct-finish edit branch contradicts it.
#
# `set -e` is dropped so every check runs and the suite reports an aggregate;
# each check captures its own status (rules/error-handling.md
# aggregate-reporting carve-out).
#
# Run: bash skills/herdr-teamlead/tests/test_skill_invocations.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

# Herdr skills whose SKILL.md invokes a plugin script. herdr-standup shipped
# bare invocations while this suite resolved its target through its own
# directory, so it only ever read herdr-teamlead's SKILL.md.
SKILLS=(herdr-teamlead herdr-standup)

# herdr-teamlead alone carries the standalone/Herdr mode gate. herdr-standup
# turns a non-Herdr agent away through roster.sh's exit 1, not by reading.
MODE_GATE_SKILL=herdr-teamlead

die() { echo "fatal: $*" >&2; exit 2; }
warn_cleanup() { echo "warn: could not remove $1" >&2; }
INSTALL_FIXTURE=""
cleanup() {
  if [[ -n "$INSTALL_FIXTURE" ]] && ! rm -rf "$INSTALL_FIXTURE"; then
    warn_cleanup "$INSTALL_FIXTURE"
  else
    INSTALL_FIXTURE=""
  fi
  return 0
}

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Echo the first matching line number, or "" when there is no match. Exits the
# suite on a grep tool error: an unreadable SKILL.md must never read as a
# clean file. grep exits 1 on no-match and 2+ on error, and collapsing the two
# is what `|| true` would do.
first_match_line() { # <pattern> <file>
  local out rc=0
  out="$(grep -n -- "$1" "$2")" || rc=$?
  case "$rc" in
    0) printf '%s' "$out" | head -1 | cut -d: -f1 ;;
    1) printf '' ;;
    *) die "grep failed on $2 (exit ${rc}) while matching ${1}" ;;
  esac
}

check_invocations() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # 1. No bare invocation of a plugin script, in either shape — the literal
  # mount path, or a resolved `$CP` with no interpreter in front of it.
  local bare rc=0
  bare="$(grep -nE '^[[:space:]]*("?[$]CP/[^[:space:]]+\.(sh|py)|((bash|python3)[[:space:]]+)?"?([$]HOME/)?\.tessl/plugins/[^[:space:]]+\.(sh|py))' "$skill")" || rc=$?
  case "$rc" in
    1) pass ;;
    0)
      fail "${name}: bare script invocation(s) — tessl ships plugin files 0644, so these are permission-denied on every consumer:"
      printf '%s\n' "$bare" >&2
      ;;
    *) die "grep failed scanning ${skill} for bare invocations (exit ${rc})" ;;
  esac

  # 2. The `bash ` convention is actually present, so a future rewrite that
  # drops every invocation cannot pass check 1 vacuously.
  local invocations
  rc=0
  invocations="$(grep -cE '^[[:space:]]*bash "[$]CP/[^[:space:]]+\.sh"' "$skill")" || rc=$?
  case "$rc" in
    0) if (( invocations > 0 )); then pass; else fail "${name}: no bash-prefixed invocations found"; fi ;;
    1) fail "${name}: no bash-prefixed invocations found — the convention regressed" ;;
    *) die "grep failed counting invocations in ${skill} (exit ${rc})" ;;
  esac

  # 3. Every fenced block that uses `$CP` defines it first. A block that
  # inherits the resolver from an earlier block is broken on arrival: the
  # agent runs each block as its own tool call, in a fresh shell.
  local unresolved
  rc=0
  # The bootstrap exception permits this exact directory choice only. Check
  # every shell block, including its interpreter and continuation shape.
  # shellcheck disable=SC2016 # Match the documented shell source literally.
  local bootstrap='CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"'
  local uv_python='uv run --no-project --no-python-downloads --python ">=3.11" python'
  unresolved="$(awk -v bootstrap="$bootstrap" -v uv_python="$uv_python" '
    /^```bash/ { inblock = 1; row = 0; next }
    /^```/ { if (inblock && row < 2) print FNR ": incomplete bootstrap block"; inblock = 0; next }
    !inblock || /^[[:space:]]*$/ { next }
    {
      row++
      if (row == 1 && $0 != bootstrap) print FNR ": unsupported bootstrap: " $0
      if (row == 2) {
        invocation = $0
        if (index(invocation, uv_python " ") == 1) {
          script = substr(invocation, length(uv_python) + 2)
          if (script !~ /^"\$CP\/skills\/[^[:space:]]+\.py"([[:space:]]|$)/)
            print FNR ": expected quoted co-shipped Python script: " $0
        } else if (invocation !~ /^bash "\$CP\/skills\/[^[:space:]]+\.sh"([[:space:]]|$)/) {
          print FNR ": expected bash or uv-managed Python invocation: " $0
        }
      }
      if (row > 2 && $0 !~ /^[[:space:]]+(-|<|\[|"\$CP\/)/)
        print FNR ": expected script arguments only: " $0
      if (row > 1 && (index($0, "$(") || index($0, "`") || index($0, ";") || index($0, "&&") || index($0, "||")))
        print FNR ": inline evaluation is forbidden: " $0
      if (row > 1) {
        rest = $0
        # A backslash escape can hide an operator from the quote stripping
        # below (`\" | cmd \"` is a pipeline to bash). Only the trailing
        # line continuation is documentation syntax.
        sub(/[[:space:]]*\\$/, "", rest)
        if (index(rest, "\\"))
          print FNR ": backslash escape after the script path: " $0
        gsub(/"[^"]*"/, "", rest)
        gsub(/\047[^\047]*\047/, "", rest)
        do { prev = rest; gsub(/<[^<>[:space:]]+>/, "", rest) } while (rest != prev)
        if (rest ~ /[&;<>]/)
          print FNR ": redirection or background operator after the script path: " $0
        do { prev = rest; gsub(/\[[^][]*\]/, "", rest) } while (rest != prev)
        if (index(rest, "|"))
          print FNR ": pipe after the script path: " $0
      }
    }
  ' "$skill")" || rc=$?
  if (( rc != 0 )); then
    die "awk failed scanning ${skill} for unresolved \$CP uses (exit ${rc})"
  fi
  if [[ -z "$unresolved" ]]; then
    pass
  else
    fail "${name}: \$CP used in a block that never resolved it — each block is its own shell:"
    printf '%s\n' "$unresolved" >&2
  fi
}

check_mode_gate() { # <skill-name> <skill-file>
  local name="$1" skill="$2"

  # Checks 3 and 4 read the BODY only. Matching the whole file would find
  # HERDR_ENV in the frontmatter `description`, so deleting the entire gate
  # section would still pass -- an assertion that survives the removal of the
  # thing it asserts is not a test (rules/testing-standards.md Assertions).
  local body
  body="$(awk 'BEGIN{n=0} /^---[[:space:]]*$/{n++; next} n>=2' "$skill")" \
    || die "could not strip the frontmatter from ${skill}"
  [[ -n "$body" ]] || die "${skill} has no body after its frontmatter"

  local body_file="${TMPDIR:-/tmp}/skill-body.$$.md"
  printf '%s\n' "$body" > "$body_file" || die "could not stage the skill body"

  # 4a. The mode gate is the first step, before roster/script execution.
  local gate_line step1_line step2_line
  gate_line="$(first_match_line 'HERDR_ENV' "$body_file")"
  step1_line="$(first_match_line '^## Step 1 ' "$body_file")"
  step2_line="$(first_match_line '^## Step 2 ' "$body_file")"
  if [[ -z "$gate_line" ]]; then
    fail "${name}: the body states no HERDR_ENV gate — the frontmatter alone does not gate execution"
  elif [[ -z "$step1_line" ]]; then
    fail "${name}: could not locate the Step 1 heading in the body"
  elif [[ -z "$step2_line" ]]; then
    fail "${name}: could not locate the Step 2 heading in the body"
  elif (( step1_line < gate_line && gate_line < step2_line )); then pass
  else fail "${name}: the HERDR_ENV gate (body line ${gate_line}) is outside Step 1"; fi

  # 4b. That gate tells a standalone agent to stop, and says so before the
  # next step rather than anywhere in the file.
  local head flat
  if [[ -n "$step1_line" && -n "$step2_line" ]]; then
    head="$(sed -n "${step1_line},${step2_line}p" "$body_file")" || die "could not read the mode gate"
  else
    head=""
  fi
  flat="$(printf '%s' "$head" | tr '\n' ' ' | tr -s '[:space:]' ' ' \
    | tr '[:upper:]' '[:lower:]')" || die "could not flatten the preamble"
  if [[ "$flat" == *"this skill does not apply"* ]]; then pass
  else fail "${name}: Step 1 does not tell a non-Herdr agent the skill does not apply"; fi

  rm -f "$body_file" || warn_cleanup "$body_file"
}

# 5. Read Step 1's `- **Set, ...**` branches, each joined with its
# continuation lines. A branch that finishes here may not name an edit or a
# review in its label; one branch naming an edit must route to Step 2.
check_lead_routing() { # <skill-name> <skill-file>
  local name="$1" skill="$2" verdict rc=0
  verdict="$(awk '
    /^## Step 1 /{in1=1; next}
    /^## Step 2 /{in1=0}
    !in1{next}
    /^- /{if (b != "") print b; b=$0; next}
    /^  /{if (b != "") b=b " " $0; next}
    {if (b != "") print b; b=""}
    END{if (b != "") print b}
  ' "$skill" | awk '
    /^- \*\*Set,/{
      label=$0; sub(/^- \*\*/, "", label); sub(/\*\*.*/, "", label); label=tolower(label)
      if ($0 ~ /Finish here/ && label ~ /edit|review/) print "direct:" label
      if (label ~ /edit/ && $0 ~ /Step 2/) routed=1
    }
    END{if (!routed) print "unrouted"}
  ')" || rc=$?
  (( rc == 0 )) || die "could not read the Step 1 branches of ${skill}"
  if [[ -z "$verdict" ]]; then pass
  else fail "${name}: Step 1 lets the lead finish an edit or review itself, or routes no edit to Step 2: ${verdict//$'\n'/; }"; fi
}

# The routing guard rejects the direct-edit branch it replaced and accepts the
# current shape, so it cannot pass vacuously.
check_lead_routing_fixtures() {
  local fixture
  fixture="$(mktemp -d)" || die "cannot create routing fixture"
  INSTALL_FIXTURE="$fixture"
  printf '%s\n' '## Step 1 — Gate' \
    '- **Set, with a team task or new retrospective** — proceed immediately to Step 2.' \
    '- **Set, with a single edit, question, lookup, or existing-code review** — do' \
    '  it directly. Finish here.' '## Step 2 — Next' > "$fixture/old.md" || die "cannot write routing fixture"
  printf '%s\n' '## Step 1 — Gate' \
    '- **Set, with a team task, new retrospective, edit, or review of existing' \
    '  code** — proceed immediately to Step 2.' \
    '- **Set, with a question or one bounded read-only lookup** — answer it' \
    '  directly and cite what you read. Edit nothing. Finish here.' '## Step 2 — Next' > "$fixture/new.md" \
    || die "cannot write routing fixture"
  if bash -c 'source "$1"; check_lead_routing old "$2"; (( FAIL > 0 ))' \
    bash "${BASH_SOURCE[0]}" "$fixture/old.md" > "$fixture/guard.log" 2>&1; then pass
  else fail "routing guard accepted a Step 1 branch that finishes an edit directly"; fi
  if bash -c 'source "$1"; check_lead_routing new "$2"; (( FAIL == 0 && PASS == 1 ))' \
    bash "${BASH_SOURCE[0]}" "$fixture/new.md" > "$fixture/guard.log" 2>&1; then pass
  else fail "routing guard rejected a Step 1 that routes edits to Step 2"; cat "$fixture/guard.log" >&2; fi
  cleanup
  [[ -z "$INSTALL_FIXTURE" ]] || die "routing fixture cleanup failed; the exit trap will retry"
}

# Execute a documented block's resolver and invocation against packaged-mode
# fixtures. Substitute a task-owned fixture root for the HOME token without
# changing the process's HOME or touching the user's installed plugin.
check_install_shapes() { # <skill-file>
  local fixture resolver original_resolver invocation script code output rc shape local_root global_root
  fixture="$(mktemp -d)" || die "cannot create install-shape fixture"
  INSTALL_FIXTURE="$fixture"
  resolver="$(awk '/^CP=/{print; exit}' "$1")" || die "cannot read documented resolver"
  # The first complete one-line invocation: a plugin script plus only plain
  # word arguments, so it runs as written against the fixture.
  # shellcheck disable=SC2016 # Match the documented `$CP` literally.
  invocation="$(awk '/^bash "[$]CP\/[^"]+[.]sh"( [a-z][a-z-]*)*$/{print; exit}' "$1")" || die "cannot read a script invocation"
  [[ -n "$resolver" && -n "$invocation" ]] || die "missing executable script example"
  script="${invocation#bash \"\$CP/}"
  script="${script%%\"*}"
  original_resolver="$resolver"
  resolver="${resolver//\$HOME/\$INVOCATION_FIXTURE_GLOBAL}"
  code="$resolver"$'\n'"$invocation"
  local_root="$fixture/project with spaces/.tessl/plugins/gamussa/coding-policy"
  global_root="$fixture/global with spaces/.tessl/plugins/gamussa/coding-policy"
  mkdir -p "$(dirname "$local_root/$script")" "$(dirname "$global_root/$script")" \
    || die "cannot create installed plugin fixtures"
  printf 'printf "local\\n"\n' > "$local_root/$script" || die "cannot write local fixture"
  printf 'printf "global\\n"\n' > "$global_root/$script" || die "cannot write global fixture"
  chmod 0644 "$local_root/$script" "$global_root/$script" \
    || die "cannot set published file modes"
  for shape in local global missing; do
    rc=0
    output="$(cd "$fixture/project with spaces" && INVOCATION_FIXTURE_GLOBAL="$fixture/global with spaces" bash -c "$code" 2>&1)" || rc=$?
    if [[ "$shape" == missing ]]; then
      if (( rc != 0 )); then pass; else fail "missing installs must fail visibly"; fi
    elif (( rc == 0 )) && [[ "$output" == "$shape" ]]; then pass
    else fail "$shape install invocation: rc=$rc output=$output"; fi
    if [[ "$shape" == local ]]; then
      mv "$local_root" "$fixture/local-unused" || die "cannot stage global-only install"
    elif [[ "$shape" == global ]]; then
      mv "$global_root" "$fixture/global-unused" || die "cannot stage missing install"
    fi
  done
  local bad_block
  for bad_block in \
    $'CP=.tessl/plugins/gamussa/coding-policy\nbash "$CP/skills/herdr-teamlead/roster.sh"' \
    $'bash .tessl/plugins/gamussa/coding-policy/skills/herdr-teamlead/roster.sh' \
    $'"$HOME/.tessl/plugins/gamussa/coding-policy/skills/herdr-teamlead/roster.sh"' \
    "$original_resolver"$'\npython3 "$CP/skills/herdr-standup/standup-render.py"' \
    "$original_resolver"$'\nuv run --no-project --python ">=3.11" python "$CP/skills/herdr-standup/standup-render.py"' \
    "$original_resolver"$'\nuv run --no-project --no-python-downloads --python ">=3.11" python $CP/skills/herdr-standup/standup-render.py' \
    "$original_resolver"$'\n'"$invocation"$'\n  eval unsafe' \
    "$original_resolver"$'\n'"$invocation"' | other-command' \
    "$original_resolver"$'\n'"$invocation"'|other-command' \
    "$original_resolver"$'\nuv run --no-project --no-python-downloads --python ">=3.11" python "$CP/skills/herdr-standup/standup-render.py" | other-command' \
    "$original_resolver"$'\n'"$invocation"' > out.txt' \
    "$original_resolver"$'\n'"$invocation"' >out.txt' \
    "$original_resolver"$'\n'"$invocation"' >> out.txt' \
    "$original_resolver"$'\n'"$invocation"' < in.txt' \
    "$original_resolver"$'\n'"$invocation"' 2> err.txt' \
    "$original_resolver"$'\n'"$invocation"' 2>&1' \
    "$original_resolver"$'\n'"$invocation"' &' \
    "$original_resolver"$'\n'"$invocation"' ; other-command' \
    "$original_resolver"$'\n'"$invocation"' "quoted arg" | other-command' \
    "$original_resolver"$'\n'"$invocation"' \" | other-command \"' \
    "$original_resolver"$'\n'"$invocation"" \\' | other-command \\'" \
    "$original_resolver"$'\n'"$invocation"$' \\\n  --flag value | other-command' \
    "$original_resolver"$'\n'"$invocation"$' \\\n  [--state <state-file>] > out.txt' \
    "$original_resolver"$'\n'"$invocation"$' \\\n  [--state <state-file> > out.txt]'; do
    # shellcheck disable=SC2016 # Backticks delimit Markdown, not shell commands.
    printf '```bash\n%s\n%s\n```\n```bash\n%s\n```\n' "$original_resolver" "$invocation" "$bad_block" > "$fixture/invalid.md" || die "cannot write invalid bootstrap fixture"
    if bash -c 'source "$1"; check_invocations invalid "$2"; (( FAIL > 0 ))' \
      bash "${BASH_SOURCE[0]}" "$fixture/invalid.md" > "$fixture/guard.log" 2>&1; then
      pass
    else
      fail "bootstrap guard accepted a nonconforming command block: ${bad_block##*$'\n'}"
    fi
  done
  # Placeholders, bracketed alternatives, and quoted arguments may carry
  # operator characters; the guard must keep accepting them.
  local good_block
  for good_block in \
    "$original_resolver"$'\n'"$invocation"' --judge-mode <adjudication|diagnosis> --pane <pane>' \
    "$original_resolver"$'\n'"$invocation"$' \\\n  [--retain-context | --retain-specialist | --no-clear] [claude|codex|grok]' \
    "$original_resolver"$'\n'"$invocation"$' \\\n  [--exclude <role>=<agent>[,<agent>...]]... \\\n  <round-reports-dir>/review-<base7>..<head7>.diff' \
    "$original_resolver"$'\n'"$invocation"' "quoted | literal > text"' \
    "$original_resolver"$'\nuv run --no-project --no-python-downloads --python ">=3.11" python "$CP/skills/herdr-standup/standup-render.py" \\\n  --team "<label>" \\\n  [--roles <roles-json>]'; do
    # shellcheck disable=SC2016 # Backticks delimit Markdown, not shell commands.
    printf '```bash\n%s\n%s\n```\n```bash\n%s\n```\n' "$original_resolver" "$invocation" "$good_block" > "$fixture/valid.md" || die "cannot write valid bootstrap fixture"
    if bash -c 'source "$1"; check_invocations valid "$2"; (( FAIL == 0 ))' \
      bash "${BASH_SOURCE[0]}" "$fixture/valid.md" > "$fixture/guard.log" 2>&1; then
      pass
    else
      fail "bootstrap guard rejected a conforming command block: ${good_block##*$'\n'}"
      cat "$fixture/guard.log" >&2
    fi
  done
  cleanup
  [[ -z "$INSTALL_FIXTURE" ]] || die "install fixture cleanup failed; the exit trap will retry"
}

check_cleanup_retry() {
  local fixture
  fixture="$(mktemp -d)" || die "cannot create cleanup fixture"
  INSTALL_FIXTURE="$fixture"
  # A failed explicit removal leaves the path available for the exit trap.
  if bash -c '
    source "$1"
    INSTALL_FIXTURE="$2"
    cleanup_calls=0
    rm() {
      cleanup_calls=$((cleanup_calls+1))
      if (( cleanup_calls == 1 )); then return 1; fi
      command rm "$@"
    }
    cleanup
    [[ "$INSTALL_FIXTURE" == "$2" && -d "$2" ]] || exit 1
    cleanup
    [[ -z "$INSTALL_FIXTURE" && ! -e "$2" ]]
  ' bash "${BASH_SOURCE[0]}" "$fixture"; then
    pass
  else
    fail "failed cleanup must retain the path and retry removal"
  fi
  cleanup
  [[ -z "$INSTALL_FIXTURE" ]] || die "cleanup retry fixture remains; the exit trap will retry"
}

main() {
  local skills_root skill name
  trap cleanup EXIT
  skills_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || die "could not resolve the skills dir"

  for name in "${SKILLS[@]}"; do
    skill="${skills_root}/${name}/SKILL.md"
    [[ -r "$skill" ]] || die "SKILL.md not found at ${skill}"
    check_invocations "$name" "$skill"
    check_install_shapes "$skill"
  done

  check_invocations round-setup "$skills_root/herdr-teamlead/references/round-setup.md"
  check_cleanup_retry

  skill="${skills_root}/${MODE_GATE_SKILL}/SKILL.md"
  check_mode_gate "$MODE_GATE_SKILL" "$skill"
  check_lead_routing "$MODE_GATE_SKILL" "$skill"
  check_lead_routing_fixtures

  echo
  echo "results: ${PASS} pass, ${FAIL} fail"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
