"""Policy boundaries for tier choice and launch proof."""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teamlead.errors import ConfigError, HerdrError, UsageError
from teamlead.tiers import launch_flags, mechanical_allowed, parse_tiers, select_tier as _select_tier, verify_argv, verify_worker_permissions, worker_launch_args


def select_tier(*args, **kwargs):
    result = _select_tier(*args, **kwargs)
    assert result is not None, "the test configured a tier table"
    return result


def agent(kind="claude"):
    model = {"claude": "opus-5", "codex": "gpt-5.6-sol", "grok": "grok-4.6"}[kind]
    return SimpleNamespace(name=kind, kind=kind, tiers=parse_tiers({
        "review": {"model": model, "effort": "high"},
        "hostile_verify": {"model": model, "effort": "high"},
        "build": {"model": "sonnet-5" if kind == "claude" else model, "effort": "high"},
        "fix": {"model": "sonnet-5" if kind == "claude" else model, "effort": "high"},
        "mechanical": ({"model": "claude-haiku-4-5"} if kind == "claude" else {"model": model, "effort": "low"}),
    }, kind))


def mechanical_context():
    return {"task_kind": "rebase", "spec_complete": True, "no_semantic_decisions": True,
            "whole_result_oracle": True, "exact_plan": True, "risk_flags": [], "files": 2,
            "input_bytes": 64000}


#: Synthetic session identifiers for resumed-process proof (#382); no real session.
SESSION = "00000000-0000-0000-0000-000000000000"
OTHER_SESSION = "11111111-1111-4111-8111-111111111111"
YOLO = {"claude": "--dangerously-skip-permissions", "codex": "--dangerously-bypass-approvals-and-sandbox",
        "grok": "--always-approve"}


