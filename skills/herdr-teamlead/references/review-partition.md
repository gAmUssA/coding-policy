# Review Partition

The document `validate-partition` checks. One reviewer per slice, so a change
reaches a state that means "reviewed": a slice is saturated when its reviewer
reports clean at the current tip, and the change is reviewed when every slice
is saturated at one tip.

`rules/agent-team-operation.md` Review Before PR carries the contract this
format serves. Write the document only for a round filling several seats of one
role; a single-seat round needs none.

## Format

```json
{
  "schema_version": 1,
  "role": "reviewer",
  "slices": [
    {"name": "api",  "paths": ["src/api/*", "docs/api/**"]},
    {"name": "core", "paths": ["src/core/*", "README.md"]}
  ]
}
```

- `schema_version` — `1`. Any other value is refused.
- `role` — optional, `reviewer` when absent. The role the slices seat. It never
  contains `#`.
- `slices` — at least two. Each is `{name, paths}` and carries nothing else.
  - `name` — non-empty, unique within the document. It names the slice in its
    reviewer's brief and in that reviewer's report.
  - `paths` — a non-empty array of globs matched against the round's changed
    paths, `fnmatch`-style (`*` does not stop at `/`; `**` is ordinary text).

## Validate before planning

```bash
teamlead.sh validate-partition --repo <repo> --base <base> [--head <head>] \
  --partition <partition.json>
```

Exit 0 emits the ownership payload — every slice with the changed paths it
owns, plus the full changed set:

```json
{"schema_version": 1,
 "slices": [{"name": "api", "paths": ["src/api/routes.py"]},
            {"name": "core", "paths": ["README.md", "src/core/db.py"]}],
 "changed": ["README.md", "src/api/routes.py", "src/core/db.py"]}
```

Exit 1 names everything that cannot carry a verdict, in one run, with the paths
in `details`:

- `unowned` — a changed path no slice owns; a gap reads as a clean slice
- `overlaps` — a changed path more than one slice owns; two verdicts, no owner
- `empty` — a slice owning no changed path; a worker spent for no verdict

A slice party to an overlap is not also reported empty: that overlap is why it
owns nothing. Fix the document and re-run. Dispatch only once it exits 0.

## Seating

One `plan` run fills one reviewer seat. A partitioned round therefore runs its
slices as separate reviewer dispatches against the same tip: plan and apply
each slice as an ordinary `reviewer` round, with that slice's brief, excluding
the workers the earlier slices already used so each slice gets its own
reviewer. Assignments stay keyed `reviewer`, which is what dispatch resolves
briefs, requirements and round tiers by.

Filling several slices from ONE plan is #434; the `<role>#<slice>` seat name is
reserved for it and means nothing today.

Each slice's brief names its own slice and forbids roaming. An observation
outside the slice belongs in a separate section of that report and forms no
part of that slice's verdict.
