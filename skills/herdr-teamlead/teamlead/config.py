"""Agent configuration: which agents exist and how each reports its usage.

The config is operator-owned. teamlead never writes it -- the operator copies
`config.example.json` into place, so a machine that has not been set up fails
with an actionable message instead of silently inventing agents.
"""

import json
import math
import os
from pathlib import Path

from .errors import ConfigError
from .herdr import SLASH_DELIVERIES, SLASH_DELIVERY_PASTE
from .tiers import parse_launch_args, parse_tiers

CONFIG_SCHEMA_VERSION = 2
READABLE_CONFIG_VERSIONS = frozenset({1, 2})

REQUIRED_AGENT_FIELDS = ("name", "kind", "usage_prompt", "usage_marker", "usage_read_source", "clear_prompt")

VALID_READ_SOURCES = frozenset({"visible", "recent", "recent-unwrapped", "detection"})

#: Optional per-agent fields that default to an empty list of strings.
OPTIONAL_LIST_FIELDS = (
    "close_keys",
    "idle_markers",
    "working_markers",
    "dialog_next_tab_keys",
    "recover_keys",
    "composer_placeholders",
)

#: Default when an agent does not name one. `paste` is the older path and the
#: one that works for claude and codex; grok needs `type`.
DEFAULT_SLASH_DELIVERY = SLASH_DELIVERY_PASTE

#: Dim composer text is never somebody's typing: it is a placeholder or a
#: ghost-text suggestion. On for every kind, because reading dim hint text as
#: occupied is what killed an idle Codex.
DEFAULT_COMPOSER_IGNORE_DIM = True

#: Enters sent with a typed slash command. Codex opens an autocomplete popup
#: on `/` where the first Enter only accepts the completion, so it needs 2.
DEFAULT_SLASH_ENTER_COUNT = 1
MAX_SLASH_ENTER_COUNT = 5


class Agent:
    """One configured agent. A plain value object, never mutated after load."""

    __slots__ = REQUIRED_AGENT_FIELDS + OPTIONAL_LIST_FIELDS + (
        "slash_delivery",
        "composer_glyph",
        "composer_ignore_dim",
        "slash_enter_count",
        "model_label",
        "window_group",
        "tiers",
        "launch_args",
    )

    def __init__(self, name, kind, usage_prompt, usage_marker, usage_read_source, clear_prompt, close_keys=(), idle_markers=(), working_markers=(), dialog_next_tab_keys=(), recover_keys=(), composer_placeholders=(), slash_delivery=DEFAULT_SLASH_DELIVERY, composer_glyph="", composer_ignore_dim=DEFAULT_COMPOSER_IGNORE_DIM, slash_enter_count=DEFAULT_SLASH_ENTER_COUNT, model_label="", window_group="", tiers=None, launch_args=()):
        self.name = name
        self.kind = kind
        self.usage_prompt = usage_prompt
        self.usage_marker = usage_marker
        self.usage_read_source = usage_read_source
        self.clear_prompt = clear_prompt
        self.close_keys = tuple(close_keys)
        # Footer signatures the idle probe matches when herdr's title-derived
        # state says `working`. See teamlead/probe.py.
        self.idle_markers = tuple(idle_markers)
        self.working_markers = tuple(working_markers)
        # Keys that move a usage dialog to its next tab, when the report is
        # behind one teamlead did not land on.
        self.dialog_next_tab_keys = tuple(dialog_next_tab_keys)
        # Keys that clear a stuck composer. Sent EXACTLY once, never in a
        # loop, and only for text teamlead can account for. Codex ships with
        # NONE: ctrl+c on an idle Codex exits the process.
        self.recover_keys = tuple(recover_keys)
        # Hint text a runtime draws in an EMPTY composer, matched exactly
        # after trimming. Codex draws "Ask Codex to do anything"; reading that
        # as typed text is what sent ctrl+c into an idle Codex and killed it.
        self.composer_placeholders = tuple(composer_placeholders)
        # Shown on the worker's pane after a dispatch, so the sidebar says
        # which model is doing the work. Cosmetic and optional: empty means the
        # label carries the role alone.
        self.model_label = model_label
        self.window_group = window_group
        self.tiers = tiers or {}
        self.launch_args = tuple(launch_args)
        # "paste" (agent prompt) or "type" (pane send-text plus Enter).
        self.slash_delivery = slash_delivery
        # The prompt glyph that marks the composer row, so teamlead can see
        # whether a slash command was consumed. Empty disables the check.
        self.composer_glyph = composer_glyph
        # Treat dim / grey composer text as empty. Claude Code pre-fills a
        # ghost-text suggestion nobody typed; a runtime without ghost text
        # leaves this off so every character still counts.
        self.composer_ignore_dim = composer_ignore_dim
        # How many Enters a typed slash command needs to submit. Codex's
        # autocomplete popup swallows the first one.
        self.slash_enter_count = slash_enter_count

    def __repr__(self):
        return "Agent(name={!r}, kind={!r})".format(self.name, self.kind)

    def __eq__(self, other):
        if not isinstance(other, Agent):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def as_dict(self):
        record = {field: getattr(self, field) for field in REQUIRED_AGENT_FIELDS}
        for field in OPTIONAL_LIST_FIELDS:
            record[field] = list(getattr(self, field))
        record["slash_delivery"] = self.slash_delivery
        record["composer_glyph"] = self.composer_glyph
        record["composer_ignore_dim"] = self.composer_ignore_dim
        record["slash_enter_count"] = self.slash_enter_count
        if self.tiers:
            record["tiers"] = self.tiers
        if self.launch_args:
            record["launch_args"] = list(self.launch_args)
        return record


