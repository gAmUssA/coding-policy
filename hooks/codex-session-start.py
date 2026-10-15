#!/usr/bin/env python3
"""Codex SessionStart adapter; no third-party dependencies.

stdin: native SessionStart JSON. Optional argv: a hook filename listed in
hooks/session-start-hooks.txt.
stdout: native hookSpecificOutput with policy paths or translated hook context.
exit: 0 on success/no-op, the child status on failure, 1 on invalid input or timeout.
The no-argument mode only reads packaged files. Child hooks retain their documented
side effects. On POSIX hosts, HOOK_TIMEOUT_SECONDS bounds the hook process group,
and each child sees it as SESSION_START_CHILD_DEADLINE_SEC so its own deadlines
nest inside it. Timeout cleanup sends the group SIGTERM, waits up to
TERM_GRACE_SECONDS for every member (a hooks/bounded-run.sh inside reaps its own
group on SIGTERM), sends SIGKILL to what is left, and drains the output for at
most DRAIN_SECONDS. Errors go to stderr.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypeGuard, cast

ROOT = Path(__file__).resolve().parents[1]
HOOK_TIMEOUT_SECONDS = 45
# Longer than bounded-run.sh's TRAP_GRACE_SEC, so a runner inside the hook
# finishes reaping its own process group before the SIGKILL.
TERM_GRACE_SECONDS = 3
DRAIN_SECONDS = 2
POLL_SECONDS = 0.05


def is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def emit_context(context: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )


def rule_scope(path: Path) -> str:
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path.name}: missing rule frontmatter")
    end = lines.index("---", 1)
    metadata = lines[1:end]
    if "alwaysApply: true" in metadata:
        return "always-on"
    if "alwaysApply: false" in metadata and any(
        line.startswith("applyTo:") for line in metadata
    ):
        return "conditional; read frontmatter scope"
    raise ValueError(f"{path.name}: missing alwaysApply or conditional applyTo")


def bundled_path(name: str) -> Path:
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("manifest path escapes the plugin root")
    return path


def policy_context(manifest: dict[str, Any]) -> str:
    lines = [
        f"gamussa/coding-policy {manifest['version']} is installed at {ROOT}.",
        "Read the always-on rules listed below before repository work; apply conditional rules when their frontmatter scope matches the task. Preserve the scope and exceptions written in each rule.",
        "Rule references such as rules/foo.md resolve against this plugin root. Repository-specific instructions still apply.",
        "Codex installation path adapter: when a bundled skill invokes its own scripts using .tessl/plugins/gamussa/coding-policy or the same path under $HOME, use this installed plugin root instead. This only substitutes paths to co-shipped files; preserve explicit Tessl onboarding and dependency-management operations.",
    ]
    for name in manifest["rules"]:
        path = bundled_path(name)
        lines.append(f"- {path} ({rule_scope(path)})")
    return "\n".join(lines)


def signal_group(pgid: int, sig: signal.Signals) -> bool:
    """Send sig to the group; False when no member is left."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    return True


def group_alive(pgid: int) -> bool:
    """True while any member of the group exists (signal 0 probes only)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def stop_group(process: subprocess.Popen[str]) -> None:
    """Stop a timed-out hook's whole process group and close its pipes."""
    pgid = process.pid
    if signal_group(pgid, signal.SIGTERM):
        deadline = time.monotonic() + TERM_GRACE_SECONDS
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader so only live members count.
            if not group_alive(pgid):
                break
            time.sleep(POLL_SECONDS)
        signal_group(pgid, signal.SIGKILL)
    try:
        process.communicate(timeout=DRAIN_SECONDS)
    except subprocess.TimeoutExpired:
        # A process that left the group still holds a pipe: stop waiting on it.
        process.kill()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait()


def run_hook(command: list[str], event_text: str) -> subprocess.CompletedProcess[str]:
    if os.name != "posix":
        raise ValueError("hook process groups require macOS, Linux, or WSL")
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env={
            **os.environ,
            "TESSL_PLUGIN_DIR": str(ROOT),
            "SESSION_START_CHILD_DEADLINE_SEC": str(HOOK_TIMEOUT_SECONDS),
        },
    ) as process:
        try:
            stdout, stderr = process.communicate(
                input=event_text, timeout=HOOK_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            stop_group(process)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def main() -> int:
    try:
        event_text = sys.stdin.read()
        event = json.loads(event_text)
        if not is_json_object(event):
            raise ValueError("hook input must be a JSON object")
        if event.get("hook_event_name") != "SessionStart":
            return 0
        manifest = json.loads((ROOT / ".tessl-plugin/plugin.json").read_text())
        if len(sys.argv) == 1:
            emit_context(policy_context(manifest))
            return 0
        # The same list hooks/session-start.sh runs for Tessl-configured agents.
        allowed = {
            line.strip()
            for line in (ROOT / "hooks/session-start-hooks.txt").read_text().splitlines()
            if line.strip()
        }
        if len(sys.argv) != 2 or sys.argv[1] not in allowed:
            raise ValueError(
                "expected one SessionStart hook filename from hooks/session-start-hooks.txt"
            )
        result = run_hook(
            ["bash", str(bundled_path("hooks/" + sys.argv[1]))],
            event_text,
        )
        if result.stderr:
            sys.stderr.write(result.stderr)
        if result.returncode:
            print(
                f"codex-compat: {sys.argv[1]} exited {result.returncode}; inspect the hook diagnostics",
                file=sys.stderr,
            )
            return result.returncode if result.returncode > 0 else 1
        if result.stdout.strip():
            output = json.loads(result.stdout)
            if not is_json_object(output) or not isinstance(
                output.get("additionalContext"), str
            ):
                raise ValueError(
                    "SessionStart hook did not return additionalContext text"
                )
            # The isinstance check above proved the value is text.
            emit_context(cast(str, output["additionalContext"]))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(
            f"codex-compat: cannot load policy context: {error}; inspect the plugin installation",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
