---
bump: patch
---
### Fixed

- **`onboard-repo` templates fail closed when no verdict exists**. `assert-no-secret-leak.sh` returned 0 (clean) when the auth file or the review output was missing, and `post-review.sh` accepted any valid JSON, so `{}` became zero findings and an APPROVE with no review behind it. The guard now exits 2 on a missing input, and `post-review.sh` refuses a result whose `summary` lacks the schema's `Policy loaded: N rule files from <dir>` indicator (N >= 1) or whose `findings` is not an array of objects, posting nothing. Only a well-formed advisory finding is non-gating; a malformed one counts as blocking. Raised by the scaffolded reviewer on gAmUssA/reels-producer-skill#69 (#85); the new reject cases fail on the old templates and pass on the new. Consumers refresh with `scaffold.sh --override`.
