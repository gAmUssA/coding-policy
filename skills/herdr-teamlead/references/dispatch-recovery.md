# Dispatch and Wait Recovery

## Dispatch context

Keep the same `--task` identifier from initial development through all its
fixes. Omit `--fix-round` on the initial assignment; supply it on every fix.
Never trim or merge legacy identities. Retained fixes dispatch developer alone
with `--retain-context`; other roles get separate cleared assignments. Retention
requires matching confirmed history, live native-session continuity, and a
compatible verified tier. Missing evidence requires owner recovery, preserving
the original record and counter. Tiered dispatch requires current qualification.

After a recorded release clear, dispatch the next developer correction fresh
within the same task and allowance. No context-change permission is required.
Carry the release report, findings, original base, and cumulative count into
the brief. Step 12's full verification remains required before release.

After another role clears the developer, use `recover-role-clear` under the
owner-managed recovery contract below. Pass its same `--work` to plan and fresh apply.

Reuse an approved bounded correction plan while its scope and budget hold.
An unknown dispatch outcome pauses implementation for evidence-based recovery.
An identical completed retry returns its recorded result without sending again.

## Dispatch outcomes

Each outcome names where the round goes next. Only a dispatched worker can
produce a report, so Step 11 waits on exactly the roles that landed here.

- **Exit 0** — every role was dispatched. Proceed to Step 11.
- **Non-zero with a busy target** — the whole round was refused before any
  keystroke went out. Nothing was dispatched, so there is nothing to wait for:
  wait for that worker to reach idle and re-run this step, or re-run it with a
  plan that omits the busy worker. Do not go to Step 11.
- **Non-zero with `"status": "sent_but_not_started"`** — the message was sent
  and no turn began for that role. Read that worker's pane; do not re-dispatch
  on top of it. Go to Step 11 for the roles whose records say `started`, and
  treat this role as producing no report this round.
- **A worker failed on its clear command** — its assignment was never sent, and
  the round is short that role. Go to Step 11 for the rest; re-dispatch this one
  by re-running this step for that role alone once its pane is clear.
- **A refusal saying the screen did not change** — the clear was unconfirmed;
  no brief was sent to that worker. Read the pane and clear it by hand.
  Re-run with `--no-clear` once the pane shows a fresh session. Fresh fix
  rounds that require an automatic clear must re-run without that flag.
  `--no-clear` records `cleared: false, clear_reason: hand`;
  `--retain-context` records `cleared: false, clear_reason: retained`.
  Never retain the previous task's context or retain across a role change.
- **A refusal naming an unaccounted composer** — the worker's input line holds
  text the lead did not send. Inspect the recorded dispatch status before
  retrying: a refusal during send confirmation can leave an uncertain outcome. Read the
  pane and clear it by hand, or re-run this step with `--allow-recovery` once
  you know whose text it is. Codex sends no recovery key at all: its clear key
  exits an idle Codex.
- **A refusal the message does not cover** — report it verbatim and finish
  here. A dispatch nobody understands is not a round to wait on.
- `--dry-run` prints the context choice and commands without Herdr calls or
  state writes. It reads current recovery bounds but proves neither retained
  history nor live readiness. An older ledger requires `teamlead state` to
  perform its owner migration before this preview.
  It dispatches nothing; finish here after reading it.
- Each confirmed hand-off relabels that worker's pane with the work it took.
  `--task <label>` puts the round's task in the label. A hand-off that never
  started is left unlabelled.

The delivery mechanics behind those outcomes — composer confirmation, recovery
keys, ghost text, the rejection strings, the settle knobs — are in:

```text
skills/herdr-teamlead/references/herdr.md
```

The prompt text, the refusal predicate, and every constant are the utility's
own contract; see `skills/herdr-teamlead/teamlead/assign.py`.

Proceed to Step 11 with the roles that were dispatched.

## Owner-managed recovery

Resolve the plugin root for each call as in the skill's command blocks, then
use `bash "$CP/skills/herdr-teamlead/teamlead.sh"` for the commands below,
with the same `--state FILE` throughout. Every owner
mutation takes `--record FILE` containing a JSON object and optional `--now`.
Success prints the recorded object on stdout, exit 0. Exit 1 prints an
actionable JSON error on stderr; resolve that cause before proceeding. Do not
edit state.json or a prior assignment row by hand.

