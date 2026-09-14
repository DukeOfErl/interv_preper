"""The agent harness: turn state, and the middleware that shapes the loop.

Everything here is a policy about *how* the loop runs, kept out of the tools,
which only report what they fetched. Each safeguard we derived the hard way has
a named home:

    screen every tool result       ContentPolicyMiddleware  (@wrap_tool_call)
    "your tools are gone"          announce_exhausted_tools (@before_model)
    a tool call written as prose   catch_typed_tool_call    (@after_model)

**Turn state versus session state.** ``InterviewState`` holds what one turn
produces. Graph state is per-invocation, and we build a fresh agent per turn,
so anything that must survive *between* turns deliberately does not live here:
``seen_terms`` and ``code_corpus`` are session-scoped (Streamlit holds them)
and are passed in to be appended to. That boundary is load-bearing — moving
them into graph state would silently re-scope them per turn, and the
fabrication checks would start flagging a file the interviewer really did read
two turns ago (ADR-0130).
"""

from __future__ import annotations

import operator
import re
from typing import Annotated

from langchain.agents.middleware import (
    AgentState,
    after_model,
    before_model,
    wrap_tool_call,
)
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command
from typing_extensions import NotRequired

from .config import MAX_TOOL_HOPS
from .policy import ToolOutcome
from .spend import OverBudget

# The loop offers tools on every hop but the last; the limit middleware counts
# calls rather than hops, so this is the budget in the units it uses.
TOOL_CALL_BUDGET = MAX_TOOL_HOPS - 1

TYPED_CALL_CORRECTION = (
    "SYSTEM NOTE: that reply contained a tool call written as text. Text is "
    "never delivered to a tool. Request the tool properly through the tool "
    "interface, or, if you do not need one, answer the candidate in plain "
    "prose with no JSON."
)


def merge_cards(existing, new):
    """Combine evaluation cards, replacing any with the same question.

    A repeat call for the SAME question is a correction (observed live — models
    re-record with revised scores on a later hop); a different question is a
    second scored answer and appends. This lives in the reducer rather than in
    the tool because a model may record two cards in one hop, and only the
    reducer sees both.
    """
    cards = list(existing or [])
    for card in new or []:
        cards = [c for c in cards if c["question"] != card["question"]] + [card]
    return cards


class InterviewState(AgentState):
    """What one turn produces, beyond its messages.

    Read by ``announce_exhausted_tools`` while the turn runs, and by the caller
    once it ends — which is why it is state rather than attributes on an object
    the caller has to reach into.

    Every field carries a **reducer**, because a model may request several
    tools in one hop and their middleware then runs concurrently in one graph
    step. Without one, LangGraph rejects the second write ("can receive only
    one value per step") — and a read-modify-write of the whole value would be
    worse than the error, since two calls would read the same base and one
    would silently clobber the other. Each call therefore contributes only its
    own delta.
    """

    # Sources of every admitted research digest, for the sources panel.
    citations: NotRequired[Annotated[list, operator.add]]
    # Evaluation cards recorded this turn (ADR-0120).
    evaluations: NotRequired[Annotated[list, merge_cards]]
    # Files whose contents were fetched AND admitted, named in the exhaustion
    # note so the model can be honest about what it actually read.
    files_read: NotRequired[Annotated[list, operator.add]]
    # Tool activity, for the exhaustion note and the developer panel.
    tool_calls_made: NotRequired[Annotated[int, operator.add]]
    mcp_calls: NotRequired[Annotated[int, operator.add]]
    # USD spent by tool sub-completions, which the main loop's usage
    # accounting never sees.
    extra_cost: NotRequired[Annotated[float, operator.add]]


def final_hop_note(state):
    """A reminder to inject before the answer is forced, or ``""``.

    The loop withholds tools on its last hop to force a text answer. A model
    still mid-exploration is then cornered: it must say something, cannot
    fetch, and (observed repeatedly) either invents a repository or writes out
    the tool calls it *would* have made along with their imagined results.
    Naming the situation is what makes stopping honestly an available move, so
    the note fires whenever any tool ran this turn, not only when nothing was
    read.
    """
    if not state.get("tool_calls_made"):
        return ""
    preamble = (
        "SYSTEM NOTE: you have no tool calls left this turn. Whatever you "
        "have already received is all you get. Do NOT write tool calls, "
        "tool arguments, or tool results in your reply, and do not "
        "describe what a further call would have returned — a model in "
        "your position has been observed acting out the rest of its "
        "exploration in prose, which is fabrication. "
    )
    files_read = state.get("files_read") or []
    if state.get("mcp_calls") and not files_read:
        return preamble + (
            "This turn obtained no file contents at all — only listings, "
            "searches, or errors — so you have not read any of this "
            "candidate's code. Say that plainly and ask which file to "
            "look at, or ask them to describe it."
        )
    if state.get("mcp_calls"):
        return preamble + (
            "You read: "
            + ", ".join(files_read)
            + ". Work only from those (and your retrieved context). If you "
            "need a file you did not read, say so and ask the candidate "
            "about it, or offer to read it next turn."
        )
    return preamble + (
        "Answer from what the tools already returned; if it was not "
        "enough, say so plainly."
    )


