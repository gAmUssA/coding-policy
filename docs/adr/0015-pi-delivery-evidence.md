# 0015 — Normalize pi delivery evidence at the runtime boundary

Status: accepted

Date: 2026-10-26

## Context

Pi owns its native JSONL v3 transcript format. Teamlead reads it across report
probes, but cannot change that third-party writer to stamp its own record
versions. A raw transcript alone leaves teamlead's persisted delivery evidence
without an owner-controlled migration and reader contract.

## Decision

The pi adapter owns a schema-1 `delivery.json` beside each launch's native
transcript. Each probe imports the unique bound transcript into normalized
records, stamps every record, atomically writes the artifact and reads it
back before evaluating delivery. Preserve only fields used by delivery;
the schema and reader/writer contract live in
`skills/herdr-teamlead/state-schema.md`. Unsupported or corrupt owner evidence
is preserved and proves no delivery. There is no predecessor schema to migrate.

The saved-plan owner also validates schema 7, upgrades versions 1–6 without
inventing missing judgment or authorization evidence, rewrites file-backed
plans atomically, and refuses future versions. Existing assignment-map
instructions remain usable. This enforces the existing stateful-artifact
ownership policy at the CLI boundary.

## Consequences

Native transcripts remain untouched. Existing pi receipts need no migration;
their next successful probe creates normalized evidence. An extra write is
required for delivery, and an unwritable sidecar yields unconfirmed delivery.
Saved evidence is only a hint: every probe still verifies source bytes, exact
prompt, report presence and live process/receipt continuity. No saved result
authorizes delivery after process replacement or source changes.
