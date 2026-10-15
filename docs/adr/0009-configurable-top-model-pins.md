# Configurable Top-Model Pins for Judgment Rounds

## Status

accepted

## Date

2026-10-15

## Context

Judgment rounds (`JUDGMENT_ROUNDS` in `skills/herdr-teamlead/teamlead/tiers.py`) accept only a pinned top model per CLI kind at high effort or above (ADR 0007). The pins lived only in source (`TOP_MODELS`), so a model release reached a consumer only through a plugin release. Issue #47: upgrading a consumer from 0.7.10 refused a working config with a `config_error` at `preflight`, before roster, authority or headroom ran. The live CLIs already ran `gpt-6-astra`, `gpt-6.1-sol`, `opus-5-5` and `claude-fable-5-1`, none of them pinned. The error named no agent, model or accepted value. The `judge` block, meant to hold the most capable model, was not checked against the pins at all.

Options considered:

- **Bump the built-in list only.** Fixes today's models; the next release repeats the cliff.
- **Make the list fully operator-owned.** Lets a config drop or replace the built-in pins, so the floor becomes whatever a config says.
- **Built-in pins plus an additive config block.** Operators admit a new model the day it ships; the built-in floor stays intact.
- **For the judge:** keep it exempt and document why, or hold it to the same pins.

## Decision

1. `TOP_MODELS` gains the current CLI models; superseded ids stay so existing configs parse.
2. An optional top-level `top_models` block in config.json (`{kind: [model id, ...]}`) extends the built-in pins per kind. It never replaces them. Validation is strict: known kinds, non-empty arrays of distinct model ids. An id already built in is accepted, so a later built-in addition never breaks a config. The block requires config schema 4; schemas 1 to 3 stay readable, and one carrying `top_models` is refused with the instruction to set `schema_version` 4. The config is operator-owned, so the operator makes that bump.
3. The effective pins (built-in plus configured) apply to every consumer of the pin: tier-table parsing, risk escalation's top-model switch in `select_tier`, and the `judge` block.
4. The judge is validated, not exempt: when its agent is configured, its model must be a pin of that agent's kind and its effort high or above. A configured judge agent of a kind with no pins is refused. `preflight` checks the judge before any worker contact, and `start-judge` refuses a plan whose judge tier or agent kind differs from the current config or misses the floor.
5. Codex efforts gain `max` and `ultra`, the reasoning levels codex-cli 0.160.0 lists for its top models; a high-risk escalation never lowers them.
6. A refusal names the agent (or judge seat), the round, the model and effort, the accepted models and efforts for that kind, and `top_models` as the fix.

## Consequences

- **Easier:** a consumer tracks model releases with a config edit; the refusal says what to edit.
- **Harder:** a judge configured on a model outside the pins, previously accepted silently, is now refused until the model is added to `top_models` or the judge moved to a pinned model.
- **Later work must honor:**
  - configured pins only extend the built-in floor; no config path removes a built-in pin;
  - every new reader of the judgment floor uses the effective pins (`top_models_for`), never `TOP_MODELS` alone;
  - the judge stays at least as strong as the judgment-round floor.
