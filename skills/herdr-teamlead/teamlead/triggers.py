"""Diff-time detection of the four non-exhaustion Team Composition triggers.

The exhaustion trigger is enforced in code: `recovery.require_investigation_before_judge`
refuses a judge dispatch at an exhausted allowance with no assessment. The
architect, security, UX-and-product and documentation triggers shipped as prose
alone (#408), so the operative rule was "the lead notices, or it does not
happen" -- the condition the triggers were written to replace. A trigger nobody
mechanically evaluates is a check nobody runs (`rules/language-diagnostics.md`).

This module is that evaluation. It reads the consuming repo's own declaration
of its trigger surfaces, classifies the task's diff against them, and refuses
the round when a fired trigger is neither staffed nor answered by a recorded
staffing decision. Every signal is mechanical: a package directory absent from
the base, a changed path in the declared trust-boundary set, an added line
carrying a declared CLI-surface marker, an added file in the declared
user-facing docs set.

The declaration is the repo's, never this module's. The architect trigger reads
"above the size the repo states", and a repo that states no size has a trigger
that fires never or always depending on the reader (#415), so
`package_change_lines` is required and a missing declaration is refused rather
than silently treated as "nothing fires".

Contract:

* `load_declaration(repo)` reads `<repo>/.herdr/triggers.json`.
* `detect(...)` is pure over the declaration and the diff facts.
* `run_command(args, runner=...)` collects those facts through `runner`, a
  callable taking a git argument list and returning its stdout.
* The payload lists every trigger with its evidence; the failure object names
  the fired triggers that are neither staffed nor decided.
"""

import fnmatch
import json
import subprocess
from pathlib import Path
from typing import Any

from .composition import REQUIREMENTS_SCHEMA_VERSION
from .errors import UsageError


DECLARATION_SCHEMA_VERSION = 1
DETECTION_SCHEMA_VERSION = 1
DECISIONS_SCHEMA_VERSION = 1

#: Repo-relative location of the consuming repo's trigger declaration.
DECLARATION_FILE = ".herdr/triggers.json"

#: The four triggers `rules/agent-team-operation.md` Team Composition states
#: outside the exhaustion path, each mapped to the planned responsibility or
#: requirements specialty that staffs it.
TRIGGER_ROLES = {"architect": "architect"}
TRIGGER_SPECIALTIES = {"security": "security", "ux-product": "ux-product",
                       "documentation": "documentation"}
TRIGGERS = tuple(sorted(set(TRIGGER_ROLES) | set(TRIGGER_SPECIALTIES)))

#: Declaration fields, all required. A repo declares an empty list to state
#: that a surface does not exist here; omitting the field states nothing.
GLOB_FIELDS = ("package_roots", "trust_boundary_paths", "cli_spec_paths", "user_doc_paths")
DECLARATION_FIELDS = frozenset({"schema_version", "package_change_lines",
                                "cli_surface_markers", *GLOB_FIELDS})


def _globs(value, label):
    if not isinstance(value, list) or len(value) > 200:
        raise UsageError("Trigger declaration {} must be a list of at most 200 path globs; state [] when the surface does not exist in this repo.".format(label), {})
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or any(ord(char) < 32 for char in entry):
            raise UsageError("Trigger declaration {} holds an empty or control-character glob; state each surface as a repo-relative path glob.".format(label), {})
    return list(value)


