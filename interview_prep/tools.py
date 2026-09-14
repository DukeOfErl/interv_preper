"""The tools the interviewer can call.

A *tool* is two things kept deliberately separate:

  1. a **schema** the model sees (name, description, JSON-Schema parameters) —
     prompt text, and the only thing that makes the model decide to call it, so
     the description does the real work (including the consent policy);
  2. an implementation the model never sees.

Two local tools, with opposite invocation policies (policy lives per-tool, in
the description — never in the loop):

* ``web_research(query, topic)`` — CONSENT-GATED: only on the user's explicit
  request. See ``web_research.py`` for the dual-LLM rationale.
* ``record_evaluation(...)`` — ALWAYS-CALL: after every scored answer. Not an
  action but a structured-output channel (ADR-0120): the card the model would
  otherwise only state as prose is captured as data for the Evaluations tab.
  The one tool whose output is not external text, so the one that skips the
  screen — and it says so, by returning a ``Command`` rather than an outcome.

MCP tools (``github_mcp.GitHubMCP``) join the same list; their schemas are
discovered from the remote server rather than written here.

**Nothing here screens, indexes, or records provenance.** A tool reports what
it got as a ``ToolOutcome`` on the artifact channel and puts a placeholder in
the content the model would read; ``middleware.content_policy_middleware``
decides what is admitted. That is what makes screening impossible to forget —
it is no longer something a tool does.
"""

from __future__ import annotations

import re
from datetime import date

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from .config import (
    EVALUATION_DIMENSIONS,
    EVALUATION_SCORE_MAX,
    EVALUATION_SCORE_MIN,
    QUESTION_TYPES,
    WEB_TOPICS,
)
from .ingest import IngestedDocument
from .policy import ToolOutcome
from .spend import OverBudget


# One dimension actually being scored: the dimension name, then at most a
# colon/dash separator (optionally bold-wrapped), then a 1-5 score — either
# "n/5" or a standalone digit. The lookahead rejects digits that are part of
# a range ("Relevance (1-5)" in a rubric announcement) or a decimal
# ("relevance averaged 4.2" in a recap): those turns describe scores without
# assigning one, and correctly record no card.
_DIMENSION_SCORED = r"\b{d}\b\*{{0,2}}\s*[:\-–—]?\s*(?:[1-5]\s*/\s*5|[1-5](?![\d.\-–/]))"


def looks_like_feedback(reply_text) -> bool:
    """Heuristic: does this reply read like scored answer feedback?

    Used to detect a skipped ``record_evaluation`` call (feedback given, no
    card recorded). Deliberately conservative — a false "you skipped a card"
    warning is worse than a missed one — so it requires at least three rubric
    dimensions each actually being assigned a 1-5 score.
    """
    text = (reply_text or "").lower()
    scored = sum(
        1
        for dimension in EVALUATION_DIMENSIONS
        if re.search(_DIMENSION_SCORED.format(d=dimension), text)
    )
    return scored >= 3


# The one tool whose output is not external text: an evaluation card is the
# model's own words coming back to it. Every other tool result is screened, so
# adding a tool that reaches outside needs no thought — and adding one that
# should skip the screen is a visible, reviewable claim made here.
MODEL_AUTHORED_TOOLS = {"record_evaluation"}


