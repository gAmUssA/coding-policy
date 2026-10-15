"""`top_models` through the real `teamlead.sh plan` entry point (#47, PR #54)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[1] / "teamlead.sh"
AT = "2026-10-15T10:00:00+00:00"


#: Marks `top_models` as omitted, so an explicit null can be written.
_OMIT = object()


def _config(version, top_models=_OMIT, review_model="gpt-7-nova"):
    """A codex worker whose review row names `review_model`; `top_models` only when given."""
    config = {"schema_version": version, "agents": [{
        "name": "codex", "kind": "codex", "usage_prompt": "/status", "usage_marker": "Weekly limit",
        "usage_read_source": "recent-unwrapped", "clear_prompt": "/new",
        "tiers": {"review": {"model": review_model, "effort": "high"}}}]}
    if top_models is not _OMIT:
        config["top_models"] = top_models
    return config


class TopModelsPlanTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "config.json"
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps({"agents": {"codex": {"headroom_pct": 90}}}), encoding="utf-8")

    def plan(self, config):
        self.config.write_text(json.dumps(config), encoding="utf-8")
        return subprocess.run(
            ["bash", str(LAUNCHER), "--config", str(self.config), "--state", str(self.root / "state.json"),
             "plan", "--roles", "reviewer", "--round", "reviewer=review",
             "--snapshot", str(self.snapshot), "--now", AT],
            env={**os.environ, "PY_BIN": sys.executable}, capture_output=True, text=True, check=False)

    def refusal(self, result):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, "")
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"], "config_error")
        return payload["message"]

    def test_a_schema_4_custom_pin_admits_the_judgment_row(self):
        result = self.plan(_config(4, {"codex": ["gpt-7-nova"]}))
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["assignments"], {"reviewer": "codex"})
        self.assertEqual(plan["tiers"]["reviewer"]["model"], "gpt-7-nova")

    def test_without_the_pin_the_row_is_refused_with_the_fix(self):
        message = self.refusal(self.plan(_config(4)))
        self.assertIn("'gpt-7-nova'", message)
        self.assertIn("top_models.codex", message)

    def test_an_invalid_block_is_refused(self):
        for block in (None, {"agy": ["gpt-7-nova"]}, {"codex": []}, {"codex": ["gpt-7-nova", "gpt-7-nova"]}):
            with self.subTest(block=block):
                message = self.refusal(self.plan(_config(4, block)))
                self.assertIn("`top_models", message)

    def test_an_explicit_null_block_is_refused_as_a_non_object(self):
        message = self.refusal(self.plan(_config(4, None)))
        self.assertIn("`top_models` is a JSON null, not an object", message)

    def test_an_under_versioned_block_is_refused_with_the_version_to_set(self):
        message = self.refusal(self.plan(_config(3, {"codex": ["gpt-7-nova"]})))
        self.assertIn('Set "schema_version": 4', message)

    def test_a_non_string_kind_is_a_structured_config_error(self):
        for kind in (["codex"], {"name": "codex"}):
            config = _config(4)
            config["agents"][0]["kind"] = kind
            config["agents"][0].pop("tiers")
            with self.subTest(kind=kind):
                result = self.plan(config)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("agents[0].kind", self.refusal(result))

    def test_a_newer_config_version_is_refused(self):
        message = self.refusal(self.plan(_config(5, {"codex": ["gpt-7-nova"]})))
        self.assertIn("schema_version 5", message)


if __name__ == "__main__":
    unittest.main()
