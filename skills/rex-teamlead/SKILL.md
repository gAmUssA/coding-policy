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

| Command | Reads | stdout fields | Side effects |
| --- | --- | --- | --- |
| `up` | `--task`, `--repo` | `session`, `base`, `reused`, `workers`, `tab_label` | Creates or reuses the task's Rex session and state record |
| `spawn` | `--task`, `--role`, `--agent`, optional `--model`, `--effort`, `--ref`, `--read-only`, `--timeout` | `block`, `worktree`, `branch`, `commit`, `state`, `reused`, `tab_label` | Creates or adopts a worktree, opens the worker's tab |
| `assign` | `--task`, `--role`, `--brief` | `report`, `nonce`, `tab_label` | Pastes the brief plus its delivery section into the worker |
| `wait` | `--task`, `--role`, optional `--timeout` | `state`, `kind`, `delivered`, `reason`, `report`, `tab_label` | Relabels the tab with the outcome |
| `status` | `--task` | `workers.<role>`: `agent`, `state`, `delivered` | None |
| `down` | `--task`, optional `--role` | `closed`, `worktrees_removed`, `worktrees_kept`, `worktrees_left` | Closes tabs, removes reviewer checkouts, forgets workers |

Exit 0 is success; exit 1 carries an error `code` such as `not_ready`,
`worker_busy`, `role_taken`, `worktree_conflict`, `rex_failed`, `git_failed`
or `state_corrupt`, with the fix in its `message`; exit 2 is `usage_error`.
Every tab label is cosmetic: `tab_label_error` reports a label Rex refused.

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

`not_ready` means a startup dialog the agent does not report. Tell the
operator which tab (`dev · <agent>`) waits on them, then run this step again:
a repeated `spawn` reconnects to the same worker and reports its `state`.
Once `state` is `idle`, proceed immediately to Step 4.

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
tip of `team/<task>-dev`. `<review-role>` is `review` for the first review
and `review-<n>` for the `n`th, so each candidate gets a fresh reviewer; use
the same `<review-role>` through Step 11. The reviewer gets a read-only
checkout of the candidate:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh spawn --task <task> --role <review-role> --agent <other-kind> --ref <candidate-sha> --read-only
```

Handle `not_ready` as in Step 3.

Proceed immediately to Step 8.

## Step 8 — Write the Reviewer Brief

Fill `skills/rex-teamlead/templates/brief-reviewer.md` with the task, the
candidate SHA and the base from Step 2 into a file outside the repository.
Proceed immediately to Step 9.

## Step 9 — Dispatch the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh assign --task <task> --role <review-role> --brief <brief-file>
```

Proceed immediately to Step 10.

## Step 10 — Wait for the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh wait --task <task> --role <review-role>
```

Route `blocked`, `timeout` and an undelivered report as in Step 6. On
`delivered: true`, proceed immediately to Step 11.

## Step 11 — Gate the Candidate

Read the review report in full. Its `reviewed:` SHA must equal the candidate.

- `verdict: approve` with no blocking finding — proceed immediately to Step 12
- Blocking findings — run `down --task <task> --role <review-role>`, brief the developer with the findings verbatim (Step 5), then review the new candidate under the next `<review-role>` (Step 7); stop after three such loops and bring the findings to the operator

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
