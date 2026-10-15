#!/usr/bin/env bash
# check-changelog-fragment.sh <base-ref> — release-readiness gate CI runs on every PR.
# Exit 0 = green, exit 1 = red, exit 2 = the gate could not decide (bad base ref, git or grep failure).

set -euo pipefail

BASE="${1:-origin/main}"
PASS=true

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# matching <ERE>: stdin lines matching the pattern. No match is an empty
# result; a grep failure (exit 2) stops the gate instead of reading as PASS.
matching() {
    local rc=0
    grep -E -- "$1" || rc=$?
    if (( rc > 1 )); then
        echo "error: grep failed (exit $rc) — the gate cannot decide; re-run" >&2
        exit 2
    fi
}

# not_matching <ERE>: stdin lines NOT matching the pattern, same exit handling.
not_matching() {
    local rc=0
    grep -E -v -- "$1" || rc=$?
    if (( rc > 1 )); then
        echo "error: grep failed (exit $rc) — the gate cannot decide; re-run" >&2
        exit 2
    fi
}

# diff_of <args...>: git diff from the merge base, or a stop with what to do.
diff_of() {
    git diff "$BASE"...HEAD "$@" \
        || { echo "error: git diff against '$BASE' failed — fetch the base ref (git fetch origin) or pass an existing one" >&2; exit 2; }
}

echo "Checking changelog fragment vs $BASE ..."

if ! git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
    echo "error: base ref '$BASE' does not exist — pass the branch the PR targets, e.g. main" >&2
    exit 2
fi

# Get list of changed files
CHANGED=$(diff_of --name-only)

# Check 1: At least one changelog.d/<name>.md added (not README.md)
FRAGMENTS=$(printf '%s\n' "$CHANGED" | matching '^changelog\.d/[^/]+\.md$' | not_matching '^changelog\.d/README\.md$')

if [ -z "$FRAGMENTS" ]; then
    echo -e "${RED}✗ FAIL${NC}: No changelog fragment found in changelog.d/ (expected changelog.d/<name>.md)"
    PASS=false
else
    echo -e "${GREEN}✓ PASS${NC}: Fragment(s) found: $FRAGMENTS"

    # Check 2 & 3: Validate each fragment
    while IFS= read -r frag; do
        if [ ! -f "$frag" ]; then
            echo -e "${RED}✗ FAIL${NC}: Fragment listed in diff but file not found: $frag"
            PASS=false
            continue
        fi

        # Check bump front matter
        BUMP=$(matching '^bump:' < "$frag" | head -n1 | sed 's/bump:[[:space:]]*//' | tr -d '[:space:]')
        if [[ "$BUMP" != "patch" && "$BUMP" != "minor" && "$BUMP" != "major" ]]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: missing or invalid 'bump:' front matter (got: '$BUMP', expected: patch|minor|major)"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: bump=$BUMP"
        fi

        # Check ### Section headings
        SECTIONS=$(matching '^### ' < "$frag" | wc -l | tr -d ' ')
        if [ "$SECTIONS" -lt 1 ]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: no '### <Section>' headings found"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: $SECTIONS section heading(s)"
        fi

        # Check for bullet items
        BULLETS=$(matching '^- ' < "$frag" | wc -l | tr -d ' ')
        if [ "$BULLETS" -lt 1 ]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: no bullet items under section headings"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: $BULLETS bullet(s)"
        fi
    done <<< "$FRAGMENTS"
fi

# Check 4: plugin.json version not edited
PLUGIN_CHANGED=$(printf '%s\n' "$CHANGED" | matching '^plugin\.json$')
if [ -n "$PLUGIN_CHANGED" ]; then
    VERSION_DIFF=$(diff_of -- plugin.json | matching '^[+-].*"version"')
    if [ -n "$VERSION_DIFF" ]; then
        echo -e "${RED}✗ FAIL${NC}: plugin.json 'version' was edited — the publish workflow assigns it on merge"
        echo "$VERSION_DIFF"
        PASS=false
    else
        echo -e "${GREEN}✓ PASS${NC}: plugin.json touched but version field unchanged"
    fi
else
    echo -e "${GREEN}✓ PASS${NC}: plugin.json version not edited"
fi

# Check 5: No ## <version> heading added to CHANGELOG.md
CHANGELOG_DIFF=$(diff_of -- CHANGELOG.md | matching '^\+## [0-9]')
if [ -n "$CHANGELOG_DIFF" ]; then
    echo -e "${RED}✗ FAIL${NC}: A '## <version>' heading was added to CHANGELOG.md — the publish workflow writes version headings on merge"
    echo "$CHANGELOG_DIFF"
    PASS=false
else
    echo -e "${GREEN}✓ PASS${NC}: No version heading added to CHANGELOG.md"
fi

echo ""
if [ "$PASS" = true ]; then
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
else
    echo -e "${RED}One or more checks failed.${NC}"
    exit 1
fi
