---
bump: minor
---
### Changed

- **`herdr-teamlead` `SKILL.md` is now a step router; each step's detail loads only when the lead reaches it.** The lead loaded the whole 26 KB skill on every invocation, and `tessl plugin lint` warned about its size. `SKILL.md` keeps the 20 flat steps, the Step 1 mode gate and routing, and the command blocks no reference already carried; it drops to about 8.6 KB. The rest moved without changing its meaning: Steps 2–7 detail and command blocks into `references/round-setup.md`, which already carried most of them twice. Steps 8–10 moved into the new `references/round-dispatch.md`, the judge chain into `references/judge-round.md`, read only when a judge trigger fires, and Steps 18 and 20 into `references/round-completion.md`. Every bootstrap command block stays in a file the `rules/script-delegation.md` carve-out already covers, so the carve-out is unchanged. Adapted from upstream jbaruch/herdr-policy c2416d4a (#40).
- **`tests/test_skill_invocations.sh` follows the moved blocks.** It reads the carve-out's covered reference list from one array. It rejects a `$CP` bootstrap block in any other Herdr skill file, and it requires every `references/<file>.md` Step N pointer in `SKILL.md` to land on a real step heading. Both new guards are tested with a fixture they must reject and one they must accept.
