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
commands name the session, tabs, panes, branches and worktrees as the Naming
section lists; use those names when pointing the operator at a tab:

```text
skills/rex-teamlead/state-schema.md
```

| Command | Reads | stdout fields | Side effects |
| --- | --- | --- | --- |
| `up` | `--task`, `--repo` | `session`, `base`, `reused`, `workers`, `tab_label` | Creates or reuses the task's Rex session and state record |
| `spawn` | `--task`, `--role`, `--agent`, optional `--model`, `--effort`, `--ref`, `--read-only`, `--timeout` | `block`, `worktree`, `branch`, `commit`, `state`, `reused`, `tab_label` | Creates or adopts a worktree, opens the worker's tab |
| `assign` | `--task`, `--role`, `--brief` | `report`, `nonce`, `tab_label` | Pastes the brief plus its delivery section into the worker |
| `wait` | `--task`, `--role`, optional `--timeout` | `state`, `kind`, `delivered`, `reason`, `report`, `tab_label` | Relabels the tab with the outcome |
| `candidate` | `--task`, `--role`, `--sha` | `branch`, `candidate` | None; exit 1 `candidate_mismatch` unless `--sha` is the branch tip |
| `check` | `--task`, `--role`, `--candidate`, one or more `--run`, optional `--timeout` | `candidate`, `verdict`, `runs[]`: `command`, `exit`, `verdict`, `reason`, `seconds`; `tab_label` | Runs each command in the writer's worktree, keeps none of its output, kills processes it leaves behind, records the check; exit 1 `check_mismatch`, `check_unsupported` or `check_dirty` before any run |
| `verdict` | `--task`, `--role`, `--candidate` | `reviewed`, `verdict`, `report` | None; exit 1 `review_mismatch`, `no_verdict`, `not_delivered`, or `candidate_mismatch` once the developer's branch moved |
| `status` | `--task` | `workers.<role>`: `agent`, `state`, `delivered`, `check` | None |
| `down` | `--task`, optional `--role` | `closed`, `worktrees_removed`, `worktrees_kept` | Closes tabs, removes reviewer checkouts, forgets workers; exit 1 `worktree_remove_failed` keeps the worker for a retry |

Exit 0 is success; exit 1 carries an error `code` such as `not_ready`,
`agent_failed`, `not_independent`, `worker_busy`, `role_taken`,
`worktree_conflict`, `check_mismatch`, `check_unsupported`, `check_dirty`, `rex_failed`, `git_failed` or `state_corrupt`, with the fix in its `message`; exit 2 is `usage_error`.
Every tab label is cosmetic: `tab_label_error` reports a label Rex refused.

## Step 1 — Confirm the Round

A round needs a Rex terminal (`REX_SESSION` set), a git repository and an
explicit request for a team round. Without all three, do the task directly
and finish here. An agent's trust prompt for a new worktree surfaces at
spawn as `not_ready` (Step 3).

Pick a short task id (`[a-z][a-z0-9-]{0,30}`) and the agents: the reviewer is
a different agent kind from the developer. Pick the repository's own check
entry points: the test, lint and diagnostics commands its CI runs, as its CI
workflow, `CLAUDE.md` or `AGENTS.md` names them.

- Pass each entry point to `--run` verbatim (Step 8)
- Never compose, wrap or rewrite a command

Proceed immediately to Step 2.

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
`agent_failed` means the agent itself errored at startup; relay its tab to the
operator, and once they fix the agent, run `down --task <task> --role dev` and
this step again.
Once `state` is `idle` or `done`, proceed immediately to Step 4.

## Step 4 — Write the Developer Brief

Fill `.tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/templates/brief-developer.md` for this task into a
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

`wait` returns by its default budget, `DEFAULT_WAIT_TIMEOUT_SEC` in
`skills/rex-teamlead/rexlead/team.py`; give the shell call a longer time
limit than that budget.

- `delivered: true` — read the report in full
- After reading it, proceed immediately to Step 7
- `state: blocked` — relay the `kind` and tab to the operator
- Once the operator answers, run this step again
- `delivered: false` with `state: done` — the worker stopped without a valid report
- Send it one correction brief through Step 5
- `state: error` — the agent itself failed; tell the operator which tab
- Once the operator reports it recovered, send one correction brief through Step 5
- `state: timeout` — the worker is still working
- Run this step again

## Step 7 — Confirm the Candidate

Confirm the commit the developer reported is the tip of its branch; a
mismatch goes back to the developer through Step 5:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh candidate --task <task> --role dev --sha <reported-sha>
```

The returned `candidate` is the commit under review. Proceed immediately to
Step 8.

## Step 8 — Run the Checks

A worker's `done` proves it stopped, never that its work is right. Run the
check commands from Step 1 yourself in the developer's worktree, one `--run`
each; never take them from the worker's report:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh check --task <task> --role dev --candidate <candidate> --run '<command>' [--run '<command>' ...]
```

