---
bump: patch
---
### Changed

- **The plugin carries a Tessl eval suite, and `rules/language-diagnostics.md` states that a style note never fails a script**. `evals/` holds ten scenarios generated from the plugin: PR format, the shell syntax gate, changelog fragments, review triage, onboarding, the merge and publish sequence, Herdr routing, trigger declarations, standups and the release gate. They run with `tessl eval run .`. The first run, on 2026-10-14 with the default model, scored the agent 98% with the plugin against 84% without it. In that run, an agent with the plugin marked a script failed over a shellcheck SC2181 style note. The rule already kept info and style findings out of the Stop hook's block, but did not say so for a report or a gate; it now does. Scenario 3's rubric penalized folding an advisory into an open blocking round, which `rules/review-severity.md` prescribes. It now accepts either disposition the rule allows. Six scenarios whose task text spelled out the convention under test were rewritten so that only the plugin supplies it.
