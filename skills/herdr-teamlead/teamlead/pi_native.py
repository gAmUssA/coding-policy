"""Pi 1.4.0 fresh-launch receipt, local profile and linear JSONL v3 delivery.

Owner of pi/<seat hash>/launch.json (schema 1) under the teamlead state dir.
The receipt records requested argv plus observed PID/pane/terminal, never ps
arguments: pi changes process.title. Dispatches never adopt an unrecorded pi.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .errors import HerdrError
from .tiers import EFFORTS, PI_MODEL, PI_PROFILE, launch_flags


def seat_dir(agent):
    from .state import default_state_path
    root = Path(agent.state_dir) if agent.state_dir is not None else default_state_path().parent
    return root.resolve() / "pi" / hashlib.sha256(agent.name.encode()).hexdigest()[:24]


def profile_args(agent, tier, directory):
    return PI_PROFILE + launch_flags("pi", tier) + ["--session-dir", str(directory)]


def new_session_dir(agent):
    root = seat_dir(agent) / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = root / str(number)
        try:
            path.mkdir()
            return path
        except FileExistsError:
            number += 1


def validate_proof(proof, tier, root=None):
    """Archived and live evidence share the exact profile contract."""
    if not isinstance(proof, dict) or not isinstance(tier, dict) or not isinstance(tier.get("model"), str) or not PI_MODEL.fullmatch(tier["model"]) or not isinstance(tier.get("effort"), str) or tier["effort"] not in EFFORTS["pi"]:
        return False
    argv = proof.get("argv")
    directory = proof.get("session_dir")
    if (proof.get("source") != "launch_argv" or type(proof.get("pid")) is not int or proof["pid"] <= 0
            or not all(isinstance(proof.get(key), str) and proof[key] for key in ("pane_id", "terminal_id", "session_dir"))
            or not Path(directory).is_absolute() or root is not None and (Path(directory).resolve().parent != (root / "sessions").resolve() or not Path(directory).name.isdecimal())
            or proof.get("model") != tier.get("model") or proof.get("effort") != tier.get("effort")):
        return False
    return argv == ["pi"] + PI_PROFILE + launch_flags("pi", tier) + ["--session-dir", directory]


def local_provider(agent, tier):
    """Resolve model before provider URL, refusing dynamic/auth URL overrides.

    Pi's captured ModelRegistry uses definition.baseUrl ?? provider.baseUrl.
    This profile supports only the evidenced static Ollama config; arbitrary
    auth integrations, shell values and environment URL expansion fail closed.
    """
    root = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent")))
    try:
        version = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=5, check=False)
        if version.returncode or version.stdout.strip() != "1.4.0":
            raise ValueError("installed pi profile is not the evidenced 1.4.0")
        config = json.loads((root / "models.json").read_text(encoding="utf-8"))
        auth_path = root / "auth.json"
        auth = json.loads(auth_path.read_text(encoding="utf-8")) if auth_path.exists() else {}
        if not isinstance(config, dict) or not isinstance(config.get("providers"), dict) or not isinstance(config["providers"].get("ollama"), dict):
            raise ValueError("missing Ollama provider object")
        provider = config["providers"]["ollama"]
        if not isinstance(provider.get("models"), list) or any(not isinstance(row, dict) for row in provider["models"]):
            raise ValueError("missing model definitions")
        models = [row for row in provider["models"] if row.get("id") == tier["model"].removeprefix("ollama/")]
        if len(models) != 1 or "ollama" in auth or provider.get("api") != "openai-completions":
            raise ValueError("ambiguous model, auth override or unsupported API")
        endpoint = models[0].get("baseUrl", provider.get("baseUrl"))
        url = urlsplit(endpoint)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.port != 11434 or url.path.rstrip("/") != "/v1" or url.username or url.password or url.query or url.fragment
                or models[0].get("api", provider["api"]) != "openai-completions"):
            raise ValueError("endpoint is not the commissioned loopback URL")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise HerdrError("Cannot establish local inference for {!r} (pi_local_provider_unproven). Configure its Ollama provider to the documented loopback endpoint, verify the exact model, then re-run measurement. No unmetered capacity was assigned.".format(agent.name), {"reason": str(exc)}) from None
    return {"endpoint": endpoint, "version": "1.4.0"}


def foreground(client, pane):
    info = client.pane_process_info(pane)
    rows = info.get("foreground_processes")
    if (info.get("pane_id") != pane or not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict) or type(rows[0].get("pid")) is not int or rows[0]["pid"] <= 0):
        raise HerdrError("pi_permission_unproven: require one positive foreground PID in the launched pane; inspect its terminal before dispatch.", {})
    return rows[0]


def save_launch(client, agent, pane, tier, result):
    info = result.get("agent") if isinstance(result, dict) else None
    argv = result.get("argv") if isinstance(result, dict) else None
    if not isinstance(info, dict) or not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv):
        raise HerdrError("Cannot save pi launch receipt (pi_permission_unproven): Herdr start must return an agent object and non-empty string argv. Preserve the live pane, inspect Herdr's start response and start a fresh recorded worker before dispatch; no brief was sent.", {})
    pid = foreground(client, pane)["pid"]
    proof = {"source": "launch_argv", "model": tier["model"], "effort": tier["effort"], "argv": argv,
             "pid": pid, "pane_id": pane, "terminal_id": info.get("terminal_id"), "session_dir": argv[-1]}
    if not validate_proof(proof, tier, seat_dir(agent)):
        raise HerdrError("Cannot verify pi execution mode (pi_permission_unproven). Inspect this installed pi version and start a fresh worker with the documented local profile; no brief was sent.", {})
    fresh = client.agent_get(agent.name)
    if any(fresh.get(key) != info.get(key) for key in ("name", "agent", "pane_id", "terminal_id")) or foreground(client, pane)["pid"] != pid:
        raise HerdrError("pi_permission_unproven: worker changed during launch; preserve the receipt and inspect the pane.", {})
    path = seat_dir(agent) / "launch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "agent": agent.name, "proof": proof, "dispatch_prompt": None}
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise HerdrError("Cannot save pi launch receipt: {}. Preserve the live pane; do not dispatch until its receipt is durable.".format(exc), {}) from None
    return proof


def read_launch(agent):
    try:
        value = json.loads((seat_dir(agent) / "launch.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HerdrError("pi_permission_unproven: no readable fresh-launch receipt for {!r}. Start it with teamlead start-local before dispatch.".format(agent.name), {"reason": str(exc)}) from None
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or value.get("agent") != agent.name:
        raise HerdrError("pi_permission_unproven: restore the owner-written launch receipt or start a fresh seat.", {})
    return value


def verify_running(client, agent, pane, tier):
    receipt = read_launch(agent)
    proof = receipt.get("proof")
    if not validate_proof(proof, tier, seat_dir(agent)):
        raise HerdrError("pi_permission_unproven: receipt profile differs from the requested tier; start a fresh seat.", {})
    info = client.agent_get(agent.name)
    terminal = client.pane_get(pane)
    if (terminal.get("pane_id") != pane or terminal.get("terminal_id") != proof["terminal_id"]
            or info.get("agent") != "pi" or info.get("name") != agent.name or info.get("pane_id") != pane
            or proof["pane_id"] != pane or info.get("terminal_id") != proof["terminal_id"]
            or foreground(client, pane)["pid"] != proof["pid"]):
        raise HerdrError("pi_permission_unproven: PID, pane or terminal continuity changed; no brief was sent. Start a fresh recorded seat.", {})
    return proof


def bind_prompt(agent, prompt):
    receipt = read_launch(agent)
    receipt["dispatch_prompt"] = prompt
    path = seat_dir(agent) / "launch.json"
    try:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise HerdrError("Cannot bind pi dispatch source: {}. No prompt was sent; restore writable state before retrying.".format(exc), {}) from None


def conversation(body, prompt, tier=None):
    """Only fresh, linear v3 sessions with this sole user turn can bind."""
    try:
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError):
        return None
    if not rows or any(not isinstance(row, dict) for row in rows):
        return None
    header = rows[0]
    if header.get("type") != "session" or header.get("version") != 3 or not isinstance(header.get("id"), str):
        return None
    previous, users, final, seen = None, 0, None, set()
    for row in rows[1:]:
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen or row.get("parentId") != previous:
            return None
        seen.add(identifier)
        previous = identifier
        if row.get("type") in {"model_change", "thinking_level_change"} and not users:
            continue
        if row.get("type") != "message" or not isinstance(row.get("message"), dict):
            return None
        message = row["message"]
        role, blocks = message.get("role"), message.get("content")
        if role == "system" and users == 0:
            continue
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            return None
        text = "".join(block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str))
        final = None
        if role == "user":
            users += 1
            if users != 1 or text != prompt or any(block.get("type") != "text" for block in blocks):
                return None
        elif role == "assistant" and users == 1:
            if tier is not None and (message.get("provider") != "ollama" or message.get("model") != tier["model"].removeprefix("ollama/") or message.get("thinkingLevel") != tier["effort"]):
                return None
            if message.get("stopReason") == "stop" and all(block.get("type") in {"thinking", "text"} for block in blocks):
                final = text
        elif role != "toolResult" or users != 1:
            return None
    return (header["id"], final) if users == 1 else None


def probe(client, agent, pane, report):
    """R3 source-only acceptance, with receipt/process/source stability checks."""
    failure = {"found": False, "reason": "pi_marker_unconfirmed", "message": "Pi report marker is unconfirmed (pi_marker_unconfirmed). Preserve the report and pane evidence; inspect the exact final marker and native session before recording delivery. Do not rerun the task solely because delivery is unconfirmed."}
    try:
        receipt = read_launch(agent)
        proof, prompt = receipt.get("proof"), receipt.get("dispatch_prompt")
        if not isinstance(proof, dict) or not isinstance(prompt, str) or not Path(report).is_absolute() or not Path(report).is_file():
            return failure
        verify_running(client, agent, pane, proof)
        before = client.agent_get(agent.name)
        if before.get("agent_status") not in {"idle", "done"}:
            return failure
        matches = []
        for path in Path(proof["session_dir"]).glob("*.jsonl"):
            body = path.read_bytes()
            parsed = conversation(body.decode("utf-8"), prompt, proof)
            if parsed is not None:
                matches.append((path, body, parsed))
        if len(matches) != 1:
            return failure
        path, body, (session, final) = matches[0]
        if final != "REPORT: " + report:
            return failure
        verify_running(client, agent, pane, proof)
        if read_launch(agent) != receipt or path.read_bytes() != body or client.agent_get(agent.name) != before or not Path(report).is_file():
            return failure
        return {"found": True, "basis": "pi_native_final_source", "native_session": {"agent": "pi", "kind": "id", "value": session},
                "source": {"path": str(path), "sha256": hashlib.sha256(body).hexdigest()}}
    except (HerdrError, OSError, UnicodeDecodeError):
        return failure
