# Script Today's Standup

## Problem Description

You lead a Herdr team of three agents, and it is time for today's daily standup. This sandbox has no live Herdr session, so instead of running it, write the script you would run to hold the standup from start to finish, ending with the result shown to the operator.

What you have from this morning:

- `inputs/roster.json` — the team roster as it read a few minutes ago.
- `inputs/round-log.json` — your round log for the work in flight.

Use `/tmp/standup-out` as the directory for the standup's reports and output.

## Output Specification

- `standup-plan.sh` — the script, end to end.
- `NOTES.md` — a short note for whoever runs the script next. Say where each worker's row comes from and why, and for each way a worker's answer can fail to arrive, say what the script does.
