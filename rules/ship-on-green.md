---
alwaysApply: true
---

# Ship on Green

## Green Gate Is the Approval

- Required checks green and none of the exits below apply ⇒ merge. The green gates ARE the approval
- Do not pause to ask a human whether to merge
- Flag, confirm, "say go and I'll", "worth one confirmation", "want me to?" are asking in a costume — each resolves to asking, and asking IS the decision to not ship
- Governs every merge, whether or not `skills/release` was invoked — an agent running the flow by hand is bound identically

## Stakes Raise Care, Not Permission

- Scary, wide-reaching, always-on, ships-to-prod, deletes-a-thing ⇒ verify harder — more tests, tighter review, a pristine-checkout lint
- A change being high-impact is never itself an exit — stakes buy care, never permission

## The Three Exits Are Exhaustive

- Merge is blocked only by one of these three, each with a literal test — no fourth exit, no vibe word:
- **Red** — a required check is failing or pending. Not "I feel uneasy"
- **No undo** — `git revert` cannot restore it: force-push over protected history, an immutable published artifact, deleted data, sent external comms. A supersedable version bump is undoable; shipping to prod is NOT "no undo"
- **Murky** — the task's own source states 2+ conflicting options AND names no default. Source names a default ⇒ take it, never murky

## Relationship to Other Rules

- `skills/release/SKILL.md` Step 7 references this rule for the merge decision rather than restating it
- The release contract's watch and verify steps (`rules/ci-safety.md`) feed the Red test; they add no fourth exit
- Advisory findings never gate (`rules/review-severity.md`), so an unaddressed advisory does not make a gate Red
