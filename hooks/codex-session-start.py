#!/usr/bin/env python3
"""Codex SessionStart adapter; no third-party dependencies.

stdin: native SessionStart JSON. Optional argv: a manifest-declared hook filename.
stdout: native hookSpecificOutput with policy paths or translated hook context.
exit: 0 on success/no-op, the child status on failure, 1 on invalid input or timeout.
The no-argument mode only reads packaged files. Child hooks retain their documented
side effects. HOOK_TIMEOUT_SECONDS bounds each child; errors go to stderr.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK_TIMEOUT_SECONDS = 45


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


def policy_context(manifest: dict) -> str:
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


def main() -> int:
    try:
        event_text = sys.stdin.read()
        event = json.loads(event_text)
        if not isinstance(event, dict):
            raise ValueError("hook input must be a JSON object")
        if event.get("hook_event_name") != "SessionStart":
            return 0
        manifest = json.loads((ROOT / ".tessl-plugin/plugin.json").read_text())
        if len(sys.argv) == 1:
            emit_context(policy_context(manifest))
            return 0
        allowed = {
            Path(hook["args"][0]).name
            for group in manifest["hooks"]["SessionStart"]
            for hook in group["hooks"]
        }
        if len(sys.argv) != 2 or sys.argv[1] not in allowed:
            raise ValueError(
                "expected one manifest-declared SessionStart hook filename"
            )
        result = subprocess.run(
            ["bash", str(bundled_path("hooks/" + sys.argv[1]))],
            input=event_text,
            text=True,
            capture_output=True,
            timeout=HOOK_TIMEOUT_SECONDS,
            env={**os.environ, "TESSL_PLUGIN_DIR": str(ROOT)},
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
            if not isinstance(output, dict) or not isinstance(
                output.get("additionalContext"), str
            ):
                raise ValueError(
                    "SessionStart hook did not return additionalContext text"
                )
            emit_context(output["additionalContext"])
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