`authorization` is always `{"source": "<operator message reference>", "quote":
"<their actual words>"}`. Record existing authorization when it covers the
action. Never invent it, treat silence as consent, or convert a general shipping
instruction into permission to exceed an exhausted correction budget.

| Command | Record fields | Continuation |
| --- | --- | --- |
| `task` | `task`, original full `base_revision`, `scope`, `allowed_paths`, `authorization` | Register once before initial development; for legacy history, recover these facts from the original task and brief. Continue the same task. |
| `checkpoint` | unique `id`, `task`, concrete `defect`, `previous_attempts`, `progress`, `change_in_approach`, absolute `judge_report` | Requires the configured pinned judge's completed ruling after the latest developer attempt. Implementation waits for the bounded operator decision. |
| `authorize-corrections` | unique `id`, `task`, `checkpoint`, `scope`, `allowed_paths`, positive `additional_fixes`, `authorization`; optional `supersedes` | Store an explicit bounded approval once. Continue while it covers the next attempt; do not ask again within those bounds. A changed decision names the active plan in `supersedes`. |
| `record-report` | `dispatch`, full `head_revision`, `verdict` (`blocking` or `approved`), `review_mode` (`full` or `scoped`), independent `reviewer`, absolute `report`, `changed_paths` | Read the report in full and verify the VCS diff first. The command binds its bytes and stated head to the dispatch; it does not establish the tester, CI, external-review, or release gates. |
| `recover-context` | `task`, original `assignment_index`, `reason`, `authorization`, absolute `evidence` | For the latest confirmed developer row with null native-session proof. Records a live observation separately and permits the next fresh handoff. The original null stays null. |
| `recover-role-clear` | Fields under Verified role-clear recovery below | Record a fresh handoff after another authorized role automatically cleared the developer. Preserve known original proof and reuse existing correction bounds. |
| `reconcile` | `dispatch`, `outcome` (`applied` or `not_sent`), `reason`, `authorization`, absolute `evidence` | Resolve an interrupted send from actual evidence and an idle/done live worker. `applied` appends recovered assignment evidence without fabricating contemporaneous session proof; `not_sent` permits a transport retry. |
| `record-release-clear` | Fields under Verified release hand-clear below | Record existing required-clear evidence for a successful release `--no-clear` row. No new context-change permission is required. |
| `import-correction` | Fields under Historical manual corrections below | Import an already authorized, completed manual attempt without sending input or granting future attempts. |
| `record-historical-review` | unique `id`, `historical_attempt`, full `head_revision`, `verdict`, `review_mode`, independent `reviewer`, absolute `report` | Append an actual review receipt for an imported correction. Full review is required for approval; other verification gates remain separate. |
| `recover-report` | unique `id`, original `dispatch`, absolute `report`, `wait_receipt`, `pane`, `visible`, `source` | Append evidence of a completed delivery missed by the old watcher; see Completed native report recovery. No worker input or review approval. |

`allowed_paths` contains repository-relative paths or globs. Preserve the
original task and base across every approval. Read and verify the source diff
against the proposed scope; neither a worker's claim nor a receipt alone proves
the source change. Preserve the prior reports with their original bytes.

For an approved extra correction, pass the same `--task`, actual `--fix-round`,
`--correction-plan ID`, and `--work FILE` to plan and apply. Work JSON contains
`base_revision`, exact approved `scope`, repository-relative `paths`, and a
non-empty array of blocking `findings`. The owner validates the allowance
before tier selection and dispatch. Later authorized corrections keep the true
number and fresh top-tier behavior. Each subsequent correction within a plan
requires the preceding attempt's actual blocking review, recorded through
`record-report`. Approved or absent findings do not justify another attempt.

At budget exhaustion, return to the judge and a new concrete checkpoint.
Changed scope or a changed operator decision requires new explicit bounds;
unchanged in-scope work reuses its approval. Do not rename the task or reset
the counter. Every changed tip still needs full independent reviewer and
tester reports before release, plus the release skill's external reviews and
CI. A scoped recheck cannot replace that verification.

## Fresh sessions and interrupted sends

