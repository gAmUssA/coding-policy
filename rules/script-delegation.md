---
alwaysApply: false
applyTo: "skills/**, scripts/**, hooks/** — when authoring deterministic scripts that skills or hooks invoke"
description: Deterministic operations → script, reasoning → LLM, the regex trap, script structure conventions, scripts as black boxes
---

# Script Delegation

## The Core Principle

- Everything deterministic → script. Everything requiring reasoning → skill/LLM
- If the logic can be expressed as a pure function with known inputs and outputs, it's a script
- If it requires judgment, synthesis, or context-dependent decisions, it stays in the skill

## What Belongs Where

- Script: database queries, math, file parsing, JSON normalization, fixed-logic API polling, data transformation — any operation where the same input always produces the same output
- LLM: synthesis across multiple sources, language generation, branching decisions that require situational context, anything where the "right answer" depends on understanding intent

## The Regex Trap

- Resist the over-eager urge to declare things deterministic on a regex hunch
- If the input has too many edge cases for a reasonable regex, it's reasoning — not scripting
- Parsing natural language dates, extracting meaning from unstructured text, classifying ambiguous input — these are **not** scripting tasks
- A script should only handle patterns that are fully enumerable

## Scripts Are Real Files

- Scripts are executable files that live in the plugin (e.g., `skills/release/watch-pr-reviews.sh`) — not code blocks in SKILL.md for the agent to copy-paste
- The skill references the script and runs it; the script does the work
- Code blocks in SKILL.md are for showing the agent what command to run, not for embedding logic the agent should reproduce character-by-character
- Narrow exception for the Herdr skills' installed-plugin bootstrap.
- Applies only to command blocks in `skills/herdr-teamlead/SKILL.md`, `skills/herdr-standup/SKILL.md`, and `skills/herdr-teamlead/references/round-setup.md`
- Preconditions (all required):
  1. The block initializes `CP` to the literal `.tessl/plugins/gamussa/coding-policy`; its only inline branch tests that directory and falls back to the same path under `$HOME`
  2. The block invokes only co-shipped scripts through quoted `$CP` paths with an explicit interpreter; each independent call repeats the bootstrap
  3. The bootstrap performs no writes, network access, permission changes, sourcing, or evaluation of repository-controlled code
  4. All work after root selection stays in the invoked script; no inline business logic, loops, or additional selection heuristics
  5. `skills/herdr-teamlead/tests/test_skill_invocations.sh` checks every covered block
- Every other command block follows Scripts Are Real Files unchanged

## Script Requirements

- Scripts follow the baseline in `rules/file-hygiene.md` (exit codes, stderr, idempotency, entry-point guard) plus:
- **JSON-producing**: output structured data on stdout, not prose
- **Self-error-handling**: exit non-zero on failure, write an actionable diagnostic to stderr (`rules/error-handling.md` Shell Error Handling)
- **Single-purpose**: one script does one thing — compose scripts, don't build monoliths
- **Tested**: a `tests/test_<name>.sh` harness beside it, discovered by `scripts/run-tests.sh`

## Black Box

- Skill prose names the script's contract — required inputs, output shape, exit codes, side effects, verbatim-posted text the script emits
- Skill prose does not restate the script's internal logic — thresholds, predicates, formulas, allowlists, poll intervals, budgets
- The script header is the source of truth; the skill points at it (`see <script-path> — named constants at the top of the file`)
- One reference per concept across a skill's files — reference from the file closest to where the agent reads the contract, never fan the same reference across `SKILL.md` and `references/*.md`