def content_policy_middleware(
    policy,
    mcp_names=(),
    seen_terms=None,
    code_corpus=None,
    on_warning=None,
):
    """Screen every tool result, and record only what the screen admitted.

    The component that admits content is the component that records it. That
    is not tidiness: anything in ``seen_terms`` or ``code_corpus`` is what the
    fabrication checks treat as proven, so text the screen refused must never
    reach them, and keeping both in one place makes that structural rather than
    a rule two files have to agree on.

    Tools hand their payload over on the artifact channel and put a placeholder
    in ``content``, so a run where this middleware never fires yields a useless
    tool result rather than unscreened text reaching the model.
    """
    seen_terms = seen_terms if seen_terms is not None else set()
    code_corpus = code_corpus if code_corpus is not None else []
    warn = on_warning or (lambda name, kind, reason="": None)

    @wrap_tool_call
    def screen_tool_results(request, handler):
        name = request.tool_call.get("name")
        response = handler(request)

        # Deltas, not totals — see InterviewState. Several tools can be running
        # in this same step.
        counters = {"tool_calls_made": 1}
        if name in mcp_names:
            counters["mcp_calls"] = 1

        outcome = getattr(response, "artifact", None)
        if not isinstance(outcome, ToolOutcome):
            # A Command (record_evaluation writes state directly) or a tool
            # that reported nothing to screen. Count it and pass it through.
            if isinstance(response, Command):
                response.update.update(counters)
                return response
            if getattr(response, "status", None) == "error":
                # ToolRetryMiddleware gave up after its retries. This
                # middleware wraps it, so we see the settled outcome rather
                # than the first attempt — which is the only point at which
                # "this tool is failing" is worth telling the user.
                warn(name, "github tool" if name in mcp_names else "tool",
                     str(getattr(response, "content", "")))
            return Command(update={"messages": [response], **counters})

        if outcome.warning:
            warn(*outcome.warning)
        if outcome.billed_cost:
            counters["extra_cost"] = outcome.billed_cost

        # `apply` can raise: screening a fetched document bills, and indexing
        # re-raises a refusal rather than swallowing it. When it does, this
        # function never returns its `Command`, so `counters` — including the
        # cost of a sub-completion that has *already* been charged — never
        # reaches graph state, and the page shows a refused turn as free. The
        # ledger is unaffected (every client records its own); this carries the
        # figure out so the sidebar can still show it.
        try:
            text, admitted = policy.apply(outcome)
        except OverBudget as exc:
            exc.billed_extra = float(counters.get("extra_cost") or 0.0)
            raise
        message = ToolMessage(
            content=text,
            tool_call_id=request.tool_call["id"],
            status="success" if admitted else "error",
        )
        if not admitted:
            return Command(update={"messages": [message], **counters})

        seen_terms.update(outcome.terms)
        if outcome.on_admitted is not None:
            outcome.on_admitted()
        update = {"messages": [message], **counters, **outcome.state_update}
        # A document with a body is a file we actually read; a listing is not.
        if outcome.document is not None and outcome.inline is None:
            update["files_read"] = [outcome.document.name]
            code_corpus.append(outcome.document.text)
        return Command(update=update)

    return screen_tool_results


@before_model
def announce_exhausted_tools(state, runtime):
    """Tell a cornered model that its tools are gone, and what it got.

    Without this the model must answer, cannot fetch, and invents. Paired with
    ``ToolCallLimitMiddleware(exit_behavior="continue")``, whose alternatives
    are worse for a chat UI: ``"end"`` stops the run with no closing reply, and
    ``"error"`` aborts the turn outright.
    """
    used = sum(
        len(message.tool_calls)
        for message in state["messages"]
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None)
    )
    if used < TOOL_CALL_BUDGET:
        return None
    note = final_hop_note(state)
    return {"messages": [SystemMessage(note)]} if note else None


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")
_JSON_KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
# Enough keys that an ordinary sentence containing braces cannot qualify.
_MIN_TYPED_CALL_KEYS = 3


def _tool_parameters(tools):
    """Every argument name across the offered tools."""
    names = set()
    for tool in tools or []:
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            names |= set(schema.get("properties") or {})
        elif schema is not None:
            names |= set(getattr(schema, "model_fields", {}) or {})
    return names


def looks_like_typed_tool_call(text, tools):
    """True when a reply contains a tool call written out as prose.

    Deliberately narrow: an object literal counts only when it has at least
    ``_MIN_TYPED_CALL_KEYS`` keys and *every* key is a parameter of a tool the
    model was offered. A reply that merely discusses JSON, or shows a two-field
    example, does not qualify — a false positive costs a wasted hop and a
    re-answer.
    """
    if not text or "{" not in text:
        return False
    parameters = _tool_parameters(tools)
    if not parameters:
        return False
    for blob in _JSON_OBJECT_RE.findall(text):
        keys = set(_JSON_KEY_RE.findall(blob))
        if len(keys) >= _MIN_TYPED_CALL_KEYS and keys <= parameters:
            return True
    return False


def catch_typed_tool_call(tools):
    """A call written as prose is not an answer; correct it and ask again.

    Native tool calling has no observation marker to stop on, so the ReAct-era
    fix does not apply and the harness has to detect it.

    In a for-loop this retry was a ``continue`` statement. In a graph it is an
    edge, and an edge exists only if it is declared (``can_jump_to``) and taken
    (``jump_to``) — otherwise the correction is appended to a run that has
    already ended. There is no built-in for this: LangChain retries tool
    *exceptions* and model *exceptions*, not unacceptable model output.
    """

    # The inner function's name becomes the graph node's name, so it says what
    # it is when the compiled graph is drawn or a trace is read.
    @after_model(can_jump_to=["model"])
    def catch_typed_tool_call_hook(state, runtime):
        message = state["messages"][-1]
        if not isinstance(message, AIMessage) or getattr(
            message, "tool_calls", None
        ):
            return None
        if not looks_like_typed_tool_call(message.text, tools):
            return None
        return {
            "messages": [SystemMessage(TYPED_CALL_CORRECTION)],
            "jump_to": "model",
        }

    return catch_typed_tool_call_hook
