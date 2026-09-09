"""Compare assignment event times without moving append-only audit rows.

Assignment ``at`` is the actual event time, including for historical imports;
recovery receipt times and list offsets are not event chronology. Missing,
naive or tied times cannot prove which relevant event came last. Earlier ties
are harmless when a strictly newer event establishes the latest assignment.
"""

from datetime import datetime

from .errors import UsageError


def timestamp(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise UsageError("{} needs its original ISO-8601 timestamp with timezone; recover the event evidence without rewriting assignment history.".format(label), {})
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise UsageError("{} must be an ISO-8601 timestamp with timezone; recover the original event evidence.".format(label), {}) from None
    if parsed.tzinfo is None:
        raise UsageError("{} needs an explicit timezone; preserve the original event time.".format(label), {})
    return parsed


def assignment_after(assignments, later_index, earlier_index):
    """Prove ordering between original rows; an equal instant is uncertain."""
    later = timestamp(assignments[later_index].get("at"), "Assignment {} chronology".format(later_index))
    earlier = timestamp(assignments[earlier_index].get("at"), "Assignment {} chronology".format(earlier_index))
    if later == earlier:
        raise UsageError("Assignment chronology is uncertain at indices {} and {}: their event times are tied. Recover the original ordering evidence; do not reorder the audit or dispatch on a guess.".format(earlier_index, later_index), {})
    return later > earlier


def latest_assignment(assignments, *, agent=None, task=None, role=None, status=None, before=None):
    """Return ``(original_index, original_row)`` or None, never a sorted audit.

    Filters narrow the relevant history. ``before`` is an original assignment
    index whose event must follow every candidate; later rows are excluded by
    time, not by append position. Unknown relevant chronology raises UsageError.
    """
    filters = {"agent": agent, "task": task, "role": role, "status": status}
    candidates = []
    for index, row in enumerate(assignments):
        if index == before or any(value is not None and row.get(key) != value for key, value in filters.items()):
            continue
        at = timestamp(row.get("at"), "Assignment {} chronology".format(index))
        if before is not None and not assignment_after(assignments, before, index):
            continue
        candidates.append((at, index, row))
    if not candidates:
        return None
    latest_time = max(candidate[0] for candidate in candidates)
    latest = [(index, row) for at, index, row in candidates if at == latest_time]
    if len(latest) != 1:
        raise UsageError("Latest assignment chronology is uncertain at indices {}: their event times are tied. Recover the original ordering evidence; do not reorder the audit or dispatch on a guess.".format(
            ", ".join(str(index) for index, _row in latest)), {})
    return latest[0]
