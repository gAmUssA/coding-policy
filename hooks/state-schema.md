# Hook Throttle-Stamp Schema

Schema for the cross-invocation throttle stamps the `SessionStart` hooks write.
Each stamp has one owner hook, a `schema_version`, and a documented migration path.

## Artifacts

| Stamp path | Owner (writer + reader) | Purpose |
| ---------- | ----------------------- | ------- |
| `$FRESHNESS_STATE_DIR/last-check` (default `${TMPDIR:-/tmp}/coding-policy-freshness/last-check`) | `hooks/check-policy-freshness.sh` | Throttle the `tessl outdated` registry call |
| `$SYNC_STATE_DIR/sync-<repo-key>` (default `${TMPDIR:-/tmp}/coding-policy-sync/sync-<repo-key>`) | `hooks/check-git-sync.sh` | Throttle the `git fetch origin` call, per repo (`<repo-key>` = cksum of the repo toplevel path) |

Each stamp has exactly one owner hook that both writes and reads it. No other
skill or hook touches it.

## Record Format

One line, two space-separated fields:

```
<schema_version> <checked_at>
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `1`. Bumped on any shape change |
| `checked_at` | integer | Epoch seconds when the owner last ran its throttled call |

Example: `1 1734567890`

## Writer / Reader Contract

- **Writer** (owner hook): writes `1 <now>` before running the throttled call,
  so a slow or failed call still throttles the next session. Never writes when
  preserving a future-version record (see Migration).
- **Reader** (owner hook): throttles only when the record's `schema_version` is
  `1` **and** `checked_at` is an integer within the throttle window. Every other
  record is **no usable prior state**: the hook runs its call (no throttle).

## Migration

Only the owner hook migrates, and it distinguishes older records from newer ones
as follows:

- **Older or corrupt record** — an old bare-integer stamp (pre-versioning), a
  non-integer field, or an absent stamp: no usable prior state, and the hook
  rewrites a current `1 <now>` record on its next run.
- **Future-version record** (`schema_version` > 1): the hook is the lagging
  reader, not the migrator. It takes the no-usable-prior-state path (runs its
  call) but **must not downgrade** the record — it preserves the future stamp
  untouched until the hook is updated to accept that schema.
- Both fallbacks are safe and non-disruptive: discarding a record costs only one
  extra network / registry call. Neither escalates work.
- Bump `schema_version` for any shape change; do not repurpose `checked_at`
  silently.

## Hints, Not Authority

The stamp is a last-seen timestamp cache, never ground truth. A missing,
corrupt, or unreadable stamp degrades only to an extra call — never to a wrong
result — so the hooks tolerate its absence at every read.
