# Model Tiers

## Configuration and supported workers

`config.example.json` is the operator-owned tier table. Config schema 2 adds
per-agent `tiers`; schema 1 remains readable without tiers. The full version
gate list is in `skills/herdr-teamlead/state-schema.md`.
The utility never rewrites the operator's config. Copy the example into a new
file, preserve local agent names and UI options, then validate it with
`teamlead.sh plan` before replacing a working configuration.

Each `tiers` entry maps a round type to:

```json
{
  "model": "opus-5",
  "effort": "high",
  "multiplier": 1.0,
  "billing_evidence": null
}
```

Omit `effort` for a model that accepts no effort control. Omit
`billing_evidence` until measurements exist. The example carries no invented
live measurement. A `qualification` field left from the retired validation
battery is accepted and ignored (`RETIRED_TIER_FIELDS` in
`skills/herdr-teamlead/teamlead/tiers.py`); delete it at the next config edit.

Judgment rounds accept only a pinned top model for the agent's kind at high
effort or above. The built-in pins are `TOP_MODELS` in
`skills/herdr-teamlead/teamlead/tiers.py`. The optional top-level
`top_models` block in config.json adds ids per kind without waiting for a
plugin release:

```json
{
  "top_models": {
    "codex": ["gpt-6-astra", "gpt-6.1-sol"],
    "claude": ["claude-fable-5-1"]
  }
}
```

The ids extend the built-in set for that kind; they never replace it, so a
built-in pin stays valid whatever the block lists. Keys are the supported
kinds (`claude`, `codex`, `grok`); each value is a non-empty array of distinct
model ids. Listing an id that is already built in is accepted. The block
requires config `schema_version` 4; an older-version config carrying it is
refused with the instruction to set 4. The same pins
apply to every judgment round, to risk escalation's top-model check, and to
the `judge` block, whose model and effort must meet its agent kind's pins and
floor. `start-judge` re-reads that block and refuses a plan whose judge tier
or agent kind no longer matches it. A refused row names the agent, the round, the model and effort, the
accepted values, and `top_models` as the fix.

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

Metered team workers start in YOLO mode, including the pinned judge and release
worker. Local pi uses the intrinsic-execution profile documented below. The lead's assignment classifier checks each brief against the task's
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
UUID as a separate token, plus its explicit YOLO flags. Which selectors that
form accepts and which it refuses is the grammar in the same module —
`RESUME_OPTIONS`, `RESUME_SUBCOMMANDS`, `RESUME_REFUSALS` and `SESSION_UUID`,
under the comment block above them. Tiered proof (`verify_argv`) stays exact
and accepts no resume form.
Do not clear a retained worker to repair a permission mismatch. Use the normal
fresh-dispatch boundary once the current assignment is resolved, or the
same-session restoration in `skills/herdr-teamlead/references/dispatch-recovery.md` when the
operator expressly requires YOLO for that retained developer.

Recheck model availability and CLI flag spellings when upgrading a worker's
CLI or changing a model pin. Codex's `max` and `ultra` efforts follow the
reasoning levels codex-cli lists for its top models; recheck them on a Codex
CLI upgrade.

## Planning and dispatch

`plan --round ROLE=ROUND` selects a configured round type. Without it, the
role's default applies: the cheapest round its contract allows
(`DEFAULT_ROUNDS` in `skills/herdr-teamlead/teamlead/tiers.py`). Request the
judgment round explicitly when the work must settle something:

- `--round investigator=reconciliation` for the exhausted-allowance diagnosis
  input, or after a recorded prior High miss
- `--round advisor=architect` when `detect-triggers` fired `security`, or for
  the trust-boundary assessment
- `--round release=release_adjudication` when the release worker must
  interpret a dispute; an ordinary dispute still goes to the judge

`consultation` and `test_plan` are non-judgment rounds and may name a non-top
model. The example's rows for them are a starting point, not a measured
result. A table without the cheaper default row falls back to the role's
earlier judgment default when that row exists (`LEGACY_DEFAULT_ROUNDS`); copy
the example's row to take the cheaper default. Risk evidence still escalates
any round: a non-top model moves to the `review` row, Claude and Codex rise to
xhigh, and Grok rises to high, its ceiling.

