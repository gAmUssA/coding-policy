"""Bounded host activation checks; an unavailable probe never means eligible."""

import re
import subprocess

from .errors import ConfigError, HerdrError

GIB = 1073741824
CHIP = re.compile(r"Apple M([1-9][0-9]*)(?: (?:Pro|Max|Ultra))?\Z")
HOST_KEYS = {"min_apple_m_generation", "min_memory_gib"}


def parse_requirements(value, source, *, pi=False):
    if value is None and not pi:
        return None
    if not isinstance(value, dict) or set(value) != HOST_KEYS:
        raise ConfigError("{} must contain exactly min_apple_m_generation and min_memory_gib; use 5 and 64 for the Flint seat.".format(source), {})
    for key in HOST_KEYS:
        if type(value[key]) is not int or value[key] <= 0:
            raise ConfigError("{}.{} must be a positive integer{}. Use 64 for the Flint seat; booleans and unit strings are not accepted.".format(source, key, " in GiB" if key == "min_memory_gib" else ""), {})
    if pi and value["min_apple_m_generation"] < 5:
        raise ConfigError("{} needs min_apple_m_generation at least 5 for pi; retain the host activation floor.".format(source), {})
    return dict(value)


def read_host():
    observed = {}
    for key in ("machdep.cpu.brand_string", "hw.memsize"):
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"error": "sysctl {} failed: {}".format(key, exc)}
        if result.returncode:
            return {"error": "sysctl {} failed: {}".format(key, result.stderr.strip())}
        observed[key] = result.stdout.strip()
    return observed


def evaluate_host(agent, observed):
    requirements = getattr(agent, "host_requirements", None)
    if requirements is None:
        return None
    chip, memory = observed.get("machdep.cpu.brand_string", ""), observed.get("hw.memsize", "")
    match = CHIP.fullmatch(chip) if isinstance(chip, str) else None
    valid_memory = isinstance(memory, str) and re.fullmatch(r"[1-9][0-9]*", memory)
    generation, size = int(match[1]) if match else None, int(memory) if valid_memory else None
    error = observed.get("error")
    if error or generation is None or size is None:
        reason, eligible = "host_probe_failed", None
        message = ("Cannot check host requirements for {!r} (host_probe_failed): {}. This seat is disabled for this run. "
                   "Run 'sysctl -n machdep.cpu.brand_string hw.memsize', resolve the reported error, then re-run teamlead measure.").format(agent.name, error or "unrecognized chip or memory response")
    else:
        eligible = generation >= requirements["min_apple_m_generation"] and size >= requirements["min_memory_gib"] * GIB
        reason = "host_eligible" if eligible else "host_ineligible"
        message = ("Host eligible for {!r}.".format(agent.name) if eligible else
                   "Skipping {!r} (host_ineligible): requires Apple M{} or newer and {} GiB RAM; this host reports {} and {:g} GiB. Other workers remain available. Run on an eligible host to enable this seat.".format(agent.name, requirements["min_apple_m_generation"], requirements["min_memory_gib"], chip, size / GIB))
    return {"schema_version": 1, "eligible": eligible, "reason": reason, "message": message,
            "observed": {"chip": chip, "apple_m_generation": generation, "memory_bytes": size}, "requirements": requirements,
            "details": {"error": error, "unmet": [key for key, actual in (("min_apple_m_generation", generation), ("min_memory_gib", size / GIB if size else None)) if actual is not None and actual < requirements[key]]}}


def check_hosts(agents):
    observed = read_host() if any(getattr(agent, "host_requirements", None) is not None for agent in agents) else {}
    return {agent.name: evaluate_host(agent, observed) for agent in agents}


def require_host_eligible(agent):
    observation = check_hosts([agent])[agent.name]
    if observation is not None and observation["eligible"] is not True:
        message = ("Refusing {!r} (host_ineligible): this plan selected pi on another host. Re-run teamlead measure and teamlead plan on this machine. No worker was stopped, started, cleared or briefed.".format(agent.name)
                   if agent.kind == "pi" and observation["reason"] == "host_ineligible" else observation["message"])
        raise HerdrError(message, observation)
    return observation
