---
bump: patch
---
### Fixed

- **`parse_claude_usage` reads Claude Code's inline `/usage` resets**. Newer builds print `Current session · Resets 5pm (America/New_York)` and can put `(America/New_York)` on the next line, with `N% used` after the bar. The matcher required the header alone on its line and only read a `Resets` line after the percentage, so `measure` raised "No usage windows found". The stacked layout still parses. A narrow pane that ends the header at ` · ` and continues with `Resets` is accepted, and the wrapped timezone is kept in the reset text. Proven by the captured pane in `skills/herdr-teamlead/tests/test_parsers.py`, which failed on the previous parser, plus a pane with no window header that must still be rejected.
