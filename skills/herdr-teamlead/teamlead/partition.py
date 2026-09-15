"""Validate a review partition: one owner per changed file, no file unowned.

`rules/agent-team-operation.md` Review Before PR describes one reviewer per
round. A reviewer roaming an unbounded surface that reports no findings has not
established that the surface is clean -- only that this pass happened not to
reach a defect, which is why a twenty-round delivery ran three, three, two,
one, one, one, two, two, one, one blocking findings and never converged (#409).

A partition supplies the missing termination condition: a slice is saturated
when its reviewer reports clean at the current tip, and a change is reviewed
when every slice is saturated at one tip. That only holds while the partition
is DISJOINT and EXHAUSTIVE over the change. A gap is indistinguishable from a
clean slice in the result, and an overlap makes two verdicts answer for one
file while neither owns it.

This module decides that, and nothing else: it never chooses slices, never
assigns workers, and never reads a report. The lead writes the partition; the
planner seats it; this says whether it can carry a verdict.

Contract:

* `load_partition(path)` reads the partition document.
* `validate(changed, partition)` is pure over a changed-path set and that
  document.
* `run_command(args, runner=...)` collects the changed set through `runner`,
  a callable taking a git argument list and returning its stdout, and returns
  the `(payload, failure)` pair every command returns.
* The payload lists each slice with the paths it owns. A partition that cannot
  carry a verdict raises `UsageError` naming every unowned path and every
  overlap with the slices that claim it, so the round refuses before a worker
  is spent.
"""

import fnmatch
import json
from pathlib import Path

from .errors import UsageError
from .triggers import git_runner, parse_name_status

#: The partition document's own version, so a later shape change is auditable
#: (`rules/stateful-artifacts.md` Migration Policy).
PARTITION_SCHEMA_VERSION = 1

COMMANDS = frozenset({"validate-partition"})

#: Separates a seat from the slice it owns in a planned role name. A role name
#: never contains it, so `reviewer#api` will read back unambiguously once the
#: dispatch side carries seats (#434).
SEAT_SEPARATOR = "#"


def load_partition(path):
    """Read the partition document the lead wrote for this round."""
    if not path:
        raise UsageError("Pass --partition naming the round's review partition; a multi-seat review round has no partition to seat without one.", {})
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError("Cannot read the partition at {}: {}. Write a JSON object with schema_version and slices.".format(path, exc), {"path": str(path)}) from None
    if not isinstance(document, dict) or document.get("schema_version") != PARTITION_SCHEMA_VERSION:
        raise UsageError("The partition must be a JSON object at schema_version {}.".format(PARTITION_SCHEMA_VERSION), {"path": str(path)})
    partition_role(document)
    unknown = set(document) - {"schema_version", "role", "slices"}
    if unknown:
        raise UsageError("The partition carries unknown field(s) {}; it holds schema_version, an optional role, and slices.".format(", ".join(sorted(unknown))), {"path": str(path)})
    slices = document.get("slices")
    if not isinstance(slices, list) or len(slices) < 2:
        raise UsageError("A partition names at least two slices; a single-seat round needs none.", {"path": str(path)})
    seen = set()
    for entry in slices:
        if (not isinstance(entry, dict) or set(entry) != {"name", "paths"}
                or not isinstance(entry["name"], str) or not entry["name"].strip()):
            raise UsageError("Each slice is an object with a non-empty name and a paths array.", {"path": str(path)})
        if entry["name"] in seen:
            raise UsageError("Slice name {!r} appears twice; each seat owns one named slice.".format(entry["name"]), {"path": str(path)})
        seen.add(entry["name"])
        patterns = entry["paths"]
        if (not isinstance(patterns, list) or not patterns
                or any(not isinstance(item, str) or not item.strip() for item in patterns)):
            raise UsageError("Slice {!r} needs a non-empty array of path globs.".format(entry["name"]), {"path": str(path)})
    return document


def partition_role(partition):
    """The role the partition seats; `reviewer` unless the document says."""
    role = partition.get("role", "reviewer")
    if not isinstance(role, str) or not role.strip() or SEAT_SEPARATOR in role:
        raise UsageError("A partition's role is a non-empty name without {!r}.".format(SEAT_SEPARATOR), {})
    return role


def owners(path, slices):
    """The slice names whose globs match `path`, in declaration order."""
    matched = []
    for entry in slices:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in entry["paths"]):
            matched.append(entry["name"])
    return matched


def validate(changed, partition):
    """Decide whether `partition` can carry a verdict over `changed`.

    Returns the per-slice ownership payload. Raises UsageError naming every
    unowned path and every overlap: a gap reads as a clean slice, and an
    overlap leaves a file two verdicts and no owner.
    """
    slices = partition["slices"]
    assignment = {entry["name"]: [] for entry in slices}
    unowned = []
    overlaps = []
    for path in sorted(changed):
        matched = owners(path, slices)
        if not matched:
            unowned.append(path)
        elif len(matched) > 1:
            overlaps.append({"path": path, "slices": matched})
        else:
            assignment[matched[0]].append(path)
    # A slice party to an overlap owns nothing yet, but its emptiness is that
    # overlap's doing and naming it again would send the reader after the wrong
    # fix.
    contested = {name for row in overlaps for name in row["slices"]}
    empty = sorted(name for name, paths in assignment.items()
                   if not paths and name not in contested)
    # Every problem in ONE run. Raising on the first class would hide an
    # overlap behind a gap and cost a round per class to find them all.
    if unowned or overlaps or empty:
        parts = []
        if unowned:
            parts.append("leaves {} changed path(s) unowned, starting with {} (a gap is indistinguishable from a clean slice in the result)".format(
                len(unowned), ", ".join(unowned[:5])))
        if overlaps:
            first = overlaps[0]
            parts.append("gives {} changed path(s) more than one owner, starting with {} claimed by {} (two verdicts over one file leave it owned by neither)".format(
                len(overlaps), first["path"], ", ".join(first["slices"])))
        if empty:
            parts.append("has slice(s) {} owning no changed path (a seat with nothing to review is a worker spent for no verdict)".format(
                ", ".join(empty)))
        raise UsageError(
            "The partition cannot carry a verdict: it {}. Extend, narrow or drop the slices named in the details and re-run.".format(
                "; and it ".join(parts)),
            {"unowned": unowned, "overlaps": overlaps, "empty": empty},
        )
    return {"schema_version": PARTITION_SCHEMA_VERSION,
            "slices": [{"name": entry["name"], "paths": assignment[entry["name"]]} for entry in slices],
            "changed": sorted(changed)}


def register_commands(sub, common):
    parser = sub.add_parser(
        "validate-partition", parents=[common],
        help="Check that a review partition is disjoint and exhaustive over the round's changed paths.",
    )
    parser.add_argument("--repo", required=True, metavar="PATH", help="The repository whose diff the partition covers.")
    parser.add_argument("--base", required=True, metavar="REV", help="The revision the round started from.")
    parser.add_argument("--head", metavar="REV", help="The pushed head; omit to read the working tree.")
    parser.add_argument("--partition", required=True, metavar="FILE", help="The round's partition document.")


def run_command(args, runner=None):
    """Validate this round's partition against the paths its diff changed."""
    partition = load_partition(args.partition)
    run = runner if runner is not None else git_runner(args.repo)
    head = getattr(args, "head", None)
    span = [args.base + "..." + head] if head else [args.base]
    changes = parse_name_status(run(["diff", "--no-renames", *span, "--name-status", "-z"]))
    return validate(set(changes), partition), None