class TierConfigTest(unittest.TestCase):
    def test_claude_all_five_efforts_and_haiku_omission(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(parse_tiers({"build": {"model": "opus-5", "effort": effort}}, "claude")["build"]["effort"], effort)
        tier = parse_tiers({"mechanical": {"model": "claude-haiku-4-5"}}, "claude")["mechanical"]
        self.assertIsNone(tier["effort"])

    def test_effort_missing_invalid_or_impossible_is_refused(self):
        for entry in ({"model": "opus-5"}, {"model": "opus-5", "effort": "ultra"},
                      {"model": "claude-haiku-4-5", "effort": "low"}):
            with self.subTest(entry=entry), self.assertRaises(ConfigError):
                parse_tiers({"build": entry}, "claude")

    def test_judgment_cannot_be_lowered_by_config(self):
        for round_type in ("review", "critic", "recheck", "test_plan", "release_adjudication"):
            for entry in ({"model": "sonnet-5", "effort": "high"}, {"model": "opus-5", "effort": "medium"}):
                with self.subTest(round_type=round_type, entry=entry), self.assertRaises(ConfigError):
                    parse_tiers({round_type: entry}, "claude")

    def test_dead_rounds_kinds_and_bad_costs_are_refused(self):
        with self.assertRaises(ConfigError):
            parse_tiers({"review": {"model": "gemini-3.1-pro", "effort": "high"}}, "agy")
        with self.assertRaises(ConfigError):
            parse_tiers({"typo": {"model": "opus-5", "effort": "high"}}, "claude")
        for multiplier in (0, -1, True, "0.5", float("nan"), float("inf")):
            with self.subTest(multiplier=multiplier), self.assertRaises(ConfigError):
                parse_tiers({"build": {"model": "opus-5", "effort": "high", "multiplier": multiplier}}, "claude")


class SelectionTest(unittest.TestCase):
    def test_initial_build_and_late_fix_have_different_models(self):
        worker = agent()
        self.assertEqual(select_tier(worker, "developer")["model"], "sonnet-5")
        self.assertEqual(select_tier(worker, "developer", fix_round=4)["model"], "opus-5")
        for number in (6, 7):
            self.assertEqual(select_tier(worker, "developer", fix_round=number)["model"], "opus-5")
        self.assertEqual(select_tier(worker, "developer", context={"failed_gates": 2})["model"], "opus-5")
        for context, fix_round, requested in (({}, None, "build"), ({"failed_gates": 2}, None, "build"),
                                             ({}, 4, "fix"), ({"prior_high_miss": True}, None, "build")):
            tier = select_tier(worker, "developer", context=context, fix_round=fix_round)
            self.assertEqual(tier["round"], requested)
            self.assertEqual(tier["tier_row"], "review" if context or fix_round else "build")

    def test_risk_and_hostile_verification_select_top_xhigh(self):
        for context in ({"risk_flags": ["network", "persistence"]}, {"input_bytes": 250001}, {"prior_high_miss": True}):
            with self.subTest(context=context):
                tier = select_tier(agent(), "developer", context=context)
                self.assertEqual((tier["model"], tier["effort"]), ("opus-5", "xhigh"))
        self.assertEqual(select_tier(agent("codex"), "tester")["effort"], "xhigh")
        self.assertEqual(select_tier(agent("grok"), "tester")["effort"], "high")

    def test_mechanical_requires_whole_predicate(self):
        context = mechanical_context()
        self.assertTrue(mechanical_allowed(context))
        self.assertEqual(select_tier(agent(), "developer", "mechanical", context)["model"], "claude-haiku-4-5")
        for key, value in (("spec_complete", False), ("files", 3), ("input_bytes", 64001),
                           ("risk_flags", ["network"]), ("unplanned_file", True),
                           ("tool_retries", 3), ("gate_red_after_repair", True)):
            changed = copy.deepcopy(context)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(UsageError):
                select_tier(agent(), "developer", "mechanical", changed)

    def test_reviewer_cannot_claim_mechanical_role(self):
        with self.assertRaises(UsageError):
            select_tier(agent(), "reviewer", "mechanical", mechanical_context())

    def test_missing_tier_never_falls_back_to_untiered_dispatch(self):
        worker = agent()
        del worker.tiers["review"]
        with self.assertRaises(UsageError):
            select_tier(worker, "reviewer")


class ArgvTest(unittest.TestCase):
    def test_invalid_live_options_are_distinct_from_restrictive_modes(self):
        for argv in (["grok", "--always-approve", "--always-approve"],
                     ["grok", "--permission-mode", "unknown"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(HerdrError, "invalid permission/UI") as caught:
                verify_worker_permissions("grok", argv)
            self.assertNotIn("restrictive permission", str(caught.exception))
        with self.assertRaisesRegex(HerdrError, "restrictive permission"):
            verify_worker_permissions("grok", ["grok", "--permission-mode", "plan"])

    def test_legacy_live_permission_proof_accepts_canonical_and_equivalent_modes(self):
        for kind, argv in (
            ("claude", ["claude", "--dangerously-skip-permissions", "--model", "opus-5"]),
            ("claude", ["claude", "--permission-mode", "bypassPermissions"]),
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "-c", "model_reasoning_effort=high"]),
            ("codex", ["codex", "-a", "never", "-s", "danger-full-access", "--no-alt-screen"]),
            ("grok", ["grok", "--always-approve", "--no-subagents", "--reasoning-effort", "high"]),
        ):
            with self.subTest(kind=kind, argv=argv):
                self.assertIsNone(verify_worker_permissions(kind, argv))

    def test_legacy_live_permission_proof_rejects_missing_overrides_and_lookalikes(self):
        for kind, argv in (
            ("claude", ["claude", "--model", "opus-5"]),
            ("claude", ["claude", "--model", "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--dangerously-skip-permissions", "--permission-mode", "plan"]),
            ("claude", ["claude", "--permission-mode"]),
            ("codex", ["codex", "-a", "never"]),
            ("codex", ["codex", "-s", "danger-full-access"]),
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "-c", 'approval_policy="on-request"']),
            ("grok", ["grok", "--always-approve", "--resume", "session"]),
            ("grok", ["grok", "--always-approve", "--permission-mode", "default"]),
            ("grok", ["wrapper", "grok", "--always-approve"]),
            ("grok", "grok --always-approve"), ("grok", ["grok", 1]),
            ("unknown", []),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)

    def test_resumed_live_permission_proof_accepts_one_explicit_session_with_yolo(self):
        for kind, argv in (
            # The issue's own reproduction: a resumed Codex worker with the explicit YOLO flag.
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["/opt/homebrew/bin/codex", "resume", "--dangerously-bypass-approvals-and-sandbox", SESSION, "--no-alt-screen"]),
            ("codex", ["codex", "resume", SESSION, "-a", "never", "-s", "danger-full-access", "-c", "model_reasoning_effort=high"]),
            ("claude", ["claude", "--resume", SESSION, "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--dangerously-skip-permissions", "-r", SESSION.upper(), "--model", "opus-5", "--effort", "high"]),
            ("claude", ["claude", "--permission-mode", "bypassPermissions", "--resume", SESSION]),
            ("grok", ["grok", "-r", SESSION, "--always-approve", "--no-subagents"]),
            ("grok", ["grok", "--resume", SESSION, "--permission-mode", "bypassPermissions", "--reasoning-effort", "high"]),
        ):
            with self.subTest(kind=kind, argv=argv):
                self.assertIsNone(verify_worker_permissions(kind, argv))

    def test_resumed_proof_still_requires_yolo_and_refuses_restrictive_modes(self):
        for kind, argv, pattern in (
            ("codex", ["codex", "resume", SESSION], "do not prove YOLO"),
            ("codex", ["codex", "resume", SESSION, "-a", "never"], "do not prove YOLO"),
            ("codex", ["codex", "resume", SESSION, "--full-auto"], "restrictive permission"),
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-s", "read-only"], "restrictive permission"),
            ("codex", ["codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox", "-c", 'approval_policy="on-request"'], "config override"),
            ("claude", ["claude", "--resume", SESSION, "--permission-mode", "plan"], "restrictive permission"),
            ("claude", ["claude", "--resume", SESSION, "--dangerously-skip-permissions", "--permission-mode", "acceptEdits"], "restrictive permission"),
            ("claude", ["claude", "--resume", SESSION, "--model", "opus-5"], "do not prove YOLO"),
            ("grok", ["grok", "--resume", SESSION], "do not prove YOLO"),
            ("grok", ["grok", "--resume", SESSION, "--always-approve", "--always-approve"], "invalid permission/UI"),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, pattern) as caught:
                verify_worker_permissions(kind, argv)
            # The diagnostic names both recoveries: a fresh worker, or the same-session restoration.
            self.assertIn("dispatch-recovery.md", str(caught.exception))
            self.assertIn("before dispatch", str(caught.exception))

    def test_ambiguous_or_identity_changing_resume_forms_are_refused(self):
        for kind, extra in (
            ("claude", ["--resume"]), ("claude", ["--resume", "--model", "opus-5"]),
            ("claude", ["--continue"]), ("claude", ["-c"]),
            ("claude", ["--resume", SESSION, "--fork-session"]), ("claude", ["--session-id", SESSION]),
            ("claude", ["--resume", "search term"]), ("claude", ["--resume", SESSION.replace("-", "")]),
            ("claude", ["--resume", SESSION, "--resume", SESSION]), ("claude", ["-r", SESSION, "--resume", OTHER_SESSION]),
            ("claude", ["--resume=" + SESSION]), ("claude", ["--from-pr", "12"]), ("claude", ["--teleport", SESSION]),
            ("codex", ["resume"]), ("codex", ["resume", "--last"]), ("codex", ["resume", "--all", SESSION]),
            ("codex", ["resume", "--include-non-interactive", SESSION]), ("codex", ["resume", "my-session-name"]),
            ("codex", ["resume", SESSION, "fix the bug"]), ("codex", ["resume", SESSION, OTHER_SESSION]),
            ("codex", [SESSION]),
            ("grok", ["--resume"]), ("grok", ["--continue"]), ("grok", ["-c"]),
            ("grok", ["--resume", "Session Title"]), ("grok", ["--resume", SESSION, "--fork-session"]),
            ("grok", ["-s", SESSION]), ("grok", ["--session-id", SESSION]),
            ("grok", ["--resume", SESSION, "--restore-code"]), ("grok", ["--resume", SESSION, "--worktree", "feat"]),
        ):
            argv = [kind] + extra + [YOLO[kind]]
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)
        for kind, argv in (
            ("codex", ["codex", "--dangerously-bypass-approvals-and-sandbox", "resume", SESSION]),
            ("codex", ["sh", "-c", "codex resume " + SESSION + " --dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["wrapper", "codex", "resume", SESSION, "--dangerously-bypass-approvals-and-sandbox"]),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "before dispatch"):
                verify_worker_permissions(kind, argv)

    def test_ambiguous_resume_diagnostics_name_the_explicit_session_requirement(self):
        for kind, argv in (
            ("codex", ["codex", "resume", "--last", "--dangerously-bypass-approvals-and-sandbox"]),
            ("codex", ["codex", "resume", "--dangerously-bypass-approvals-and-sandbox"]),
            ("claude", ["claude", "--continue", "--dangerously-skip-permissions"]),
            ("claude", ["claude", "--resume", "--dangerously-skip-permissions"]),
            ("grok", ["grok", "--resume", "Session Title", "--always-approve"]),
        ):
            with self.subTest(kind=kind, argv=argv), self.assertRaisesRegex(HerdrError, "explicit session UUID"):
                verify_worker_permissions(kind, argv)

    def test_launch_args_and_tier_proof_keep_refusing_resume_forms(self):
        for kind, options in (("claude", ["--resume", SESSION]), ("codex", ["resume", SESSION]), ("grok", ["-r", SESSION])):
            with self.subTest(kind=kind), self.assertRaisesRegex(ConfigError, "resume"):
                worker_launch_args(kind, options)
        tier = select_tier(agent(), "reviewer")
        with self.assertRaises(HerdrError):
            verify_argv("claude", tier, ["claude", "--dangerously-skip-permissions", "--resume", SESSION, "--model", "opus-5", "--effort", "high"],
                        ["--dangerously-skip-permissions"])

    def test_worker_defaults_and_ui_options_use_yolo_for_each_cli(self):
        for kind, options, expected in (
            ("claude", [], ["--dangerously-skip-permissions"]),
            ("codex", ["--no-alt-screen"], ["--dangerously-bypass-approvals-and-sandbox", "--no-alt-screen"]),
            ("grok", ["--no-subagents", "--no-alt-screen"], ["--always-approve", "--no-subagents", "--no-alt-screen"]),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(worker_launch_args(kind, options), expected)
                self.assertEqual(worker_launch_args(kind, expected), expected)

    def test_permission_aliases_normalize_without_duplicate_flags(self):
        cases = (("claude", ["--permission-mode", "bypassPermissions"], "--dangerously-skip-permissions"),
                 ("codex", ["-a", "never", "--sandbox", "danger-full-access"], "--dangerously-bypass-approvals-and-sandbox"),
                 ("grok", ["--permission-mode", "bypassPermissions", "--always-approve"], "--always-approve"))
        for kind, options, expected in cases:
            with self.subTest(kind=kind):
                self.assertEqual(worker_launch_args(kind, options), [expected])

    def test_restrictive_options_cannot_override_yolo(self):
        cases = (("claude", ["--permission-mode", "default"]),
                 ("claude", ["--dangerously-skip-permissions", "--permission-mode", "plan"]),
                 ("codex", ["--full-auto"]), ("codex", ["-a", "on-request"]),
                 ("codex", ["--sandbox", "workspace-write"]),
                 ("codex", ["--dangerously-bypass-approvals-and-sandbox", "-s", "read-only"]),
                 ("grok", ["--permission-mode", "acceptEdits"]))
        for kind, options in cases:
            with self.subTest(kind=kind, options=options), self.assertRaisesRegex(ConfigError, "required YOLO mode"):
                worker_launch_args(kind, options)

    def test_unknown_kind_and_unsupported_arguments_fail(self):
        for kind, options in (("unsupported", []), ("codex", ["--model", "other"]),
                              ("claude", ["--permission-mode"]), ("grok", ["--always-approve", "--always-approve"])):
            with self.subTest(kind=kind), self.assertRaises(ConfigError):
                worker_launch_args(kind, options)

    def test_each_installed_cli_has_exact_flags(self):
        for kind in ("claude", "codex", "grok"):
            tier = select_tier(agent(kind), "reviewer")
            argv = ["/usr/local/bin/" + kind] + launch_flags(kind, tier)
            self.assertEqual(verify_argv(kind, tier, argv)["effort"], "high")

    def test_no_effort_flag_for_haiku(self):
        tier = select_tier(agent(), "developer", "mechanical", mechanical_context())
        self.assertEqual(launch_flags("claude", tier), ["--model", "claude-haiku-4-5"])

    def test_wrong_missing_duplicate_or_transcript_values_do_not_verify(self):
        tier = select_tier(agent(), "reviewer")
        wanted = ["claude", "--model", "opus-5", "--effort", "high"]
        for argv in (" ".join(wanted), [], wanted[:-2], wanted[:-1] + ["xhigh"],
                     wanted + ["--effort", "low"], wanted + ["--resume"],
                     ["echo"] + wanted, ["claude", "--model", "opus-5-extra", "--effort", "high"]):
            with self.subTest(argv=argv), self.assertRaises(HerdrError):
                verify_argv("claude", tier, argv)


if __name__ == "__main__":
    unittest.main()