def default_config_path():
    """`$XDG_CONFIG_HOME/teamlead/config.json`, falling back to `~/.config`."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "teamlead" / "config.json"


def _missing_config_error(path):
    return ConfigError(
        "Agent config not found at {} - copy the shipped example into place "
        "with `mkdir -p {} && cp config.example.json {}`, then edit the agent "
        "names to match `herdr agent list`.".format(path, path.parent, path),
        {"path": str(path)},
    )


def parse_config(payload, source="<memory>"):
    """Validate an already-decoded config mapping and return a list of Agents.

    Pure apart from the `source` string used in error messages, so tests build
    configs inline instead of writing files.
    """
    if not isinstance(payload, dict):
        raise ConfigError(
            "Config at {} must be a JSON object with `schema_version` and "
            "`agents` - see config.example.json.".format(source),
            {"source": source},
        )

    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in READABLE_CONFIG_VERSIONS:
        raise ConfigError(
            "Config at {} has schema_version {!r}; this build reads versions {}. "
            "Update the file against config.example.json.".format(
                source, version, ", ".join(str(item) for item in sorted(READABLE_CONFIG_VERSIONS))
            ),
            {"source": source, "found": version, "expected": CONFIG_SCHEMA_VERSION},
        )

    raw_agents = payload.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise ConfigError(
            "Config at {} has no `agents` array - add at least one agent entry "
            "shaped like the ones in config.example.json.".format(source),
            {"source": source},
        )

    agents = []
    seen = set()
    for index, entry in enumerate(raw_agents):
        if not isinstance(entry, dict):
            raise ConfigError(
                "Config at {}: agents[{}] is not a JSON object - each agent is "
                "an object with the fields {}.".format(
                    source, index, ", ".join(REQUIRED_AGENT_FIELDS)
                ),
                {"source": source, "index": index},
            )
        missing = [field for field in REQUIRED_AGENT_FIELDS if not entry.get(field)]
        if missing:
            raise ConfigError(
                "Config at {}: agents[{}] is missing {} - add {} to that entry.".format(
                    source, index, ", ".join(missing), " and ".join(missing)
                ),
                {"source": source, "index": index, "missing": missing},
            )
        read_source = entry["usage_read_source"]
        if read_source not in VALID_READ_SOURCES:
            raise ConfigError(
                "Config at {}: agents[{}] has usage_read_source {!r}; herdr "
                "accepts {}.".format(
                    source, index, read_source, ", ".join(sorted(VALID_READ_SOURCES))
                ),
                {"source": source, "index": index, "usage_read_source": read_source},
            )
        name = entry["name"]
        if "tiers" in entry and version < 2:
            raise ConfigError("Tier tables need config schema_version 2; upgrade the operator-owned config.", {"source": source})
        if name in seen:
            raise ConfigError(
                "Config at {}: agent name {!r} appears twice - herdr agent "
                "names are unique among live agents.".format(source, name),
                {"source": source, "name": name},
            )
        seen.add(name)

        lists = {}
        for field in OPTIONAL_LIST_FIELDS:
            value = entry.get(field, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ConfigError(
                    "Config at {}: agents[{}].{} must be an array of strings - "
                    "close_keys holds herdr key names such as [\"esc\"], "
                    "idle_markers and working_markers hold literal footer text "
                    "the idle probe looks for.".format(source, index, field),
                    {"source": source, "index": index, "field": field},
                )
            lists[field] = value

        delivery = entry.get("slash_delivery", DEFAULT_SLASH_DELIVERY)
        if delivery not in SLASH_DELIVERIES:
            raise ConfigError(
                "Config at {}: agents[{}].slash_delivery is {!r}; use {}. "
                "\"paste\" sends the command with `herdr agent prompt`; "
                "\"type\" types it into the pane and presses Enter. Which one "
                "an agent needs depends on its TUI.".format(
                    source, index, delivery, " or ".join(repr(v) for v in SLASH_DELIVERIES)
                ),
                {"source": source, "index": index, "slash_delivery": delivery},
            )
        glyph = entry.get("composer_glyph", "")
        window_group = entry.get("window_group", "")
        if not isinstance(window_group, str):
            raise ConfigError(
                "Config at {}: agents[{}].window_group is {!r}; use a string "
                "naming the usage window this agent shares with others, or "
                "omit it when the agent has a window of its own.".format(
                    source, index, window_group
                ),
                {"source": source, "index": index, "window_group": window_group},
            )

        model_label = entry.get("model_label", "")
        if not isinstance(model_label, str):
            raise ConfigError(
                "Config at {}: agents[{}].model_label is {!r}; use a string "
                "such as \"gpt-5.6\", or leave it out.".format(
                    source, index, model_label
                ),
                {"source": source, "index": index, "model_label": model_label},
            )
        if not isinstance(glyph, str):
            raise ConfigError(
                "Config at {}: agents[{}].composer_glyph must be a string - the "
                "prompt glyph that starts the composer row, e.g. \"\u203a \" "
                "for codex. Omit it to skip the consumed-command check.".format(
                    source, index
                ),
                {"source": source, "index": index},
            )
        ignore_dim = entry.get("composer_ignore_dim", DEFAULT_COMPOSER_IGNORE_DIM)
        if not isinstance(ignore_dim, bool):
            raise ConfigError(
                "Config at {}: agents[{}].composer_ignore_dim must be true or "
                "false - true (the default) treats dim composer text as empty, "
                "which is what Claude Code's ghost-text suggestion and Codex's "
                "\"Ask Codex to do anything\" placeholder both need.".format(
                    source, index
                ),
                {"source": source, "index": index},
            )
        enters = entry.get("slash_enter_count", DEFAULT_SLASH_ENTER_COUNT)
        if (
            isinstance(enters, bool)
            or not isinstance(enters, int)
            or not 1 <= enters <= MAX_SLASH_ENTER_COUNT
        ):
            raise ConfigError(
                "Config at {}: agents[{}].slash_enter_count is {!r}; use an "
                "integer from 1 to {}. Codex needs 2 because its autocomplete "
                "popup swallows the first Enter.".format(
                    source, index, enters, MAX_SLASH_ENTER_COUNT
                ),
                {"source": source, "index": index, "slash_enter_count": enters},
            )
        agents.append(
            Agent(
                name=name,
                kind=entry["kind"],
                usage_prompt=entry["usage_prompt"],
                usage_marker=entry["usage_marker"],
                usage_read_source=read_source,
                clear_prompt=entry["clear_prompt"],
                slash_delivery=delivery,
                composer_glyph=glyph,
                composer_ignore_dim=ignore_dim,
                model_label=model_label,
                window_group=window_group,
                tiers=parse_tiers(entry.get("tiers"), entry["kind"]),
                launch_args=parse_launch_args(entry.get("launch_args", []), entry["kind"]),
                slash_enter_count=enters,
                **lists
            )
        )
    return agents


def _read_config(path):
    """Decode the config JSON at `path`, or raise an actionable ConfigError.

    Split out from `load_config` so the two readers of this one file -- the
    agent list and the `role_costs` weights -- share every read and decode
    error message rather than growing a second, drifting copy.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _missing_config_error(path) from None
    except PermissionError as exc:
        raise ConfigError(
            "Agent config at {} is not readable: {} - fix its permissions "
            "with `chmod u+r {}`.".format(path, exc.strerror, path),
            {"path": str(path)},
        ) from None
    except IsADirectoryError:
        raise ConfigError(
            "Agent config path {} is a directory - point --config at the "
            "config.json file itself.".format(path),
            {"path": str(path)},
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("Cannot read config {}: {}. Supply a readable UTF-8 JSON file with --config.".format(path, exc),
                          {"path": str(path)}) from None

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "Agent config at {} is not valid JSON ({} at line {} column {}) - "
            "validate it with `python3 -m json.tool {}`.".format(
                path, exc.msg, exc.lineno, exc.colno, path
            ),
            {"path": str(path)},
        ) from None