def load_declaration(repo):
    """Read and validate the consuming repo's trigger declaration.

    A missing or incomplete declaration is refused, never defaulted: the
    architect trigger's size and the other three surfaces are the repo's to
    state, and an invented default would fire on repos it was never measured
    against.
    """
    root = Path(repo)
    path = root / DECLARATION_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("No trigger declaration at {} ({}); state this repo's package roots and size, trust-boundary paths, CLI spec surface and user-facing docs paths there before composing a round.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Trigger declaration {} is invalid JSON ({}); repair it before composing a round.".format(path, exc.msg), {}) from None
    if not isinstance(payload, dict) or set(payload) != DECLARATION_FIELDS:
        raise UsageError("Trigger declaration {} requires exactly {}; state every surface, using [] for one this repo does not have.".format(
            path, ", ".join(sorted(DECLARATION_FIELDS))), {})
    if payload["schema_version"] != DECLARATION_SCHEMA_VERSION:
        raise UsageError("Trigger declaration {} is schema_version {}; this owner reads version {}.".format(
            path, payload["schema_version"], DECLARATION_SCHEMA_VERSION), {})
    size = payload["package_change_lines"]
    if type(size) is not int or size < 1:
        raise UsageError("Trigger declaration {} needs a positive package_change_lines: the architect trigger fires above the size this repo states, and an unstated size fires never or always depending on the reader.".format(path), {})
    markers = payload["cli_surface_markers"]
    if not isinstance(markers, list) or len(markers) > 50:
        raise UsageError("Trigger declaration {} cli_surface_markers must list at most 50 literal substrings; state [] when this repo declares no CLI spec surface.".format(path), {})
    for marker in markers:
        if not isinstance(marker, str) or not marker.strip() or any(ord(char) < 32 for char in marker):
            raise UsageError("Trigger declaration {} holds an empty or control-character CLI surface marker; each marker is a literal substring an added line carries.".format(path), {})
    declaration = {"schema_version": DECLARATION_SCHEMA_VERSION, "package_change_lines": size,
                   "cli_surface_markers": list(markers)}
    for field in GLOB_FIELDS:
        declaration[field] = _globs(payload[field], field)
    declaration["path"] = str(path)
    return declaration


def load_decisions(path):
    """Read the lead's recorded staffing decisions for fired triggers."""
    if path is None:
        return {}
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read staffing decisions at {} ({}); write the recorded decision or omit --decisions.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Staffing decisions {} are invalid JSON ({}); repair the recorded decision.".format(path, exc.msg), {}) from None
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "decisions"}
            or payload["schema_version"] != DECISIONS_SCHEMA_VERSION
            or not isinstance(payload["decisions"], dict)):
        raise UsageError("Staffing decisions must be a schema_version {} object with a decisions map of trigger to reason.".format(DECISIONS_SCHEMA_VERSION), {})
    decisions = {}
    for name, reason in payload["decisions"].items():
        if name not in TRIGGERS:
            raise UsageError("Staffing decision names unknown trigger {!r}; the recorded triggers are {}.".format(name, ", ".join(TRIGGERS)), {})
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise UsageError("Staffing decision for {} needs its reason in 1-4000 characters; silence is never that decision.".format(name), {})
        decisions[name] = reason.strip()
    return decisions


PLAN_FIELDS = frozenset({"schema_version", "added", "changed", "package_lines", "cli_surface"})


def load_plan(path):
    """Read the surfaces a round intends to touch, before it has a diff.

    The triggers gate work *before* implementation, and a task's first round has
    nothing committed to read: a diff-only detector reports every trigger quiet
    on exactly the round the architect and security triggers exist for (#415).
    The lead declares the intended surfaces here, and the same declaration
    classifies them. Later rounds keep reading the diff, which is evidence
    rather than intent.
    """
    if path is None:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read planned surfaces at {} ({}); write the plan or omit --planned.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Planned surfaces {} are invalid JSON ({}); repair the plan.".format(path, exc.msg), {}) from None
    if not isinstance(payload, dict) or set(payload) != PLAN_FIELDS or payload["schema_version"] != DECLARATION_SCHEMA_VERSION:
        raise UsageError("Planned surfaces must be a schema_version {} object with added, changed, package_lines and cli_surface; state [] or {{}} for one this round has none of.".format(
            DECLARATION_SCHEMA_VERSION), {})
    plan: dict[str, Any] = {"schema_version": DECLARATION_SCHEMA_VERSION}
    for field in ("added", "changed", "cli_surface"):
        plan[field] = _globs(payload[field], "planned " + field)
    lines = payload["package_lines"]
    if not isinstance(lines, dict):
        raise UsageError("Planned package_lines maps a package directory to the lines this round will change there; state {} when none.", {})
    for name, count in lines.items():
        if not isinstance(name, str) or not name.strip() or type(count) is not int or count < 0:
            raise UsageError("Planned package_lines needs a package directory and a non-negative line count; state the size this round will change.", {})
    plan["package_lines"] = dict(lines)
    return plan


