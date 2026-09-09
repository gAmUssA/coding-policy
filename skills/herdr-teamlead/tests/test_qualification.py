"""A score or a successful launch cannot substitute for the validation battery."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import UsageError
from teamlead.qualification import require_qualification

AT = "2026-01-08T12:00:00+00:00"


def qualified_tier():
    report = {"caught_blocker": True, "severity": "blocking", "absorbed_judgment": False,
              "model": "sonnet-5", "effort": "high", "cli_version": "fixture-1", "prompt_hash": "a" * 64,
              "tokens": 100, "compactions": 0, "window_before": "unknown", "window_after": "unknown"}

    def trials(prefix, count):
        return [{"case_id": prefix + str(index), "blinded": True,
                 "baseline": {**report, "model": "opus-5"}, "candidate": dict(report)} for index in range(count)]

    return {"model": "sonnet-5", "effort": "high", "qualification": [{
        "schema_version": 1, "role": "developer", "model": "sonnet-5", "effort": "high",
        "screen": trials("screen-", 5), "promotion": trials("promotion-", 20),
        "canary": {"at": "2026-01-08T11:00:00Z", "trials": trials("screen-", 5)},
    }]}


class QualificationTest(unittest.TestCase):
    def test_complete_matching_battery_and_current_canary_qualify(self):
        proof = require_qualification(qualified_tier(), "developer", AT)
        self.assertEqual(proof["promotion_cases"], 20)

    def test_missing_wrong_role_or_wrong_effort_refuses(self):
        for tier, role in (({"model": "sonnet-5", "effort": "high"}, "developer"),
                           (qualified_tier(), "reviewer"), ({**qualified_tier(), "effort": "xhigh"}, "developer")):
            with self.subTest(role=role), self.assertRaises(UsageError):
                require_qualification(tier, role, AT)

    def test_a_screen_alone_or_a_missed_blocker_cannot_promote(self):
        for field in ("promotion", "screen"):
            tier = qualified_tier()
            tier["qualification"][0][field].pop()
            with self.assertRaises(UsageError):
                require_qualification(tier, "developer", AT)
        for field, value in (("caught_blocker", False), ("severity", "advisory"),
                             ("absorbed_judgment", True), ("prompt_hash", ""), ("tokens", None),
                             ("cli_version", ""), ("window_after", None)):
            tier = qualified_tier()
            tier["qualification"][0]["promotion"][0]["candidate"][field] = value
            with self.subTest(field=field), self.assertRaises(UsageError):
                require_qualification(tier, "developer", AT)

    def test_expired_future_or_unstable_canary_refuses(self):
        for stamp in ("2025-12-31T00:00:00Z", "2026-01-09T00:00:00Z", "invalid"):
            tier = qualified_tier()
            tier["qualification"][0]["canary"]["at"] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(UsageError):
                require_qualification(tier, "developer", AT)
        tier = qualified_tier()
        tier["qualification"][0]["canary"]["trials"][0]["case_id"] = "unseen"
        with self.assertRaises(UsageError):
            require_qualification(tier, "developer", AT)
        tier = qualified_tier()
        for report in ("baseline", "candidate"):
            tier["qualification"][0]["canary"]["trials"][0][report]["prompt_hash"] = "b" * 64
        with self.assertRaises(UsageError):
            require_qualification(tier, "developer", AT)

    def test_reused_or_unblinded_cases_refuse(self):
        for mutation in ("duplicate", "unblind", "different-prompt"):
            tier = qualified_tier()
            trials = tier["qualification"][0]["promotion"]
            if mutation == "duplicate":
                trials[1] = copy.deepcopy(trials[0])
            elif mutation == "unblind":
                trials[0]["blinded"] = False
            else:
                trials[0]["baseline"]["prompt_hash"] = "b" * 64
            with self.subTest(mutation=mutation), self.assertRaises(UsageError):
                require_qualification(tier, "developer", AT)


if __name__ == "__main__":
    unittest.main()
