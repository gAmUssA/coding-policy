# Fleet supervision

The lead owns fleet observation separately from assignment acceptance and task
completion. The watcher never sends worker input. Herdr status, a report-file
change, and a visible marker candidate are observations to reconcile through
the report-delivery contract and task ledger.

## Bind and enroll

Run the installed `skills/herdr-teamlead/teamlead.sh` with explicit `bash` and
the plugin root resolved by the skill. The examples below name subcommands and
arguments; nonzero exits require handling the diagnostic.

Run `supervision-bind --state <owner-state>` from the lead's own
Herdr pane. It discovers `HERDR_PANE_ID` through a read-only pane lookup and
binds that pane's native Claude or Codex session identity. It refuses missing or
unsupported native proof. Do not bind from a worker pane or substitute a guessed
session ID. Preserve the canonical owner-state path through resume and handoff.

Binding writes the adjacent supervision document and a discovery record for the
native Stop hook. `--record <file>` accepts an explicitly verified binding with
`kind`, `value`, `cwd`, `herdr_env`, and `pane_id`; `kind` is the native identity's
`id` or `path`. The normal flow uses automatic discovery.

Enroll every dispatched assignment, including reviewer, tester, judge, and
release. Supply `apply --report <role>=<absolute-report-path>` for every role
alongside `--task`; use the exact fresh path from that role's brief. A bound
lead's apply refuses incomplete mappings before worker input. The dispatch integration records enrollment before sending input, so
an interrupted or unknown send remains visible. Manual/imported assignments use
`supervision-enroll --record <file>` with the following contract:

```json
{
  "id": "dispatch-identifier",
  "agent": "worker-name",
  "task": "original-task-identifier",
  "report": "/absolute/reports/worker.md",
  "pane_id": "w1:p2",
  "native_session": null
}
```

`native_session` preserves the native identity object when available. A null
identity is uncertainty, never proof of an unused or completed worker. A worker
can have only one active enrollment. Reusing an enrollment ID with different
assignment evidence refuses. The integration may append a refinement that fills
missing native identity after a confirmed send; it cannot replace known proof.

## Watch the fleet

Run `supervision-watch --state <owner-state>` as a foreground tool operation and
retain its real execution handle while it runs. Await that handle; a saved PID,
watcher row, or prior conversation promise is not a live handle. This command
provides no detached daemon, native asynchronous wake delivery, or automatic
restart after a turn ends.

The JSON result contains `reason`, `watcher`, `through`, and `events`. Existing
unacknowledged events replay before more worker reads. Each sweep observes all
active enrollments concurrently and stores changed observations. One worker's
failure does not conceal another worker's result. Failure events preserve a
bounded, sanitized diagnostic, the failed operation, and recovery guidance.
A failed visible-output read preserves any report evidence already observed. A quiet budget checkpoint
returns without declaring any work complete; continue the fleet loop while
obligations remain. Polling bounds, read timeouts, and heartbeat constants live
in `skills/herdr-teamlead/teamlead/supervision_runtime.py`.

For each event, read its assignment and reconcile the live source. Run the
report checkpoint `wait-report.sh --once` for delivery candidates and pending
rechecks. Read delivered reports in full before recording acceptance. Follow
its blocked/refusal and native-delivery recovery contracts. A lifecycle event
never satisfies those contracts. Record user-facing failures, questions, and
promised follow-ups in the attention queue before continuing housekeeping.

`supervision-status` returns binding, active assignments, unacknowledged events,
current hold, and watcher health. Health verifies the live process identity and
heartbeat; a reused PID is not the recorded watcher. A live process with a stale
heartbeat requires inspection of its existing execution handle, not a duplicate
watcher. A failed process probe is unverified liveness, never proof of absence;
restore process visibility before replacing the watcher. After the process is
proved absent or replaced, the next watch records
a durable loss event. Handle it before resuming observation.

## Handle and acknowledge

`supervision-drain` reads a snapshot without changing anything. Its `through`
sequence bounds subsequent acknowledgements. Save each lead outcome with its
report checkpoint, task-ledger entry, or attention-queue handoff evidence, then
run `supervision-ack --record <file>`:

```json
{
  "through": 7,
  "outcomes": [
    {
      "event": "event-7",
      "outcome": "Report checkpoint remains pending; no assignment accepted.",
      "evidence": ["/absolute/reports/checkpoint.json"],
      "pending": true
    }
  ]
}
```