def web_research_spec():
    """The ``web_research`` schema.

    A function, not a constant: the description carries today's date, and a
    module-level constant would freeze it at import time — which in a
    long-running Streamlit process means telling the model the wrong day.
    """
    return {
        "name": "web_research",
        "description": (
            "Search the web and return cited fact bullets. Use it "
            "for current information you cannot know: the target "
            "company, up-to-date technologies for the role, "
            "salary data, recent news. CONSENT POLICY: call this "
            "only when the candidate has explicitly asked for web "
            "research, or has just said yes to your offer to "
            "research something. If you believe a search would "
            "help but the candidate has not asked, offer it and "
            "wait for their answer — never search preemptively. "
            "Fuller excerpts from earlier searches may already "
            "appear in your retrieved context; prefer those over "
            "repeating a search. Today's date is "
            f"{date.today():%B %d, %Y} — your own knowledge ends "
            "earlier than that, so never write past years into "
            "the query from memory; ask for what is current or "
            "recent instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A standalone web search query, e.g. "
                        "'Acme Corp engineering culture and recent news'."
                    ),
                },
                "topic": {
                    "type": "string",
                    "enum": WEB_TOPICS,
                    "description": (
                        "Why you are searching. Shown back to you later as "
                        "the label on retrieved excerpts from this research."
                    ),
                },
            },
            "required": ["query", "topic"],
            "additionalProperties": False,
        },
    }


def record_evaluation_spec():
    """The ``record_evaluation`` schema (see ADR-0120)."""
    return {
        "name": "record_evaluation",
        "description": (
            "Record your evaluation of the interview answer you "
            "just scored. Call this EVERY time you give feedback "
            "on an answer, immediately after scoring it, with the "
            "SAME scores and the same strengths-and-gaps feedback "
            "you told the candidate — never different numbers. "
            "Call it once per scored answer; calling again for "
            "the same question replaces that card. Do not call "
            "it outside answer feedback (not during intake, not "
            "for small talk)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The interview question that was answered, "
                        "shortened to one line."
                    ),
                },
                "question_type": {"type": "string", "enum": QUESTION_TYPES},
                "scores": {
                    "type": "object",
                    "description": (
                        "Your 1-5 score per rubric dimension — identical to "
                        "the scores in your reply."
                    ),
                    "properties": {
                        dimension: {
                            "type": "integer",
                            "minimum": EVALUATION_SCORE_MIN,
                            "maximum": EVALUATION_SCORE_MAX,
                        }
                        for dimension in EVALUATION_DIMENSIONS
                    },
                    "required": list(EVALUATION_DIMENSIONS),
                    "additionalProperties": False,
                },
                "verbal_feedback": {
                    "type": "string",
                    "description": (
                        "Your strengths-and-gaps feedback for this answer, "
                        "as markdown — the same content as your chat reply."
                    ),
                },
            },
            "required": [
                "question",
                "question_type",
                "scores",
                "verbal_feedback",
            ],
            "additionalProperties": False,
        },
    }


PENDING_SCREEN = "(pending safety screen — the harness replaces this)"


def validate_evaluation_card(question, question_type, scores, verbal_feedback):
    """Return ``(card, None)`` or ``(None, error_message)``.

    The schema already constrains all of this, but providers enforce JSON
    Schema unevenly — re-validating means a malformed card comes back to the
    model as an error it can correct, instead of poisoning the Evaluations tab.
    """
    if not isinstance(question, str) or not question.strip():
        return None, "error: question must be a non-empty string"
    question = question.strip()
    if not isinstance(verbal_feedback, str):
        return None, "error: verbal_feedback must be a string"
    if question_type not in QUESTION_TYPES:
        question_type = "other"
    if not isinstance(scores, dict):
        return None, "error: scores must be an object of dimension -> integer"
    missing = [d for d in EVALUATION_DIMENSIONS if d not in scores]
    if missing:
        return None, f"error: scores is missing dimensions: {', '.join(missing)}"
    clean_scores = {}
    for dimension in EVALUATION_DIMENSIONS:
        value = scores[dimension]
        # Accept integral floats (4.0): JSON Schema's "integer" does, so
        # rejecting them would fail a schema-conforming call — and models retry
        # such rejections verbatim until the hops run out.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not EVALUATION_SCORE_MIN <= value <= EVALUATION_SCORE_MAX
        ):
            return None, (
                f"error: score for {dimension!r} must be an integer "
                f"between {EVALUATION_SCORE_MIN} and {EVALUATION_SCORE_MAX}"
            )
        clean_scores[dimension] = value
    return {
        "question": question,
        "question_type": question_type,
        "scores": clean_scores,
        "verbal_feedback": verbal_feedback.strip(),
    }, None