The lead's seat is the `coordination` round: a non-judgment row selected like
a worker's tier, never pinned to the top model. A `lead` row from an earlier
table is the same seat under its old name and parses as non-judgment. Only the
judge seat keeps a model pin.

A tester round's floor is its configured row: the `hostile_verify` round name
alone never raises effort. Escalation reads only the round context's risk
flags, input size and prior-miss evidence.

`--round-context FILE` reads a JSON object keyed by assigned role. A
developer's mechanical context has this shape:

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

Only the developer's `mechanical` round requires this predicate. A
`release_mechanics` round needs none; the release skill's own gates fail it
loudly. These fields describe evidence the lead must establish from the task
and its oracle. They are not permission to declare judgment mechanical. The numeric
limits and escape conditions are owned by `tiers.py`, not by the lead.
Additional context fields cover homogeneous enumerated edits, failed gates,
prior High misses, and the named escape conditions. Supply `--fix-round` to
both plan and apply for a fix; a plan made for a different fix context is
refused. A new tiered role requires an explicit cost instead of inheriting an
unrelated fallback weight.

Plan schema 4 carries `tiers`, `rounds`, and the task's `task_context` alongside `assignments`. Apply
records the requested task phase as `round` and the selected config row as
`tier_row`; escalation may select a stronger row and raise its effort. Apply
recomputes the tier from current config and round inputs, refusing a stale or
edited pair. `apply --dry-run` prints the requested tier and relaunch argv
without contacting Herdr or writing state. It does not establish live
readiness, process identity, or session continuity.

The owner validates correction allowance before selecting tiers. A bounded
approval does not lower a late correction's tier or reset its cumulative
number. A fresh developer handoff after a required release clear uses the
normal relaunch checks; verified retained fixes keep their
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
measurements, not substitutes for launch proof.

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

## Model fit without a battery

No trial battery gates a tier. Judgment rounds stay pinned to a top model
at high effort or above, every other round's output is independently reviewed
and tested, and the judge seat meets the same pins. Launch-argv proof still
binds every tiered dispatch to its configured model and effort.

The operator owns billing inputs inside config.json; these readers never write
or migrate that file. Billing evidence records use schema 1. The state owner
records launch proof in assignment rows; rows written before the battery's
retirement may also carry a `qualification` summary, which readers ignore. See
`state-schema.md` for migration and reader behavior.


## Optional local pi evidence seats

Config 5 supports pi 1.4.0 with the explicitly selected static Ollama loopback
profile. Commission with `teamlead.sh start-local --config FILE --state FILE
--agent local_pi --pane PANE --task TASK`. The pane must hold an idle shell.
The host gate runs before pi contact; see `teamlead/host.py` for its predicate.
The adapter supplies the disabled-extension/MCP profile and the session
directory. It invents no YOLO switch: `--approve` concerns project trust,
not tool permissions. Extra profile overrides and resume are refused.

Pi allows investigator and parallel tester roles, using consultation and
test_plan rows. Select parallel tester evidence explicitly with
`--round tester=test_plan`; ordinary tester planning retains metered judgment. Its explicit capabilities include repo-read and command-run.
The tester row collects command evidence for lead verification; it does not
replace the independent tester of record. No pi reviewer, judge, development,
release adjudication or risk-escalation tier is accepted. `allowed_roles` and
`top_models` cannot waive these limits. Existing metered configurations remain
readable; adding pi to an untiered fleet keeps those legacy candidates.

The commissioned model resolves its model-level URL before its provider URL.
Only the captured static loopback profile is supported; auth overrides and
other versions require new runtime evidence. No endpoint, config, model or
installation is created on the operator's behalf.

Measure records local_unmetered capacity with null percentage and no usage
commands. Host-ineligible or absent local seats are expected skips. Probe or
identity failures remain visible failures. The planner prioritizes eligible
local seats within an equally familiar affordable field and never charges a
subscription pool for them. Apply rechecks eligibility before transitions and
starts a fresh recorded process; retained/no-clear dispatches are unavailable.
Delivery uses the dedicated native source contract in state-schema.md.
