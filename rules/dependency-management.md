---
alwaysApply: true
---

# Dependency Management

## Stdlib First

- Prefer the standard library over external dependencies
- Only add a dependency when it provides significant value over a stdlib solution
- Adding one is a stated decision — see `rules/environment-changes.md` New Project Dependencies

## Declaration and Pinning

- All dependencies declared in the project's manifest file (`build.gradle.kts`, `pom.xml`, `Package.swift`, `package.json`, `pyproject.toml`)
- No undeclared dependencies — if your code imports it, the manifest lists it
- Pin versions or use a lock file to ensure reproducible builds
- Lock files are committed to the repo
- Separate test/dev dependencies from production dependencies using the project's convention (`testImplementation`, `devDependencies`, `[dependency-groups]`)
- Every dependency must be installable in CI — if something exists as a package, install it properly

## Freshness

- Every pinned dependency needs a stated renewal mechanism
- Automate it where a scanner supports the ecosystem: a committed Dependabot or Renovate config
- Where no scanner tracks the pin — a version baked into a script or action step — document the renewal cadence beside the pin
- A version bump is its own focused change, never bundled with feature work
- Formatter and linter bumps especially (see `rules/code-formatting.md` Separation of Concerns)
- Match a stale pin locally to ship the current task; bump it in a separate change

## Runtime-Managed Manifest Carve-Out

- Narrow exception for runtime-managed manifests.
- Applies when a tool produces the resolved-version state at runtime and gitignores it — either rewriting the manifest in place, or resolving a stable floating specifier into a separate gitignored resolved state
- The manifest may use a floating-but-explicit specifier (e.g., `"version": "latest"`) and skip the lock file
- Preconditions (each covered manifest, all required):
  1. An authority-of-record rule names the carve-out and lists every covered manifest
  2. A deterministic check surfaces any disallowed specifier — a deploy-time gate, or a plugin-shipped `SessionStart` hook that reads the manifest each session
  3. Each covered manifest is named explicitly in the authority-of-record rule
- Every other manifest in the repo still pins

### Authority of Record — consumer `tessl.json`

- Covered manifest: a consumer repo's `tessl.json`
- tessl writes its resolved state into the gitignored `.tessl/`
- `gAmUssA/*`-owned dependencies use the `latest` specifier
- Deterministic check: the plugin-shipped `hooks/check-tessl-latest.sh` `SessionStart` hook, which flags any `gAmUssA/*` dependency not at `latest`
- `skills/onboard-repo` sets `latest` and gitignores `.tessl/` at onboarding
- Third-party dependencies (`tessl-labs/*`, `tessl/npm-*`) pin normally and stay out of scope

## No Vendoring

- Don't copy library source code into the repo
- Use the language's package manager to install dependencies
- Tessl plugins count as dependencies — never vendor them. Install via `tessl install` at runtime; never commit `.tessl/plugins/**` into the consumer repo
- Product code that copies third-party content into a user's project by documented design (a migration bridge, an offline mirror) is out of scope; reviewing that copying is a correctness question under the other rules