def load_requirements(path):
    """Collect the specialties a requirements file staffs, for trigger cover."""
    if path is None:
        return set()
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError("Cannot read requirements at {} ({}); pass the same file Step 6 gives `plan`.".format(path, exc.strerror), {}) from None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError("Requirements {} are invalid JSON ({}); repair the file before planning.".format(path, exc.msg), {}) from None
    if (not isinstance(payload, dict) or set(payload) != {"schema_version", "assignments"}
            or payload["schema_version"] != REQUIREMENTS_SCHEMA_VERSION
            or not isinstance(payload["assignments"], dict)):
        raise UsageError("Requirements must be a schema_version {} object with an assignments map; use the documented requirements file.".format(REQUIREMENTS_SCHEMA_VERSION), {})
    found = set()
    for record in payload["assignments"].values():
        if isinstance(record, dict) and isinstance(record.get("specialty"), str):
            found.add(record["specialty"])
    return found


def matches(path, globs):
    """True when `path` matches any path glob. `*` spans path separators.

    A declared surface is a subtree -- `docs/*` covers everything under `docs`
    -- so the path sets deliberately span separators.
    """
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def package_matches(directory, roots):
    """True when `directory` is one of the declared package roots.

    A package root names a directory, not a subtree, so `*` matches within one
    path segment here: `skills/*` is every skill, never a directory nested
    inside one.
    """
    segments = directory.split("/")
    for glob in roots:
        parts = glob.split("/")
        if len(parts) == len(segments) and all(
                fnmatch.fnmatchcase(segment, part) for segment, part in zip(segments, parts)):
            return True
    return False


def package_of(path, roots):
    """The declared package directory holding `path`, or None.

    The nearest matching ancestor wins, so a nested declared root claims its
    own files rather than leaving them with the outer one.
    """
    parts = path.split("/")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:cut])
        if package_matches(candidate, roots):
            return candidate
    return None


def detect(declaration, changes, churn, base_packages, planned_lines=None):
    """Classify one diff against the declaration. Pure.

    `changes` maps a repo-relative path to its git status letter under
    `--no-renames`, so a rename reads as a delete plus an add. `churn` maps a
    path to its added-plus-deleted line count; a binary path contributes 0.
    `base_packages` maps each candidate package directory to whether the base
    revision held it -- the one fact the diff cannot supply. `planned_lines`
    seeds a package's changed-line count from the round's declared plan, for
    work that has not been written yet.
    """
    fired = {}
    packages = dict(planned_lines or {})
    for path in changes:
        package = package_of(path, declaration["package_roots"])
        if package is not None:
            packages.setdefault(package, 0)
            packages[package] += churn.get(path, 0)
    new_packages = sorted(name for name in packages if not base_packages.get(name, True))
    grown = sorted(name for name, lines in packages.items()
                   if base_packages.get(name, True) and lines > declaration["package_change_lines"])
    if new_packages or grown:
        fired["architect"] = [
            *({"signal": "new_package", "evidence": name} for name in new_packages),
            *({"signal": "package_change_lines", "evidence": "{} ({} lines > {})".format(name, packages[name], declaration["package_change_lines"])}
              for name in grown),
        ]
    boundary = sorted(path for path in changes if matches(path, declaration["trust_boundary_paths"]))
    if boundary:
        fired["security"] = [{"signal": "trust_boundary_path", "evidence": path} for path in boundary]
    documents = sorted(path for path, status in changes.items()
                       if status == "A" and matches(path, declaration["user_doc_paths"]))
    if documents:
        fired["documentation"] = [{"signal": "added_user_document", "evidence": path} for path in documents]
    return fired


def cli_surface(declaration, changes, added_lines):
    """The added CLI-surface lines, keyed by the spec path that carries them."""
    found = []
    for path in sorted(changes):
        if not matches(path, declaration["cli_spec_paths"]):
            continue
        for line in added_lines.get(path, ()):
            if any(marker in line for marker in declaration["cli_surface_markers"]):
                found.append({"signal": "added_cli_surface", "evidence": path + ": " + line.strip()[:200]})
    return found


