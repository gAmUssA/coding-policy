"""Actionable, scrubbed observation failures must not hide another worker's result."""

import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from teamlead import supervision as store
from teamlead import supervision_runtime as runtime
from teamlead.errors import HerdrError, StateError
from tests.test_supervision import AT, Clock, NATIVE, PROCESS


class SupervisionDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="supervision-diagnostics-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state.json"
        self.clock = Clock()
        self.calls = []
        store.bind(self.state, store.identity("lead", str(self.root), "fixture", pane_id="p0"), AT, root=self.root / "bindings")
        for name in ("a", "b"):
            store.enroll(self.state, {"id": "dispatch-" + name, "agent": name, "task": "task-" + name,
                                     "report": str(self.root / (name + ".md")), "pane_id": "pane-" + name,
                                     "native_session": NATIVE}, AT)
        (self.root / "b.md").write_text("Worker B finished its requested report.\n", encoding="utf-8")

    def runner(self, a_get=None, a_read=None):
        def run(argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            operation, name = argv[1:3], argv[3]
            response = a_get if operation == ["agent", "get"] and name == "a" else a_read if operation == ["agent", "read"] and name == "a" else None
            if isinstance(response, Exception):
                raise response
            if response is not None:
                return response
            if operation == ["agent", "get"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"agent": {
                    "pane_id": "pane-" + name, "agent_status": "idle" if name == "b" else "working", "agent_session": NATIVE}}}), "")
            if operation == ["agent", "read"]:
                return subprocess.CompletedProcess(argv, 0, "REPORT: " + str(self.root / (name + ".md")), "")
            raise AssertionError("Unexpected operation: " + repr(argv))
        return run

    def watch(self, a_get=None, a_read=None):
        with patch.object(runtime.subprocess, "run", side_effect=self.runner(a_get, a_read)):
            result = runtime.watch(self.state, runtime.read_client(binary="/fixture/herdr"), AT,
                                   clock=self.clock.now, sleeper=self.clock.sleep, probe=lambda _pid: PROCESS,
                                   pid=101, duration=4, interval=1)
        self.assertTrue(any(row["member"] == "dispatch-b" and row["kind"] == "report_observed" for row in result["events"]))
        self.assertTrue(all(row["active"] for row in store.load(self.state)["members"]))
        return result

    def diagnostic(self, result, kind="observation_error_observed"):
        return next(row["data"] for row in result["events"] if row["member"] == "dispatch-a" and row["kind"] == kind)

    def test_missing_executable_preserves_install_guidance_and_other_worker_report(self):
        data = self.diagnostic(self.watch(FileNotFoundError("fixture executable missing")))
        self.assertEqual(data["code"], "herdr_error")
        self.assertEqual(data["operation"], "herdr agent get")
        self.assertEqual(data["agent"], "a")
        self.assertIn("install Herdr", data["message"])
        self.assertIn("TEAMLEAD_HERDR_BIN", data["message"])
        self.assertIn("does not prove completion", data["recovery"])

    def test_timeout_names_actual_observation_bound_without_raw_output(self):
        failure = subprocess.TimeoutExpired(["/fixture/herdr", "agent", "get", "a"], runtime.OBSERVATION_TIMEOUT,
                                            output="unrelated private pane body", stderr="unrelated private stderr")
        result = self.watch(failure)
        data = self.diagnostic(result)
        self.assertIn(str(runtime.OBSERVATION_TIMEOUT) + "s read timeout", data["message"])
        self.assertNotIn("300", data["message"])
        self.assertNotIn("unrelated private", json.dumps(result))
        self.assertTrue(all(kwargs["timeout"] == runtime.OBSERVATION_TIMEOUT for _argv, kwargs in self.calls))

    def test_startup_permission_failure_is_scrubbed_and_other_worker_is_observed(self):
        data = self.diagnostic(self.watch(PermissionError("executable denied; token=opaque-fixture-value")))
        self.assertIn("Cannot start", data["message"])
        self.assertIn("executable access", data["message"])
        self.assertIn("[redacted]", data["message"])
        self.assertNotIn("opaque-fixture-value", store.store_path(self.state).read_text(encoding="utf-8"))

    def test_non_json_response_keeps_structural_cause_without_stdout_excerpt(self):
        response = subprocess.CompletedProcess([], 0, "private conversation text, not a control response", "")
        data = self.diagnostic(self.watch(response))
        self.assertIn("non-JSON control response", data["message"])
        self.assertNotIn("private conversation", store.store_path(self.state).read_text(encoding="utf-8"))

    def test_missing_control_result_preserves_actionable_shape_diagnostic(self):
        response = subprocess.CompletedProcess([], 0, '{"private-field-name":"private value"}', "")
        data = self.diagnostic(self.watch(response))
        self.assertIn("omitted the result field", data["message"])
        self.assertNotIn("private-field-name", store.store_path(self.state).read_text(encoding="utf-8"))

    def test_non_object_control_result_does_not_abort_other_worker_observation(self):
        for value in (None, [], "unexpected string"):
            with self.subTest(value=value):
                # Direct observe uses the real bounded client and does not need a
                # second watcher while earlier events remain unacknowledged.
                member = store.load(self.state)["members"][0]
                response = subprocess.CompletedProcess([], 0, json.dumps({"result": value}), "")
                with patch.object(runtime.subprocess, "run", side_effect=self.runner(response)):
                    data = runtime.observe(runtime.read_client(binary="/fixture/herdr"), member)["observation_error"]
                self.assertIn("result is not an object", data["message"])
        result = self.watch(subprocess.CompletedProcess([], 0, '{"result":[]}', ""))
        self.assertEqual(self.diagnostic(result)["operation"], "herdr agent get")

    def test_malformed_agent_record_preserves_name_recovery(self):
        data = self.diagnostic(self.watch(subprocess.CompletedProcess([], 0, '{"result":{"agent":null}}', "")))
        self.assertIn("returned no agent record", data["message"])
        self.assertIn("herdr agent list", data["message"])

    def test_control_failure_redacts_credentials_and_bounds_message(self):
        secret = "ghp_" + "a" * 36
        response = subprocess.CompletedProcess([], 1, "unneeded stdout", json.dumps({"error": {
            "code": "AUTH_FAILED", "message": "Connection denied " + secret + " " + "x" * 5000}}))
        result = self.watch(response)
        data = self.diagnostic(result)
        self.assertIn("AUTH_FAILED", data["message"])
        self.assertIn("[redacted]", data["message"])
        self.assertIn("[truncated", data["message"])
        self.assertLess(len(data["message"]), 2200)
        saved = store.store_path(self.state).read_text(encoding="utf-8")
        self.assertNotIn(secret, saved)
        self.assertNotIn("unneeded stdout", saved)

    def test_arbitrary_exception_details_are_not_persisted(self):
        failure = HerdrError("Connection failed; retry its read.", {"stdout": "private arbitrary detail", "stderr": "another private detail"})
        result = self.watch(failure)
        self.assertEqual(self.diagnostic(result)["message"], failure.message)
        saved = store.store_path(self.state).read_text(encoding="utf-8")
        self.assertNotIn("private arbitrary detail", saved)
        self.assertNotIn("another private detail", saved)

    def test_visible_read_failure_preserves_existing_report_observation(self):
        (self.root / "a.md").write_text("Worker A report candidate.\n", encoding="utf-8")
        result = self.watch(a_read=HerdrError("Visible pane read disconnected; reconnect Herdr.", {}))
        self.assertEqual(self.diagnostic(result)["operation"], "herdr agent read --source visible")
        self.assertTrue(any(row["member"] == "dispatch-a" and row["kind"] == "report_observed" for row in result["events"]))

    def test_unreadable_report_has_scrubbed_cause_and_delivery_recovery(self):
        original = Path.read_bytes
        def read_bytes(path):
            if path == self.root / "a.md":
                raise PermissionError("file access denied; password=fixture-private")
            return original(path)
        with patch.object(Path, "read_bytes", read_bytes):
            data = self.diagnostic(self.watch(), "report_error_observed")
        self.assertEqual(data["operation"], "read report file")
        self.assertEqual(data["path"], str(self.root / "a.md"))
        self.assertIn("file access denied", data["message"])
        self.assertIn("[redacted]", data["message"])
        self.assertIn("report-delivery checkpoint", data["recovery"])
        self.assertNotIn("fixture-private", store.store_path(self.state).read_text(encoding="utf-8"))

    def test_ps_absence_requires_both_output_streams_empty(self):
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, " \n", " \n")):
            self.assertIsNone(runtime.process_identity(202))
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "process visibility denied")):
            with self.assertRaisesRegex(StateError, "process visibility denied"):
                runtime.process_identity(202)

    def test_ps_stderr_failure_preserves_watcher_and_prevents_duplicate_start(self):
        store.transaction(self.state, lambda data: data["watchers"].append({
            "schema_version": 1, "id": "watch-1", "at": AT, "heartbeat": AT,
            "deadline": "2026-09-01T12:00:45+00:00", "process": {"pid": 202, "identity": "original-identity"},
            "status": "running", "reason": None, "ended_at": None}))
        before = store.store_path(self.state).read_bytes()
        def ps(argv, **_kwargs):
            self.assertEqual(argv[0], "ps")
            if argv[2] == "101":
                return subprocess.CompletedProcess(argv, 0, "Tue Sep 1 12:00:00 2026 python watcher", "")
            return subprocess.CompletedProcess(argv, 1, "", "visibility denied token=fixture-ps-private")
        with patch.object(runtime.subprocess, "run", side_effect=ps):
            with self.assertRaisesRegex(StateError, "existing execution handle") as raised:
                runtime.watch(self.state, runtime.read_client(), AT, clock=self.clock.now, sleeper=self.clock.sleep,
                              probe=runtime.process_identity, pid=101)
        self.assertNotIn("fixture-ps-private", raised.exception.message)
        self.assertIn("[redacted]", raised.exception.message)
        self.assertEqual(store.store_path(self.state).read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_ps_other_failures_keep_scrubbed_recovery_details(self):
        cases = [PermissionError("ps denied; secret=fixture-private"),
                 subprocess.CompletedProcess([], 2, "unused stdout", "ps unsupported option; token=fixture-private"),
                 subprocess.CompletedProcess([], 0, "", "")]
        for failure in cases:
            with self.subTest(failure=failure):
                mock_run = (patch.object(runtime.subprocess, "run", side_effect=failure) if isinstance(failure, Exception)
                            else patch.object(runtime.subprocess, "run", return_value=failure))
                with mock_run:
                    with self.assertRaises(StateError) as raised:
                        runtime.process_identity(202)
                self.assertIn("202", raised.exception.message)
                self.assertNotIn("fixture-private", raised.exception.message)
                self.assertNotIn("unused stdout", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
