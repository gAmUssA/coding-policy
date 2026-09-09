"""Fail-closed promotion and weekly canary evidence for model tiers.

The #321 research requires a paired, blinded five-case screen followed by a
twenty-case promotion battery. This reader evaluates the operator's recorded
results; it does not manufacture live model trials from unit-test fixtures.
Every promoted configuration has its own model/effort/role identity. Missing
metadata or an expired canary refuses live tier dispatch, never a dry run.
"""

import re
from datetime import datetime, timedelta

from .errors import UsageError

QUALIFICATION_SCHEMA_VERSION = 1
SCREEN_CASES = 5
PROMOTION_CASES = 20
CANARY_CASES = 5
CANARY_VALIDITY = timedelta(days=7)
PROMPT_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _report(report, tier=None):
    if not isinstance(report, dict):
        return False
    if (report.get("caught_blocker") is not True or report.get("severity") != "blocking"
            or report.get("absorbed_judgment") is not False):
        return False
    if tier is not None and (report.get("model") != tier["model"] or report.get("effort") != tier.get("effort")):
        return False
    if any(not isinstance(report.get(key), str) or not report[key].strip()
           for key in ("model", "cli_version")) or "effort" not in report:
        return False
    if not isinstance(report.get("prompt_hash"), str) or not PROMPT_HASH.fullmatch(report["prompt_hash"]):
        return False
    if any(isinstance(report.get(key), bool) or not isinstance(report.get(key), int) or report[key] < 0
           for key in ("tokens", "compactions")):
        return False
    return all(report.get(key) == "unknown" or (isinstance(report.get(key), dict) and bool(report[key]))
               for key in ("window_before", "window_after"))


def _trials(trials, count, tier):
    if not isinstance(trials, list) or len(trials) != count:
        return None
    ids = set()
    for trial in trials:
        if not isinstance(trial, dict) or trial.get("blinded") is not True:
            return None
        case_id = trial.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in ids:
            return None
        baseline, candidate = trial.get("baseline"), trial.get("candidate")
        if not isinstance(baseline, dict) or not isinstance(candidate, dict) or not _report(baseline) or not _report(candidate, tier):
            return None
        if baseline["prompt_hash"] != candidate["prompt_hash"]:
            return None
        ids.add(case_id)
    return ids


def require_qualification(tier, role, at):
    """Require a complete matching promotion plus a current stable canary."""
    now = _time(at)
    evidence = tier.get("qualification", [])
    if not isinstance(evidence, list):
        evidence = []
    for record in evidence:
        if (not isinstance(record, dict)
                or type(record.get("schema_version")) is not int
                or record["schema_version"] != QUALIFICATION_SCHEMA_VERSION
                or record.get("role") != role or record.get("model") != tier["model"]
                or record.get("effort") != tier.get("effort")):
            continue
        screen = _trials(record.get("screen"), SCREEN_CASES, tier)
        promotion = _trials(record.get("promotion"), PROMOTION_CASES, tier)
        canary = record.get("canary")
        if not screen or not promotion or screen & promotion or not isinstance(canary, dict):
            continue
        checked_at = _time(canary.get("at"))
        cases = _trials(canary.get("trials"), CANARY_CASES, tier)
        original_prompts = {trial["case_id"]: trial["candidate"]["prompt_hash"]
                            for trial in record["screen"] + record["promotion"]}
        if (now is None or checked_at is None or not cases or not cases <= screen | promotion
                or not timedelta(0) <= now - checked_at <= CANARY_VALIDITY):
            continue
        if any(trial["candidate"]["prompt_hash"] != original_prompts[trial["case_id"]]
               for trial in canary["trials"]):
            continue
        return {"schema_version": QUALIFICATION_SCHEMA_VERSION, "role": role,
                "model": tier["model"], "effort": tier.get("effort"), "canary_at": canary["at"],
                "screen_cases": len(screen), "promotion_cases": len(promotion)}
    raise UsageError(
        "Tier {} / {} for {} has no complete paired promotion and current canary. "
        "Run the validation battery in references/model-tiers.md and record its real results "
        "in this config tier's qualification array; fixture results do not qualify a live model.".format(
            tier["model"], tier.get("effort"), role), {"role": role},
    )
