---
bump: patch
---
### Fixed

- **`stateful-artifacts` accepts any single owner: a skill, hook, runtime, or service**. The rule required an "owner skill", but `hooks/state-schema.md` documents throttle stamps that each belong to one hook, so our own tree could not satisfy it. Required Attributes, Migration Policy (including the operator-authored configuration exception, whose first precondition now reads "No component writes the file"), and Rename / Removal now say "owner", and the writer / reader contract names components. Every other requirement is unchanged. The policy reviewer prompt (`skills/onboard-repo/templates/prompt.md` step 5) now tells the reviewer to apply the widened owner definition and never demand an owner skill. Upstream's runtime-owned database-table bullets and its classification-approval change were not adopted: no artifact here is a runtime-owned table, and we never took the base Bounded Classification section. Adapted from upstream jbaruch/coding-policy 4e9694dd (#778).
