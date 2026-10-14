#!/usr/bin/env bash
# check-changelog-fragment.sh <base-ref> — release-readiness gate CI runs on every PR.
# Exit 0 = green, exit 1 = red.

set -euo pipefail

BASE="${1:-origin/main}"
PASS=true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "Checking changelog fragment vs $BASE ..."

# Get list of changed files
CHANGED=$(git diff --name-only "$BASE"...HEAD 2>/dev/null || git diff --name-only HEAD~1 HEAD 2>/dev/null || true)

# Check 1: At least one changelog.d/<name>.md added (not README.md)
FRAGMENTS=$(echo "$CHANGED" | grep -E '^changelog\.d/[^/]+\.md$' | grep -v '^changelog\.d/README\.md$' || true)

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
        BUMP=$(grep -m1 '^bump:' "$frag" | sed 's/bump:[[:space:]]*//' | tr -d '[:space:]' || true)
        if [[ "$BUMP" != "patch" && "$BUMP" != "minor" && "$BUMP" != "major" ]]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: missing or invalid 'bump:' front matter (got: '$BUMP', expected: patch|minor|major)"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: bump=$BUMP"
        fi

        # Check ### Section headings
        SECTIONS=$(grep -c '^### ' "$frag" || true)
        if [ "$SECTIONS" -lt 1 ]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: no '### <Section>' headings found"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: $SECTIONS section heading(s)"
        fi

        # Check for bullet items
        BULLETS=$(grep -c '^- ' "$frag" || true)
        if [ "$BULLETS" -lt 1 ]; then
            echo -e "${RED}✗ FAIL${NC}: $frag: no bullet items under section headings"
            PASS=false
        else
            echo -e "${GREEN}✓ PASS${NC}: $frag: $BULLETS bullet(s)"
        fi
    done <<< "$FRAGMENTS"
fi

# Check 4: plugin.json version not edited
PLUGIN_CHANGED=$(echo "$CHANGED" | grep '^plugin\.json$' || true)
if [ -n "$PLUGIN_CHANGED" ]; then
    VERSION_DIFF=$(git diff "$BASE"...HEAD -- plugin.json 2>/dev/null | grep '^[+-].*"version"' || true)
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
CHANGELOG_DIFF=$(git diff "$BASE"...HEAD -- CHANGELOG.md 2>/dev/null | grep '^+## [0-9]' || true)
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
