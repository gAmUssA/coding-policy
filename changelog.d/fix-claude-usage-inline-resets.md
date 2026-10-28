---
bump: patch
---
### Fixed

- **`parse_claude_usage` reads Claude Code's inline `/usage` resets**. Newer builds print `Current session · Resets 5pm (America/New_York)` and can put `(America/New_York)` on the next line, with `N% used` after the bar. The matcher required the header alone on its line and only read a `Resets` line after the percentage, so `measure` raised "No usage windows found". The stacked layout still parses. A narrow pane that ends the header at ` · `, or on the word `Resets`, keeps the reset text from the next line, including a wrapped `(UTC)` zone and a time phrase split across lines. A second `N% used` row under a label that already has one raises `ParseError` instead of dropping that window: a header wrapped after `Resets` had left `measure` reporting headroom 95 where the account week was 61. Proven by the captured pane in `skills/herdr-teamlead/tests/test_parsers.py` and by `skills/herdr-teamlead/tests/test_parsers_claude_layouts.py`.
