# PR Review Triage and Response Planning

## Background

Your team has a policy reviewer bot and an advisory Claude code-quality reviewer that both post findings on every pull request. The release process requires you to respond to every review thread before a PR can be merged, and there are specific protocols for how to handle different types of findings.

You have been watching PR #47 (`feat(auth): add token expiry refresh on session restore`) in the `acme-corp/auth-service` repository. The watch script has returned its result and the full snapshot is saved at `inputs/review-snapshot.json`.

The snapshot contains reviews from two review lanes — the policy reviewer (`codex-policy`) and the Claude quality reviewer (`claude-quality`), both posting as `github-actions[bot]` — each of which has posted findings. The PR is currently in `changes_requested` state. Before you push any fixes or take further action, the team lead has asked you to produce a written triage and response plan so everyone is aligned on what will be done and in what order.

## Your Task

Read the review snapshot at `inputs/review-snapshot.json` in full. Then write a response plan document at `response-plan.md` that covers every review thread.

For each thread (the policy reviewer's body and every Claude quality inline comment), your plan must:

- Identify whether the finding is blocking or advisory and briefly explain why
- State the exact reply you will post to that thread when you respond
- Describe the concrete action you will take (fix, defer, or decline) and when

Your plan should also address what needs to happen after any code fixes are pushed to the branch — specifically any steps required to keep the review process running correctly.

Do not fix any code. The response plan document is the only deliverable.
