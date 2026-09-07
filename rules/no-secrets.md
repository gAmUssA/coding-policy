---
alwaysApply: true
---

# No Secrets

## Never Commit Secrets

- Never commit API keys, tokens, passwords, private keys, or `.env` files
- This includes test/development credentials — they tend to leak into production
- If a secret was committed, rotate it immediately — removing the commit is not enough

## Use Environment Variables or Secrets Managers

- Read secrets from environment variables or a secrets manager at runtime
- Never hardcode credentials in source code, config files, or scripts

## Document Required Variables

- Maintain a `.env.example` file listing every required environment variable with placeholder values
- Document what each variable is for and where to get the value
- For hosted-CI secrets, include a deep link to the platform's secrets configuration page in the file header so a new maintainer can reach it in one click (GitHub Actions: `https://github.com/gAmUssA/coding-policy/settings/secrets/actions`; GitLab CI: `https://gitlab.com/<group>/<project>/-/settings/ci_cd`)

## Pre-commit Scanning

- Use pre-commit hooks for secret scanning (e.g., detect-secrets, gitleaks, trufflehog)
- Block commits that contain patterns matching secrets

## Logging

- **Never log secrets** — not at any log level, not in error messages, not in stack traces
- Sanitize or redact sensitive values before they reach any logging or monitoring system
