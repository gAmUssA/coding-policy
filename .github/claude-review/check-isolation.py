#!/usr/bin/env python3
"""Verify the pinned real CLI sends no project/user instructions or hooks.

Usage: check-isolation.py
Out:   {"isolation": "pass", "requests": N, "tools": [...]} on stdout.
Exit:  0 isolated; 1 with a stderr diagnostic when the CLI sent no request,
       loaded a planted instruction file, ran a planted hook, or exposed a
       tool outside the read-only set.

Runs review.py's own claude_command() against a loopback server that captures
the initial model request and answers a deliberate 401, with a dummy API
credential. No model call, real credential or external service is needed.
The review workflow runs it after installing the pinned CLI and before the
review; a CLI bump that loosens isolation fails here, before the subscription
token is used.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


CAPTURE_TIMEOUT_SECONDS = 30
STOP_TIMEOUT_SECONDS = 5
SENTINEL = "FORBIDDEN_AGENT_CONTEXT_ISOLATION_SENTINEL"
REQUIRED_TOOLS = {"Read", "Glob", "Grep"}
# Tools the CLI injects for structured output and session control; none reads
# outside the checkout or executes anything.
PERMITTED_TOOLS = REQUIRED_TOOLS | {"StructuredOutput", "EndConversation", "DeferredToolPlaceholder"}


def load_review_module():
    spec = importlib.util.spec_from_file_location("review", Path(__file__).with_name("review.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load review.py beside this probe")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    requests: list[dict] = []
    captured = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.split("?")[0] == "/v1/messages":
                requests.append(payload)
                captured.set()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"authentication_error","message":"isolation probe complete"}}')

        def log_message(self, *_args):
            pass  # Loopback probe traffic carries no operational diagnostics.

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        module = load_review_module()
        with tempfile.TemporaryDirectory(prefix="claude-isolation-") as temp:
            root = Path(temp)
            source, config = root / "source", root / "config"
            source.mkdir()
            config.mkdir()
            for path in (root / "CLAUDE.md", source / "CLAUDE.md", source / "AGENTS.md", config / "CLAUDE.md"):
                path.write_text(SENTINEL)
            hook_marker = root / "hook-ran"
            settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f"touch {hook_marker}"}]}]}}
            (config / "settings.json").write_text(json.dumps(settings))
            env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
            env.update(CLAUDE_CONFIG_DIR=str(config),
                       ANTHROPIC_API_KEY="dummy-isolation-probe-key",
                       ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
                       CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            prompt = root / "prompt.txt"
            prompt.write_text("Review this diff: +return total / len(items)")
            with prompt.open() as stream, subprocess.Popen(
                    module.claude_command(), cwd=source, env=env, stdin=stream,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
                try:
                    if not captured.wait(CAPTURE_TIMEOUT_SECONDS):
                        raise RuntimeError("CLI sent no model request to the loopback server")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=STOP_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if hook_marker.exists() or SENTINEL in json.dumps(requests):
                raise RuntimeError("CLI loaded a planted instruction file or ran a planted hook")
            tools = {tool["name"] for request in requests for tool in request.get("tools", [])}
            if not REQUIRED_TOOLS.issubset(tools) or tools - PERMITTED_TOOLS:
                raise RuntimeError("CLI exposed an unexpected tool set: " + ", ".join(sorted(tools)))
        print(json.dumps({"isolation": "pass", "requests": len(requests), "tools": sorted(tools)}))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("error: Claude isolation probe failed: " + str(exc)
              + "; do not run the review until the pinned CLI isolates again", file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    sys.exit(main())
