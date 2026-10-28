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

from .errors import HerdrError, StateError
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
            or not isinstance(directory, str) or not Path(directory).is_absolute() or root is not None and (Path(directory).resolve().parent != (root / "sessions").resolve() or not Path(directory).name.isdecimal())
            or proof.get("model") != tier.get("model") or proof.get("effort") != tier.get("effort")):
        return False
    return argv == ["pi"] + PI_PROFILE + launch_flags("pi", tier) + ["--session-dir", directory]


# Pinned to the pi build whose runtime was captured (tests/fixtures/pi, ADR 0014).
# Renovate does not track this pin: renew it by hand when a pi release
# changes the captured CLI, re-capturing tests/fixtures/pi before moving it,
# and re-check at each coding-policy minor release
# (rules/dependency-management.md Freshness).
PI_VERSION = "1.4.0"
PI_PACKAGE = "@earendil-works/pi-coding-agent"


def require_pi(agent):
    """Report missing or unsupported pi with the operator-run install command."""
    try:
        result = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=5, check=False)
    except FileNotFoundError:
        found = "not installed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        found = "not runnable ({})".format(exc)
    else:
        found = result.stdout.strip() if result.returncode == 0 else "unreadable (pi --version exit {})".format(result.returncode)
    if found != PI_VERSION:
        raise HerdrError("Cannot use pi for {!r} (pi_version_unsupported): found {}, need {}. Install it yourself with `npm install -g {}@{}`, then re-run measurement. No unmetered capacity was assigned.".format(agent.name, found, PI_VERSION, PI_PACKAGE, PI_VERSION), {})


def local_provider(agent, tier):
    """Resolve model before provider URL, refusing dynamic/auth URL overrides.

    Pi's captured ModelRegistry uses definition.baseUrl ?? provider.baseUrl.
    This profile supports only the evidenced static Ollama config; arbitrary
    auth integrations, shell values and environment URL expansion fail closed.
    """
    root = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent")))
    try:
        require_pi(agent)
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
    return {"endpoint": endpoint, "version": PI_VERSION}


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
    if not isinstance(proof, dict) or not validate_proof(proof, tier, seat_dir(agent)):
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
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    return _conversation_rows(rows, prompt, tier)


def _conversation_rows(rows, prompt, tier=None):
    if not rows or any(not isinstance(row, dict) for row in rows):
        return None
    header = rows[0]
    if header.get("type") != "session" or header.get("version") != 3 or not isinstance(header.get("id"), str):
        return None
    previous, users, final, seen = None, 0, None, set()
    for row in rows[1:]:
        if not isinstance(row.get("type"), str):
            return None
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
            if message.get("stopReason") == "stop" and all(isinstance(block.get("type"), str) and block["type"] in {"thinking", "text"} for block in blocks):
                final = text
        elif role != "toolResult" or users != 1:
            return None
    return (header["id"], final) if users == 1 else None


def delivery_evidence(directory, source, body):
    """Owner import of pi's native stream; readers consume versioned rows.

    Missing evidence is a first import. Unknown/corrupt owner artifacts are
    preserved, never replaced. Each probe imports fresh source bytes rather
    than granting delivery from a saved result. Schema contract: state-schema.md.
    """
    from .state import save_state
    path = Path(directory) / "delivery.json"
    missing = False
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = None
        missing = True
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not missing and not _valid_delivery_evidence(previous):
        return None
    try:
        rows = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not rows or any(not isinstance(row, dict) or "schema_version" in row for row in rows):
        return None
    records = []
    for row in rows:
        record = {"schema_version": 1, **{key: row[key] for key in ("type", "version", "id", "parentId") if key in row}}
        if isinstance(row.get("message"), dict):
            message = row["message"]
            content = message.get("content")
            if isinstance(content, list):
                if any(not isinstance(block, dict) for block in content):
                    return None
                content = [{key: block[key] for key in ("type", "text") if key in block} for block in content]
            normalized_message = {key: message.get(key) for key in ("role", "provider", "model", "thinkingLevel", "stopReason")}
            normalized_message["content"] = content
            record["message"] = normalized_message
        records.append(record)
    value = {"schema_version": 1, "source_path": str(source), "source_sha256": hashlib.sha256(body).hexdigest(), "records": records}
    if not _valid_delivery_evidence(value):
        return None
    try:
        save_state(path, value)
        # Consume the durable artifact, never the pre-write in-memory rows.
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (StateError, OSError, UnicodeDecodeError, ValueError):
        return None
    return saved["records"] if _valid_delivery_evidence(saved) and saved == value else None


def _valid_delivery_evidence(value):
    return (isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 1
            and set(value) == {"schema_version", "source_path", "source_sha256", "records"}
            and isinstance(value["source_path"], str) and Path(value["source_path"]).is_absolute()
            and isinstance(value["source_sha256"], str) and len(value["source_sha256"]) == 64
            and all(char in "0123456789abcdef" for char in value["source_sha256"])
            and isinstance(value["records"], list) and bool(value["records"])
            and all(_valid_delivery_record(row, index) for index, row in enumerate(value["records"])))


def _valid_delivery_record(row, index):
    if not isinstance(row, dict) or type(row.get("schema_version")) is not int or row["schema_version"] != 1 or not isinstance(row.get("id"), str) or not row["id"]:
        return False
    if index == 0:
        return set(row) == {"schema_version", "type", "version", "id"} and row["type"] == "session" and type(row["version"]) is int and row["version"] == 3
    if not isinstance(row.get("type"), str) or row["type"] not in {"model_change", "thinking_level_change", "message"}:
        return False
    if "parentId" not in row or row["parentId"] is not None and not isinstance(row["parentId"], str):
        return False
    if row["type"] != "message":
        return set(row) == {"schema_version", "type", "id", "parentId"}
    message = row.get("message")
    if set(row) != {"schema_version", "type", "id", "parentId", "message"} or not isinstance(message, dict) or set(message) != {"role", "content", "provider", "model", "thinkingLevel", "stopReason"}:
        return False
    if not isinstance(message["role"], str) or message["role"] not in {"system", "user", "assistant", "toolResult"}:
        return False
    if any(message[key] is not None and not isinstance(message[key], str) for key in ("provider", "model", "thinkingLevel", "stopReason")):
        return False
    content = message["content"]
    if message["role"] == "system" and isinstance(content, str):
        return True
    return (isinstance(content, list) and all(isinstance(block, dict) and set(block).issubset({"type", "text"})
            and isinstance(block.get("type"), str) and ("text" not in block or isinstance(block["text"], str)) for block in content))


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
        records = delivery_evidence(proof["session_dir"], path, body)
        if records is None or _conversation_rows(records, prompt, proof) != (session, final):
            return failure
        if final != "REPORT: " + report:
            return failure
        verify_running(client, agent, pane, proof)
        if read_launch(agent) != receipt or path.read_bytes() != body or client.agent_get(agent.name) != before or not Path(report).is_file():
            return failure
        return {"found": True, "basis": "pi_native_final_source", "native_session": {"agent": "pi", "kind": "id", "value": session},
                "source": {"path": str(path), "sha256": hashlib.sha256(body).hexdigest()}}
    except (HerdrError, OSError, UnicodeDecodeError):
        return failure
