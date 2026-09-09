"""Public CLI continuity flows stay offline and preserve independent owners."""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead import attention, cli, memory


NOW = "2025-01-01T12:00:00Z"
LATER = "2025-01-01T12:01:00Z"


class ContinuityCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state.json"
        self.source = self.root / "TASK-LEDGER.md"
        self.source.write_text("Verified task evidence.\n", encoding="utf-8")

    def invoke(self, *arguments, now=NOW):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, "load_config", side_effect=AssertionError("offline command read config")), \
                patch.object(cli, "_client", side_effect=AssertionError("offline command contacted Herdr")), \
                patch.object(cli, "load_state_checked", side_effect=AssertionError("offline command read dispatch state")):
            code = cli.main([*arguments, "--state", str(self.state), "--now", now], stdout=out, stderr=err)
        return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()

    def record_input(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def obligation(self):
        return {"id": "review-result", "kind": "review", "task": "task-1",
                "title": "Review the proposed result", "context": "The draft is ready.",
                "consequence": "Publication awaits review.",
                "resolution_condition": "Record the user's review outcome.",
                "sources": [{"schema_version": 1, "kind": "task_ledger", "ref": str(self.source)}]}

    def test_empty_read_creates_no_files_even_with_unreadable_dispatch_content(self):
        self.state.write_text("not valid dispatch JSON", encoding="utf-8")
        before = {str(path): path.read_bytes() for path in self.root.iterdir()}
        for command in ("memory-list", "memory-show", "catch-up", "attention-list"):
            with self.subTest(command=command):
                code, payload, errors = self.invoke(command)
                if command == "memory-show":
                    self.assertEqual(code, 1)
                    self.assertIsNone(payload)
                    self.assertIn("No saved memory matches", errors)
                else:
                    self.assertEqual(code, 0, errors)
                    self.assertIsNotNone(payload)
        after = {str(path): path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_lesson_and_stow_survive_new_cli_invocations_and_lost_source(self):
        lesson = {"id": "lesson-1", "lesson_id": "report-source", "supersedes": None,
                  "status": "active", "lesson": "Check the report before relying on its claim.",
                  "scopes": ["task:task-1"], "sources": [str(self.source)],
                  "last_verified_at": NOW, "expires_at": None,
                  "revalidate_when": "The report is replaced.", "reason": "Observed during review."}
        code, payload, errors = self.invoke("memory-record", "--record", self.record_input("lesson.json", lesson))
        self.assertEqual(code, 0, errors)
        assert payload is not None, "successful write must return its receipt"
        self.assertFalse(payload["replayed"])
        stow = {"id": "handoff-1", "capture": "Review is pending and evidence is in the task ledger.",
                "unresolved_work": ["Read the task ledger before proceeding."], "gaps": [],
                "required_reads": [str(self.source)]}
        code, _, errors = self.invoke("memory-stow", "--record", self.record_input("stow.json", stow))
        self.assertEqual(code, 0, errors)
        saved = memory.location(self.state).read_bytes()
        self.source.unlink()
        code, shown, errors = self.invoke("memory-show", "--id", "handoff-1", now=LATER)
        self.assertEqual(code, 0, errors)
        self.assertIn("Review is pending", json.dumps(shown))
        assert shown is not None, "successful read must return the saved capture"
        self.assertFalse(shown["record"]["reset_ready"])
        self.assertEqual(saved, memory.location(self.state).read_bytes())
        self.assertFalse(self.state.exists())

    def test_presented_review_stays_in_catch_up_until_review_outcome(self):
        code, _, errors = self.invoke("attention-record", "--record", self.record_input("entry.json", self.obligation()))
        self.assertEqual(code, 0, errors)
        present = {"event_id": "show-review", "id": "review-result", "expected_revision": 1,
                   "action": "present", "reason": "The review request was shown.",
                   "evidence": {"schema_version": 1, "kind": "delivery", "ref": "message:2", "summary": "Presented the draft for review."}}
        code, _, errors = self.invoke("attention-update", "--record", self.record_input("present.json", present))
        self.assertEqual(code, 0, errors)
        saved = attention.storage_path(self.state).read_bytes()
        code, view, errors = self.invoke("catch-up", now=LATER)
        self.assertEqual(code, 0, errors)
        assert view is not None, "successful catch-up must return its view"
        self.assertEqual(view["attention"]["total"], 1)
        self.assertEqual(view["attention"]["items"][0]["status"], "open")
        self.assertEqual(saved, attention.storage_path(self.state).read_bytes())
        answer = {"event_id": "review-answer", "id": "review-result", "expected_revision": 2,
                  "action": "resolve", "reason": "The user reviewed the draft.",
                  "evidence": {"schema_version": 1, "kind": "review_outcome", "ref": "message:3", "summary": "The user approved the draft for publication."}}
        code, _, errors = self.invoke("attention-update", "--record", self.record_input("answer.json", answer), now=LATER)
        self.assertEqual(code, 0, errors)
        code, view, errors = self.invoke("catch-up", "--include-closed", now=LATER)
        self.assertEqual(code, 0, errors)
        assert view is not None, "successful catch-up must return its view"
        self.assertEqual(view["attention"]["total"], 0)
        self.assertEqual(view["closed"]["total"], 1)
        self.assertFalse(self.state.exists())

    def test_future_attention_schema_is_preserved_with_structured_error(self):
        path = attention.storage_path(self.state)
        path.write_text('{"schema_version": 999, "events": []}', encoding="utf-8")
        before = path.read_bytes()
        code, output, errors = self.invoke("catch-up")
        self.assertEqual(code, 1)
        self.assertIsNone(output)
        self.assertIsInstance(json.loads(errors), dict)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(Path(str(path) + ".lock").exists())


if __name__ == "__main__":
    unittest.main()
