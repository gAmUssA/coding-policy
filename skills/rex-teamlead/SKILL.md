---
name: rex-teamlead
description: >
  Run a Rex-native team round: a developer and an independent reviewer as
  interactive agent tabs (Claude Code, Codex, Grok) in one Rex session, each in
  its own worktree, dispatched with a brief and awaited on the agent's own Rex
  program status plus a nonce-bound report file. Use when the operator asks to
  run the team, dispatch workers, or have a change built and reviewed by
  separate agents inside Rex. Standalone tasks and Herdr rounds skip this skill.
---

# Rex Team Lead Skill

Process steps in order. Do not skip ahead.

Every command below runs `team`, the `rex-team.sh` launcher; its header and
`skills/rex-teamlead/rexlead/team.py` carry the contract: one JSON object on
stdout, `{"error":{"code","message"}}` on stderr, exit 0, 1 or 2. Worker state
and delivery come only from the command's JSON, never from reading a tab. The
commands name the session, tabs, panes, branches and worktrees as
`skills/rex-teamlead/state-schema.md` Naming lists; use those names when
pointing the operator at a tab.

## Step 1 — Confirm the Round

A round needs a Rex terminal (`REX_SESSION` set), a git repository the
operator already trusts in each agent, and an explicit request for a team
round. Without all three, do the task directly and finish here.

Pick a short task id (`[a-z][a-z0-9-]{0,30}`) and the agents: the reviewer is
a different agent kind from the developer. Proceed immediately to Step 2.

## Step 2 — Open the Team

Creates or reuses the task's Rex session and records the base commit:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh up --task <task> --repo <repo-path>
```

Proceed immediately to Step 3.

## Step 3 — Spawn the Developer

Provisions a writer worktree on branch `team/<task>-dev` under
`~/.worktrees/` and starts the agent in its own tab:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh spawn --task <task> --role dev --agent <claude|codex|grok> [--model <id>] [--effort <level>]
```

`not_ready` means a startup dialog the agent does not report: tell the
operator which tab (`<task>-dev`) waits on them. Step 5 refuses a worker that
is not yet idle, so it is safe to continue. Proceed immediately to Step 4.

## Step 4 — Write the Developer Brief

Fill `skills/rex-teamlead/templates/brief-developer.md` for this task into a
file outside the repository. `assign` appends the delivery section, report
path and nonce; the brief never names them. Proceed immediately to Step 5.

## Step 5 — Dispatch the Developer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh assign --task <task> --role dev --brief <brief-file>
```

Proceed immediately to Step 6.

## Step 6 — Wait for the Developer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh wait --task <task> --role dev
```

- `delivered: true` — read the report in full; proceed immediately to Step 7
- `state: blocked` — relay the `kind` and tab to the operator; once answered, run this step again
- `delivered: false` with `state: done` — the worker stopped without a valid report; send one correction brief through Step 5
- `state: timeout` — tell the operator the tab is still working, then run this step again

## Step 7 — Spawn the Reviewer

Use the candidate commit the developer reported, after confirming it is the
tip of `team/<task>-dev`. The reviewer gets a read-only checkout of it:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh spawn --task <task> --role review --agent <other-kind> --ref <candidate-sha> --read-only
```

Proceed immediately to Step 8.

## Step 8 — Write the Reviewer Brief

Fill `skills/rex-teamlead/templates/brief-reviewer.md` with the task, the
candidate SHA and the base from Step 2 into a file outside the repository.
Proceed immediately to Step 9.

## Step 9 — Dispatch the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh assign --task <task> --role review --brief <brief-file>
```

Proceed immediately to Step 10.

## Step 10 — Wait for the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh wait --task <task> --role review
```

Route `blocked`, `timeout` and an undelivered report as in Step 6. On
`delivered: true`, proceed immediately to Step 11.

## Step 11 — Gate the Candidate

Read the review report in full. Its `reviewed:` SHA must equal the candidate.

- `verdict: approve` with no blocking finding — proceed immediately to Step 12
- Blocking findings — brief the developer with them verbatim (Step 5), then a fresh reviewer on the new candidate under a new role name such as `review-2` (Step 7); stop after three such loops and bring the findings to the operator

## Step 12 — Close the Round

Close the worker tabs. Reviewer checkouts are removed; the developer's
worktree stays for the release:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh down --task <task>
```

Proceed immediately to Step 13.

## Step 13 — Release the Branch

Ship `team/<task>-dev` from the developer's worktree through the release
skill, whose cleanup removes that worktree and branch:

```text
Skill(skill: "release")
```

Finish here.
