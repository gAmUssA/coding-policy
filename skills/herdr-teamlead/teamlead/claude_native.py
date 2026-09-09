"""Claude Code's completed-turn evidence, read from its own session transcript.

Claude Code appends JSONL to `<config>/projects/<slug>/<session-id>.jsonl`, one
row per API content block: rows sharing `message.id` are one assistant message
ordered by `apiBlockIndex`, and every row carrying a `uuid` links to the one
before it through `parentUuid`. Blocks of one message repeat that message's
`stop_reason`, which is null on a block written before the message settled.
Verified on Claude Code 2.1.263 under Herdr 0.8.2.

Nothing here reads rendered pane text. A completed final message is the last
main-chain assistant message: `end_turn`, built from text and thinking blocks
alone, with no user turn after it. Bookkeeping rows (`system`, `attachment`,
`mode`, `permission-mode`, `ai-title`, `last-prompt`, ...) carry no task, so
they neither complete nor reset a turn -- but a row that DECLARES itself a turn
is held to the turn contract even when it carries none of a turn's fields. A
user row that is not tool output is a new turn and ends the pending message.

Parallel tool calls come in two orderings, both from this same pinned CLI, and
which one appears depends on when the results are flushed:

* interleaved -- `tool_use` block, its `user` tool-result row, the next
  `tool_use` block of the SAME message, its result. Every row links to the one
  before it.
* queued -- both `tool_use` blocks, then both results. Claude Code links a
  result to the block row that REQUESTED it, so the first result's parent is
  not the row before it and the chain branches.

So a result names its requester in BOTH orderings -- verified on every tool
result across the preserved sessions -- and that is the rule: a `tool_result`
row links to the block row whose `tool_use` id it answers, in the message being
assembled, and each call is answered exactly once, including within a single
row. Every other row links to the row before it. A broken parent chain, a
parent naming an abandoned branch or a row that merely occurred earlier, a
mismatched or repeated tool result, a subagent row on the main chain, a foreign
session id, contradictory completion metadata, or an unreadable shape stays
unconfirmed.
"""

import os
from pathlib import Path

#: Content blocks that produce no rendered message text. Every other block type
#: -- `tool_use` above all -- means the message is still doing work, so its
#: text is not a final answer.
SILENT_BLOCKS = ("thinking", "redacted_thinking")

#: Row types that speak for the worker or the operator, and are therefore held
#: to the turn contract in `main_chain`.
TURN_TYPES = ("user", "assistant")

#: Directory depth from the sessions root to the transcript's own directory:
#: `<config>/projects/<slug>/<session-id>.jsonl`.
SESSION_DEPTH = 1


