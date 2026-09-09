"""Durable fleet events, exact acknowledgements, crash recovery, and Stop scope."""

import os as _os
import sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import argparse
import copy
import json
import os
import subprocess
import shlex
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from typing import Callable

from teamlead import supervision as store
from teamlead import supervision_runtime as runtime
from teamlead import supervision_hook as hook
from teamlead.errors import HerdrError, StateError, UsageError

AT = "2026-09-01T12:00:00+00:00"
PROCESS = {"pid": 101, "identity": "live-process-identity"}
NATIVE = {"source": "herdr:codex", "agent": "codex", "kind": "id", "value": "native-worker"}


class Clock:
    def __init__(self):
        self.value = store.timestamp(AT)
        self.sleeps = []
        self.on_sleep: Callable[[], object] | None = None

    def now(self):
        return self.value.isoformat()

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep()


class Client:
    def __init__(self):
        self.agents = {}
        self.reads = []
        self.visible = {}
        self.panes = {}
        self.failures = set()

    def agent_get(self, name):
        self.reads.append(name)
        if name in self.failures:
            raise HerdrError("Read unavailable; inspect the worker connection.", {})
        return copy.deepcopy(self.agents[name])

    def agent_read(self, name, **_kwargs):
        return self.visible.get(name, "working")

    def pane_get(self, pane):
        return copy.deepcopy(self.panes[pane])


class SupervisionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="teamlead-supervision-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "owner.json"
        self.bindings = self.root / "bindings"
        self.evidence = self.root / "TASK-LEDGER.md"
        self.evidence.write_text("Lead verified this bounded outcome; task release still pending.\n")
        self.who = store.identity("lead-session", str(self.root), "fixture", pane_id="lead-pane")
        store.bind(self.path, self.who, AT, root=self.bindings)
        self.client = Client()
        self.clock = Clock()
        self.probe = lambda _pid: PROCESS
        self.payload = {"cwd": str(self.root), "session_id": "lead-session", "stop_hook_active": False}
        self.environ = {"HERDR_ENV": "fixture", "HERDR_PANE_ID": "lead-pane"}

    def member(self, name="a", status="working", native=NATIVE):
        record = {"id": "dispatch-" + name, "agent": name, "task": "task-" + name,
                  "report": str(self.root / (name + ".md")), "pane_id": "pane-" + name,
                  "native_session": native}
        store.enroll(self.path, record, AT)
        self.client.agents[name] = {"pane_id": record["pane_id"], "agent_status": status, "agent_session": native}
        return record

    def watch(self, **kwargs):
        return runtime.watch(self.path, self.client, self.clock.now(), clock=self.clock.now,
                             sleeper=self.clock.sleep, probe=self.probe, pid=101, duration=4, interval=1, **kwargs)

    def emit(self, member="dispatch-a", kind="test"):
        return store.transaction(self.path, lambda data: store.append_event(data, AT, member, kind, {"observation_only": True}))

    def ack(self, result=None):
        result = result or store.drain(self.path)
        return store.acknowledge(self.path, {"through": result["through"], "outcomes": [
            {"event": row["id"], "outcome": "Reconciled source; no acceptance inferred.", "evidence": [str(self.evidence)]}
            for row in result["events"]]}, AT)

    def stop(self, payload=None, environ=None):
        return hook.check(payload or self.payload, self.environ if environ is None else environ,
                          self.clock.now(), root=self.bindings, probe=self.probe)

    def hold(self, members):
        return store.hold(self.path, {"id": "hold-1", "kind": "waiting_for_user", "resume_condition": "User supplies the named decision.",
            "evidence": [str(self.evidence)], "dispositions": [
                {"member": member, "outcome": "Explicit user-held decision recorded in the ledger.", "evidence": [str(self.evidence)]}
                for member in members]}, AT)

    def test_worker_b_event_is_returned_while_a_is_still_working(self):
        self.member("a")
        b = self.member("b")
        self.clock.on_sleep = lambda: Path(b["report"]).write_text("Candidate report; review still required")
        result = self.watch()
        self.assertEqual(result["reason"], "events")
        self.assertIn("a", self.client.reads)
        self.assertIn("b", self.client.reads)
        self.assertTrue(any(row["member"] == "dispatch-b" and row["kind"] == "report_observed" for row in result["events"]))
        self.assertTrue(all(row["active"] for row in store.load(self.path)["members"]))

    def test_done_idle_are_only_observations(self):
        self.member(status="done")
        result = self.watch()
        self.assertEqual(result["events"][0]["kind"], "lifecycle_observed")
        self.ack(result)
        self.assertTrue(store.load(self.path)["members"][0]["active"])
        self.assertIsNotNone(self.stop())

    def test_blocked_is_observed_without_answering_or_confirming_a_dialog(self):
        self.member(status="blocked")
        self.client.visible["a"] = "Approval needed; use downstream two-read dialog verification"
        result = self.watch()
        self.assertIn("lifecycle_observed", [row["kind"] for row in result["events"]])
        self.assertNotIn("Approval needed", json.dumps(result))

    def test_identical_observations_deduplicate_after_ack(self):
        record = self.member(status="idle")
        Path(record["report"]).write_text("Ready for report-delivery reconciliation")
        first = self.watch()
        self.ack(first)
        second = self.watch()
        self.assertEqual(second["reason"], "deadline")
        self.assertEqual(second["events"], [])
        self.assertEqual(second["through"], first["through"])

    def test_new_visible_marker_changes_wake_even_if_report_bytes_do_not(self):
        record = self.member(status="idle")
        Path(record["report"]).write_text("Body")
        self.ack(self.watch())
        self.client.visible["a"] = "REPORT: " + record["report"]
        result = self.watch()
        self.assertEqual([row["kind"] for row in result["events"]], ["visible_observed"])

    def test_pending_ack_schedules_durable_recheck_without_changed_bytes(self):
        self.member()
        self.emit()
        request = {"through": 1, "outcomes": [{"event": "event-1", "outcome": "The exact report checkpoint is still pending.",
                    "evidence": [str(self.evidence)], "pending": True}]}
        store.acknowledge(self.path, request, AT)
        saved = store.load(self.path)["acknowledgements"][0]
        store.acknowledge(self.path, request, "2026-09-01T12:00:01+00:00")
        self.assertEqual(store.load(self.path)["acknowledgements"][0], saved)
        self.clock.value = store.timestamp(saved["recheck_at"])
        result = self.watch()
        rechecks = [row for row in result["events"] if row["kind"] == "recheck_due"]
        self.assertEqual(len(rechecks), 1)
        self.assertEqual(rechecks[0]["data"]["event"], "event-1")
        self.ack(result)
        self.assertEqual(self.watch()["events"], [])

    def test_recheck_before_ack_is_rejected_without_mutation(self):
        self.member()
        self.emit()
        before = store.store_path(self.path).read_bytes()
        with self.assertRaises(UsageError):
            store.acknowledge(self.path, {"through": 1, "outcomes": [{"event": "event-1", "outcome": "pending", "evidence": [str(self.evidence)], "recheck_at": "2026-09-01T11:00:00+00:00"}]}, AT)
        self.assertEqual(store.store_path(self.path).read_bytes(), before)

    def test_pending_replay_does_not_touch_workers(self):
        self.member()
        self.emit()
        result = self.watch()
        self.assertEqual(result["reason"], "pending_events")
        self.assertEqual(self.client.reads, [])

    def test_drain_is_read_only_and_does_not_acknowledge(self):
        self.member()
        self.emit()
        before = store.store_path(self.path).read_bytes()
        first = store.drain(self.path)
        self.assertEqual(first, store.drain(self.path))
        self.assertEqual(store.store_path(self.path).read_bytes(), before)
        self.assertEqual(len(first["events"]), 1)

    def test_ack_bound_does_not_lose_events_arriving_after_drain(self):
        self.member()
        self.emit()
        drained = store.drain(self.path)
        later = self.emit(kind="later")
        result = self.ack(drained)
        self.assertEqual([row["id"] for row in result["remaining"]], [later["id"]])
        with self.assertRaises(UsageError):
            store.acknowledge(self.path, {"through": drained["through"], "outcomes": [
                {"event": later["id"], "outcome": "Not observed in that drain", "evidence": [str(self.evidence)]}]}, AT)

    def test_ack_requires_evidence_and_preserves_original_outcome(self):
        self.member()
        self.emit()
        request = {"through": 1, "outcomes": [{"event": "event-1", "outcome": "Handled", "evidence": []}]}
        with self.assertRaises(UsageError):
            store.acknowledge(self.path, request, AT)
        request["outcomes"][0]["evidence"] = [str(self.evidence)]
        store.acknowledge(self.path, request, AT)
        store.acknowledge(self.path, request, "2026-09-01T13:00:00+00:00")
        request["outcomes"][0]["outcome"] = "Different"
        with self.assertRaises(UsageError):
            store.acknowledge(self.path, request, AT)
        self.assertEqual(len(store.load(self.path)["acknowledgements"]), 1)

    def test_unavailable_worker_does_not_prevent_other_worker_observation(self):
        self.member("a")
        self.member("b", status="blocked")
        self.client.failures.add("a")
        result = self.watch()
        self.assertEqual({row["member"] for row in result["events"]}, {"dispatch-a", "dispatch-b"})

    def test_worker_relaunch_is_an_observation_and_never_reassigns(self):
        self.member()
        self.client.agents["a"]["agent_session"] = {**NATIVE, "value": "replacement"}
        result = self.watch()
        self.assertIn("identity_changed_observed", [row["kind"] for row in result["events"]])
        self.assertEqual(store.load(self.path)["members"][0]["assignment"]["native_session"], NATIVE)

    def test_native_context_session_pane_field_does_not_false_positive(self):
        self.member(native={"pane_id": "pane-a", **NATIVE})
        self.client.agents["a"]["agent_session"] = NATIVE
        self.assertEqual(self.watch()["events"], [])

    def test_first_watch_records_missing_native_identity(self):
        self.member(native=None)
        self.assertIn("identity_unverified_observed", [row["kind"] for row in self.watch()["events"]])

    def test_refine_only_fills_missing_identity_and_preserves_original(self):
        self.member(native=None)
        store.refine(self.path, "dispatch-a", "pane-a", NATIVE, AT)
        row = store.load(self.path)["members"][0]
        self.assertIsNone(row["assignment"]["native_session"])
        self.assertEqual(store.expected_assignment(row)["native_session"], NATIVE)
        with self.assertRaises(UsageError):
            store.refine(self.path, "dispatch-a", "pane-a", {**NATIVE, "value": "changed"}, AT)

    def test_dead_process_receipt_emits_loss_before_restart(self):
        self.member()
        self.watch()
        def corrupt_to_crashed(data):
            data["watchers"][-1].update(status="running", process={"pid": 99, "identity": "old"})
        store.transaction(self.path, corrupt_to_crashed)
        result = self.watch()
        self.assertEqual(result["reason"], "pending_events")
        self.assertEqual(result["events"][0]["kind"], "watcher_lost")
        self.ack(result)
        self.assertEqual(self.watch()["reason"], "deadline")

    def test_live_process_blocks_duplicate_even_with_old_heartbeat(self):
        self.member()
        self.watch()
        store.transaction(self.path, lambda data: data["watchers"][-1].update(status="running"))
        self.clock.value += timedelta(seconds=100)
        self.assertEqual(runtime.status(self.path, self.clock.now(), self.probe)["watcher"]["state"], "unresponsive")
        with self.assertRaisesRegex(UsageError, "still exists"):
            self.watch()

    def test_pid_reuse_is_not_live_identity(self):
        self.member()
        self.watch()
        store.transaction(self.path, lambda data: data["watchers"][-1].update(status="running"))
        changed = lambda _pid: {**PROCESS, "identity": "different-start-or-argv"}
        self.assertEqual(runtime.status(self.path, self.clock.now(), changed)["watcher"]["state"], "dead")

    def test_interrupt_records_stopped_watch_without_resolving_assignment(self):
        self.member()
        def interrupt():
            raise KeyboardInterrupt()
        self.clock.on_sleep = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.watch()
        data = store.load(self.path)
        self.assertEqual(data["watchers"][-1]["status"], "stopped")
        self.assertEqual(data["watchers"][-1]["reason"], "interrupted")
        self.assertTrue(data["members"][0]["active"])

    def test_only_explicit_resolution_ends_observation(self):
        self.member()
        self.emit()
        request = {"id": "dispatch-a", "outcome": "Lead reconciled assignment; release tracked separately", "evidence": [str(self.evidence)]}
        with self.assertRaises(UsageError):
            store.resolve(self.path, request, AT)
        self.ack()
        store.resolve(self.path, request, AT)
        self.assertIsNone(self.stop())
        self.assertFalse(self.path.exists(), "Supervision must not rewrite dispatch/task state")

    def test_active_worker_always_blocks_stop_even_with_loop_guard(self):
        self.member()
        self.assertEqual(self.stop()["decision"], "block")
        self.assertEqual(self.stop({**self.payload, "stop_hook_active": True})["decision"], "block")

    def test_worker_and_unrelated_session_do_not_inherit_lead_gate(self):
        self.member()
        self.assertIsNone(self.stop({**self.payload, "session_id": "worker-session"}))
        self.assertIsNone(self.stop(environ={**self.environ, "HERDR_PANE_ID": "worker-pane"}))
        self.assertIsNone(self.stop(environ={}))
        self.assertIsNone(self.stop({**self.payload, "cwd": str(self.root / "other")}))

    def test_user_hold_requires_disposition_for_every_active_member(self):
        self.member("a")
        self.member("b")
        with self.assertRaisesRegex(UsageError, "every active"):
            self.hold(["dispatch-a"])
        self.hold(["dispatch-a", "dispatch-b"])
        self.assertIsNone(self.stop())
        store.resume(self.path, AT)
        self.assertEqual(self.stop()["decision"], "block")

    def test_new_event_or_assignment_invalidates_held_boundary(self):
        self.member()
        self.hold(["dispatch-a"])
        self.emit()
        self.assertIsNotNone(self.stop())
        self.ack()
        self.assertIsNotNone(self.stop(), "A prior hold does not cover a later event merely because it was acked")

    def test_pending_events_cannot_be_hidden_by_hold(self):
        self.member()
        self.emit()
        with self.assertRaisesRegex(UsageError, "pending"):
            self.hold(["dispatch-a"])

    def test_canonical_alias_uses_one_store_and_binding(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.member()
        self.assertEqual(store.drain(alias / self.path.name), store.drain(self.path))
        who = store.identity("lead-session", str(alias), "fixture", pane_id="lead-pane")
        self.assertEqual(store.binding_path(who, self.bindings), store.binding_path(self.who, self.bindings))

    def test_corrupt_newer_state_is_preserved_and_blocks_bound_stop(self):
        for body in ('{"schema_version":900}', '{"broken"', '[1,2]', 'null'):
            store.store_path(self.path).write_text(body)
            with self.assertRaises(StateError):
                store.enroll(self.path, {"id": "d", "agent": "a", "task": "t", "report": str(self.evidence), "pane_id": None, "native_session": None}, AT)
            self.assertEqual(store.store_path(self.path).read_text(), body)
            self.assertEqual(self.stop()["decision"], "block")

    def test_missing_owner_state_is_not_proof_of_no_active_work(self):
        store.store_path(self.path).unlink()
        self.assertEqual(self.stop()["decision"], "block")

    def test_conflicting_native_binding_is_preserved(self):
        original = store.binding_path(self.who, self.bindings).read_bytes()
        with self.assertRaises(StateError):
            store.bind(self.root / "different-state.json", self.who, AT, root=self.bindings)
        self.assertEqual(store.binding_path(self.who, self.bindings).read_bytes(), original)

    def test_interrupted_new_lead_binding_blocks_new_session_until_retry(self):
        self.member()
        new_who = store.identity("new-lead", str(self.root), "fixture", pane_id="lead-pane")
        original_save = store.save_state
        def fail_owner(path, data):
            if Path(path) == store.store_path(self.path):
                raise StateError("Simulated interrupted owner commit; retry binding.", {})
            return original_save(path, data)
        with patch.object(store, "save_state", side_effect=fail_owner):
            with self.assertRaises(StateError):
                store.bind(self.path, new_who, AT, root=self.bindings)
        self.assertEqual(self.stop({**self.payload, "session_id": "new-lead"})["decision"], "block")
        store.bind(self.path, new_who, AT, root=self.bindings)
        self.assertIsNone(self.stop(), "Old native lead no longer owns the committed binding")
        self.assertEqual(self.stop({**self.payload, "session_id": "new-lead"})["decision"], "block")

    def test_bind_current_uses_read_only_native_pane_evidence(self):
        self.client.panes["lead-pane"] = {"pane_id": "lead-pane", "agent_session": {**NATIVE, "value": "lead-session"}}
        result = runtime.bind_current(self.path, self.client, AT, environ=self.environ, cwd=str(self.root), root=self.bindings)
        self.assertEqual(result["identity"], self.who)

    def test_path_identity_matches_native_hook_transcript_path(self):
        transcript = str(self.root / "native-session.jsonl")
        self.client.panes["lead-pane"] = {"pane_id": "lead-pane", "agent_session": {**NATIVE, "kind": "path", "value": transcript}}
        runtime.bind_current(self.path, self.client, AT, environ=self.environ, cwd=str(self.root), root=self.bindings)
        self.member()
        self.assertIsNotNone(self.stop({**self.payload, "session_id": "unrelated-native-id", "transcript_path": transcript}))

    def test_drain_and_stop_do_not_require_original_evidence_files(self):
        self.member()
        self.emit()
        self.ack()
        self.hold(["dispatch-a"])
        self.evidence.unlink()
        self.assertEqual(store.drain(self.path)["events"], [])
        self.assertIsNone(self.stop())

    def test_live_process_probe_invokes_ps_with_start_and_command(self):
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Tue Sep 1 12:00:00 2026 python watch", "")) as run:
            self.assertIsNotNone(runtime.process_identity(123))
            self.assertEqual(run.call_args.args[0], ["ps", "-p", "123", "-o", "lstart=", "-o", "command="])
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertIsNone(runtime.process_identity(123))

    def test_registered_commands_route_readback_without_worker_or_config_access(self):
        parser = argparse.ArgumentParser()
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument("--state")
        sub = parser.add_subparsers(dest="command", required=True)
        runtime.register_commands(sub, common)
        self.member()
        self.emit()
        args = parser.parse_args(["supervision-drain", "--state", str(self.path), "--now", AT])
        result = runtime.run_command(args, self.path, AT)
        self.assertEqual(result["through"], 1)
        args = parser.parse_args(["supervision-status", "--now", AT])
        result = runtime.run_command(args, self.path, AT, process_probe=self.probe)
        self.assertEqual(result["active"][0]["id"], "dispatch-a")
        missing = self.root / "missing.json"
        args = parser.parse_args(["supervision-bind", "--record", str(missing), "--now", AT])
        with self.assertRaisesRegex(UsageError, "missing"):
            runtime.run_command(args, self.path, AT)

    def test_mutating_command_inputs_produce_ack_and_resolution_receipts(self):
        parser = argparse.ArgumentParser()
        runtime.register_commands(parser.add_subparsers(dest="command", required=True), argparse.ArgumentParser(add_help=False))
        self.member()
        self.emit()
        request = self.root / "ack.json"
        request.write_text(json.dumps({"through": 1, "outcomes": [{"event": "event-1", "outcome": "Reconciled report candidate", "evidence": [str(self.evidence)]}]}))
        args = parser.parse_args(["supervision-ack", "--record", str(request), "--now", AT])
        self.assertEqual(runtime.run_command(args, self.path, AT)["acknowledged"], ["event-1"])
        request.write_text(json.dumps({"id": "dispatch-a", "outcome": "Accepted assignment in ledger; task release remains open", "evidence": [str(self.evidence)]}))
        args = parser.parse_args(["supervision-resolve", "--record", str(request), "--now", AT])
        self.assertFalse(runtime.run_command(args, self.path, AT)["active"])

    def test_hook_wrapper_subprocess_exact_scope_and_json(self):
        self.member()
        native_root = self.root / "xdg" / "teamlead" / "supervision-bindings"
        store.bind(self.path, self.who, AT, root=native_root)
        script = Path(_ROOT).parent.parent / "hooks" / "herdr-supervision-stop.sh"
        environment = {**os.environ, **self.environ, "XDG_STATE_HOME": str(self.root / "xdg")}
        result = subprocess.run(["bash", str(script)], input=json.dumps(self.payload), env=environment, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        unrelated = subprocess.run(["bash", str(script)], input=json.dumps({**self.payload, "session_id": "worker"}), env=environment, capture_output=True, text=True, check=False)
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        self.assertEqual(unrelated.stdout, "")

    def test_wrapper_reports_python_startup_failure_without_native_exit_gate(self):
        binaries = self.root / "binaries"
        binaries.mkdir()
        python = binaries / "python3"
        python.write_text("#!/usr/bin/env bash\necho 'fixture: Python import failed' >&2\nexit 7\n")
        python.chmod(0o755)
        script = Path(_ROOT).parent.parent / "hooks/herdr-supervision-stop.sh"
        environment = {**os.environ, **self.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run(["bash", str(script)], input=json.dumps(self.payload), env=environment,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Python hook failed (exit 7)", result.stderr)
        self.assertIn("restore the hook installation", result.stderr)

    def test_published_manifest_hooks_run_from_paths_with_spaces_at_mode_0644(self):
        self.member()
        native_root = self.root / "xdg" / "teamlead" / "supervision-bindings"
        store.bind(self.path, self.who, AT, root=native_root)
        repo = Path(_ROOT).parent.parent
        plugin = self.root / "installed plugin"
        shutil.copytree(Path(_ROOT) / "teamlead", plugin / "skills/herdr-teamlead/teamlead",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (plugin / "hooks").mkdir()
        script = plugin / "hooks/herdr-supervision-stop.sh"
        shutil.copyfile(repo / "hooks/herdr-supervision-stop.sh", script)
        script.chmod(0o644)
        manifest = json.loads((repo / ".tessl-plugin/plugin.json").read_text())
        environment = {**os.environ, **self.environ, "XDG_STATE_HOME": str(self.root / "xdg")}
        for agent in ("claude-code", "codex"):
            with self.subTest(agent=agent):
                entries = [item for group in manifest["nativeHooks"][agent]["Stop"]
                           for item in group["hooks"] if "herdr-supervision-stop.sh" in json.dumps(item)]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                expand = lambda value: value.replace("${TESSL_PLUGIN_DIR}", str(plugin))
                argv = shlex.split(expand(entry["command"])) + [expand(arg) for arg in entry.get("args", [])]
                result = subprocess.run(argv, input=json.dumps(self.payload), env=environment,
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["decision"], "block")


if __name__ == "__main__":
    unittest.main()