def load_config(path):
    """Read and validate the config file at `path`."""
    path = Path(path)
    return parse_config(_read_config(path), source=str(path))


#: Reasoning-effort levels `claude --effort` accepts. A tier may name no
#: effort at all: claude-haiku-4-5 takes no effort flag, and a model that
#: does not accept one must not be forced to carry a value here.
VALID_JUDGE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class Judge:
    """The pinned judge tier. A plain value object, never mutated after load."""

    __slots__ = ("agent", "model", "effort")

    def __init__(self, agent, model, effort=""):
        self.agent = agent
        self.model = model
        self.effort = effort


def parse_judge(payload, source="<memory>"):
    """Validate the optional top-level `judge` block, `None` when absent.

    The judge seat is pinned rather than ranked: the planner never chooses who
    judges, so the model lives in config where swapping the top tier is a
    one-line edit rather than a code change and a republish.
    """
    if not isinstance(payload, dict):
        raise ConfigError(
            "Config at {} must be a JSON object with `schema_version` and "
            "`agents` - see config.example.json.".format(source),
            {"source": source},
        )
    raw = payload.get("judge")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(
            "Config at {}: `judge` is a JSON {}, not an object naming the "
            "pinned seat - write it as {{\"agent\": \"judge\", \"model\": "
            "\"claude-fable-5-1\", \"effort\": \"max\"}}, or remove it.".format(
                source, type(raw).__name__
            ),
            {"source": source, "judge_type": type(raw).__name__},
        )
    for field in ("agent", "model"):
        value = raw.get(field)
        if not isinstance(value, str) or not value:
            raise ConfigError(
                "Config at {}: `judge.{}` is {!r}; name the {} as a non-empty "
                "string.".format(
                    source,
                    field,
                    value,
                    "agent that holds the seat" if field == "agent" else "model it is pinned to",
                ),
                {"source": source, "field": field, "value": value},
            )
    effort = raw.get("effort", "")
    if not isinstance(effort, str):
        raise ConfigError(
            "Config at {}: `judge.effort` is {!r}; use one of {}, or omit it "
            "for a model that accepts no effort flag.".format(
                source, effort, ", ".join(sorted(VALID_JUDGE_EFFORTS))
            ),
            {"source": source, "effort": effort},
        )
    if effort and effort not in VALID_JUDGE_EFFORTS:
        raise ConfigError(
            "Config at {}: `judge.effort` is {!r}; use one of {}, or omit it "
            "for a model that accepts no effort flag.".format(
                source, effort, ", ".join(sorted(VALID_JUDGE_EFFORTS))
            ),
            {"source": source, "effort": effort},
        )
    # Legacy banner_pattern is ignored: a screen cannot prove launch flags.
    return Judge(
        agent=raw["agent"],
        model=raw["model"],
        effort=effort,
    )