def sessions_root():
    """Where Claude Code keeps per-project transcripts."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects"


def transcript_name(session):
    return session + ".jsonl"


def tool_output(body):
    """True for a user row that carries nothing but tool results."""
    content = body.get("content")
    return (isinstance(content, list) and bool(content)
            and all(isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in content))


def tool_answers(body):
    """The `tool_use` ids a user row answers, or None if it answers none.

    A call is answered once. A row naming the same id twice does not answer two
    calls, so its claim is unreadable -- and reading it as one answer would let
    the repeat ride along on the first id's requester.
    """
    if not tool_output(body):
        return None
    answered = [block.get("tool_use_id") for block in body["content"]]
    if not all(isinstance(identity, str) and identity for identity in answered):
        return None
    return answered if len(set(answered)) == len(answered) else None


def requested_by(body, pending):
    """The one block row that asked for everything this user row answers.

    `pending` holds the tool calls the message under assembly has made and not
    yet had answered, so an id from an abandoned branch, an earlier message, or
    a second answer to the same call resolves to nothing.
    """
    answered = tool_answers(body)
    if not answered or any(identity not in pending for identity in answered):
        return None
    requesters = {pending[identity] for identity in answered}
    return requesters.pop() if len(requesters) == 1 else None


def main_chain(rows, session):
    """The session's own non-subagent rows in order; a rewound file is None.

    Rows are linear apart from a queued parallel tool group, where a result
    links to the block row that requested it (see the module docstring).
    """
    chain, head, message, pending = [], None, None, {}
    for row in rows:
        kind, body = row.get("type"), row.get("message")
        turn = kind in TURN_TYPES
        # A declared turn is a turn even with every one of its fields missing.
        # Reading such a row as bookkeeping would let it slip past the chain
        # and leave an earlier answer standing as the latest completed one.
        carries_turn = turn or "uuid" in row or "message" in row
        identifier = row.get("sessionId")
        if identifier is None:
            # Snapshot bookkeeping is written without a session stamp; a row
            # that carries a turn without one is not this session's evidence.
            if carries_turn:
                return None
            continue
        if identifier != session:
            return None
        if row.get("isSidechain") is True:
            continue
        if "uuid" not in row:
            if carries_turn:
                return None
            continue
        if not isinstance(row["uuid"], str) or not row["uuid"]:
            return None
        if turn and (not isinstance(body, dict) or body.get("role") != kind):
            return None
        # Bookkeeping never speaks as the worker or the operator.
        if not turn and isinstance(body, dict) and body.get("role") in TURN_TYPES:
            return None
        parent = row.get("parentUuid")
        if kind == "user" and tool_output(body):
            # A result names the block row that asked for it, never a row that
            # merely occurred earlier.
            if parent is None or parent != requested_by(body, pending):
                return None
        elif parent != head:
            return None
        if kind == "assistant":
            content = body.get("content")
            # An assistant message's content is a list of blocks. Anything else
            # is an unreadable shape: a truthy scalar raises when iterated, and
            # a string or a mapping walks characters or keys that are not
            # blocks, so a tool call could hide behind either.
            if not isinstance(content, list):
                return None
            # Tool calls belong to the message that made them, so a result can
            # never reach back past the message under assembly.
            if body.get("id") != message:
                message, pending = body.get("id"), {}
            for block in content:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and isinstance(block.get("id"), str) and block["id"]):
                    pending[block["id"]] = row["uuid"]
        elif kind == "user":
            answered = tool_answers(body)
            if answered is None:
                pending = {}
            else:
                for identity in answered:
                    pending.pop(identity, None)
        head = row["uuid"]
        chain.append(row)
    return chain


def final_message(rows, session):
    """The latest completed assistant message's text, joined block by block."""
    chain = main_chain(rows, session)
    if chain is None:
        return None
    message, reason, text, blocks, complete, usable = None, None, "", 0, False, False
    for row in chain:
        kind = row.get("type")
        if kind == "user":
            # A tool result belongs to the message that asked for it. Every
            # other user row opens a new turn, and so does any row that follows
            # a completed answer.
            if complete or not tool_output(row["message"]):
                message, reason, text, blocks, complete, usable = None, None, "", 0, False, False
            continue
        if kind != "assistant":
            continue
        body, index = row["message"], row.get("apiBlockIndex")
        if not isinstance(body.get("id"), str) or not body["id"] or type(index) is not int:
            return None
        if body["id"] != message:
            # A message whose first block is missing was truncated or replaced.
            if index != 0:
                return None
            message, reason, text, usable = body["id"], None, "", True
        elif index != blocks:
            return None
        settled = body.get("stop_reason")
        if settled is not None:
            # Blocks of one message repeat its outcome. A null block has not
            # settled yet; two different outcomes are contradictory evidence,
            # never a completed answer one sibling can vouch for.
            if reason is not None and settled != reason:
                return None
            reason = settled
        blocks, complete = index + 1, False
        content = body.get("content")
        if not isinstance(content, list) or not content:
            return None
        for block in content:
            if not isinstance(block, dict):
                return None
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    return None
                text += block["text"]
            elif block.get("type") not in SILENT_BLOCKS:
                usable = False
        if usable:
            complete = bool(text) and reason == "end_turn"
    return text if complete else None


def prompt_text(rows, session):
    """The latest human-typed prompt; injected and quoted rows never count."""
    chain = main_chain(rows, session)
    if chain is None:
        return None
    latest = None
    for row in chain:
        origin = row.get("origin")
        if (row.get("type") != "user" or row.get("isMeta") is True
                or row.get("promptSource") != "typed"
                or not isinstance(origin, dict) or origin.get("kind") != "human"):
            continue
        content = row["message"].get("content")
        if isinstance(content, str):
            latest = content
        elif (isinstance(content, list) and content
                and all(isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str) for block in content)):
            latest = "".join(block["text"] for block in content)
        else:
            return None
    return latest
