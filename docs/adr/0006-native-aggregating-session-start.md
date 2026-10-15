# One Native Aggregating SessionStart Hook

## Status

accepted

## Date

2026-10-15

## Context

The plugin declared five SessionStart hooks in one portable `hooks` group: `check-policy-freshness`, `check-git-sync`, `check-tessl-latest`, `herdr-team-status`, `beans-prime`. Tessl installs a portable group as one `tessl hook run` command. Probed on tessl 0.115.0 with a fixture plugin, `tessl hook run` keeps only the last hook's output in a SessionStart group, also across groups, and hands a hook only `TESSL_*` plus a minimal environment, so `HERDR_ENV` never arrives. The last hook, `beans-prime`, is silent outside a beans repo. Claude Code sessions therefore received `{}`: no sync, version, or team status, and worker detection never fired. The Codex plugin was unaffected: `hooks/hooks.json` registers each hook as its own handler through `hooks/codex-session-start.py`, and Codex keeps the environment.

Options considered:

- **Keep the portable group and reorder it.** Only the last hook would ever report; the rest stay lost.
- **Declare each hook under `nativeHooks.claude-code`.** The environment survives, but every agent configured through the portable tier keeps the last-output loss, and the hook list exists in two places in the manifest.
- **One aggregating hook, native for Claude Code and portable for everyone else.** One script runs every hook and merges their statuses itself, so no host merge behavior matters.

## Decision

Use one aggregating hook, `hooks/session-start.sh`.

- The manifest declares it as the only portable SessionStart hook and as `nativeHooks.claude-code.SessionStart`, in the single-string `command` form.
- It runs, in order, the hooks listed in `hooks/session-start-hooks.txt`, each with stdin from `/dev/null`.
- Each child runs under `hooks/bounded-run.sh`, a pure-bash deadline runner that kills the child's whole process group: 45s per child, capped by what is left of a 120s total. A child past its deadline, or one never started because the total was spent, gets its own status line, and the list continues. The child sees its deadline as `SESSION_START_CHILD_DEADLINE_SEC` and keeps any deadline of its own inside it; a runner stopped by an outer runner reaps its own process group first.
- Payload contract: one JSON object whose `additionalContext` holds, joined by blank lines in list order, first every status (a child payload beginning `Session-start status — `), the block `rules/hook-action-reporting.md` relays; then every other child payload, such as the beans guide, each under its own `Agent context, not a session-start status — do not relay it to the user (from <hook>):` header that ends the status block. Nothing when no child reported. A child that exits non-zero or prints anything other than one `additionalContext` object becomes its own `Session-start status — hook <name> ...` line naming the command to rerun it. Without jq the children still run and the payload is one fixed status naming the jq install step. The hook always exits 0.
- Mode from `TESSL_AGENT`: unset is native and emits Claude Code's `hookSpecificOutput`; `claude-code` exits silently, since the native entry already ran; any other agent is portable, emits the consensus `{"additionalContext"}`, and exports `SESSION_START_MODE=portable` to the children.
- In portable mode a child that would write reports instead. `check-git-sync` neither fetches nor fast-forwards from a linked worktree, which may be a Herdr worker's, and never fast-forwards. `check-tessl-latest` runs no `tessl update` and reports versions as installed, never as latest. The other children only read: `check-policy-freshness` runs `tessl outdated`, `herdr-team-status` reads the roster, `beans-prime` prints `beans prime`.
- `hooks/session-start-hooks.txt` is the one hook list. The Codex adapter reads it as its allowlist; `hooks/hooks.json` keeps one Codex handler per listed hook.

## Consequences

- Claude Code sessions receive every status with the session's environment intact, so Herdr worker detection and `herdr-team-status` work again.
- Agents on the portable tier receive every status, without `HERDR_ENV`. Their sessions are treated as possibly a worker's wherever the role matters.
- Tessl writes both entries into a Claude Code consumer's `.claude/settings.json`: the portable `tessl hook run` entry and the native one. The portable run exits silently for Claude Code, so each session reports once. Existing consumers pick this up on their next `tessl update`; no consumer action is needed.
- A child that hangs costs at most its deadline; later children still run and earlier statuses are kept. A child killed mid-run (a `tessl update` included) is reported, never retried. `check-git-sync` also holds its own fetch to `SYNC_FETCH_TIMEOUT` through the same runner.
- Codex installed through Tessl, as opposed to the Codex plugin, stays on the portable tier until a `nativeHooks.codex` entry is added.
- Later work must honor this contract: add a SessionStart hook by listing it in `hooks/session-start-hooks.txt` and registering a matching handler in `hooks/hooks.json`, never as a new manifest entry; a child emits at most one `{"additionalContext"}` object and exits 0; a child that acts reads `SESSION_START_MODE` and only reports in portable mode.
