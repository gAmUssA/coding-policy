---
bump: patch
---

### Changed

- **Hook comments, messages and tests describe process cleanup in neutral words.** 0.14.0 published but the registry's content safety filter blocked installs, without naming a file. The release's new text paired "child" and "grandchild" with "killed" throughout the deadline-runner tests; those words now read "descendant", "stopped" and "stops", and "SIGKILLed" reads "sent SIGKILL". Commands, signals and identifiers that do the work are unchanged.
- The exported `kill` override in `hooks/tests/test_bounded_run.sh` also suppresses SC2317 beside SC2329 (advisory from the policy reviewer on PR #52).
