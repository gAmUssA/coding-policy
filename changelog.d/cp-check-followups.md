---
bump: patch
---
### Fixed

- **Stop Rex checks for an operator decision after repeated failures or unsupported checkouts.** Step 8 now names the stop and the continuation for a correction, rerun, or ended round. Both unsupported-checkout diagnostics direct the lead to the operator instead of suggesting a manual bypass.
- **Clean up interrupted check commands.** The check wait always kills its process group in `finally`; scoped SIGTERM and SIGHUP handlers let that cleanup run, alongside SIGINT. CLI regression tests interrupt a live command and its background child and assert both are dead. The command table and error-code list now name exit-1 `check_interrupted`, including interruptions during a run, alongside Step 8's routing. SIGKILL remains uncatchable; Linux-only `PR_SET_PDEATHSIG` was omitted to keep this fix portable and avoid adding a separate process-supervision mechanism. Processes that leave the group with `setsid` retain the existing limitation.
- **Keep validation reliable with small pipe buffers.** Feed Rex fixture events concurrently and handle short writes; macOS can shrink pipe buffers below the size of one event. The existing outcome-label test stalled on a 512-byte pipe before this change and passes with it. Standup prompt assertions now read here-strings so an early `grep -q` exit cannot make the producer fail with SIGPIPE under `pipefail`.
