# Herdr Reference for the Team Lead

Everything the `herdr-teamlead` skill needs to know about driving [Herdr](https://herdr.dev),
a terminal multiplexer that recognizes coding agents running in its panes.
Gathered from a real three-agent round on 2026-09-01 that built
`github.com/jbaruch/agentic-context-registry` with workers `claude` (Claude
Code), `codex` (OpenAI Codex CLI), and `grok` (Grok CLI).

`herdr --skill` prints the authoritative control skill shipped with the
installed binary. `herdr agent`, `herdr pane`, `herdr workspace` (each group
with no subcommand) print the current command surface. When this file and the
binary disagree, the binary wins.

## Am I Inside Herdr

```bash
test "${HERDR_ENV:-}" = 1
```

Herdr injects the caller's own identifiers into every managed pane:
`$HERDR_WORKSPACE_ID` (`w1`), `$HERDR_TAB_ID` (`w1:t1`), `$HERDR_PANE_ID`
(`w1:p1`). Outside Herdr, say so and stop — do not drive a session you are not
part of.

Control commands print JSON on stdout. A server error is JSON on stderr with
exit 1; a syntax error exits 2.

## Roster and Names

```bash
herdr agent list
herdr agent rename <pane-id> <name>
```

`agent list` returns `{"result":{"agents":[...]}}`. Each element carries
`agent` (the kind: `claude`, `codex`, `grok`), `agent_status`
(`idle|working|blocked|done|unknown`), `pane_id`, `cwd`, and an optional
`name`. Names match `[a-z][a-z0-9_-]{0,31}`, are unique among live agents, and
follow the pane occupant — a name is cleared when that agent exits or is
replaced. Name each worker once; every later command addresses it by name.

`skills/herdr-teamlead/roster.sh` is the skill's read of this surface — see its
header for the contract.

## Driving a Worker

```bash
herdr agent prompt <name> "<text>"          # text + Enter, bracketed paste honored
herdr agent prompt <name> "<text>" --wait   # wait for the first settled idle/done/blocked
herdr agent wait <name> [--until <state>] [--timeout <ms>]
herdr agent send-keys <name> esc            # also ctrl+c and other logical keys
herdr agent get <name>
herdr agent read <name> --source visible|recent|recent-unwrapped --lines <n>
```

`agent prompt` refuses with `agent_blocked` when the agent sits at an approval
or question dialog, before sending any input. A prompt sent from a non-working
state must produce an observed lifecycle change within 5 seconds, otherwise
Herdr returns `agent_prompt_stalled` rather than waiting forever.

## Why Reports Are Files

Claude Code and Grok render on the terminal's **alternate screen**. Rows that
leave it never enter Herdr's host scrollback, so a bigger `--lines` cannot
recover them. `herdr agent read` additionally fails with `agent_not_idle` while
the agent is working.

Every substantive worker output therefore travels through a REPORT FILE. Pane
reads are for lifecycle state and the last few lines — never for the work
product.

## State Flicker Is Structural, Not a Bug

None of the three workers has a lifecycle-authority integration. Herdr
classifies Claude Code, Codex, and Grok from the live bottom-buffer screen
snapshot, matched against TOML manifests, and the hooks that
`herdr integration install` adds provide session identity and restore only —
Claude Code and Codex need no restart for them
([agents](https://herdr.dev/docs/agents/),
[integrations](https://herdr.dev/docs/integrations/)).

A screen-derived state flickers by construction: it is whatever the pane looked
like at the moment of the read. The report file plus the `REPORT: ` marker is
the report-delivery signal. The lead assesses the actual work and records
acceptance separately in `references/task-ledger.md`.

Observed in the 2026-09-01 round:

- `herdr agent wait claude` returned `done` several times while Claude Code was
  mid-task, between tool calls and while streaming.
- Grok reported `working` while sitting idle at startup.
- Codex read `blocked` for a single read while working with nothing on screen.
  It runs in Full Access, where a permission prompt resolves itself before
  anything can observe it.

No Herdr status establishes task completion, even across repeated reads. Report
delivery is the conjunction: the report file exists on disk AND the worker's final
message ends with the literal last line `REPORT: <path>`. The reliable wait
primitive is

```bash
herdr pane wait-output --match 'REPORT: ' --source visible --lines 40 --timeout <ms> <pane-id>
```

followed by verifying the file. `skills/herdr-teamlead/wait-report.sh` does both;
its header carries the contract, its constants the poll interval and budget.

`blocked` gets the same treatment as `done`: it is terminal only when two reads
a confirmation delay apart both say `blocked` AND the visible pane shows a
dialog row. A lone `blocked` read keeps the wait alive. The delay and the
marker list are constants at the top of `skills/herdr-teamlead/wait-report.sh`.

Herdr is deliberately strict about `blocked`: it reports it only when the live
bottom-buffer snapshot matches known visible approval, question, or permission
UI, and an unrecognized prompt defaults to `idle`
([agents](https://herdr.dev/docs/agents/)). A `blocked` that vanishes on the
next read is therefore a real dialog that flashed past — a permission prompt
auto-resolving under Codex's Full Access, for instance — rather than a
misclassification. Confirming across two reads is what tells those apart.

When a classification surprises you, ask herdr why before theorizing:

```bash
herdr agent explain <name-or-pane-id> --json
```

`pane wait-output` searches the existing snapshot before it polls, so a marker
left in the scrollback by an earlier round can satisfy the wait immediately.
Clear the worker's context between rounds and verify the file's content belongs
to the current round.

`pane wait-output` is also not trustworthy on its own: it timed out against
Grok's modal on the alternate screen while the dialog was plainly up. Treat a
failed wait as a warning and confirm the marker in the text that gets parsed —
`herdr agent read` on a bounded poll. Match a marker with `--match`, never
`--regex`: the marker is literal config text, and a regex engine only adds a
second opinion about what an escaped space means. Pass `--lines` on every read.

### A Stale `working` Is Not a Busy Worker

Herdr derives the lifecycle state from the agent's window title, and that title
goes stale. Grok keeps reporting `working` after a `/usage` dialog has been
opened and dismissed while it sits at an empty prompt; taken at face value that
refuses the agent forever.

The utility therefore confirms a `working` verdict against the pane before
acting on it. It reads
`herdr agent read <name> --source visible --lines 40` and decides from the
footer. Two invariants shape the probe:

- `blocked` is never probed. Herdr recognizing an approval dialog is a positive
  signal, not a stale one.
- The bias runs toward refusing. A false `working` costs a skipped round; a
  false `idle` types over somebody's work. Anything the probe cannot read
  confidently stays inconclusive, and inconclusive keeps the refusal.

Every measured record says which signal decided: `state_source` is `herdr` or
`probe`, and `herdr_state` carries what herdr claimed. A probe that overturns
herdr warns on stderr and the run continues.

The footer signatures live in the config, never in code — a CLI that restyles
its footer is a config edit. Two optional per-agent keys drive it:

| Key | Meaning |
| --- | ------- |
| `idle_markers` | Literal footer text meaning "at the prompt". Empty (the default) means never probe, and herdr's verdict stands |
| `working_markers` | Literal footer text meaning "mid-turn". Checked before `idle_markers` |

Both are literal substrings matched against the last non-empty footer rows.
Shipped defaults are in `skills/herdr-teamlead/config.example.json`; the precedence
order and the row count are in `skills/herdr-teamlead/teamlead/probe.py`.

## Sending a Slash Command Is Per-Agent

There is no one way to deliver a slash command. The TUIs disagree, and each
worker names its own mechanism in config as `slash_delivery`:

| Value | Mechanism | Ships for |
| ----- | --------- | --------- |
| `paste` | `herdr agent prompt <name> <text>` | claude |
| `type` | `herdr pane send-text <pane> <text>`, then `herdr pane send-keys <pane> enter` | codex, grok |

Both failure modes were found live:

- **Pasting into Grok.** `agent prompt` delivers through the pane's live
  bracketed-paste mode, and Grok reads `/usage` as a chat message — it reasons
  about billing and starts reading files instead of opening the panel.
- **Pasting into Codex.** Codex opens an autocomplete popup on `/` and swallows
  the Enter that `agent prompt` sends. `/new` sat unsent in the composer,
  `agent wait --until idle` returned at once (Codex never left idle), and the
  assignment was pasted onto the end of it — Codex received
  `/newNew assignment from the team lead…` and rejected it.

The tell for a new worker: a command that lands as prose wants `type`. An
unrecognized value is a config error, never a silent fallback. An assignment
message always pastes — it is a message.

### Sending Is Not Receiving

Every slash command is confirmed after it goes out. The composer row is the
last row starting with the worker's `composer_glyph`, and the check is whether
the command text is gone from it:

1. Composer empty — consumed, carry on.
2. Command still there — one more Enter, then re-read. An extra Enter on an
   empty composer is harmless.
3. Still there — fail that worker, and never send the assignment. Pasting a
   brief onto a stuck command is the accident this exists to prevent.

**Dim text is not occupied text.** Claude Code pre-fills its input box with a
ghost-text suggestion after a task (`check the other issues (#28, #30) for
follow-up work`). Nobody typed it, Esc does not remove it, and the next
keystroke replaces it. A plain-text read cannot tell it from a real command, so
the composer is read with `--format ansi` and characters rendered dim or in the
grey palette are dropped before the decision. Bold counts as deliberate, and
normal-weight text typed after a suggestion still counts, which keeps a real
command from hiding behind one. `composer_ignore_dim` turns this on per worker
(`true` for claude, `false` elsewhere), and a herdr that cannot produce ANSI
falls back to plain text with a warning. The SGR codes are in
`skills/herdr-teamlead/teamlead/composer.py`.

**A runtime's empty-composer placeholder is not text either.** Codex draws
`Ask Codex to do anything` whenever nothing is typed. Live, that read as
somebody's input, the one recovery keystroke went out — `ctrl+c` — and ctrl+c
on an empty Codex composer EXITS Codex. The process died and had to be
restarted. Two independent guards stop it now: dim text is dropped, and
`composer_placeholders` lists the hint verbatim so an exact match after
trimming counts as empty even if the runtime stops drawing it dim. A command
typed over a placeholder is still a command.

### Recovery Keys Are the Most Dangerous Thing the Lead Sends

They clear somebody's input line, and on Codex the key that does it kills an
idle process. `recover_keys` go out only when every one of these holds:

1. The composer is genuinely occupied — non-empty, not a placeholder.
2. The read carried ANSI. A plain-text fallback can only refuse: without
   intensity, a placeholder is indistinguishable from typed text.
3. The text is not dim. `--allow-recovery` cannot override this.
4. The worker configures `recover_keys` at all. **Codex ships with `[]`**.
5. The text is a command the lead itself sent earlier in this run, or the
   operator passed `--allow-recovery`.

By default nothing recovers text the lead did not type: it refuses and names
the pane for a human to look at. Sent once, never twice.

A worker with no `composer_glyph` cannot be checked, so the read is skipped
rather than spent on a row nothing can interpret.

### Sending Is Not Starting

A leftover `/` from the clear turned an assignment into `/New assignment …`.
Claude Code answered `Args from unknown skill`, no turn ever began, and the
round reported as applied. Every assignment is now confirmed:

1. The clear gets a settle delay before the next paste — the redraw racing the
   paste is what left the `/` behind.
2. The transcript must show the message as a user message, and `unknown skill`
   / `Unrecognized command` must NOT appear. Either one fails that worker
   immediately, with the recovery: send `esc` once, then resend.
3. The worker must leave idle, or the transcript must show the prompt.

Neither confirmed records `"status": "sent_but_not_started"`, warns on stderr,
and exits non-zero. Reporting a hand-off nobody started is worse than reporting
nothing. Each record carries `landed`, `started`, and `status`.

`sent_but_not_started` is an observation, never a diagnosis: a worker that
finished a very short turn before the check looked is indistinguishable from
one that never began.

`cleared: true` in the `apply` output is earned, not assumed: the clear command
was consumed AND the pane's content changed (a fresh Codex banner, an emptied
Claude transcript, Grok's redrawn `session_start`). Consumed but unchanged is
refused before the brief goes out — nothing is pasted onto a context that was
not cleared. `cleared: false` appears only under `--no-clear`, the path for a
pane the lead cleared by hand.

Three per-agent config keys drive it:

| Key | Meaning |
| --- | ------- |
| `composer_glyph` | Prompt glyph starting the composer row (`"› "` Codex, `"❯ "` Claude, `"│ ❯"` Grok). Empty skips the check |
| `composer_ignore_dim` | `true` (the default for every kind) reads dim/grey composer text as empty — ghost-text suggestions and placeholders alike |
| `composer_placeholders` | Hint text a runtime draws in an empty composer, matched exactly after trimming (`["Ask Codex to do anything"]`). Always counts as empty |
| `recover_keys` | Keys that clear a stuck composer, sent at most once and only under the five conditions above. **Empty for Codex**: its clear key is `ctrl+c`, which exits an idle Codex |
| `model_label` | Model name shown on the worker's pane after a dispatch (`"gpt-5.6"`). Optional; empty leaves the label carrying the role alone |
| `slash_delivery` | `paste` or `type`, per the table above |

The placeholder list is per runtime and hand-maintained: a Codex release that
reworded its hint would reintroduce the failure, which is why the dim check
sits in front of it rather than behind it. Two guards, either one sufficient.

Dim detection is SGR parsing, not semantics: a runtime that draws a real draft
in grey reads as empty, and one that draws its suggestion at normal weight
reads as occupied. The per-worker setting keeps a runtime with no ghost text
away from either.

Glyph matching is signature matching: a TUI that restyles its prompt makes the
composer unreadable, which surfaces as an unsent command going uncaught — the
very failure the check exists for. The glyph lives in config, so the fix is an
edit. The screen-change half cannot tell a cleared session from a repaint
either; it gates a status flag, never a keystroke. Both live in
`skills/herdr-teamlead/teamlead/composer.py`.

### Tracing a Live Run

`--trace` (or `TEAMLEAD_TRACE=1`) prints every herdr invocation to stderr with
its exit status and its output, while stdout stays the machine-readable
document.

Traced text is never raw. Pane output is whatever the worker had on screen, and
a worker that just ran `gh auth status` has a token there. Every traced field,
argv included, is redacted for credential shapes and then capped per field with
an explicit `[truncated N bytes]` marker; redaction runs before the cap, so a
truncation cannot slice a token in half and emit the front of it. The masked
value's key name survives, so the trace still says what was sent.

The shape list and the cap are the script's own constants — see
`SECRET_PATTERNS` and `TRACE_FIELD_CAP_BYTES` at the top of
`skills/herdr-teamlead/teamlead/herdr.py`. It is signature matching, never proof: a
shape absent from that list reaches the sink, and the fix is a pattern there
rather than a guard at a call site.

`pane send-text`, `pane send-keys`, and `agent send-keys` exit 0 with **empty
stdout**. herdr's stdout contract is per-command: most control commands return
JSON, `agent read` returns raw pane text, and the pane writes return nothing.
Judge a write on its exit status alone.

A `type` worker needs a pane id, which comes from the same status read that
gates the refusal. A worker herdr reports with no pane is refused rather than
typed at blindly.

## Reading Subscription Headroom

Each worker reports its own remaining budget through its native slash command,
sent as a prompt. Never send one to an agent that is `working` or `blocked` —
it would queue as a prompt and land in the middle of that agent's work.

| Kind | Command | Rendering | What it prints |
| ---- | ------- | --------- | -------------- |
| Claude Code | `/usage` | Full-screen dialog on the alternate screen; read with `--source visible`, close with `send-keys esc` | `Current session N% used`, `Current week (all models) N% used`, plus per-model weeks |
| Codex | `/status` | Box in the transcript | `Weekly limit: [...] N% left (resets ...)`, `5h limit: ... N% left`; a line like `GPT-5.3-Codex-Spark limit:` starts a secondary-model section, and the lines after it belong to that model |
| Grok | `/usage` | Two renderings — see below | `Weekly limit: N%` (used), plus `Credits: $X` |

**Grok has two renderings, and a restart can switch them.** On a fresh session
`/usage` prints inline text in the transcript (`Weekly limit: 0%`,
`Next reset:`, `Credits:`). After a restart it opens a boxed modal
(`Weekly limit (X Premium+)`, a bar row carrying the percentage, `Resets:`,
`Credits:`) that must be closed with Esc (`close_keys: ["esc"]`,
`usage_read_source: visible`). Both parse to the same `Weekly limit` window, so
nothing downstream cares which one appeared; the modal also reports a plan name,
carried as the informational `plan` field. The modal is painted OVER the
transcript, so each row carries transcript text outside the box — the parser
reads the interior between the row's first and last `│`.

A usage dialog can also open on the wrong tab: Grok's has three (Context usage,
Usage limit, Session info). The optional `dialog_next_tab_keys` per-agent key
names the keys that cycle it, tried a bounded number of times when the marker is
absent. Empty (the default) means never tab. Pressing keys into somebody's
dialog is the last resort, after the wait and the read poll. An unclosed dialog leaves the pane
holding a modal, which flips the agent to `working` and swallows the next
prompt — the close keys go out even when the wait, the read, or the parse
failed.

Headroom is the **minimum** remaining percentage across an agent's windows. A
worker with 100% of its week and 2% of its five-hour window has 2% of headroom;
handing it the developer role stalls the round in ten minutes. Grok's credit
balance is informational and never feeds headroom — a dollar balance and a
percentage are not the same currency.

## Naming the Layout

A sidebar of `w1 w2 w3 w4` tells the operator nothing at 3am.
`skills/herdr-teamlead/label-workspaces.sh` names the lead's workspace `lead`,
each worker's workspace after its agent, and each worker's pane after its kind.
Run it once per team, not once per round.

After a CONFIRMED hand-off, `apply` relabels the worker's pane with the work:
`<role> #<task> · <model>`, dropping whichever parts the round did not supply.
The agent's name is deliberately absent — the workspace row above already
carries it, and repeating it spends the sidebar's width saying the same thing
twice. A hand-off that never started is not labelled: a pane claiming a role
nobody began is worse than an unlabelled one.

The sidebar's own layout is the operator's, not the skill's. Herdr reads
`~/.config/herdr/config.toml`, and setting the agents rows to
`[["state_icon","pane"],["state_text"]]` with the spaces rows to
`[["state_icon","workspace"]]` stops the two lists repeating each other once
the panes carry roles. This skill never writes that file; the operator does.

## Clearing Context

| Kind | Command |
| ---- | ------- |
| Claude Code | `/clear` |
| Codex | `/new` |
| Grok | `/new` |

Each goes out by that worker's own `slash_delivery` path and is confirmed
consumed before anything else is sent. Wait for the agent to settle before the
brief, so the brief does not land in the clearing dialog.

## Worker Runtime Quirks

- **Grok does not follow includes.** It reads `AGENTS.md` and Claude's settings
  but does NOT follow rule includes. The brief must name the resolved
  `POLICY_INDEX` and tell the worker to read it and every linked rule file.
- **Same plugin, every worker.** All workers run from the shared checkout with
  the same tessl plugin installed, so the same hooks and skills load at session
  start and the same policy governs every role.
- **Bare worker starts can enable permission prompts.** Follow the YOLO
  launch contract in `references/model-tiers.md` for every start and restart.
  Read the installed CLI's help when upgrading an adapter; a runtime's default
  mode or an old pane banner never proves the required launch flags.
- **One GitHub account.** The workers usually share the operator's GitHub
  account, and GitHub refuses `APPROVE` / `REQUEST_CHANGES` on that account's
  own PR. Internal reviews are therefore COMMENT reviews, and the lead enforces
  the blocking findings itself.

## Parsing Note

Every number above comes from text an agent chose to draw on a terminal. A CLI
update that renames a window label or reorders a status box breaks the
corresponding parser. The utility fails loudly on an unreadable pane
(`ParseError`, the agent listed in `failed_agents`, exit 1) rather than
reporting a silently wrong number — treat a parse failure as "measure that one
by hand", never as "it has no headroom".
