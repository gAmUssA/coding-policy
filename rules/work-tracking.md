---
alwaysApply: true
description: In a repo that tracks work with beans (`.beans.yml`), every task lives in a bean — created before the work, kept current during it, completed after it, committed with the code
---

# Work Tracking

## Scope

- Governs a repo that carries `.beans.yml` — work is tracked with the `beans` CLI, issues as Markdown files under `.beans/`
- The `beans-prime` SessionStart hook loads the CLI's own usage guide (`beans prime`) at session start; that guide is the command reference and this rule is the discipline around it
- Every other repo is untouched: no bean, no `.beans/`, no substitute tracker invented

## A Bean Per Task

- Before starting a task, find its bean (`beans list --json --ready`, `beans list --json -S "<words>"`) or create one: `beans create --json "<title>" -t <type> -d "<description>" -s in-progress`
- Beans replace every in-session todo list: the agent's todo tool, a scratch checklist in chat, a plan kept only in context
- The bean's body carries the task's checklist; each item is checked off as it lands, never in one sweep at the end
- A bean is completed only when no unchecked item remains, with a `## Summary of Changes` section stating what was done
- A bean is scrapped with a `## Reasons for Scrapping` section, never silently deleted
- Deferred work found along the way becomes a follow-up bean, offered to the user (`rules/boy-scout.md` How to Apply: in a beans repo the "issue" is a bean)

## Beans Travel With the Code

- The bean file is staged and committed with the code it describes, in the same commit (`rules/commit-conventions.md` One Change Per Commit still governs the shape)
- The PR body's Policy and risks section names the bean id (`skills/release/SKILL.md` Step 2)
- `beans archive` runs only when the user asks; a completed bean stays visible until then
- Concurrent updates use etags: `ETAG=$(beans show <id> --etag-only)` then `beans update <id> --if-match "$ETAG" ...`; a conflict is resolved by re-reading, never by force

## Relationship to Other Rules

- The Herdr lead's task ledger (`rules/agent-team-operation.md` Task Ledger) is round evidence owned by the lead; a bean is the repo's record of the work — both exist in a beans repo, neither substitutes for the other
- A bean whose decision alters the architecture links its ADR (`rules/architecture-decisions.md`)
- Status and priority follow the repo's `.beans.yml`; a type is always given with `-t`
