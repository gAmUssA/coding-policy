# Python Execution with uv

## Status

Accepted

## Date

2026-09-23

## Context

Agents running Python directly inherit whichever interpreter and packages happen to be on the machine. As repository scripts gain dependencies, the same command can behave differently across developer machines and CI. A shared runner gives agents an explicit environment contract.

Some installed plugin hooks must run before developer tooling is available. Making those standard-library-only entry points depend on uv would introduce a new failure mode at session startup and supervision boundaries.

## Decision

Use uv for agent-invoked repository Python scripts and tests, as specified in `rules/dependency-management.md` Python Execution. Project commands use their project environment. Standalone scripts avoid inheriting unrelated projects. Declare supported Python versions and dependencies, commit locks for third-party dependencies, and use locked execution in automation that consumes those locks.

Missing uv is a setup failure, with no silent fallback to ambient Python. Installing uv or downloading Python requires the authorization defined by `rules/environment-changes.md`. Commands without download authorization disable automatic Python downloads.

Allow existing interpreters for documented bootstrap scripts and runtime hooks using only standard-library and first-party code. The owning README must name the entry point, explain why uv cannot be assumed, and state its Python requirement. Child processes may reuse the interpreter already selected by uv.

Apply the agent invocation rule immediately. New or changed developer and CI execution paths adopt the same documented uv command. Migrate older execution paths when touched and keep the remaining work recorded in README. The Herdr invocation validator checks the supported skill command forms; it is not a universal shell-command parser.

## Consequences

Agents share a consistent runner and can reproduce declared dependency environments. Developers need uv for ordinary Python tooling, while the documented runtime hooks keep working without it. Machines lacking a suitable interpreter fail visibly instead of acquiring one without authorization.

The staged migration leaves existing CI and shell-wrapper entry points to subsequent scoped changes. Their current behavior remains unchanged by this release. Direct Python everywhere would retain environment ambiguity; requiring uv at every runtime boundary would break the bootstrap and hook contract.
