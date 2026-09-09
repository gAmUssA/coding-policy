"""Durable user-facing obligations: evidence, replay, offline views and recovery."""

import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import argparse
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import attention
from teamlead.attention_view import catch_up
from teamlead.errors import StateError, UsageError
from teamlead.state import state_lock

AT = "2026-09-01T10:00:00Z"
LATER = "2026-09-01T11:00:00Z"
DUE = "2026-09-01T12:00:00Z"
AFTER = "2026-09-01T13:00:00Z"


def source(kind="user_message", ref="conversation/1/message/3"):
    return {"schema_version": 1, "kind": kind, "ref": ref}


def evidence(kind="user_answer", summary="The user selected the bounded migration."):
    return {"schema_version": 1, "kind": kind, "ref": "conversation/1/message/5", "summary": summary}


def obligation(name="q1", kind="question", priority=50):
    return {"id": name, "kind": kind, "task": "owner/repo#5", "title": "Choose the migration boundary",
            "context": "The current authorization ends at the local migration.", "consequence": "Public migration remains blocked.",
            "resolution_condition": "Record the user's explicit choice about the migration boundary.",
            "priority": priority, "options": ["Keep the local migration", "Authorize the public migration"],
            "recommendation": "Keep the local migration for this release.", "sources": [source()]}


class AttentionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-attention-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "state.json"

    def record(self, data=None, at=AT):
        return attention.write(self.path, "record", obligation() if data is None else data, at)

    def update(self, action, *, at=LATER, name="q1", revision=1, event_id="event-1", **extra):
        data = {"event_id": event_id, "id": name, "expected_revision": revision, "action": action,
                "reason": "Lead verified the linked event against this obligation.", **extra}
        return attention.write(self.path, "update", data, at)

    def saved(self):
        return attention.storage_path(self.path).read_bytes()

    def test_fresh_read_is_offline_and_creates_nothing(self):
        before = list(self.root.iterdir())
        result = catch_up(self.path, AT)
        self.assertEqual(result["attention"]["total"], 0)
        self.assertEqual(result["attention_markdown"], "")
        self.assertIn("completion is unknown", result["markdown"])
        self.assertEqual(list(self.root.iterdir()), before)

    def test_survives_independent_process_without_main_state_or_sources(self):
        self.record()
        env = {**_os.environ, "PYTHONPATH": _ROOT}
        env.pop("HERDR_ENV", None)
        script = "from teamlead.attention_view import catch_up; import json, sys; print(json.dumps(catch_up(sys.argv[1], sys.argv[2])))"
        run = subprocess.run([_sys.executable, "-c", script, str(self.path), LATER], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout)["attention"]["items"][0]["id"], "q1")
        self.assertFalse(self.path.exists())

    def test_presentation_keeps_question_open_and_unrelated_reads_never_close(self):
        self.record()
        self.update("present", evidence=evidence("delivery", "Question appeared in the user's catch-up message."))
        before = self.saved()
        for _ in range(3):
            result = catch_up(self.path, AFTER, since=LATER)
            self.assertEqual(result["attention"]["total"], 1)
            self.assertIn("Still awaiting your answer.", result["attention_markdown"])
        self.assertEqual(self.saved(), before)
        self.assertIsNone(attention.show(self.path, "q1")["entry"]["resolution"])

    def test_only_matching_explicit_evidence_resolves(self):
        required = {"question": "user_answer", "decision": "user_answer", "review": "review_outcome",
                    "blocker": "acknowledgement", "failure": "acknowledgement", "followup": "delivery", "update": "delivery"}
        for kind, proof in required.items():
            with self.subTest(kind=kind):
                self.record(obligation(kind, kind))
                before = self.saved()
                with self.assertRaises(UsageError):
                    self.update("resolve", name=kind, event_id=kind + "-bad", evidence=evidence("source"), at=AT)
                self.assertEqual(self.saved(), before)
                self.update("resolve", name=kind, event_id=kind + "-yes", evidence=evidence(proof), at=AT)
                entry = attention.show(self.path, kind)["entry"]
                self.assertEqual(entry["status"], "resolved")
                self.assertEqual(entry["resolution"]["kind"], proof)
        self.assertEqual(catch_up(self.path, AFTER)["attention"]["total"], 0)

    def test_informational_failure_can_resolve_after_delivery_without_user_chore(self):
        data = {**obligation(kind="failure"), "resolution_condition": "Notify the user that the optional export failed."}
        self.record(data)
        delivered = evidence("delivery", "User update reported that the optional export failed; no decision or action is needed.")
        self.update("present", evidence=delivered)
        self.assertEqual(catch_up(self.path, LATER)["attention"]["total"], 1)
        self.update("resolve", revision=2, event_id="delivery-complete", evidence=delivered, at=DUE)
        result = attention.show(self.path, "q1")
        self.assertEqual(result["entry"]["status"], "resolved")
        self.assertEqual(result["entry"]["resolution"]["kind"], "delivery")
        self.assertEqual(len(result["history"]), 3)
        self.assertEqual(catch_up(self.path, DUE)["attention"]["total"], 0)

    def test_failure_can_resolve_from_verified_outcome_but_questions_stay_typed(self):
        data = {**obligation(kind="failure"), "resolution_condition": "Confirm the failed export recovered and its artifact is usable."}
        self.record(data)
        outcome = evidence("verified_outcome", "The successful retry produced the expected artifact; the linked ledger records validation.")
        self.update("resolve", evidence=outcome)
        self.assertEqual(attention.show(self.path, "q1")["entry"]["resolution"], outcome)
        for kind in ("question", "decision", "review"):
            self.record(obligation(kind, kind), at=LATER)
            for proof in ("delivery", "verified_outcome"):
                with self.subTest(kind=kind, proof=proof):
                    with self.assertRaises(UsageError):
                        self.update("resolve", name=kind, event_id=kind + proof, evidence=evidence(proof), at=DUE)
            self.assertEqual(attention.show(self.path, kind)["entry"]["status"], "open")

    def test_verified_recovery_closes_blocker_without_answering_a_pending_decision(self):
        blocker = {**obligation("recovery", kind="blocker"), "resolution_condition": "Verify that the unavailable build service is usable again."}
        self.record(blocker)
        self.record(obligation("choice", kind="decision"))
        self.update("present", name="recovery", event_id="show-recovery", evidence=evidence("delivery", "Reported the service outage."))
        self.assertEqual(attention.show(self.path, "recovery")["entry"]["status"], "open")
        outcome = evidence("verified_outcome", "The independent health check and successful build verify service recovery; validation is saved in the linked ledger.")
        self.update("resolve", name="recovery", revision=2, event_id="recovery-verified", evidence=outcome, at=DUE)
        self.assertEqual(attention.show(self.path, "recovery")["entry"]["resolution"], outcome)
        with self.assertRaises(UsageError):
            self.update("resolve", name="choice", event_id="recovery-is-not-an-answer", evidence=outcome, at=DUE)
        pending = catch_up(self.path, DUE)["attention"]["items"]
        self.assertEqual([row["id"] for row in pending], ["choice"])
        self.assertIsNone(pending[0]["resolution"])

    def test_deferral_resurfaces_at_due_time_without_writing(self):
        self.record()
        self.update("defer", until=DUE, evidence=evidence("source", "Lead will revisit at the recorded checkpoint."))
        before = self.saved()
        early = catch_up(self.path, LATER)
        self.assertEqual(early["attention"]["total"], 0)
        self.assertEqual(early["deferred"]["total"], 1)
        due = catch_up(self.path, DUE)
        self.assertEqual(due["deferred"]["total"], 0)
        self.assertTrue(due["attention"]["items"][0]["resurfaced"])
        self.assertEqual(due["attention"]["items"][0]["status"], "deferred")
        self.assertEqual(due["attention"]["items"][0]["effective_status"], "open")
        self.assertEqual(self.saved(), before)

    def test_defer_past_refused_without_losing_obligation(self):
        self.record()
        with self.assertRaises(UsageError):
            self.update("defer", until=AT, evidence=evidence("source"))
        self.assertEqual(catch_up(self.path, LATER)["attention"]["total"], 1)

    def test_idempotent_create_update_and_conflicting_id(self):
        self.assertFalse(self.record()["replayed"])
        before = self.saved()
        self.assertTrue(self.record(at=LATER)["replayed"])
        self.assertEqual(self.saved(), before)
        with self.assertRaises(UsageError):
            self.record({**obligation(), "title": "Changed question"})
        self.update("resolve", evidence=evidence())
        after = self.saved()
        self.assertTrue(self.update("resolve", evidence=evidence(), at=AFTER)["replayed"])
        self.assertEqual(self.saved(), after)
        with self.assertRaises(UsageError):
            self.update("resolve", evidence=evidence(summary="Different answer"), at=AFTER)

    def test_reopen_and_supersede_preserve_original_answer_and_replacement(self):
        self.record()
        self.update("resolve", evidence=evidence())
        self.update("reopen", revision=2, event_id="reopen", at=DUE, evidence=evidence("user_answer", "User reversed the answer."))
        self.record(obligation("q2"), at=DUE)
        self.update("supersede", revision=3, event_id="supersede", at=AFTER, replacement="q2", evidence=evidence("user_answer", "User requested the expanded question."))
        result = attention.show(self.path, "q1")
        self.assertEqual(result["entry"]["superseded_by"], "q2")
        self.assertEqual(len(result["history"]), 4)
        self.assertEqual(result["history"][1]["data"]["evidence"]["kind"], "user_answer")
        self.assertEqual([row["id"] for row in catch_up(self.path, AFTER)["attention"]["items"]], ["q2"])

    def test_supersede_requires_distinct_open_replacement(self):
        self.record()
        for replacement in ("missing", "q1"):
            with self.assertRaises(UsageError):
                self.update("supersede", replacement=replacement, evidence=evidence())
        self.assertEqual(catch_up(self.path, AFTER)["attention"]["total"], 1)

    def test_stale_revision_and_clock_refused_without_rewriting_history(self):
        self.record()
        self.update("present", evidence=evidence("delivery"))
        before = self.saved()
        with self.assertRaises(UsageError):
            self.update("resolve", event_id="answer", evidence=evidence(), at=AFTER)
        with self.assertRaises(UsageError):
            self.update("resolve", event_id="answer", revision=2, evidence=evidence(), at=AT)
        self.assertEqual(self.saved(), before)

    def test_priority_amendment_and_pagination_preserve_all_obligations(self):
        for name, rank in (("low", 5), ("high", 100), ("mid", 50)):
            self.record(obligation(name, priority=rank))
        self.update("amend", name="low", changes={"priority": 75}, evidence=evidence("source", "Release deadline increased impact."))
        first = catch_up(self.path, LATER, limit=1, since=LATER)
        self.assertEqual(first["attention"]["items"][0]["id"], "high")
        self.assertEqual(first["attention"]["omitted"], 2)
        self.assertEqual(first["attention"]["next_offset"], 1)
        self.assertIn("2 actionable obligations", first["attention_markdown"])
        ids = [catch_up(self.path, LATER, limit=1, offset=offset)["attention"]["items"][0]["id"] for offset in range(3)]
        self.assertEqual(ids, ["high", "low", "mid"])

    def test_progress_is_explicit_assessment_and_sources_are_pointers(self):
        self.record()
        data = {"id": "p1", "task": "owner/repo#5", "summary": "Worker delivered a report; acceptance is unverified.",
                "assessment": "reported", "sources": [source("task_ledger", "/missing/TASK-LEDGER.md")]}
        attention.write(self.path, "progress", data, LATER)
        result = catch_up(self.path, AFTER)
        self.assertEqual(result["progress"]["items"][0]["assessment"], "reported")
        self.assertIn(data["sources"][0], result["sources"])
        self.assertLess(result["markdown"].index("Choose the migration"), result["markdown"].index("Worker delivered"))
        self.assertEqual(catch_up(self.path, AFTER, since=DUE)["progress"]["total"], 0)
        self.assertEqual(catch_up(self.path, AFTER, since=DUE)["attention"]["total"], 1)
        self.assertTrue(attention.write(self.path, "progress", data, AFTER)["replayed"])

    def test_progress_requires_ledger_and_review_has_visible_artifact_link(self):
        data = {"id": "p1", "task": "owner/repo#5", "summary": "Ready", "assessment": "verified", "sources": [source()]}
        with self.assertRaises(UsageError):
            attention.write(self.path, "progress", data, AT)
        self.assertFalse(attention.storage_path(self.path).exists())
        self.record({**obligation(kind="review"), "sources": [source("artifact", "/reports/My Report.md")]})
        result = catch_up(self.path, LATER)
        self.assertIn("[artifact](</reports/My Report.md>)", result["attention_markdown"])

    def test_reader_sees_decision_before_bookkeeping(self):
        data = {**obligation(), "sources": [source(), source("artifact", "/reports/design.md"),
                                               source("task_ledger", "/reports/TASK-LEDGER.md")]}
        self.record(data)
        result = catch_up(self.path, LATER)
        rendered = result["attention_markdown"]
        self.assertIn("## Choose the migration boundary", rendered)
        self.assertIn(data["context"], rendered)
        self.assertIn(data["consequence"], rendered)
        self.assertIn("**Needed:** " + data["resolution_condition"], rendered)
        self.assertIn("Recommendation: " + data["recommendation"], rendered)
        self.assertNotIn("`q1`", rendered)
        self.assertNotIn("priority 50", rendered)
        self.assertNotIn("not yet recorded as presented", rendered)
        self.assertNotIn("conversation/1/message/3", rendered)
        self.assertNotIn("Saved sources", result["markdown"])
        self.assertNotIn("grants no task acceptance", result["markdown"])
        self.assertEqual(result["attention"]["items"][0]["id"], "q1")
        self.assertEqual(result["attention"]["items"][0]["priority"], 50)
        self.assertEqual(result["attention"]["items"][0]["sources"], data["sources"])
        self.assertIn("[artifact](</reports/design.md>)", rendered)

    def test_progress_freshness_is_readable_without_repeating_storage_paths(self):
        ledger = source("task_ledger", "/reports/TASK-LEDGER.md")
        for index, assessment in enumerate(("verified", "reported", "unknown")):
            attention.write(self.path, "progress", {"id": "p" + str(index), "task": "owner/repo#5",
                            "summary": "Progress snapshot " + str(index), "assessment": assessment, "sources": [ledger]}, AT)
        result = catch_up(self.path, LATER)
        self.assertIn("Verified when recorded · 2026-09-01T10:00:00+00:00", result["markdown"])
        self.assertIn("Reported; acceptance unverified", result["markdown"])
        self.assertIn("Acceptance unknown", result["markdown"])
        self.assertNotIn(result["attention_path"], result["markdown"])
        self.assertIn("retrospective_index", result)

    def test_task_filter_and_closed_history(self):
        self.record()
        self.record({**obligation("other"), "task": "owner/repo#6"})
        self.update("resolve", evidence=evidence())
        result = catch_up(self.path, AFTER, task="owner/repo#5", include_closed=True)
        self.assertEqual(result["attention"]["total"], 0)
        self.assertEqual(result["closed"]["total"], 1)
        self.assertEqual(catch_up(self.path, AFTER, since=DUE, include_closed=True)["closed"]["total"], 0)

    def test_canonical_state_alias_has_one_queue(self):
        self.path.write_text("unreadable main state is irrelevant", encoding="utf-8")
        alias = self.root / "alias.json"
        alias.symlink_to(self.path)
        attention.write(alias, "record", obligation(), AT)
        self.assertEqual(catch_up(self.path, LATER)["attention"]["total"], 1)
        self.assertEqual(attention.storage_path(alias), attention.storage_path(self.path))

    def test_corrupt_unknown_schema_or_event_is_preserved(self):
        self.record()
        valid = json.loads(self.saved())
        values = [b"{", json.dumps({**valid, "schema_version": 0}).encode(), json.dumps({**valid, "schema_version": 2}).encode()]
        for mutate in (lambda doc: doc["events"][0].update(schema_version=2),
                       lambda doc: doc["events"][0].update(action="unknown"),
                       lambda doc: doc["events"][0]["data"]["sources"][0].update(kind=[])):
            document = copy.deepcopy(valid)
            mutate(document)
            values.append(json.dumps(document).encode())
        for value in values:
            with self.subTest(value=value[:40]):
                attention.storage_path(self.path).write_bytes(value)
                with self.assertRaises(StateError):
                    catch_up(self.path, AFTER)
                with self.assertRaises(StateError):
                    self.record(obligation("new"), at=AFTER)
                self.assertEqual(self.saved(), value)

    def test_lock_and_failed_atomic_write_preserve_history(self):
        self.record()
        before = self.saved()
        with state_lock(attention.storage_path(self.path)):
            with self.assertRaises(StateError):
                self.record(obligation("another"))
        self.assertEqual(self.saved(), before)
        with patch("teamlead.state.os.replace", side_effect=PermissionError("fixture")):
            with self.assertRaises(StateError):
                self.record(obligation("another"))
        self.assertEqual(self.saved(), before)

    def test_cli_contract_and_invalid_input_errors(self):
        parser = argparse.ArgumentParser()
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--state")
        sub = parser.add_subparsers(dest="command", required=True)
        attention.register_commands(sub, common)
        record = self.root / "request.json"
        record.write_text(json.dumps(obligation()), encoding="utf-8")
        args = parser.parse_args(["attention-record", "--record", str(record), "--now", AT])
        attention.run_command(args, self.path, AFTER)
        args = parser.parse_args(["catch-up", "--limit", "1"])
        self.assertEqual(attention.run_command(args, self.path, LATER)["attention"]["total"], 1)
        args = parser.parse_args(["attention-show", "--id", "q1", "--now", LATER])
        self.assertEqual(attention.run_command(args, self.path, LATER)["entry"]["id"], "q1")
        for limit, offset in ((0, 0), (51, 0), (1, -1)):
            with self.assertRaises(UsageError):
                catch_up(self.path, LATER, limit=limit, offset=offset)
        record.write_text("broken", encoding="utf-8")
        with self.assertRaises(UsageError):
            attention.run_command(parser.parse_args(["attention-record", "--record", str(record)]), self.path, LATER)


if __name__ == "__main__":
    unittest.main()
