---
alwaysApply: true
description: A reviewer's state classifies whether it gates the merge, not whether its body must be read.
---

# Reviewer Feedback Reading

## State Classifies Gating, Not Reading

- A review's state (`APPROVED`, `CHANGES_REQUESTED`, `COMMENTED`) classifies whether the review gates the merge — it never classifies whether the review's content must be read
- `COMMENTED` and "non-blocking" describe the merge gate, never permission to skip the review body
- Read every reviewer's review body in full and every inline comment body before judging that any item needs no action
- A `COMMENTED` review with zero inline comments still carries a body that must be read
- State classifies gating, severity classifies acting, neither classifies reading — read all, then act per `rules/review-severity.md`

## Read the Text, Not the Tally

- Review states and inline-comment counts are loop-control and gate signals, never a substitute for the words a reviewer wrote
- Fetch the actual review body and inline comment text; conclude an item is non-blocking only after reading it
- "Non-blocking" is a conclusion reached after reading, never a reason to not read

## Report the Substance

- Never report a reviewer as handled by its state label alone — "COMMENTED (non-blocking)" is not a summary of what the reviewer said
- Name each reviewer whose body you read and summarize the content, not the classification
- Lumping reviewers under one state label hides unread feedback — account for each reviewer's body separately
