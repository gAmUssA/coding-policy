name: PR Policy Review (Codex, subscription)

# Reviews every same-repo pull request against the gAmUssA/coding-policy rules
# using the Codex CLI authenticated by a ChatGPT subscription (no API key).
# Scaffolded by the coding-policy `onboard-repo` skill; refresh it with the
# skill's --override mode rather than editing by hand.
#
# The policy is read from a fresh checkout of gAmUssA/coding-policy (public,
# no token) under .coding-policy/rules — the consumer never vendors the rules.
#
# The subscription token is read ONLY from the CODEX_AUTH_JSON secret at
# runtime — never persisted on the runner. Set it once per repo:
#   codex login                                   # Sign in with ChatGPT, trusted machine
#   gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json
# Re-run the second command whenever the review fails on expired auth.
# See https://learn.chatgpt.com/docs/auth/ci-cd-auth
#
# Security posture: same-repo PRs require write access, which GitHub already
# treats as trusted (forks get no secret and are skipped). Every token is
# registered with ::add-mask:: before Codex runs, the review output is scanned
# for credential material, and the token is deleted before the post step.

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: codex-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

env:
  RULES_DIR: .coding-policy/rules
  REVIEW_DIR: .github/codex-review

jobs:
  review:
    # Fork PRs cannot read secrets, so the review only runs for same-repo PRs.
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      # The policy itself. Tracks main so every review uses the current rules;
      # the checkout lands inside the workspace and is excluded from the diff.
      - uses: actions/checkout@v7
        with:
          repository: gAmUssA/coding-policy
          ref: main
          path: .coding-policy

      - uses: actions/setup-node@v7
        with:
          node-version: "24"

      # renovate: datasource=npm depName=@openai/codex
      - name: Install Codex CLI
        run: npm install -g @openai/codex@0.153.4

      - name: Write auth.json from the secret (runtime-only, never persisted)
        env:
          CODEX_AUTH_JSON: ${{ secrets.CODEX_AUTH_JSON }}
        run: |
          set -euo pipefail
          if [ -z "${CODEX_AUTH_JSON:-}" ]; then
            echo "error: CODEX_AUTH_JSON secret is empty — run 'codex login' on a trusted machine and 'gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json' (see this workflow's header)" >&2
            exit 1
          fi
          export CODEX_HOME="$HOME/.codex"
          mkdir -p "$CODEX_HOME"; chmod 700 "$CODEX_HOME"
          printf '%s' "$CODEX_AUTH_JSON" > "$CODEX_HOME/auth.json"
          chmod 600 "$CODEX_HOME/auth.json"

      - name: Redact the credential from CI logs
        run: bash "$REVIEW_DIR/mask-secrets.sh" "$HOME/.codex/auth.json"

      - name: Run Codex policy review
        env:
          BASE_REF: ${{ github.event.pull_request.base.ref }}
        run: |
          set -euo pipefail
          git fetch --no-tags --depth=1 origin "$BASE_REF"
          { printf 'Environment: RULES_DIR=%s BASE_REF=%s\n\n' "$RULES_DIR" "$BASE_REF"; cat "$REVIEW_DIR/prompt.md"; } \
            | codex exec \
                --json \
                --skip-git-repo-check \
                --dangerously-bypass-approvals-and-sandbox \
                --output-schema "$REVIEW_DIR/schema.json" \
                --output-last-message /tmp/codex-final.json \
                -

      - name: Guard against credential leakage, then drop the token
        run: |
          set -euo pipefail
          bash "$REVIEW_DIR/assert-no-secret-leak.sh" "$HOME/.codex/auth.json" /tmp/codex-final.json
          rm -f "$HOME/.codex/auth.json"

      - name: Post the review
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          bash "$REVIEW_DIR/post-review.sh" \
            "${{ github.repository_owner }}" \
            "${{ github.event.repository.name }}" \
            "${{ github.event.pull_request.number }}" \
            /tmp/codex-final.json
