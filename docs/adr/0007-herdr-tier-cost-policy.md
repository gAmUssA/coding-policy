# Herdr Tier Cost Policy Without a Qualification Battery

## Status

accepted

## Date

2026-10-15

## Context

Herdr dispatches each worker on a tier: a model and effort that the operator's `config.json` assigns to a round type. Our live config has tier tables for the claude, codex and grok workers, so tier policy decides real dispatches and real token spend.

Several parts of the policy cost more than the safety they bought:

- **Qualification battery.** A worker could not run a tier live until the operator recorded, for that model, effort and role, a paired 5-case screen, a 20-case promotion battery and a canary under 7 days old (`teamlead/qualification.py`). No config held a recorded result. The battery therefore either blocked tiered dispatch or meant a weekly campaign of paid trial runs.
- **Judgment defaults on non-judgment seats.** Advisor and investigator defaulted to judgment rounds pinned to the top model, release to `release_adjudication`, and `test_plan` sat in the judgment set.
- **Tester escalation by name.** Every `hostile_verify` round ran at xhigh effort because of its name alone, and testers run in every round.
- **Pinned lead.** `lead` was a judgment round, so the lead, the longest-lived context in a round, ran on the top model.
- **Correlated verifiers.** Reviewer independence came only from contribution history. A verifier on the same model family as the author was never deprioritised.

Options considered:

- **Keep the battery, record trials.** This keeps a model-fit gate, but it costs a recurring paid campaign per model, effort and role, and it is the first thing to fail when a tier table changes.
- **Replace the battery with a sourced capability table.** Upstream jbaruch/coding-policy did this (`capabilities.py`), with a weekly consultation and provider fetches. It is roughly 1k lines and adds a new external dependency.
- **Retire the battery and rely on the guarantees already in place.** These are the judgment pin, independent review and test of every other round, and launch-argv proof.
- **For the defaults:** keep the expensive defaults, or bump the config schema with mandatory new rows, or add the cheaper rows additively with a fallback.

## Decision

1. **Retire the qualification battery.** Remove `teamlead/qualification.py` and its plan and apply gates. These still guarantee tier safety:
   - judgment rounds stay pinned to the top model at high effort or above (`JUDGMENT_ROUNDS`, `TOP_MODELS` in `skills/herdr-teamlead/teamlead/tiers.py`);
   - every non-judgment round's output is reviewed and tested by independent workers;
   - launch-argv proof (`verify_argv`) still binds every tiered dispatch to its configured model and effort;
   - the judge seat keeps its own model pin in the config's `judge` block.
2. **Default each seat to the cheapest round it allows:**
   - advisor and investigator default to a non-judgment `consultation` round;
   - release defaults to `release_mechanics`, and only a developer's `mechanical` round still needs the mechanical predicate;
   - `test_plan` leaves the judgment set;
   - the judgment round is requested explicitly with `--round ROLE=ROUND`.

   A table without the new row falls back to the role's earlier default when that row exists (`LEGACY_DEFAULT_ROUNDS`).
3. **Escalate tester rounds on evidence.** A tester round escalates on risk flags, context size or a recorded prior miss, never on its round name.
4. **Select the lead's tier.** `lead` leaves the judgment set. The lead's seat is a non-judgment `coordination` round, selected like a worker's tier. A `lead` row parses as the same seat under its old name.
5. **Prefer another model family as a tie-break.** For reviewer and tester seats, the planner breaks a headroom tie in favour of a worker whose kind differs from every recorded contributor's. This comes after headroom and before rotation and name, and it never excludes a worker.
6. **Keep existing configs and ledgers compatible:**
   - a tier row's `qualification` field is accepted and ignored (`RETIRED_TIER_FIELDS`), and `plan --preview-tiers` is a no-op; there is no config schema bump;
   - the owner-managed state ledger moves to schema 7, whose rows never carry a `qualification` summary. The owner's 6 → 7 migration drops it, a current row carrying it is refused as unowned, and a newer ledger is left untouched. Recovery dispatch evidence keeps its original bytes, and a reconciled pre-upgrade dispatch is promoted from a copy of its tier without the retired field (`skills/herdr-teamlead/teamlead/state.py`, `skills/herdr-teamlead/state-schema.md`).

## Consequences

- **Easier:**
  - tiered dispatch needs no trial campaign;
  - consultations, release, tester and lead seats run on cheaper rows by default once the operator adds them;
  - verifier seats gain model diversity at no headroom cost.
- **Harder:**
  - nothing measures a cheap tier's fit before it runs live, so a model that quietly misses defects is caught only by independent review and test, or by the judge;
  - operators must add `consultation` and `coordination` rows, and lower `hostile_verify` to `high`, to realise the savings;
  - until they do, the fallback keeps their old costs.
- **Later work must honor:**
  - a new judgment round goes into `JUDGMENT_ROUNDS` and keeps the top-model floor;
  - a cheaper default never removes the explicit judgment escalation path;
  - any future model-fit gate needs its own ADR;
  - the family preference stays a tie-break, never an exclusion;
  - an owner build older than schema 7 treats a migrated ledger as newer and reads no prior state, so every reader and dispatcher upgrades together.
