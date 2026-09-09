"""Conservative billing-window attribution from isolated before/after evidence.

This is the first #324 deliverable: an unmeasured tier is unknown, including
Spark. A provider/model name never establishes a separate quota. Evidence is
operator-collected data; this module checks its shape and measured movement,
not whether another process secretly consumed the subscription concurrently.
The caller must bind it to the requested model and effort.
"""

import math


UNKNOWN = "unknown"
EVIDENCE_SCHEMA_VERSION = 1


def _percentage(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return False
    return math.isfinite(value)


def billing_window(tier):
    """Return the sole observed window, or `unknown` without complete proof.

Required evidence includes the CLI version, prompt hash, isolated-run
declaration, matching model/effort, and every observed window before and
after. Each window is {remaining_pct, reset_at}; resets or a second moving
window make attribution indeterminate. A rounded zero delta proves nothing.
"""
    evidence = tier.get("billing_evidence")
    if (not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int
            or evidence["schema_version"] != EVIDENCE_SCHEMA_VERSION):
        return UNKNOWN
    if evidence.get("isolated") is not True:
        return UNKNOWN
    if any(not isinstance(evidence.get(key), str) or not evidence[key].strip()
           for key in ("cli_version", "prompt_hash", "measured_at")):
        return UNKNOWN
    if evidence.get("model") != tier.get("model") or evidence.get("effort") != tier.get("effort"):
        return UNKNOWN
    before, after = evidence.get("before"), evidence.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict) or not before or before.keys() != after.keys():
        return UNKNOWN
    changed = []
    for name, start in before.items():
        end = after[name]
        if not isinstance(name, str) or not name or not isinstance(start, dict) or not isinstance(end, dict):
            return UNKNOWN
        if (not _percentage(start.get("remaining_pct")) or not _percentage(end.get("remaining_pct"))
                or not isinstance(start.get("reset_at"), str) or not start["reset_at"]
                or start["reset_at"] != end.get("reset_at")):
            return UNKNOWN
        movement = start["remaining_pct"] - end["remaining_pct"]
        if movement < 0:
            return UNKNOWN
        if movement > 0:
            changed.append(name)
    return changed[0] if len(changed) == 1 else UNKNOWN


def tier_billing(tiers):
    """Snapshot every configured round's model and observed billing window."""
    return {
        round_type: {
            "model": tier["model"], "effort": tier.get("effort"),
            "window": billing_window(tier),
        }
        for round_type, tier in (tiers or {}).items()
    }


def effective_multiplier(tier):
    """Never award speculative savings to an unmeasured tier."""
    multiplier = tier.get("multiplier", 1.0)
    return max(1.0, multiplier) if billing_window(tier) == UNKNOWN else multiplier
