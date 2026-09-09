# Model Tiers

## Configuration and supported workers

`config.example.json` is the operator-owned tier table. Config schema 2 adds
per-agent `tiers` and `launch_args`; schema 1 remains readable without tiers.
The utility never rewrites the operator's config. Copy the example into a new
file, preserve local agent names and UI options, then validate it with
`teamlead.sh plan --preview-tiers` before replacing a working configuration.

Each `tiers` entry maps a round type to:

```json
{
  "model": "opus-5",
  "effort": "high",
  "multiplier": 1.0,
  "billing_evidence": null,
  "qualification": []
}
```

Omit `effort` for a model that accepts no effort control. Omit
`billing_evidence` until measurements exist. An empty qualification array is
unqualified, never a passing trial. The example carries no invented live
measurement or qualification results.

Accepted round names, role mappings, model pins, effort enums, mechanical
eligibility, risk escalation, and supported launch options are the constants
and functions in `skills/herdr-teamlead/teamlead/tiers.py`. The parser rejects
unknown rounds and unsupported adapters. Claude Code, Codex CLI, and Grok
Build have launch adapters verified against their installed CLI help.
Antigravity's research column remains a future adapter; the example does not
declare an inactive agent or accept unused tier rows for it.

The issue's requirement that reviewer/tester judgment stays at the top tier
governs scoped rechecks too. This resolves the research table's conflicting
lower-tier recheck examples. The separate pinned judge remains outside the
rotating workers' tier tables and shares its configured usage window.

Every team worker starts in YOLO mode, including the pinned judge and release
worker. The lead's assignment classifier checks each brief against the task's
authorization and permitted actions before dispatch. Worker permission prompts
are not a second assignment gate; the brief's role, path, and authority limits
still apply in YOLO mode.

`launch_args` preserves supported UI options across restarts. The launcher
supplies each runtime's YOLO flags and refuses conflicting permission options
before stopping a worker. The permission contract is in
`skills/herdr-teamlead/teamlead/tiers.py` (`YOLO_FLAGS`, `worker_launch_args`,
and `verify_worker_permissions`). Tier flags, resume options, command
strings, and prompt operands remain forbidden in `launch_args`; change the tier
table to change the model or effort.

For initial manual starts through Herdr, pass the runtime's YOLO options after
`--`. Verify the resulting launch or foreground-process argv before sending a
brief. Existing workers, including non-tiered workers, require that same proof.
A non-tiered existing process also proves permission in the documented resume
form: the runtime's resume option or subcommand naming one explicit session
UUID as a separate token, plus its explicit YOLO flags. The accepted and
refused resume tokens are `RESUME_OPTIONS`, `RESUME_SUBCOMMANDS`,
`RESUME_REFUSALS`, and `SESSION_UUID` in the same module; pickers, most-recent
selectors, titles, forks, new session IDs, and prompt operands refuse. Tiered
proof (`verify_argv`) stays exact and accepts no resume form.
Do not clear a retained worker to repair a permission mismatch. Use the normal
fresh-dispatch boundary once the current assignment is resolved, or the
same-session restoration in `references/dispatch-recovery.md` when the
operator expressly requires YOLO for that retained developer.

Recheck model availability and CLI flag spellings when upgrading a worker's
CLI or changing a model pin. Refresh the qualification for every changed
model/effort/role configuration before using it live.

## Planning and dispatch

`plan --round ROLE=ROUND` selects a configured round type. Without it, the
role's default applies. `--round-context FILE` reads a JSON object keyed by
assigned role. A mechanical context has this shape:

```json
{
  "developer": {
    "task_kind": "apply_exact_patch",
    "spec_complete": true,
    "no_semantic_decisions": true,
    "whole_result_oracle": true,
    "exact_plan": true,
    "risk_flags": [],
    "files": 1,
    "input_bytes": 1000,
    "tool_retries": 0,
    "repair_rounds": 0
  }
}
```

These fields describe evidence the lead must establish from the task and its
oracle. They are not permission to declare judgment mechanical. The numeric
limits and escape conditions are owned by `tiers.py`, not by the lead.
Additional context fields cover homogeneous enumerated edits, failed gates,
prior High misses, and the named escape conditions. Supply `--fix-round` to
both plan and apply for a fix; a plan made for a different fix context is
refused. A new tiered role requires an explicit cost instead of inheriting an
unrelated fallback weight.

Default planning ranks only qualified candidates. `plan --preview-tiers` allows
inspection before commissioning; it does not authorize live dispatch.

Plan schema 4 carries `tiers`, `rounds`, and the task's `task_context` alongside `assignments`. Apply
records the requested task phase as `round` and the selected config row as
`tier_row`; escalation may select a stronger row and raise its effort. Apply
recomputes the tier from current config and round inputs, refusing a stale or
edited pair. `apply --dry-run` prints the requested tier and relaunch argv
without contacting Herdr or writing state. It does not establish live
qualification, readiness, process identity, or session continuity.

The owner validates correction allowance before selecting tiers. A bounded
approval does not lower a late correction's tier or reset its cumulative
number. A fresh developer handoff after a required release clear uses the
normal relaunch and qualification checks; verified retained fixes keep their
existing compatible model and effort.