A normal cleared release assignment preserves the previous developer row and
permits its next early developer fix in a fresh session. Register the task's
original metadata if legacy history lacks it, then omit `--retain-context`
and `--no-clear`. Include the release result, current findings, prior reports,
original base, and cumulative fix count in the fresh brief. No additional
context-change permission is needed. The following fix may retain that new
session when the normal same-role continuity preconditions hold.

Fresh developer dispatch correlates the official native session after the
first prompt, including delayed IDs. A stale pre-clear identity, changed pane
or session, malformed identity, or exhausted correlation leaves null proof.
The confirmed assignment remains recorded; wait for its report. Use
`recover-context` for that missing proof only after collecting the actual
evidence and applicable operator recovery authorization. Its later live
observation never retroactively proves the original session.

Labelled dispatches persist a reservation before clearing or relaunching, and
mark sending before the assignment can enter the terminal. Confirmed results
are saved before cosmetic pane labels. `--dispatch-id` supplies a stable
caller identity; otherwise the owner derives it from the task, role, worker,
count, context choices, and brief bytes. Identical completed retries return
`replayed: true` without Herdr calls or another attempt. Changed inputs under
the same explicit identity refuse.

An interrupted reservation or send holds its slot. `sent_but_not_started`
also remains uncertain; idle alone cannot establish whether it happened.
Inspect the live worker and actual report or transport evidence, then use
`reconcile`. Never resend, delete the reservation, or consume another attempt
to work around uncertainty. Reconciliation records its later observations
separately and preserves any original unconfirmed assignment row.

`teamlead status` prints implementation state, confirmed fixes, active plan,
and remaining allowance. `waiting_for_operator` explicitly pauses implementation
while its checkpoint awaits approval; an audit worker may still be active.
`dispatch_outcome_unknown` requires reconciliation, and
`judge_checkpoint_required` requires the next exhausted-budget checkpoint.
Neither an active worker nor a dispatch receipt proves that implementation or
release has finished.

## Same-session YOLO restoration

A retained developer whose foreground process no longer proves YOLO mode — a
terminal restart, a bare relaunch, a restrictive launch — cannot receive its
early fix: `apply --retain-context` refuses before any input. When the operator
has expressly required YOLO for every worker, restore that developer's own
native session instead of clearing it or starting a fresh worker. The
restoration keeps the task, native session ID, fix count, and ledger rows
unchanged; it grants no attempt, review approval, or tier change.

Preconditions, all required: the worker is live and reads `idle` or `done`;
its `agent_session` from `herdr agent get <name>` equals the `context_session`
value of its preceding confirmed developer row in `teamlead state`, with
`kind: id`; its composer is empty; the operator's YOLO requirement is recorded
in the task ledger; the worker is non-tiered. A tiered retained fix verifies
exact launch argv (`verify_argv`) and accepts no resume form; use the
fresh-dispatch boundary for it. A pane that already holds only its shell has
no live identity to archive and is outside this procedure: do not resume the
ledger's UUID into an empty pane under it; recover through `recover-context`
or a recorded fresh handoff without resetting the counter.

Stopping the process is a relaunch of a worker with outgoing work. Before it,
run `retro-check` with one transition for this agent — `context: "start"`,
`role: "developer"`, the same `task`, null `model` and `effort` for a
non-tiered worker, its `pane`, and its outgoing `report` — and record the
completed note with `triggers` including `transition` under
`references/retrospectives.md`. That is the only retrospective the restoration
carries: the later `apply --retain-context` targets the same role, task, and
tier, so it demands no new transition coverage; only the daily cadence can
refuse it.

