---
bump: minor
---
### Added

- **Configurable judgment pins: `top_models` in config.json.** An optional top-level `{kind: [model id, ...]}` block extends the built-in `TOP_MODELS` per kind, never replacing it. Validation is strict (known kinds, non-empty arrays of distinct model ids); an id already built in is accepted so a later built-in addition never breaks a config. The effective pins reach tier parsing, `select_tier`'s high-risk top-model switch, and the `judge` check. No config schema bump: every config version tolerates the key. Decision recorded in `docs/adr/0009-configurable-top-model-pins.md`. Fixes #47, where a 0.7.10 config broke on upgrade because the frozen allowlist lagged the CLIs.

### Changed

- **Built-in pins track the current CLIs.** Codex adds `gpt-6-astra` and `gpt-6.1-sol`; Claude adds `opus-5-5` and `claude-fable-5-1` (`claude-opus-5-5` was already pinned). Superseded ids stay.
- **Codex accepts `max` and `ultra` effort.** codex-cli 0.160.0 lists `low, medium, high, xhigh, max, ultra` as `supported_reasoning_levels` for `gpt-6.1-sol`, `gpt-6-astra` and `gpt-5.6-sol` (`~/.codex/models_cache.json`); `codex exec --help` documents no effort enum. Risk escalation no longer lowers a `max` or `ultra` row to `xhigh`.
- **The judge block meets the judgment floor.** When the judge's agent is configured, its model must be a pin of that agent's kind (`top_models` included) at high effort or above; `preflight` now checks the judge before any worker contact. A judge on an unpinned model, previously accepted silently, is refused with the `top_models` fix named. Test fixtures that pinned the judge to retired ids (`claude-opus-4-6`, `grok-4`) move to current pins.

### Fixed

- **A refused judgment row says what to change.** The error names the agent, the round, the refused model and effort, the accepted models and efforts for that kind, and `top_models.<kind>` in config.json, with the same facts in the error details.