def _record_evaluation_tool():
    """The evaluation-card tool: writes graph state, screens nothing.

    The one tool whose output is not external text — an evaluation card is the
    model's own words coming back — so it returns a ``Command`` that updates
    state directly rather than an outcome for the policy to admit.
    """
    spec = record_evaluation_spec()

    def call(question, question_type, scores, verbal_feedback, runtime: ToolRuntime):
        card, error = validate_evaluation_card(
            question, question_type, scores, verbal_feedback
        )
        if error is not None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=error,
                            tool_call_id=runtime.tool_call_id,
                            status="error",
                        )
                    ]
                }
            )
        # Only this call's card: replacing an earlier one for the same question
        # is the reducer's job (``middleware.merge_cards``), because two cards
        # can be recorded in one hop and only the reducer sees both.
        existing = runtime.state.get("evaluations") or []
        note = (
            "evaluation recorded (replaced your previous card for this question)"
            if any(c["question"] == card["question"] for c in existing)
            else "evaluation recorded"
        )
        return Command(
            update={
                "evaluations": [card],
                "messages": [
                    ToolMessage(content=note, tool_call_id=runtime.tool_call_id)
                ],
            }
        )

    return StructuredTool(
        name=spec["name"],
        description=spec["description"],
        args_schema=spec["parameters"],
        func=call,
    )


def _web_research_tool(researcher, cache):
    """The web-research tool: reports a digest, admits nothing itself."""
    spec = web_research_spec()

    def call(query, topic, runtime: ToolRuntime):
        # Type-check before touching str methods: JSON-valid arguments can
        # still be the wrong type, and an AttributeError here would surface as
        # a tool failure rather than something the model can correct.
        if not isinstance(query, str) or not query.strip():
            return PENDING_SCREEN, ToolOutcome.own_output(
                "error: query must be a non-empty string"
            )
        query = query.strip()
        if topic not in WEB_TOPICS:
            topic = "other"

        cached = cache.get(query.lower())
        if cached is not None:
            # Screened when it was first fetched; re-screening would bill a
            # second scan for text already admitted this session.
            return PENDING_SCREEN, ToolOutcome.own_output(cached)

        runtime.stream_writer({"progress": f"Researching the web: {query}…"})
        try:
            result = researcher.research(query)
        except OverBudget as exc:
            # Caught here rather than left to ToolRetryMiddleware, which would
            # retry a *hard* refusal twice and then tell the model the lookup
            # failed because of "a fault in the interview app" (R22.8). It is
            # not a fault and it will not succeed on retry — the cap is the
            # answer, not a transient. This is the guardrail's fail-open
            # wearing a different costume: a refusal reaching a generic
            # handler and coming back out as a degraded answer.
            #
            # The turn itself continues. The cap was cleared when the turn
            # started; what ran out is the budget for an *extra* paid
            # sub-completion, and R22.7 accepts the turn in flight.
            return PENDING_SCREEN, ToolOutcome.own_output(
                "error: web research was refused because the spend limit for "
                "this account has been reached. Do not retry it. Tell the "
                "candidate plainly that you could not search the web, and "
                "continue the interview without it.",
                warning=(query, "budget", str(exc)),
            )
        if result.errored:
            return PENDING_SCREEN, ToolOutcome.own_output(
                "error: web research is unavailable right now",
                warning=(
                    query,
                    "web research",
                    result.error_reason or "the search request failed",
                ),
            )

        document = (
            IngestedDocument(
                name=f"web: {query[:60]}",
                doc_type="web search",
                text=result.raw_text,
                topic=topic,
            )
            if result.raw_text
            else None
        )
        return PENDING_SCREEN, ToolOutcome(
            inline=result.bullets,
            document=document,
            kind="web",
            label=query,
            blocked_as="web research blocked",
            document_blocked_as="web research not indexed",
            blocked_message=(
                "error: the research results were withheld by a safety screen; "
                "tell the candidate web research is unavailable for this query"
            ),
            # The sub-completion ran and was billed whatever the screen decides.
            billed_cost=result.cost or 0.0,
            # Sources reach the panel only if the digest itself is admitted.
            state_update={"citations": list(result.citations)},
            on_admitted=lambda: cache.__setitem__(query.lower(), result.bullets),
        )

    return StructuredTool(
        name=spec["name"],
        description=spec["description"],
        args_schema=spec["parameters"],
        func=call,
        response_format="content_and_artifact",
    )