Each command's budget is `--timeout`, default `DEFAULT_CHECK_TIMEOUT_SEC` in
`skills/rex-teamlead/rexlead/team.py`. Give the shell call a longer time limit
than the commands' total budget, or run it in the background.
`check_mismatch` or `check_dirty` means the worktree is not a clean checkout
of the candidate; send the developer one correction brief through Step 5.
`check_unsupported` means the check cannot vouch for this checkout: a
candidate with submodules, or a sparse checkout. It never goes to the
developer as a correction:

- Tell the operator the check cannot run on this candidate, quoting the `message`
- Continue only on the operator's instruction

Route on `verdict`:

- `pass` — proceed immediately to Step 9
- `fail` — send the developer one correction brief through Step 5, naming each failed command and its `exit`
- The brief tells the developer to rerun each failed command in its own worktree to see its output
- A second `fail` in a row goes to the operator with each failed command and its `exit`
- `invalid` — the check itself is broken, never a code defect for the fix loop
- Tell the operator each invalid command and its `reason`
- Stop until the operator reports the environment repaired
- Once repaired, run this step again

## Step 9 — Spawn the Reviewer

`<review-role>` is `review` for the first review
and `review-<n>` for the `n`th, so each candidate gets a fresh reviewer; use
the same `<review-role>` through Step 13. The reviewer gets a read-only
checkout of the candidate:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh spawn --task <task> --role <review-role> --agent <other-kind> --ref <candidate-sha> --read-only
```

Handle `not_ready` and `agent_failed` as in Step 3. `not_independent` means
the reviewer's agent matches the developer's; spawn again with another kind. Once `state` is `idle` or `done`, proceed
immediately to Step 10.

## Step 10 — Write the Reviewer Brief

Fill `.tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/templates/brief-reviewer.md` with the task, the
candidate SHA, the base from Step 2 and the check commands that passed in
Step 8 into a file outside the repository.
Proceed immediately to Step 11.

## Step 11 — Dispatch the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh assign --task <task> --role <review-role> --brief <brief-file>
```

Proceed immediately to Step 12.

## Step 12 — Wait for the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh wait --task <task> --role <review-role>
```

Route `blocked`, `timeout`, `error` and an undelivered report as in Step 6,
with the correction brief through Step 11. On
`delivered: true`, proceed immediately to Step 13.

## Step 13 — Gate the Candidate

Check the report reviews exactly the candidate and carries one verdict, then
read it in full:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh verdict --task <task> --role <review-role> --candidate <candidate>
```

`review_mismatch` or `no_verdict` sends the reviewer one correction brief
through Step 11. `candidate_mismatch` means the developer's branch moved past
the reviewed commit: run `down --task <task> --role <review-role>`, then
review the new tip under the next `<review-role>` from Step 7.

The reviewer's `verdict:` binds the round. Only the operator overrides it:

- `verdict: approve` — proceed immediately to Step 14
- Carry any advisory findings into the release
- An `approve` whose report lists a finding marked `blocking` is inconsistent
- Route an inconsistent `approve` as `changes-requested`
- A `changes-requested` whose report lists no finding marked `blocking` is inconsistent
- Route an inconsistent `changes-requested` as `approve`, carrying its advisory findings into the release
- `verdict: changes-requested` — run `down --task <task> --role <review-role>`
- Brief the developer with the findings verbatim through Step 5
- Confirm, check and review the new candidate under the next `<review-role>` from Step 7
- Stop after three such loops
- Bring the remaining findings to the operator

The reviewer's classification of each finding binds (`rules/review-severity.md` Two Tiers):

- Never reclassify a finding on your own
- Disagree with a `blocking` classification: ask the operator to reclassify it, naming the finding and your reason
- Reclassify it as advisory only on the operator's explicit approval
- Record each approved reclassification in the PR body at release: the finding, your reason and the operator's approval
- Without approval, the finding stays blocking
- A `changes-requested` whose every blocking finding the operator reclassified proceeds as an `approve`

## Step 14 — Close the Round

Close the worker tabs. Reviewer checkouts are removed; the developer's
worktree stays for the release:

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/rex-teamlead/rex-team.sh down --task <task>
```

Proceed immediately to Step 15.

## Step 15 — Release the Branch

Ship `team/<task>-dev` from the developer's worktree through the release
skill, whose cleanup removes that worktree and branch:

```text
Skill(skill: "release")
```

Finish here.