def load_judge(path):
    """The `judge` block from the config at `path`; None when there is none.

    Missing-config tolerance matches `load_role_costs`: `plan` contacts no
    agent and runs on a machine that was never set up, so no config means "no
    pinned judge", not a failed plan. A config that EXISTS and names a
    malformed `judge` block still fails loudly.
    """
    path = Path(path)
    if not path.exists():
        return None
    return parse_judge(_read_config(path), source=str(path))


def parse_role_costs(payload, source="<memory>"):
    """Validate the optional `role_costs` override map, `{}` when it is absent.

    Each value is what one round in that seat is expected to burn out of an
    agent's remaining headroom percentage; the planner merges the map over its
    own defaults, one role at a time. Validation lives here rather than in the
    planner so the message names the file the operator has to edit.
    """
    if not isinstance(payload, dict):
        raise ConfigError(
            "Config at {} must be a JSON object with `schema_version` and "
            "`agents` - see config.example.json.".format(source),
            {"source": source},
        )
    costs = payload.get("role_costs")
    if costs is None:
        return {}
    if not isinstance(costs, dict):
        raise ConfigError(
            "Config at {}: `role_costs` is a JSON {}, not an object mapping a "
            "role to its cost weight - write it as "
            "{{\"developer\": 12, \"tester\": 10, \"reviewer\": 5}}, or remove "
            "it to take the planner's defaults.".format(
                source, type(costs).__name__
            ),
            {"source": source, "role_costs_type": type(costs).__name__},
        )
    validated = {}
    for role, value in costs.items():
        # bool is a subclass of int, and `true` is not a one-point round.
        numeric_value = None
        if not isinstance(value, bool) and isinstance(value, (int, float)):
            try:
                numeric_value = float(value)
            except OverflowError:
                pass
        if (
            numeric_value is None
            or math.isnan(numeric_value)
            or math.isinf(numeric_value)
            or numeric_value < 0
        ):
            raise ConfigError(
                "Config at {}: role_costs[{!r}] is {!r}; use a non-negative "
                "number of headroom points, e.g. 12. A weight only has to "
                "order the seats against each other.".format(source, role, value),
                {"source": source, "role": role, "value": value},
            )
        validated[role] = numeric_value
    return validated


def load_role_costs(path):
    """`role_costs` from the config at `path`; `{}` when there is no config.

    `plan` contacts no agent and needs no roster, so it runs on a machine that
    has never been set up. A missing config there means "no overrides", not a
    failed plan -- but a config that EXISTS and is malformed still fails
    loudly, exactly as it does for `measure`.
    """
    path = Path(path)
    if not path.exists():
        return {}
    return parse_role_costs(_read_config(path), source=str(path))


def select_agents(agents, names):
    """Return the subset of `agents` named in `names`, in config order.

    An empty/None `names` selects every agent. An unknown name is an error,
    never a silent no-op.
    """
    if not names:
        return list(agents)
    by_name = {agent.name: agent for agent in agents}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ConfigError(
            "No configured agent named {} - configured agents are {}. "
            "Check the name against `herdr agent list`.".format(
                ", ".join(repr(name) for name in unknown),
                ", ".join(sorted(by_name)),
            ),
            {"unknown": unknown, "configured": sorted(by_name)},
        )
    wanted = set(names)
    return [agent for agent in agents if agent.name in wanted]
