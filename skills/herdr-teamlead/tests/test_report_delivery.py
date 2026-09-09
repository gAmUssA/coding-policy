"""Native-source fixtures distinguish authored formatting from UI decoration."""

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teamlead import cli, recovery, report_delivery as delivery, state
from teamlead.assign import assignment_text
from teamlead.errors import UsageError
from teamlead.herdr import HerdrClient
from tests.fakes import FakeRunner
from tests import test_recovery_cli as owner_fixture

AT = "2026-09-01T12:00:00+00:00"
SESSION = "native-session-361"
PANE = "w1:p1"


def identity(kind):
    return {"source": "herdr:" + kind, "agent": kind, "kind": "id", "value": SESSION}


def codex_rows(final):
    return [
        {"type": "session_meta", "payload": {"id": SESSION}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                                              "content": [{"type": "output_text", "text": final}]}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": final}},
    ]


def grok_row(update, meta=None):
    return {"method": "session/update", "params": {"sessionId": SESSION, "update": update, "_meta": meta or {}}}


def grok_rows(final):
    return [
        grok_row({"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "Write the fresh report"}}),
        grok_row({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": final}},
                 {"promptId": "prompt-1", "streamStartMs": 42}),
        grok_row({"sessionUpdate": "turn_completed", "prompt_id": "prompt-1", "stop_reason": "end_turn"}),
    ]


def encode(rows):
    return "\n".join(json.dumps(row) for row in rows) + "\n"


class NativeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.report = self.tmp / "report [361]+.md"
        self.report.write_text("Current report bytes.\n")
        self.marker = "REPORT: " + str(self.report)
        self.source = self.tmp / "native.jsonl"
        self.pane = {"pane_id": PANE, "agent_status": "idle", "agent_session": identity("codex"),
                     "terminal_id": "terminal-1", "revision": 1, "scroll": {"offset_from_bottom": 0}}
        self.visible = "• " + self.marker

    def write_source(self, rows):
        self.source.write_text(encode(rows))

    def test_completed_native_sources_preserve_authored_context(self):
        bad = ["- " + self.marker, "• " + self.marker, "> " + self.marker, "    " + self.marker,
               "     " + self.marker, "`" + self.marker + "`", "```\n" + self.marker,
               "~~~\n" + self.marker, "````\n```\n" + self.marker,
               "```\n```not-a-close\n" + self.marker, self.marker + ".old",
               self.marker.replace("report ", "report\n"), self.marker + "\nMore content",
               "> quoted example\n" + self.marker, "- authored example\n" + self.marker,
               "-\tauthored example\n" + self.marker, "1.\tauthored example\n" + self.marker]
        for kind, factory in (("codex", codex_rows), ("grok", grok_rows)):
            with self.subTest(kind=kind):
                self.assertTrue(delivery.bare_final(delivery.source_final(encode(factory(self.marker)), kind, SESSION), str(self.report)))
            for text in bad:
                with self.subTest(kind=kind, text=text):
                    self.assertFalse(delivery.bare_final(delivery.source_final(encode(factory(text)), kind, SESSION), str(self.report)))
        self.assertTrue(delivery.bare_final("```\nold example\n```\n" + self.marker, str(self.report)))
        self.assertTrue(delivery.bare_final("- earlier list\n\n" + self.marker, str(self.report)))
        for fenced in ("- removed", "> quoted example", "1. numbered example"):
            self.assertTrue(delivery.bare_final("```\n" + fenced + "\n```\n" + self.marker, str(self.report)))

    def test_latest_completion_and_identity_are_required(self):
        rows = codex_rows(self.marker)
        for candidate in (rows[:-1], rows + [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new"}}],
                          rows + [{"type": "event_msg", "payload": {"type": "error"}}]):
            self.assertIsNone(delivery.source_final(encode(candidate), "codex", SESSION))
        self.assertIsNone(delivery.source_final(encode(rows), "codex", "different-session"))
        for payload in ({"id": "another-session"}, []):
            self.assertIsNone(delivery.source_final(encode(rows[:1] + [{"type": "session_meta", "payload": payload}] + rows[1:]), "codex", SESSION))
        rows[-1]["payload"]["turn_id"] = "other-turn"
        self.assertIsNone(delivery.source_final(encode(rows), "codex", SESSION))
        for body in ("not json", '{"type":"session_meta","payload":[]}\n', '{}\n'):
            self.assertIsNone(delivery.source_final(body, "codex", SESSION))
        rows = grok_rows(self.marker)
        for candidate in (rows[:-1], rows + [grok_row({"sessionUpdate": "user_message_chunk"})],
                          rows + [grok_row({"sessionUpdate": "error"})]):
            self.assertIsNone(delivery.source_final(encode(candidate), "grok", SESSION))
        for field, value in (("stop_reason", "refusal"), ("prompt_id", "different-prompt")):
            altered = copy.deepcopy(rows)
            altered[-1]["params"]["update"][field] = value
            self.assertIsNone(delivery.source_final(encode(altered), "grok", SESSION))
        self.assertIsNone(delivery.source_final(encode(rows), "grok", "other-session"))

    def test_grok_native_chunks_join_only_within_the_final_stream(self):
        rows = grok_rows("REPORT: ")
        rows.insert(-1, grok_row({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": str(self.report)}},
                               {"promptId": "prompt-1", "streamStartMs": 42}))
        self.assertEqual(delivery.source_final(encode(rows), "grok", SESSION), self.marker)
        rows[-2]["params"]["_meta"]["streamStartMs"] = 43
        self.assertEqual(delivery.source_final(encode(rows), "grok", SESSION), str(self.report))

    def test_native_display_allowlist_never_joins_rows(self):
        self.assertEqual(delivery.decorated_row(self.visible, "codex", str(self.report)), self.visible)
        for suffix in ("", "          11:47 PM"):
            row = "     " + self.marker + suffix
            self.assertEqual(delivery.decorated_row(row, "grok", str(self.report)), row)
        for kind in ("codex", "grok", "unknown"):
            for row in ("- " + self.marker, "> " + self.marker, "    " + self.marker,
                        "      " + self.marker, "• " + self.marker + " extra", "     " + self.marker + " .old",
                        "     " + self.marker + "  32:75 PM", "• REPORT: \n" + str(self.report)):
                self.assertIsNone(delivery.decorated_row(row, kind, str(self.report)))

    def fake_client(self, after=None, visible=None):
        test = self

        class Client:
            def __init__(self):
                self.reads = 0

            def agent_get(self, agent):
                return dict(test.pane)

            def pane_get(self, pane_id):
                self.reads += 1
                return copy.deepcopy(after if self.reads > 1 and after is not None else test.pane)

            def pane_read(self, pane_id, lines):
                return (test.visible if visible is None else visible) + "\n"

        return Client()

    def test_probe_requires_stable_native_pane_source_and_file(self):
        self.write_source(codex_rows(self.marker))
        with patch.object(delivery, "source_path", return_value=self.source):
            self.assertTrue(delivery.probe(self.fake_client(), "worker", PANE, str(self.report), self.visible, 40)["found"])
            for field, value in (("terminal_id", "replacement"), ("revision", 2), ("agent_status", "working"),
                                 ("scroll", {"offset_from_bottom": 1}), ("agent_session", identity("grok"))):
                after = {**self.pane, field: value}
                self.assertFalse(delivery.probe(self.fake_client(after), "worker", PANE, str(self.report), self.visible, 40)["found"])
            self.assertFalse(delivery.probe(self.fake_client(visible="new output"), "worker", PANE, str(self.report), self.visible, 40)["found"])
            self.report.unlink()
            self.assertFalse(delivery.probe(self.fake_client(), "worker", PANE, str(self.report), self.visible, 40)["found"])

    def test_source_resolution_never_selects_the_newest_other_session(self):
        root = self.tmp / "sessions" / "2026" / "09" / "01"
        root.mkdir(parents=True)
        matching = root / ("rollout-fixed-" + SESSION + ".jsonl")
        matching.write_text(encode(codex_rows(self.marker)))
        (root / "rollout-newer-other-session.jsonl").write_text("{}\n")
        with patch.dict(os.environ, {"CODEX_HOME": str(self.tmp)}):
            self.assertEqual(delivery.source_path(identity("codex")), matching)
            self.assertIsNone(delivery.source_path({**identity("codex"), "value": "missing"}))
            (root / ("rollout-duplicate-" + SESSION + ".jsonl")).write_text("{}\n")
            self.assertIsNone(delivery.source_path(identity("codex")))

    def test_probe_rechecks_source_when_new_turn_has_not_repainted(self):
        self.write_source(codex_rows(self.marker))
        client = self.fake_client()

        def append_turn(pane_id, lines):
            self.source.write_text(encode(codex_rows(self.marker) + [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new-turn"}}]))
            return self.visible

        with patch.object(delivery, "source_path", return_value=self.source), patch.object(client, "pane_read", side_effect=append_turn):
            self.assertFalse(delivery.probe(client, "worker", PANE, str(self.report), self.visible, 40)["found"])

    def test_public_probe_reports_first_read_and_reread_failures(self):
        source_bytes = encode(codex_rows(self.marker)).encode("utf-8")
        errors = (PermissionError(13, "Permission denied", str(self.source)),
                  OSError(5, "Input/output error", str(self.source)),
                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"))
        args = ["probe-report", "--agent", "worker", "--pane", PANE, "--report", str(self.report), "--lines", "40"]
        for read_number in (1, 2):
            for error in errors:
                outcomes = [source_bytes] * (read_number - 1) + [error]
                output, diagnostic = io.StringIO(), io.StringIO()
                with self.subTest(read_number=read_number, error=type(error).__name__), \
                        patch.object(delivery, "source_path", return_value=self.source), \
                        patch.object(Path, "read_bytes", side_effect=outcomes), patch.object(sys, "stdin", io.StringIO(self.visible)):
                    code = cli.main(args, stdout=output, stderr=diagnostic, client=self.fake_client())
                    self.assertEqual(code, 1)
                    self.assertEqual(output.getvalue(), "")
                    message = json.loads(diagnostic.getvalue())["message"]
                    self.assertIn(str(self.source), message)
                    self.assertIn(str(error), message)
                    self.assertIn("Restore readable UTF-8 transcript bytes and file permissions", message)

    def test_absent_native_source_stays_unconfirmed_on_either_read(self):
        source_bytes = encode(codex_rows(self.marker)).encode("utf-8")
        for outcomes in ([FileNotFoundError()], [source_bytes, FileNotFoundError()]):
            with patch.object(delivery, "source_path", return_value=self.source), patch.object(Path, "read_bytes", side_effect=outcomes):
                result = delivery.probe(self.fake_client(), "worker", PANE, str(self.report), self.visible, 40)
                self.assertFalse(result["found"])
                self.assertEqual(result["reason"], "native_source_unavailable")
        with patch.object(delivery, "source_path", side_effect=FileNotFoundError()):
            self.assertFalse(delivery.probe(self.fake_client(), "worker", PANE, str(self.report), self.visible, 40)["found"])

    def test_native_directory_permission_error_is_not_a_missing_source(self):
        root = self.tmp / "sessions"
        error = PermissionError(13, "Permission denied", str(root))
        with patch.object(delivery, "source_root", return_value=root), patch("os.scandir", side_effect=error):
            with self.assertRaises(UsageError) as caught:
                delivery.probe(self.fake_client(), "worker", PANE, str(self.report), self.visible, 40)
        self.assertIn(str(root), str(caught.exception))
        self.assertIn("Permission denied", str(caught.exception))
        self.assertIn("Restore readable session directories and search permissions", str(caught.exception))

    def watcher_fixture(self):
        fake = self.tmp / "herdr"
        config = self.tmp / "fake.json"
        fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
                        "def main():\n    d=json.loads(Path(os.environ['FAKE_CONFIG']).read_text())\n"
                        "    command=sys.argv[1:3]\n"
                        "    if command==['agent','get']: print(json.dumps({'result':{'agent':d['pane']}}))\n"
                        "    elif command==['pane','get']: print(json.dumps({'result':{'pane':d['pane']}}))\n"
                        "    elif command==['pane','read']:\n"
                        "        if d.get('break_source'):\n"
                        "            counter=Path(os.environ['FAKE_CONFIG']+'.reads')\n"
                        "            count=int(counter.read_text())+1 if counter.exists() else 1\n"
                        "            counter.write_text(str(count))\n"
                        "            if count==2:\n"
                        "                source=Path(d['break_source'])\n"
                        "                source.unlink()\n"
                        "                source.mkdir()\n"
                        "        print(d['visible'])\n"
                        "    elif command==['pane','wait-output']: print('{}')\n"
                        "    else: sys.exit(2)\n"
                        "if __name__=='__main__': main()\n")
        fake.chmod(0o755)
        env = {**os.environ, "HERDR_ENV": "1", "HERDR_BIN": str(fake), "FAKE_CONFIG": str(config),
               "HOME": str(self.tmp), "CODEX_HOME": str(self.tmp / ".codex"), "TEAMLEAD_WAIT_BUDGET_SEC": "0"}
        return env, config

    def test_public_watcher_propagates_unreadable_source_as_tool_failure(self):
        env, config = self.watcher_fixture()
        source = self.tmp / (".codex/sessions/2026/09/01/rollout-fixed-" + SESSION + ".jsonl")
        source.parent.mkdir(parents=True)
        for failure in ("decode", "reread"):
            source.write_bytes(b"\xff" if failure == "decode" else encode(codex_rows(self.marker)).encode("utf-8"))
            pane = {"pane": self.pane, "visible": self.visible}
            if failure == "reread":
                pane["break_source"] = str(source)
            config.write_text(json.dumps(pane))
            result = subprocess.run(["bash", str(ROOT / "wait-report.sh"), "worker", str(self.report)],
                                    env=env, capture_output=True, text=True, check=False)
            with self.subTest(failure=failure):
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn(str(source), result.stderr)
                self.assertIn("invalid start byte" if failure == "decode" else "Is a directory", result.stderr)
                self.assertIn("Restore readable UTF-8 transcript bytes and file permissions", result.stderr)

    def test_public_watcher_uses_real_native_source_fixtures(self):
        env, config = self.watcher_fixture()
        for kind, factory, relative in (("codex", codex_rows, ".codex/sessions/2026/09/01/rollout-fixed-" + SESSION + ".jsonl"),
                                        ("grok", grok_rows, ".grok/sessions/project/" + SESSION + "/updates.jsonl")):
            source = self.tmp / relative
            source.parent.mkdir(parents=True)
            source.write_text(encode(factory(self.marker)))
            prefix = "• " if kind == "codex" else "     "
            for source_text, visible, expected in ((self.marker, prefix + self.marker, True),
                    ("```\n- removed\n```\n" + self.marker, prefix + self.marker, True),
                    ("```\n> quoted example\n```\n" + self.marker, prefix + self.marker, True),
                    ("- " + self.marker, prefix + self.marker, False), ("    " + self.marker, prefix + self.marker, False),
                    ("```\n" + self.marker, prefix + self.marker, False),
                    ("> quoted example\n" + self.marker, prefix + self.marker, False),
                    ("- authored example\n" + self.marker, prefix + self.marker, False),
                    ("-\tauthored example\n" + self.marker, prefix + self.marker, False),
                    (self.marker, prefix + "REPORT: \n" + str(self.report), False),
                    (self.marker, prefix + self.marker + ".old", False)):
                source.write_text(encode(factory(source_text)))
                config.write_text(json.dumps({"pane": {**self.pane, "agent_session": identity(kind)}, "visible": visible}))
                result = subprocess.run(["bash", str(ROOT / "wait-report.sh"), "worker", str(self.report)],
                                        env=env, capture_output=True, text=True, check=False)
                with self.subTest(kind=kind, source=source_text, visible=visible):
                    self.assertEqual(result.returncode, 0 if expected else 1, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["found"], expected)

    def recovery_fixture(self):
        document = state.empty_state()
        state.add_assignment(document, AT, "judge", "worker", task="task-361", context_session={"pane_id": PANE, **identity("codex")})
        brief = self.tmp / "brief.md"
        brief.write_text("Judge the original dispute.\n" + self.marker + "\n")
        common = self.tmp / "common.md"
        common.write_text("Shared round requirements.\n")
        assignment = document["assignments"][0]
        dispatch = {"schema_version": 1, "id": "dispatch-361", "at": AT, "fingerprint": "fingerprint-361",
                    "task": "task-361", "role": "judge", "agent": "worker", "fix_round": None, "status": "applied",
                    "assignment_index": 0, "brief": str(brief), "common": str(common),
                    "result": {**assignment, "schema_version": 1, "pane_id": PANE}, "report": None}
        document["recovery"]["dispatches"].append(dispatch)
        data = {"id": "recovery-361", "dispatch": dispatch["id"], "report": str(self.report)}
        source = codex_rows(self.marker)
        source.insert(2, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": assignment_text("judge", str(common), str(brief))}]}})
        artifacts = {"wait_receipt": json.dumps({"agent": "worker", "report_path": str(self.report), "state": "done",
                                                "found": False, "reason": "report file present, worker done on 2 consecutive reads, marker unconfirmed"}),
                     "pane": json.dumps({"result": {"pane": self.pane}}), "visible": self.visible,
                     "source": encode(source)}
        for key, body in artifacts.items():
            path = self.tmp / (key + ".txt")
            path.write_text(body)
            data[key] = str(path)
        return document, data

    def test_owner_cli_appends_recovery_preserving_original_records(self):
        document, data = self.recovery_fixture()
        document["assignments"][0]["context_session"] = None
        document["recovery"]["dispatches"][0]["result"]["context_session"] = None
        prior = copy.deepcopy(document)
        evidence_before = {key: Path(data[key]).read_bytes() for key in ("report", "wait_receipt", "source", "pane", "visible")}
        ledger_path, record = self.tmp / "state.json", self.tmp / "record.json"
        state.save_state(ledger_path, document)
        record.write_text(json.dumps(data))
        runner = FakeRunner()
        output, errors = io.StringIO(), io.StringIO()
        args = ["recover-report", "--state", str(ledger_path), "--record", str(record), "--now", AT]
        result = cli.main(args, stdout=output, stderr=errors, client=HerdrClient(runner=runner))
        self.assertEqual(result, 0, errors.getvalue())
        saved = json.loads(ledger_path.read_text())
        self.assertEqual(saved["assignments"], prior["assignments"])
        self.assertEqual(saved["recovery"]["dispatches"], prior["recovery"]["dispatches"])
        self.assertEqual(saved["recovery"]["delivery_recoveries"][0]["grants_review_approval"], False)
        self.assertEqual(runner.calls, [])
        for key, body in evidence_before.items():
            self.assertEqual(Path(data[key]).read_bytes(), body)
        before_replay = ledger_path.read_bytes()
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 0)
        self.assertEqual(ledger_path.read_bytes(), before_replay)
        self.report.write_text("Changed after original recovery\n")
        self.assertEqual(cli.main(args, stdout=io.StringIO(), stderr=io.StringIO()), 1)
        self.assertEqual(ledger_path.read_bytes(), before_replay)

    def test_recovery_accepts_actual_public_apply_output_with_null_reviewer_session(self):
        case = owner_fixture.RecoveryCommandTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.register()
        case.briefs["reviewer"].write_text("Review the original change.\n" + self.marker + "\n")
        client = case._client({"grok": "idle"})
        code, output, errors = case.invoke(case.apply_args("reviewer"), client)
        self.assertEqual(code, 0, errors)
        applied = json.loads(output)["applied"][0]
        original = case.saved()
        self.assertIsNone(original["assignments"][-1]["context_session"])
        self.assertNotIn("schema_version", applied)
        self.assertEqual(original["recovery"]["dispatches"][-1]["result"]["schema_version"], 1)
        prompt = next(call[4] for call in case.runner.calls if call[1:3] == ["agent", "prompt"])
        rows = grok_rows(self.marker)
        rows[0]["params"]["update"]["content"]["text"] = prompt
        pane = {**self.pane, "pane_id": applied["pane_id"], "agent_session": identity("grok")}
        artifacts = {"source": encode(rows), "pane": json.dumps({"result": {"pane": pane}}),
                     "visible": "     " + self.marker,
                     "wait_receipt": json.dumps({"agent": "grok", "report_path": str(self.report), "state": "idle",
                                                 "found": False, "reason": "report file present, worker idle on 2 consecutive reads, marker unconfirmed"})}
        data = {"id": "actual-apply-recovery", "dispatch": applied["dispatch_id"], "report": str(self.report)}
        for key, value in artifacts.items():
            path = self.tmp / ("actual-" + key + ".txt")
            path.write_text(value)
            data[key] = str(path)
        before_calls = list(case.runner.calls)
        code, _output, errors = case.owner("recover-report", data, client)
        self.assertEqual(code, 0, errors)
        recovered = case.saved()
        self.assertEqual(recovered["assignments"], original["assignments"])
        self.assertEqual(recovered["recovery"]["dispatches"], original["recovery"]["dispatches"])
        self.assertEqual(case.runner.calls, before_calls)

    def test_recovery_rejects_unbound_negative_receipts_and_authored_examples(self):
        for variation in ("authored", "session", "refusal", "brief", "pending", "no-prompt", "bad-pane", "path-proof"):
            document, data = self.recovery_fixture()
            if variation == "authored":
                Path(data["source"]).write_text(encode(codex_rows("- " + self.marker)))
            elif variation == "session":
                document["assignments"][0]["context_session"]["value"] = "different-session"
            elif variation == "refusal":
                negative = json.loads(Path(data["wait_receipt"]).read_text())
                negative["reason"] = "terminal_provider_refusal"
                Path(data["wait_receipt"]).write_text(json.dumps(negative))
            elif variation == "brief":
                Path(document["recovery"]["dispatches"][0]["brief"]).write_text("REPORT: /another/report.md\n")
            elif variation == "pending":
                document["recovery"]["dispatches"][0]["status"] = "sending"
            elif variation == "bad-pane":
                Path(data["pane"]).write_text('{"result": []}')
            elif variation == "path-proof":
                document["assignments"][0]["context_session"]["kind"] = "path"
                document["assignments"][0]["context_session"]["value"] = "/original/native/session.jsonl"
            else:
                document["assignments"][0]["context_session"] = None
                Path(data["source"]).write_text(encode(codex_rows(self.marker)))
            before = copy.deepcopy(document)
            with self.subTest(variation=variation), self.assertRaises(UsageError):
                delivery.recover(document["recovery"], document["assignments"], data, AT)
            self.assertEqual(document, before)

    def test_malformed_native_identity_stays_unconfirmed(self):
        for value in ({}, [], None, 42):
            self.assertIsNone(delivery.native_identity({"agent_session": {**identity("codex"), "agent": value}}))

    def test_store_upgrade_preserves_negative_and_dispatch_history(self):
        document, _data = self.recovery_fixture()
        store = document["recovery"]
        store["schema_version"] = 2
        del store["delivery_recoveries"]
        del store["role_clearances"]
        original = copy.deepcopy(store)
        self.assertTrue(recovery.migrate_store(store))
        self.assertEqual(store, {**original, "schema_version": 4, "role_clearances": [], "delivery_recoveries": []})
        self.assertFalse(recovery.migrate_store(store))


if __name__ == "__main__":
    unittest.main()
