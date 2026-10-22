---
bump: patch
---
### Fixed

- **A cancelled CI check with no replacement now reads `ci.status: pending`, never `success`.** `skills/release/poll-pr-reviews.sh` dropped every cancelled bucket and judged only the live ones, so one cancelled required job next to any other passing check read `success` and `skills/release/watch-pr-reviews.sh` reported `ready`. That let a merge pass the Red exit of `rules/ship-on-green.md`. Supersession is now per check name on head: a cancel is settled only by a live run of the same name, and that replacement's result (pass, fail or pending) drives the status. A replacement that never appears is bounded by the watcher's own budget (`pending_at_budget`). New `test_poll_pr_reviews.sh` cases cover an unreplaced cancel, a replacement by pass, a replacement by fail, and one replacement that cannot settle a different cancel. New `test_watch_pr_reviews.sh` cases run the real watch and poll scripts with a fake `gh` on PATH. The unreplaced-cancel cases failed on the old filter, which read `success` and `ready`. Adapted from upstream jbaruch/coding-policy 7702ec29 (#774).