1. Inspect and archive under the task's evidence directory: `herdr agent get
   <name>` (state, `pane_id`, `agent_session`), `herdr pane process-info --pane
   <pane>` (exactly one foreground `<kind>` process with its `pid` and `argv`),
   and `herdr agent read <name> --source visible` showing the empty composer.
   Any other reading stops the restoration. Never inspect, stop, or start
   another worker's process.
2. Stop only that PID with `kill -TERM <pid>`, then run the shipped helper.
   It owns the wait for the pane to hold only its shell and for Herdr to
   release the old name (Herdr keeps it reserved briefly after the process
   exits), and the same-name restart:

   ```bash
   teamlead restore-session --agent <name> --kind <kind> --pane <pane> \
     --shell-pid <shell_pid> --stopped-pid <pid> -- <resume argv...>
   ```

   Inputs: the archived name, kind, pane, `shell_pid`, and stopped foreground
   `pid` from step 1; after `--`, the runtime's documented resume form, the
   explicit YOLO flag, and the unchanged model and effort options, every one
   a separate token:

   ```text
   claude: --resume <uuid> --dangerously-skip-permissions
   codex:  resume <uuid> --dangerously-bypass-approvals-and-sandbox
   grok:   --resume <uuid> --always-approve
   ```

   `<uuid>` is the archived `agent_session` value, never a substitute or a
   most-recent selector. The accepted and refused resume tokens are the
   validator's contract named in `references/model-tiers.md`; the helper runs
   that validator before any Herdr call and refuses what it refuses.

   Output: exit 0 and one JSON object on stdout — `agent`, `kind`, `pane_id`,
   the started `argv` Herdr echoed (required to equal the requested tokens),
   `release` (`attempts`, `shell_pid`), `start` (`attempts`,
   `name_taken_retries`), and the `started` agent record. Exit 1 and a JSON
   error on stderr, with no brief sent, when the pane never returns to its
   shell, the name stays reserved, the shell PID differs from the archived
   one, a foreign process holds the pane, the name is bound to another pane,
   Herdr data is malformed, the started identity or argv differs, or a start
   fails for any reason other than `agent_name_taken`. A start is retried only
   after `agent_name_taken`, and only once the pane and name re-prove
   released; a start whose outcome is unknown is never retried. The helper
   never renames, chooses another pane or session, changes model or effort,
   sends a brief, or writes owner state; the ledger binds the agent name. The
   polling limits, the retry allowance, and the release predicates are the
   constants and docstring at the top of
   `skills/herdr-teamlead/teamlead/restoration.py`. Any refusal ends the
   restoration: inspect the pane by hand.
3. Reverify before any brief. `herdr pane process-info` shows one `<kind>`
   process whose argv is the form above. Herdr reports no `agent_session` for
   the restarted process until its first turn. Read the runtime's own status
   view in the pane first (Codex `/status` names the session and reports Full
   Access); it must show the archived session and the YOLO permission mode.
   Then send exactly one readiness diagnostic through `herdr agent prompt
   <name>`: a read-only prompt in the same session that forbids tools, file
   edits, and any continuation of the task, and asks for one fixed word, such
   as "Runtime readiness diagnostic only, in this existing session. Do not run
   tools, edit files, or continue implementation. Reply exactly READY." It is
   not a dispatch and records nothing. Wait for the turn to finish, then
   `herdr agent get <name>` must report the same pane and an `agent_session`
   whose `value` equals the archived one. A missing, different, or malformed
   identity ends the restoration: report the concrete limitation, keep the
   original rows, and recover through `recover-context` or a recorded fresh
   handoff without resetting the counter. Never report an agent session by
   hand, edit owner state, or prime a different task.
4. Dispatch normally with `apply --retain-context --task <task> --fix-round
   <N>`. It verifies the resumed argv (`verify_worker_permissions`), the
   ledger's preceding confirmed round, retrospective cadence, and live native
   continuity before any input, then records `cleared: false, clear_reason:
   retained` with the unchanged session. Never edit state.json, an assignment
   row, or the session value by hand.

The resume grammar is verified against the installed help of Claude Code
2.1.266, Codex CLI 0.153.2, and Grok Build 1.0.24; recheck it when a CLI
upgrades. Herdr 0.8.2 passes the tokens after `--` to the executable and
returns the started argv. One live restoration is proven with Codex: the
exact UUID with explicit YOLO and unchanged model and effort resumed the same
session, the status view and argv confirmed it, the single readiness turn
produced the original identity, and the normal retained apply then dispatched
the correction. That run drove the `agent start` form by hand; the helper's
waits and retries are proven by deterministic transport tests, not yet by a
live run. Archive each live run's evidence beside the task ledger.

## Verified role-clear recovery

Keep the developer reserved until initial and early-fix verification resolves. If the lead
already reused that worker for another role and normal apply cleared it, retain
the original developer proof. Use `recover-role-clear` with:

- `id`: a stable unique recovery identity; `task` and `base_revision`: the
  registered original task and full base SHA.
- `assignment_index`: the preceding confirmed developer assignment;
  `clearing_assignment_index`: the same worker's actual successful automatic
  clearing assignment in another role, from `teamlead state`.
- `next_fix`: the actual next cumulative correction number;
  `correction_plan`: the existing bounded plan ID for an extra correction, or
  `null` within the original allowance.
- `work`: `base_revision`, exact authorized `scope`, repository-relative `paths`,
  and the non-empty array of current blocking `findings`. This is also the
  complete content of the `--work FILE` used for plan and apply.
- `clearing_authorization`: `{"task": "<clearing task>"}` to reuse its registered
  authorization. If it is missing, use `{"authorization": {"source": "<operator
  message>", "quote": "<actual words>"}, "evidence": "<absolute artifact path>"}`.
  Preserve the actual decision naming the clearing task and role. The owner
  verifies that its meaning covers the clear; silence grants nothing.
- `evidence`: the absolute path to the original JSON output of the clearing
  `apply`, either its full envelope or individual dispatch result; `reason`:
  the actual scheduling mistake and recovery circumstances.

The command reads the idle/done worker and appends one receipt binding the
original assignments, authorization, work and archived output. Its validation
contract is in `skills/herdr-teamlead/teamlead/role_clear.py`. The later native
observation stays separate from original proof; nondeveloper assignments may
have null native evidence even after their confirmed automatic clear. Recovery
never replaces those fields, increases allowance, or sends a brief. Missing or
conflicting evidence, stale attempts, unresolved dispatches, changed task/base,
unauthorized paths and exhausted bounds refuse without modifying history.

Continue with the same task, next fix, correction plan and `--work FILE` in
plan and normal apply. Omit `--retain-context` and `--no-clear`. Apply rechecks
receipts and the recorded work, performs live readiness, automatic clear, tier
and qualification checks, then counts its one confirmed developer dispatch.
An identical completed retry returns the recorded result without sending again.
Carry the earlier reports, blocking findings, original base and cumulative count
in the fresh brief. Full independent review and testing of the corrected tip,
external review and CI remain required before release resumes.

## Verified release hand-clear

After an automatic clear times out, inspect the actual conversation and empty
composer before resuming release with `--no-clear`. Preserve the clear evidence
and any observed native identity before sending the release brief. A hand-clear row
alone never proves freshness. For a subsequent source correction, register the
original task metadata and run `record-release-clear` with these fields:

- `id`: stable unique recovery identity; `task`: the original task.
- `assignment_index`: the preserved successful release row from `teamlead state`.
- `cleared_at`: the original timezone-qualified clear observation time, between
  the preceding developer and release assignments.
- `fresh_session`: `{"kind": "id", "value": "<actual native ID>"}` or kind `path`
  for an observed native session path. Use `null` when the original clear
  observation established the fresh conversation but exposed no native identity;
  never backfill it from a later observation.
- `verified_empty_composer` and `verified_fresh_conversation`: both `true` only
  after reading the archived clear evidence; never infer them from `--no-clear`.
- `fresh_quote` and `composer_quote`: verbatim archived observations proving
  those facts in their original context. The command checks the quoted bytes;
  the owner verifies their meaning before recording them.
- `evidence`: absolute UTF-8 artifact path containing the original fresh-conversation
  and empty-composer observations;
  `reason`: the concrete required-clear recovery circumstances.

The owner binds the artifact bytes, requires a live idle/done worker,
and records the later native-session observation separately. It refuses
the preceding developer's native identity, another task or worker, stale
assignment indices, incomplete proof, and conflicting identities. A later
changed or missing native identity neither replaces nor invalidates the archived
proof. Never substitute that later observation for the original clear. The old release
row stays `cleared: false, clear_reason: hand`.

After recording, dispatch the actual next developer fix without `--retain-context`
or `--no-clear`. The dispatcher performs its normal live readiness and automatic
fresh-session checks. This required workflow clear uses existing task authority;
do not request another context-change permission. Include all prior reports,
current findings, original base and cumulative count in the fresh brief.

## Historical manual corrections

Use `import-correction` only for completed operator-authorized corrections that
predate owner dispatch reservations. Read the original task brief, exact
operator decision and bounded plan, archived clear/send/start evidence,
completion report, and actual Git diff first. Register the original task/base.
Never fabricate a reservation, edit old assignments, rerun the completed attempt,
or import an uncertain transport outcome.

The JSON record contains:

- `id`, `task`, `agent`, original full `base_revision`, approved `scope`, and
  `allowed_paths` for the completed correction.
- The actual cumulative `fix_round`, `authorization` source and exact quote,
  and positive `authorized_first_fix` / `authorized_last_fix` from that decision.
  Verify its task, scope and budget in full; neither the command nor a keyword
  match decides the meaning of an operator's approval.
- `occurred_at`: the original timezone-qualified completion time; `checkout`:
  an absolute local checkout containing the actual commits.
- Full `previous_head` before the correction and resulting `head_revision`.
  Verify that the former is the actual pre-attempt head from the archived audit.
- Absolute `authorization_evidence`, `transport_evidence`, and completion
  `report` paths. Authorization and transport evidence may each be a non-empty
  array of distinct original artifact paths when the audit is split across files;
  preserve those files and their bytes instead of synthesizing a combined report.
  The authorization artifacts contain
  the actual quote, task and approved scope; the completion report names its
  full resulting head. Restore missing original evidence before importing.
- `transport`: `sent: true`, `started: true`, `clear: fresh` or `retained`, plus
  non-empty `sent_quote`, `started_quote`, and `clear_quote` copied verbatim
  from the archived transport artifact. Read each quote in its original context
  and verify it proves the stated event for this task/worker/attempt. The command
  checks that the quoted bytes are present; it does not interpret prose.
  These are owner attestations after inspection, never facts inferred from a
  worker being idle now. Old archives need no new dispatch ID or report-marker
  format; completion is bound to the original report bytes and resulting commit.

The command checks local Git ancestry from the registered base through the
pre-attempt and resulting heads to the checkout's current HEAD, reads the diff,
and checks changed paths against both task and correction bounds. It records
byte receipts and one clearly linked historical assignment with null native
continuity proof. No Herdr call, repository write, dispatch reservation, review
approval, or correction allowance is created. Old assignment rows stay intact.

Import missing attempts in order. An identical identity/input/byte replay is
idempotent; a different identity for the same attempt, relabeled completed
report/transport evidence, changed evidence, skipped
count, conflicting head chain, pending dispatch, or out-of-scope diff refuses.
All planning, status, next-fix validation and budget checks consume the imported
count. Five canonical fixes plus imported fix6 means six consumed attempts;
fix7 still requires its applicable judge checkpoint and bounded approval.

If an existing bounded owner plan still covers the next correction, reuse that
approval. Record the imported attempt's actual blocking review through
`record-historical-review` before spending another attempt within that plan.
The command binds the independent report to the imported head and preserves
every review receipt. Its identity/input/byte replay is idempotent; changed
bytes require a new review identity. The next correction rechecks the latest
blocking report's bytes. An approval or missing report cannot justify a fix.

An older other-task import preserves an otherwise proven live developer session.
Retained apply still checks the preceding task/count and current native identity;
unknown event ordering refuses before terminal input. Inspect the original event
evidence on a chronology refusal; never reorder or rewrite the assignment audit.
The assignment chronology contract is documented in
`skills/herdr-teamlead/state-schema.md`.

An imported attempt carries no retained-session proof. Its next otherwise
authorized correction can use the normal fresh handoff with reason
`historical_correction_handoff`, preserving the same task/base/count and all
live clear/tier/readiness checks. Include the imported audit receipt, completion
report and current blocking findings in that brief. The import grants no extra
attempt and satisfies no independent reviewer, tester, external-review or CI
gate. Full verification of every changed tip remains required before release.

Owner commands serialize access with a live OS lock. A competing command
refuses before dispatch; wait for the process holding the transaction. The
lock file can remain after exit; do not delete it to bypass an active lock.

## Wait outcomes

- **Exit 0** — the report is there. Continue to the next worker, then Step 12.
- **Exit 1** — the budget ran out. Read the pane with
  `herdr agent read <name> --source visible`. If the worker is still working,
  re-run this step for it: the script's own budget applies again, never a
  number chosen here. Otherwise go to Step 12 recording that it produced no
  report.
- **Exit 2** — a tool failure. Report the message verbatim and finish here; the
  round has no reliable view of any worker.
- **Exit 4** — the report file exists and the worker reads `idle` or `done` on
  consecutive polls, but the pane never showed the marker whole. This is not
  delivery. Read the live state with
  `herdr agent get <name>` and take the first continuation that applies:
  - The command fails — report its message verbatim and finish here, as for
    exit 2.
  - The state is `blocked` or `working` — re-run this step for that worker
    once. Other exits from that re-run take their documented branches. A second
    exit 4 is terminal: record the worker as producing no report and
    continue to the next worker.
  - The state is `idle` or `done` — preserve the negative receipt. For a native
    display failure with original source evidence, follow Completed native
    report recovery below. Otherwise record no report and continue to the next
    worker. Never re-dispatch on top of it. The
    marker may be wrapped, quoted, absent, or identify another attempt. Do
    not join rows or use a matching filename as proof. Step 7 requires a fresh
    report destination and bounds its length; narrow panes can still wrap it.
- **Exit 3** — the worker is blocked at an approval or question dialog,
  confirmed across two reads and the pane. Read the dialog with
  `herdr pane read <pane-id> --source visible`, relay its text to the operator
  verbatim, and stop the round for that worker. You never answer it: the
  operator does. Resume only once `herdr agent get <name>` reports a state
  other than `blocked`, then re-run this step for that worker.
- **Exit 5** — `reason: terminal_provider_refusal` identifies an unavailable
  attempt, with `found: false`. Record the missing report and tell the operator;
  every review/release gate remains unsatisfied. Do not automatically retry,
  rephrase, switch providers/models, reconstruct withheld output, or synthesize
  a report. Continue waiting on other dispatched workers; the operator decides
  how to handle the unavailable role under the existing rules.

`wait-report.sh` owns refusal confirmation; see its header and
`confirmed_provider_refusal`. Missing terminal evidence keeps the ordinary
wait. Herdr 0.8.2's bundled API schema has no dedicated provider-refusal
outcome; the watcher uses its documented pane/state surfaces. Synthetic
fixtures verify decisions, not live provider behavior or production elapsed
time.

Proceed to Step 12 once every dispatched worker has been waited on, or once you
have recorded which of them produced no report.

## Completed native report recovery

The watcher calls `teamlead probe-report` for native display evidence. Its
inputs are `--agent`, `--pane`, an absolute `--report`, positive `--lines`, and
the observed visible text on stdin. It reads native source and Herdr without
writing state or worker input. Success emits `found` and either confirmed
source evidence or an unconfirmed `reason`; tool failure exits non-zero.
The native-source and display predicates belong to
`skills/herdr-teamlead/teamlead/report_delivery.py`. Claude Code's own source
contract — its per-block JSONL rows, the `parentUuid` chain, both parallel
tool-call orderings, and where its transcripts live — sits beside it in
`skills/herdr-teamlead/teamlead/claude_native.py`.

A Grok `/new` keeps continuity null when Herdr repeats the pre-clear ID or no
pre-clear ID was observed. Wait normally; an unconfirmed native marker is not
permission to send the assignment again. The watcher never scans for a newer
transcript. Stale-ID recovery below requires the recorded pre-clear ID. If no
pre-clear ID was observed, stale-ID recovery is unavailable and refuses
with `grok_clear_identity_unproven`. If delivery remains unconfirmed, record the
report as unavailable and notify the operator of the missing pre-clear evidence.
Keep review/release gates unsatisfied; do not resend the assignment or reconstruct
missing evidence.

For an already completed affected dispatch, preserve its original negative
wait JSON, report bytes, native source transcript, visible pane text, and the
original `herdr pane get` JSON. Read these original artifacts and the saved
dispatch's common/role briefs. Use the existing task authority to record
delivery; this recovery requests no new work or allowance.

Even a refused owner operation can persist the recovery schema 3 → 4 migration.
Use isolated ledger copies for validation until the installed owner supports
schema 4.

Run `teamlead recover-report --record FILE --state FILE` through the owner
launcher above. The record names a unique `id`, the preserved `dispatch` ID,
and absolute artifact paths in `report`, `wait_receipt`, `pane`, `visible`, and
`source`. `source` is the original native transcript, not an agent-written
summary or a reconstructed message. The command verifies the archived native
user message against the original dispatch prompt and binds all evidence bytes
in a separate receipt. A nondeveloper's historical null session remains null.
Original dispatches, assignments, negative wait receipts and reports remain
unchanged; a later role/session does not require rerunning completed work.

For the known Grok `/new` identity contradiction, add `plan` naming the original
saved plan JSON to the recovery input. Preserve all other original input paths.
The command checks the original dispatch fingerprint against the plan, the
current briefing bytes and, for a dispatch from a bound round, the exact
`--report` path the record names. The strict source adapter and named refusal
contracts are
`grok_clear_identity`, `validate_stale_binding`, and `stale_grok_source` in
`skills/herdr-teamlead/teamlead/report_delivery.py`. Unknown dispatch options,
missing original plans, reused prompt paths, contradictory sources and changed
brief bytes refuse.
Do not build a replacement transcript, choose the newest filename, or rewrite
Herdr's archived pane JSON to make identities agree.

The new receipt keeps the archived `native_session` observation and adds a separate
`source_session`; neither proves live continuity. Source selection is explicit:
inspect the original native updates for this dispatch, then name that artifact.
Multiple session identities or multiple turns remain unconfirmed. Preserve all
candidate artifacts when the source is ambiguous; do not select one by recency.

Exit 0 emits the append-only delivery receipt. Identical replay returns that
receipt; conflicting bytes or identity fail. Read the report in full and
continue the existing round gate. This receipt establishes delivery only;
it grants no review approval, retained context or extra implementation attempt.
An error preserves the negative outcome; restore the named original evidence
or record the report as unavailable. Never fabricate missing source evidence.

## Native display validation

Use fresh isolated Herdr sessions for each installed native CLI. Ask each
worker to write a unique short report file and make its entire final response
the bare `REPORT: <absolute-path>` line. Capture the native transcript,
`agent get`, `pane get`, and `pane read --source visible` after completion.
Run `wait-report.sh` with that worker and report. Pass requires exit 0 and
`found: true`, the exact marker on one rendered row, bare final source, and
the current report file. Keep original negative and positive receipts as
separate artifacts when comparing watcher versions.

Verified on 2026-09-07 with Herdr 0.8.2, Codex CLI 0.153.2 and Grok CLI 1.0.13
using Grok 4.6: both fresh workers wrote their reports and completed. The old
watcher exited 4 for both. The patched watcher accepted the same untouched
sessions and files with exit 0. Automated source/display fixtures cover
missing, changed, wrapped, quoted, authored-list and indented-code markers,
incomplete/replaced sessions and source changes during verification.

Verified again on 2026-09-08 with Herdr 0.8.2 and Claude Code 2.1.263, in a
throwaway workspace whose worker shared no name or pane with a live round. The
worker wrote its report and rendered `⏺ REPORT: <path>` on one row. The old
watcher exited 4 with `marker unconfirmed`; the patched watcher accepted the
same untouched session and file with exit 0 and basis `native_final_source`.
The same code recovered an earlier completed Claude dispatch from its preserved
negative receipt and archived transcript, on an isolated copy of the ledger.

Four fresh sessions across that day's rounds settled the parallel tool-call
shape, which took two live rounds to see whole. Claude Code writes a
`tool_result` row linked to the `tool_use` BLOCK ROW that requested it. When
the results are flushed one at a time the rows still read linearly
(`tool_use`, its result, the next `tool_use`, its result); when both calls are
written before either result lands, the first result's parent is not the row
before it and the chain branches. Both orderings come from the same pinned CLI
and both must parse. A result names the call it answers, and a call is answered
once — including within a single row, where two blocks naming the same
`tool_use_id` used to ride along on that call's requester. Distinct ids issued
by one block row are still a legitimate multi-result answer. Give a Claude
worker a SHORT report path: a long one wraps in the pane, and a wrapped marker
cannot be told from a newline, so the watcher refuses it by design.

For a stale-Grok regression, complete a short turn in an isolated Grok process,
then let normal `teamlead apply` send `/new` and a fresh report-only brief. Save
that plan, apply output and original native updates. Verify Herdr still reports
the first turn's ID while the completed source identifies a different session.
The watcher must remain unconfirmed against the stale ID. Recover against an
isolated copy of the owner state with the saved `plan` and original artifacts;
pass requires a separate source identity, unchanged original rows and bytes,
null continuity and no review approval. A fresh process whose first identity
already matches its transcript does not exercise this regression. Keep config,
caches and transcripts report-local (`GROK_HOME` selects native source storage).
Never input to, clear or restart an active team worker for this validation.
