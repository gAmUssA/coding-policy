---
name: herdr-standup
description: >
  Hold a daily standup with the named Herdr agents and print a table the
  operator can find while scrolling back through a long session: ask each idle
  worker for four lines (DONE / PLAN / BLOCKED / REPORT), fill the busy ones
  from the round log, and render a banner-topped fixed-width block plus a
  Markdown record.
  Use when the user wants a standup, a daily status round, a summary of what
  each agent is doing, "what is everyone working on", or a status table for the
  team. Requires HERDR_ENV=1.
---

# Herdr Standup Skill

Process steps in order. Do not skip ahead.

A standup is not a round of work: nothing is dispatched, no context is cleared,
and no worker is interrupted. A worker that is mid-task keeps working and its
row comes from what you already know.

Each command block resolves `CP` to the project-local plugin, falling back to
`$HOME/.tessl/plugins/gamussa/coding-policy`. Run the resolver in every call.
Prose `skills/...` paths are relative to that plugin root.

## Step 1 — Roster the Team

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Emits `{"caller":{...},"agents":[{"name","kind","pane_id","state"}]}` for every
named agent other than your own pane. Exit 1 is a precondition (outside Herdr,
`herdr` absent); exit 2 is a herdr failure. On either, report the message
verbatim and finish here.

Split the roster by state:

- `idle` or `done` — ask them in Step 2.
- `working` or `blocked` — never asked. Their row comes from the round log or
  the assignment ledger, with what they are on, in Step 4.

An empty roster means there is no team to stand up. Say so and finish here.
Proceed immediately to Step 2.

## Step 2 — Ask Each Ready Worker

One call per idle or done worker:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-standup/standup-ask.sh" \
  <agent-name> <absolute-report-path>
```

Sends the standup question as a plain message and emits
`{"agent","report_path","state","sent"}`. Exit 3 means the worker was not ready
and nothing was sent — move it to Step 4's list. Exit 1 is a precondition,
including a report path longer than the script's limit (the worker's
`REPORT: <path>` line must fit one pane row), exit 2 a herdr failure. The question text and the four-line shape it demands are the
script's contract; see the header of
`skills/herdr-standup/standup-ask.sh`.

Proceed immediately to Step 3.

## Step 3 — Wait for Each Answer

Wait for each worker asked in Step 2:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-standup/standup-wait.sh" \
  <agent-name> <absolute-report-path>
```

Same argv, stdout, and exit codes as `wait-report.sh`. The
budget is the script's constant, never a number chosen here; see the header of
`skills/herdr-standup/standup-wait.sh`. Exit 1 means the worker did not answer
inside it. It is not chased twice. Move it to Step 4's list with what you know.
Exit 3 means a dialog is up — relay it and leave that worker to the operator.
Exit 5 means the report is unavailable after a confirmed provider refusal.
Record it as missing in Step 4 and notify the operator; never automatically
retry, rephrase, switch models/providers, or synthesize an answer.
Exit 4 means the answer file exists but the pane did not show the marker
whole. That is not an answer. Read the live state with
`herdr agent get <agent-name>` and take the
first continuation that applies:

- The command fails — report its message verbatim and finish here.
- The state is `blocked` or `working` — re-run the wait for that worker once.
  Other exits take their documented branches. A second exit 4 is
  terminal: move the worker to Step 4's list with what you know.
- The state is `idle` or `done` — move the worker to Step 4's list with what
  you know. `standup-ask.sh` refuses a report path too long for one pane row,
  so this outcome means the path bypassed it.

Proceed immediately to Step 4.

## Step 4 — Write the Rows Nobody Answered

For every worker that was busy, blocked, or silent, write one entry into a JSON
file for the renderer:

```json
{
  "<agent>": {
    "roles": "developer",
    "done": "<from the round log>",
    "plan": "<from the round log>",
    "blocked": "none",
    "note": "busy: <task>"
  }
}
```

`note` renders beside the agent's name, so a busy worker reads as
`grok (busy: refactor)`. Every field is optional. Take the content from the
round log or the assignment ledger — never from a pane read, and never from a
guess about what a worker is probably doing.
Use the lead's schema-1 task ledger under
`skills/herdr-teamlead/state-schema.md` for accepted completion. Read it without
writing or migrating it. Missing or unsupported records mean unknown completion;
dispatch status alone establishes none. Worker `DONE` answers remain self-reports.

If every worker answered, skip the file. Proceed immediately to Step 5.

## Step 5 — Render the Standup

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
python3 "$CP/skills/herdr-standup/standup-render.py" \
  --reports <round-reports-dir> \
  --now <ISO-8601> \
  --team "<label>" \
  --agent <name>=<report-path> [--agent ...] \
  [--roles <roles-json>] [--extra <extra-json>]
```

Writes `standup-<date>.md` into the reports directory and prints one JSON
object on stdout: `{"markdown_path","block","answered","unasked"}`, where
`block` is the fenced terminal table. Save the output to a file. Exit 2 means a
report file did not carry the four-line shape, naming the file — ask that
worker again, or move it to `--extra`. Exit 1 is a missing or unwritable input,
and the message says what to fix.

`--now` is required and never defaults to the clock. The column widths and the wrapping are the script's contract;
see the constants at the top of
`skills/herdr-standup/standup-render.py`.

Proceed immediately to Step 6.

## Step 6 — Relay the Block

Read saved attention using the team's recorded state override or default:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" catch-up [--state <state-file>]
```

Present nonempty `attention_markdown` before the table. Follow `attention` page
counts and `next_offset` before claiming every pending item was shown. The
read never resolves an item, updates a presentation marker, or contacts workers.
On non-zero, report that saved attention is unavailable and its diagnostic;
continue with the independently gathered standup. If there are no pending items,
proceed silently. Its lifecycle contract is:

```text
skills/herdr-teamlead/references/attention.md
```

Print `block` from the saved output verbatim (`jq -r .block <file>`), exactly
as the renderer emitted it. Do not reformat it, summarize it, or replace it
with prose.

Name `markdown_path` underneath, in one line. Add a provenance line identifying
worker-reported rows and rows whose completion was verified from the task ledger.
Label unavailable acceptance evidence as unknown; dispatch records prove none.
Add your own reading of
the standup only if a row carries something the operator should act on today —
a blocker naming another worker, or a plan that contradicts the round in
flight. Otherwise the table speaks for itself. Finish here.