Fresh tiered dispatch verifies the worker is idle in the expected pane and
its composer is empty, identifies its foreground process, terminates that
process, waits for the shell, and calls `herdr agent start` with the selected
flags. The returned worker identity and argv must match before the brief is
sent. A failure after termination may leave a shell or an unbriefed worker;
inspect the named pane before retrying. No command is sent to a working or
blocked worker.

`--no-clear` and `--retain-context` verify the running foreground process
arguments, including YOLO mode, instead of restarting it. If the process record lacks argv, the
transport reads `ps` for that same foreground PID. Older Herdr builds that
cannot supply the structured process record fail closed. A retained fix also
needs the existing task/fix history and live native session identity; it keeps
a compatible higher effort instead of restarting to lower effort.

Tiered assignment messages include the selected model, effort, and an input
`prompt_hash`. The hash covers length-framed bytes of the original assignment
message, COMMON.md, and the role brief, excluding the generated metadata
footer. Workers report observed CLI version, tokens, compactions, and quota
windows; unavailable observations remain unknown. These reports are
measurements, not substitutes for launch proof or the paired battery.

The executable launch and verification contracts live in
`skills/herdr-teamlead/teamlead/launch.py` and the argv builders in
`skills/herdr-teamlead/teamlead/herdr.py`. Herdr's installed 0.8.2 schema and
the [socket API](https://herdr.dev/docs/socket-api/) describe the start and
foreground-process response shapes. Banner or transcript text is never a
verification source.

## Billing-window evidence

Collect billing evidence before assigning a tier a separate pool. Run an
isolated mechanical round with its requested model and effort, recording all
visible quota windows before and after. Capture the CLI version, exact prompt
hash, and observation timestamp. For Spark, include the main weekly window
as well as Spark's window; for Sonnet, include Claude's shared weekly window.
Concurrent users of the subscription invalidate the isolation claim.

The `billing_evidence` object has this contract:

```json
{
  "schema_version": 1,
  "isolated": true,
  "model": "<exact model id>",
  "effort": "<effort or null>",
  "cli_version": "<observed CLI version>",
  "prompt_hash": "<SHA-256 of the executed prompt>",
  "measured_at": "<observation timestamp>",
  "before": {"<window name>": {"remaining_pct": 80, "reset_at": "<reset timestamp>"}},
  "after": {"<same window name>": {"remaining_pct": 79, "reset_at": "<same reset timestamp>"}}
}
```

Include every observed window in both maps, including unchanged ones. The
attribution predicate is in `skills/herdr-teamlead/teamlead/billing.py`.
Incomplete, reset, rounded-away, ambiguous, or differently bound evidence
produces `unknown`. `measure` records each configured tier's model, effort,
and window under `agents.<name>.tier_billing`, including skipped and failed
measurements. Unknown attribution earns no speculative cost reduction.
Declared shared-window membership remains in effect.

No live isolated billing result was available during implementation outside a
Herdr team session. The shipped example therefore uses unknown attribution;
its deterministic tests are synthetic evidence of behavior, not observations
about provider billing. Do not copy test evidence into a live configuration.

## Validation before live use

The lead collects a paired, blinded screen for each model/effort/role and then
a separate promotion battery for finalists. Keep planted defects and expected
severity out of both workers' prompts. Use the same prompt for the baseline
and candidate in each pair, record both reports, and score blocker detection,
severity, and judgment absorption against the hidden answer key. Fresh
workers and separately enumerated cases prevent reuse of earlier answers.

The operator records the real trial results in that tier's `qualification`
array. Each record contains `schema_version`, `role`, `model`, `effort`,
`screen`, `promotion`, and `canary`. Screen and promotion are arrays of:

```json
{
  "case_id": "<stable hidden case identifier>",
  "blinded": true,
  "baseline": {
    "model": "<baseline id>", "effort": "<effort or null>",
    "cli_version": "<version>", "prompt_hash": "<SHA-256>",
    "tokens": 100, "compactions": 0,
    "window_before": "unknown", "window_after": "unknown",
    "caught_blocker": true, "severity": "blocking", "absorbed_judgment": false
  },
  "candidate": {
    "model": "<candidate id>", "effort": "<effort or null>",
    "cli_version": "<version>", "prompt_hash": "<same SHA-256>",
    "tokens": 100, "compactions": 0,
    "window_before": "unknown", "window_after": "unknown",
    "caught_blocker": true, "severity": "blocking", "absorbed_judgment": false
  }
}
```

Replace unknown window values with observed window maps when available.
Missing measurements stay explicitly unknown; missing metadata never becomes
a passing result. `canary` contains `at` (a timezone-aware timestamp) and
`trials` using stable case IDs and identical prompt hashes from the recorded battery. Refresh it on the
required cadence. The sample sizes, passing predicate, and validity window
are the contract of `skills/herdr-teamlead/teamlead/qualification.py`.

Apply checks the chosen model/effort/role against this evidence before any
worker operation. A missing or expired record refuses live dispatch and
names the required recovery. Dry-run remains available to inspect the launch
plan. The existing pinned judge start path has its own launch proof and does
not qualify a new rotating tier.

The operator owns qualification and billing inputs inside config.json; these
readers never write or migrate that file. Their evidence records use schema
1. The state owner records the accepted qualification summary and launch
proof in schema-5 assignment rows; see `state-schema.md` for migration and
reader behavior.