An outcome requires saved readable evidence. `pending: true` schedules a durable
recheck using the script-owned default in
`skills/herdr-teamlead/teamlead/supervision.py` (`DEFAULT_RECHECK_SECONDS`).
Use optional `recheck_at` only for a known external wait time; it cannot precede
the acknowledgement. The watcher emits a new event when that recheck becomes
due even if status, report bytes, and pane output have not changed. A completed
observation outcome omits `pending` or sets it to `false`.

Acknowledgement does not accept an assignment, complete a task, close a user
question, or consume an event that arrived after `through`. Replaying an
identical acknowledgement preserves its original receipt and schedule.
Conflicting outcomes refuse without rewriting history.

Once the lead has reconciled an assignment's actual outcome in the task ledger,
run `supervision-resolve --record <file>` with `id`, `outcome`, and `evidence`
paths. It ends that enrollment's observation obligation only. Pending events
for the enrollment must already have handled outcomes. A missing worker can be
resolved as unavailable only after recording the concrete recovery/handoff;
resolution is never a substitute for the task's acceptance and release gates.

## Pause, hand off, and resume

Native Claude and Codex Stop hooks gate only the exact bound lead identity,
Herdr environment, pane, and working directory. Workers and unrelated sessions
receive no supervision gate. The hook performs read-only local checks and
never contacts or interrupts workers. Gating uses structured JSON, never shell
exit status. A missing interpreter or failed module startup emits an installation
diagnostic without inventing a binding; restore the installation before relying
on the backstop. Once the evaluator identifies a bound lead, unreadable state or
evaluation failures block that lead through the structured result.

Active enrollments or unhandled events block a blind stop, even when a
foreground watcher is currently alive. That foreground process cannot promise
supervision after the lead's turn ends. The hook's `stop_hook_active` value does
not waive this obligation.

For an actual user-held pause or an explicit handoff, first handle outstanding
events. Persist the user's decision or recipient/continuation handoff, then run
`supervision-hold --record <file>`:

```json
{
  "id": "pause-identifier",
  "kind": "waiting_for_user",
  "resume_condition": "User answers the saved deployment question.",
  "evidence": ["/absolute/reports/user-decision.md"],
  "dispositions": [
    {
      "member": "dispatch-identifier",
      "outcome": "This assignment awaits the saved user decision.",
      "evidence": ["/absolute/reports/TASK-LEDGER.md"]
    }
  ]
}
```

Use `kind: handoff` for a saved transfer. Every active enrollment needs its own
named disposition and evidence; a global pause cannot hide unrelated work.
The lead judges whether the saved authority and handoff are real. The utility
checks coverage and evidence receipts, not the meaning of the prose. A hold
changes no task state, worker state, acceptance, or user-question resolution.
New assignments or events invalidate its covered boundary.

If the lead changes its native working directory, rebind from that directory
before continuing supervision; the prior exact-directory binding does not gate
that new identity. Keep the same owner-state path.

On active resume, run `supervision-bind` for the current native lead, then
`supervision-resume`, `supervision-status`, and `supervision-drain`. Reconcile
sources and pending events before continuing the watch loop. Retain the
supervision document and binding records during worktree cleanup. Corrupt,
missing bound-owner, or unsupported state cannot prove that work is complete;
preserve it and restore the owner-written record.

## Persistence and command contract

The owner skill is `herdr-teamlead`. `supervision.py` alone writes the document
at `<canonical selected state>.supervision.json` through the existing locked,
atomic state helpers. Discovery records live under
`<default state directory>/supervision-bindings/<identity digest>.json`.
`supervision_hook.py` and display readers are read-only, perform no migrations,
and acknowledge nothing. Dispatch state and the Markdown task ledger retain
their own authority. The full shape is documented in
`skills/herdr-teamlead/state-schema.md`.

All commands support the common `--state` and injected `--now` timestamp. Bind
and watch may use `--herdr-bin`; readback and acknowledgement need no Herdr
connection or config. Successful commands emit JSON on stdout and exit zero.
Invalid inputs or unavailable required evidence emit the CLI's structured error
on stderr and exit nonzero. A normal watch deadline is a successful checkpoint.
