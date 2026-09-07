---
alwaysApply: true
---

# Error Handling

## Specific Exceptions

- Catch specific exception types, never bare catch-all handlers
- Let unexpected exceptions propagate
- Narrow exception for outer-boundary process contracts.
- Applies when a process boundary's caller reads non-zero exit OR invalid stdout as a silent-failure signal (agent hooks, network-protocol stdout contracts, IPC handlers)
- A propagating unexpected exception silently disables the contract
- Use the language's narrowest "everything except interrupts" form — Python `except Exception:`, Kotlin `catch (e: Exception)`, or the analogous form; never a form that traps interrupts or `SystemExit`
- Preconditions (all required):
  1. The catch line, the suppressor line, or a comment directly above either carries the literal grep token `outer-boundary-process-contract`
  2. Where a linter requires a catch-all suppressor, it sits attached to the catch (same line, or the immediately-preceding line where the formatter relocates trailing comments), never separated from the catch by any other line
  3. A comment directly above the catch names three things: the caller's silent-failure shape, what the catch emits, and why propagation breaks the contract
  4. Handler at the outermost process boundary — never an inner function
- Every other catch in the file still uses specific exception types

## Shell Error Handling

- Every shell script opens with `set -euo pipefail` (except under the carve-out below)
- Never suppress a failure — no `|| true`, no `|| :`, no `2>/dev/null` standing in for a handler
- A command that can legitimately fail gets an explicit `if` or `case` on its exit code, never blanket suppression
- Distinguish an expected non-result from a tool failure — `grep` exits 1 on no-match and 2 on error, and `|| true` collapses both, so an unreadable file or a bad regex reads as "nothing found"
- Silencing a tool's diagnostic while explicitly handling its failure is not suppression: `cmd 2>/dev/null || { echo "<actionable message>" >&2; exit 1; }` replaces a worse message with a better one
- Fail visibly does not require exit non-zero — best-effort work that legitimately continues past a failure emits a warning to stderr, never nothing
- An `EXIT` trap's final command status becomes the script's exit status — end cleanup handlers with `return 0` so cleanup never rewrites the outcome
- Narrow exception for aggregate-reporting scripts that drop `set -e`.
- Applies when a script runs independent checks and reports an aggregate (test harnesses, multi-engine diagnostics gates such as `scripts/run-diagnostics.sh`)
- Preconditions (all required):
  1. Each check is independent — a later one's result never depends on an earlier one having passed
  2. The script captures each check's exit code explicitly and exits non-zero when any check failed
  3. It keeps `set -uo pipefail` — only `-e` is dropped
  4. A setup step whose failure would silently corrupt the run rather than loudly abort it carries its own explicit failure check
- Every other shell script still opens with `set -euo pipefail`
- Narrow exception for `|| true` on a `source` of the script under test.
- Applies when sourcing runs that script's own `set -euo pipefail` in the harness's shell, and its entry-point guard then returns non-zero under the `-e` it just enabled
- Preconditions (all required):
  1. The suppressed command is the `source` itself, never an assertion or a command under test
  2. `set +e` follows on the next line, restoring the harness's own discipline
  3. The harness takes the `set -e` carve-out above
- Every other `|| true` in a harness still converts to an explicit exit-code check

## Actionable Messages

- Error messages must tell the user **what to do**, not just what went wrong
- Bad: "File not found"
- Good: "Config file not found at ~/.config/app.toml — run `app init` to create one"

## Graceful Fallback

- When multiple approaches exist, try alternatives before failing
- Example: try the preferred tool, fall back to an alternative, then fail with a clear message listing what was tried

## Structured Logging

- Log at appropriate levels: DEBUG for internals, INFO for progress, WARN for recoverable issues, ERROR for failures
- Include enough context to diagnose without reproducing: input parameters, relevant state, error details
- **Never log secrets**, tokens, passwords, or credentials — not even at DEBUG level