def report(declaration, base, head, fired, roles, specialties, decisions):
    """Assemble the detection document and its unaddressed-trigger failure."""
    roles, triggers, unaddressed = set(roles), [], []
    for name in TRIGGERS:
        signals = fired.get(name, [])
        addressed = None
        if signals:
            role = TRIGGER_ROLES.get(name)
            specialty = TRIGGER_SPECIALTIES.get(name)
            if role is not None and role in roles:
                addressed = "role:" + role
            elif specialty is not None and specialty in specialties:
                addressed = "specialty:" + specialty
            elif name in decisions:
                addressed = "decision"
            else:
                unaddressed.append(name)
        triggers.append({"trigger": name, "fired": bool(signals), "signals": signals,
                         "addressed": addressed, "decision": decisions.get(name)})
    payload = {"schema_version": DETECTION_SCHEMA_VERSION, "declaration": declaration["path"],
               "base": base, "head": head, "triggers": triggers,
               "fired": [name for name in TRIGGERS if fired.get(name)],
               "unaddressed": unaddressed,
               "unused_decisions": sorted(name for name in decisions if not fired.get(name))}
    failure = None
    if unaddressed:
        failure = {"error": "unaddressed_trigger",
                   "message": "Triggers {} fired and this round neither staffs their consultation nor records a staffing decision; consult the profile in skills/herdr-teamlead/references/specialists.md or record the decision and its reason.".format(", ".join(unaddressed)),
                   "details": {"unaddressed": unaddressed}}
    return payload, failure


def _records(text):
    """Split git's NUL-delimited output, dropping the trailing empty record."""
    return [record for record in text.split("\0") if record != ""]


def parse_name_status(text):
    records = _records(text)
    if len(records) % 2:
        raise UsageError("git diff --name-status returned an odd number of NUL-delimited fields; rerun the detection against a clean checkout.", {})
    return {records[index + 1]: records[index][:1] for index in range(0, len(records), 2)}


def parse_numstat(text):
    churn = {}
    for record in _records(text):
        added, deleted, path = record.split("\t", 2)
        # `-` is git's binary marker; a binary file has no line count to add.
        churn[path] = (0 if added == "-" else int(added)) + (0 if deleted == "-" else int(deleted))
    return churn


def parse_added_lines(text):
    """Added lines of a single-file unified diff.

    The caller asks for one path at a time and already holds it, so no file
    header is read. git quotes a path carrying a quote, a backslash or a
    non-ASCII byte (`+++ "b/src/a\\"b.py"`), and a parser keyed on that header
    drops the file and its added command, flag or refusal with it (#415).
    Reading only what follows a hunk header is immune to that quoting: a
    content line reading `++ x` arrives as `+++ x` inside a hunk and is an
    added line, never a header.
    """
    added, in_hunk = [], False
    for line in text.split("\n"):
        if line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return added


def read_lines(repo, path):
    """The working tree's lines for an untracked file.

    A file git is not tracking has no diff to read, so its own bytes are the
    added lines. A binary file legitimately yields none; an unreadable one is a
    failure, never an empty result.
    """
    try:
        return Path(repo, path).read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []
    except OSError as exc:
        raise UsageError("Cannot read untracked file {} in {} ({}); remove it or commit it before detecting the triggers.".format(
            path, repo, exc.strerror), {}) from None


def collect_untracked(run, repo, changes, churn, added_lines):
    """Fold the working tree's untracked files into the diff facts.

    `git diff` reports tracked changes only, so a whole new package or a new
    user-facing document -- the very shapes the architect and documentation
    triggers exist for -- would fire nothing while they sit untracked (#415).
    They are added files by definition, and every line in them is an added
    line. A comparison against a pushed head needs none of this: an untracked
    file is in no commit.
    """
    for path in _records(run(["ls-files", "--others", "--exclude-standard", "-z"])):
        lines = read_lines(repo, path)
        changes[path] = "A"
        churn[path] = len(lines)
        added_lines[path] = lines