def _mcp_tools(mcp):
    """One LangChain tool per discovered MCP tool.

    This adapter earns its keep, unlike the dispatcher it replaced: these tools
    do not exist until ``tools/list`` has run, and their schemas are the
    server's, not ours. Each wrapper does nothing but relay and describe —
    a file body becomes a document for the policy to admit, and a listing goes
    inline but is still screened, since a repository the candidate does not
    control can put an instruction in a file *name*.
    """
    return [_one_mcp_tool(mcp, spec["function"]) for spec in mcp.specs]


def _one_mcp_tool(mcp, function):
    """One relaying tool. A function, so each closes over its own name."""
    name = function["name"]

    def call(runtime: ToolRuntime, **arguments):
        runtime.stream_writer({"progress": f"Checking GitHub: {name}…"})
        # Deliberately NOT wrapped: a transport failure has to reach
        # ToolRetryMiddleware to be retried, and catching it here made a
        # transient blip permanent (one attempt instead of three). The
        # never-raise contract is kept by that middleware's on_failure, not by
        # this function.
        return PENDING_SCREEN, _describe_mcp_result(mcp.call(name, arguments), name)

    return StructuredTool(
        name=name,
        description=function.get("description", ""),
        # The server's JSON Schema, forwarded unchanged.
        args_schema=function.get("parameters")
        or {"type": "object", "properties": {}},
        func=call,
        response_format="content_and_artifact",
    )


def _describe_mcp_result(result, name):
    """Say what one relayed result *is*; the policy decides what happens to it."""
    if result.errored:
        # The server's own error message, relayed — our text, not a repo's.
        return ToolOutcome.own_output(result.text)
    if not result.file_text:
        return ToolOutcome(
            inline=result.text,
            kind="code",
            label=result.source or name,
            blocked_as="github listing blocked",
            blocked_message=(
                "error: that listing was withheld by a safety screen; tell "
                "the candidate you could not browse it and ask them to name "
                "a file"
            ),
            terms=tuple(result.terms),
        )
    # inline=None: the model's excerpt will be a slice of this document, so one
    # scan governs both and failing it withholds everything.
    return ToolOutcome(
        document=IngestedDocument(
            name=result.source, doc_type="github", text=result.file_text
        ),
        kind="code",
        label=result.source,
        blocked_as="github file blocked",
        blocked_message=(
            "error: that file was withheld by a safety screen; tell the "
            "candidate you could not read it and ask about another file"
        ),
        terms=tuple(result.terms),
    )


def build_tools(researcher, cache, mcp=None):
    """Every tool the interviewer gets this turn, as LangChain tools.

    Dependencies are closed over rather than passed through
    ``create_agent(context_schema=...)``: the agent is rebuilt each turn, so
    context would add a schema without removing a closure — the MCP tools have
    to close over their client either way, since they do not exist until
    discovery has run.
    """
    tools = [_web_research_tool(researcher, cache), _record_evaluation_tool()]
    if mcp is None:
        return tools
    # A remote tool shadowing a local name would make the model's choice
    # ambiguous; the local tool wins, as it did under the dispatcher.
    local_names = {tool.name for tool in tools}
    return tools + [t for t in _mcp_tools(mcp) if t.name not in local_names]
