"""Billing attribution requires observed movement, not a model-name guess."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.billing import billing_window, effective_multiplier, tier_billing


def measured_tier():
    return {
        "model": "gpt-5.3-codex-spark", "effort": "low", "multiplier": 0.5,
        "billing_evidence": {
            "schema_version": 1, "isolated": True, "model": "gpt-5.3-codex-spark",
            "effort": "low", "cli_version": "fixture-cli-1", "prompt_hash": "a" * 64,
            "measured_at": "2026-01-02T03:00:00Z",
            "before": {
                "spark": {"remaining_pct": 90, "reset_at": "2026-01-03T00:00:00Z"},
                "weekly": {"remaining_pct": 80, "reset_at": "2026-01-08T00:00:00Z"},
            },
            "after": {
                "spark": {"remaining_pct": 89, "reset_at": "2026-01-03T00:00:00Z"},
                "weekly": {"remaining_pct": 80, "reset_at": "2026-01-08T00:00:00Z"},
            },
        },
    }


class BillingTest(unittest.TestCase):
    def test_spark_is_unknown_without_measurement(self):
        tier = {"model": "gpt-5.3-codex-spark", "effort": "low", "multiplier": 0.1}
        self.assertEqual(billing_window(tier), "unknown")
        self.assertEqual(effective_multiplier(tier), 1.0)
        self.assertEqual(tier_billing({"mechanical": tier})["mechanical"]["window"], "unknown")

    def test_isolated_spark_movement_names_the_window(self):
        tier = measured_tier()
        self.assertEqual(billing_window(tier), "spark")
        self.assertEqual(effective_multiplier(tier), 0.5)

    def test_claude_movement_is_in_the_shared_weekly(self):
        tier = measured_tier()
        tier["model"] = tier["billing_evidence"]["model"] = "sonnet-5"
        evidence = tier["billing_evidence"]
        evidence["after"]["spark"]["remaining_pct"] = 90
        evidence["after"]["weekly"]["remaining_pct"] = 79
        self.assertEqual(billing_window(tier), "weekly")

    def test_ambiguous_reset_incomplete_or_unbound_evidence_is_unknown(self):
        original = measured_tier()
        variants = []
        for key, value in (("isolated", False), ("model", "different"), ("effort", "high"),
                           ("cli_version", ""), ("schema_version", 2)):
            variant = copy.deepcopy(original)
            variant["billing_evidence"][key] = value
            variants.append(variant)
        for value in (90, 91, True, float("nan"), -1):
            variant = copy.deepcopy(original)
            variant["billing_evidence"]["after"]["spark"]["remaining_pct"] = value
            variants.append(variant)
        variant = copy.deepcopy(original)
        variant["billing_evidence"]["after"]["weekly"]["remaining_pct"] = 79
        variants.append(variant)
        variant = copy.deepcopy(original)
        variant["billing_evidence"]["after"]["spark"]["reset_at"] = "changed"
        variants.append(variant)
        variant = copy.deepcopy(original)
        del variant["billing_evidence"]["after"]["weekly"]
        variants.append(variant)
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(billing_window(variant), "unknown")


if __name__ == "__main__":
    unittest.main()
