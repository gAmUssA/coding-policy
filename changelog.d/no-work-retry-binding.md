---
bump: patch
---
### Fixed

- **A no-work retry's own brief must name the recovered checkout, and a spent receipt no longer takes an old retry ID** (#62). `brief_identity` masks the report path, so a retry brief whose report path prefixes its checkout path could share the original brief's identity while naming a different, never-verified checkout. Both retry gates (`prepare` and the pre-reservation `recheck`) now require the retry brief's worktree field to resolve to the receipt's canonical checkout. Separately, a listed `not_sent` retry ID was checked only while its receipt was the task's open one. After another retry applied and was refused, re-applying the old ID passed, was saved, and left the ledger unreadable. The ID's own receipt is now checked whatever the latest applied dispatch is, in `recheck` and in `reserve`, before any save. Proven by two regressions in `skills/herdr-teamlead/tests/test_no_work_recovery.py` that failed on the prior code.
