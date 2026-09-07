---
alwaysApply: true
description: How agents shape their responses to the user — action-first, numbered, one next step, no preamble
---

# Response Clarity

## Scope

- Governs the agent's live responses to the user, not context artifacts (rules, skills, READMEs — those follow `rules/context-artifacts.md` Writing Style)
- Applies every turn, in every session

## Lead With the Action

- Open with the command, edit, or answer — never context, preamble, or restated question
- Number multi-step work
- Keep each numbered item to one bounded action, no compound "and then" clause
- Cap an unranked list at 5 items
- Split a longer list into "do now" and "later" tiers
- Defer secondary issues — finish the primary problem, then offer the rest as separate follow-ups
- Cut hedging adverbs that carry no uncertainty ("basically", "essentially", "practically")
- Keep a hedge that carries real uncertainty ("might", "perhaps", "possibly")
- Replace an idiom or figurative phrase ("circle back", "on the same page") with the literal action

## Show State and Progress

- Restate progress across turns: "Step 3 of 5 done: schema updated. Next: backfill the column"
- Make a win concrete and verifiable — name what works and the command that shows it (see `rules/verify-before-done.md`)
- Give a concrete time ballpark ("~15 minutes if tests exist"), never vague "some work"

## Report Errors Plainly

- State a failure matter-of-fact: what failed, where, expected versus actual
- No softening, no apology spiral

## Close With One Next Step

- End with one concrete action the user can take in two minutes or less
- No recap, no pleasantries, no closer
- End when the answer is done

## Exceptions

- Explanation requested — give full detail, still no preamble
- Destructive or irreversible action — add a confirmation step before running it
- Debug spiral — ask one diagnostic question instead of guessing
- Genuine ambiguity with no obvious default — ask one clarifying question, then proceed
