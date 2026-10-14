---
name: env-linting
description: >
  Checks environment variable declarations across the codebase for consistency:
  ensures all variables referenced in code are declared in .env.example, all
  declared variables follow SCREAMING_SNAKE_CASE, and no secret patterns appear
  in committed files. Use when auditing environment configuration or adding new
  environment variables.
---

# Environment Linting Skill

## Step 1 — Scan for Variable References

Run the scanner to identify every environment variable accessed in source files:

```bash
bash skills/env-linting/scan-env-refs.sh
```

This produces `env-refs.json` listing each variable name and the files that reference it.

## Step 2 — Validate Against .env.example

Compare the scanned references against declared variables in `.env.example`:

```bash
bash skills/env-linting/validate-env-declarations.sh env-refs.json
```

Undeclared variables are reported as findings. Add missing declarations to `.env.example` with a one-line purpose comment.

## Step 3 — Check Naming Conventions

All environment variable names must follow SCREAMING_SNAKE_CASE. The scanner flags deviations automatically. Rename any non-conforming variables and update all references.

## Step 4 — Secret Pattern Scan

Run the secret pattern scan to ensure no tokens, passwords, or keys appear in committed files:

```bash
bash skills/env-linting/scan-secrets.sh
```

Exit 0 means clean. Exit 1 lists each finding with file and line number.