def git_runner(repo):
    """A runner executing git in `repo` and returning stdout."""

    def run(arguments):
        completed = subprocess.run(["git", "-C", str(repo), *arguments],
                                   capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise UsageError("git {} failed in {} ({}): {}".format(
                " ".join(arguments), repo, completed.returncode, completed.stderr.strip() or "no diagnostic"), {})
        return completed.stdout
    return run


def run_command(args, runner=None):
    """Detect the triggers for one task's diff and gate on the unaddressed set."""
    declaration = load_declaration(args.repo)
    decisions = load_decisions(getattr(args, "decisions", None))
    specialties = load_requirements(getattr(args, "requirements", None))
    plan = load_plan(getattr(args, "planned", None))
    roles = [role for role in (getattr(args, "roles", None) or "").split(",") if role]
    run = runner if runner is not None else git_runner(args.repo)
    head = getattr(args, "head", None)
    # `base...head` diffs from the merge base, so "absent from the base" is
    # read at that same commit rather than at the branch point's namesake.
    left = run(["merge-base", args.base, head]).strip() if head else args.base
    span = [args.base + "..." + head] if head else [args.base]
    common = ["diff", "--no-renames", *span]
    changes = parse_name_status(run([*common, "--name-status", "-z"]))
    churn = parse_numstat(run([*common, "--numstat", "-z"]))
    untracked = {}
    if not head:
        collect_untracked(run, args.repo, changes, churn, untracked)
    planned_lines = {}
    if plan is not None:
        for path in plan["added"]:
            changes.setdefault(path, "A")
        for path in plan["changed"]:
            changes.setdefault(path, "M")
        planned_lines = plan["package_lines"]
        for name in planned_lines:
            if not package_matches(name, declaration["package_roots"]):
                raise UsageError("Planned package_lines names {}, which is not one of this repo's declared package roots; name a package root or widen the declaration.".format(name), {})
        for path in plan["cli_surface"]:
            if not matches(path, declaration["cli_spec_paths"]):
                raise UsageError("Planned cli_surface names {}, which is outside this repo's declared CLI spec paths; name a spec path or widen the declaration.".format(path), {})
            # A planned CLI surface is a planned change to that file, so it is
            # classified against every surface the declaration names. A spec
            # path that is also a trust boundary fires security too; validating
            # it and classifying nothing let UX and product answer for both.
            changes.setdefault(path, "M")
    # An empty plan classifies exactly as much as an absent one, so the guard
    # reads the combined inputs rather than the plan's presence: a vacuous
    # success here is the silence the triggers exist to end (#415).
    if not changes and not planned_lines and not (plan is not None and plan["cli_surface"]):
        raise UsageError("This round classifies nothing: its diff is empty and no planned surface is declared. Name the paths, package sizes or CLI surfaces the work will touch in --planned before the developer is dispatched.", {})
    # A package declared only by its planned size has no path in `changes`, and
    # a candidate missing here reads as one the base already held -- so a
    # planned new package would fire nothing (#415).
    candidates = sorted({package for package in
                         (package_of(path, declaration["package_roots"]) for path in changes)
                         if package is not None} | set(planned_lines))
    base_packages = {name: bool(run(["ls-tree", "--name-only", left, "--", name + "/"]).strip())
                     for name in candidates}
    fired = detect(declaration, changes, churn, base_packages, planned_lines)
    spec_paths = sorted(path for path in changes if matches(path, declaration["cli_spec_paths"]))
    tracked_specs = [path for path in spec_paths if path not in untracked]
    if spec_paths and declaration["cli_surface_markers"]:
        added = dict(untracked)
        for path in tracked_specs:
            added[path] = parse_added_lines(run([*common, "--unified=0", "--", path]))
        surface = cli_surface(declaration, changes, added)
        if surface:
            fired["ux-product"] = surface
    if plan is not None and plan["cli_surface"]:
        fired.setdefault("ux-product", []).extend(
            {"signal": "planned_cli_surface", "evidence": path} for path in plan["cli_surface"])
    return report(declaration, args.base, head or "worktree", fired, roles, specialties, decisions)


def register_command(sub, common):
    parser = sub.add_parser(
        "detect-triggers", parents=[common],
        help="Classify a task's diff against the repo's declared Team Composition trigger surfaces.")
    parser.add_argument("--repo", required=True, metavar="DIR",
                        help="Repository holding " + DECLARATION_FILE + " and the commits to compare.")
    parser.add_argument("--base", required=True, metavar="REF", help="The task's recorded base revision.")
    parser.add_argument("--head", metavar="REF", help="Pushed head; omit to read the working tree.")
    parser.add_argument("--roles", metavar="ROLE[,ROLE...]", help="Roles this round plans, as given to `plan`.")
    parser.add_argument("--requirements", metavar="FILE", help="The requirements file this round gives `plan`.")
    parser.add_argument("--decisions", metavar="FILE", help="Recorded staffing decisions for fired triggers.")
    parser.add_argument("--planned", metavar="FILE",
                        help="Surfaces this round will touch, for a pre-implementation round with no diff yet.")
