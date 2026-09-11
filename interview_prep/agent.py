"""The interviewer's turn, as a LangChain agent.

``create_agent`` returns a compiled LangGraph; LangChain 1.x has no separate
agent runtime, so this is the graph with its routing prebuilt. This module is
deliberately thin — the tools live in ``tools.py``, the harness in
``middleware.py``, and the admission rules in ``policy.py``. What is left here
is the model, the stream, and the accounting.

The stream asks for three projections at once. ``messages`` drives the
typewriter, ``custom`` carries progress a tool emitted through
``runtime.stream_writer`` (which arrives on the *consuming* thread — tools
themselves run on a worker thread, and reaching sideways into Streamlit from
there is what once reported three imaginary GitHub failures), and ``values``
hands back the final state, so the reply is read from the graph rather than
reconstructed by grouping chunks.
"""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI

# `Unauthorized` is re-exported: it is raised by six clients, so it lives in
# the pure module rather than here, but callers still import it from the
# agent they were refused by.
from .authorization import Identity, Unauthorized, require_authorized  # noqa: F401
from .config import OPENROUTER_BASE_URL, TYPING_DELAY_SECONDS
from .context import estimate_prompt_tokens, estimate_text_tokens
from .pricing import billing, price_of, turn_cost
from .middleware import (
    TOOL_CALL_BUDGET,
    InterviewState,
    announce_exhausted_tools,
    catch_typed_tool_call,
    content_policy_middleware,
)
from .spend import UNCAPPED, resolve_budget


def tool_failure_message(exc):
    """What the model is told when a tool keeps failing.

    Replaces the framework's default wording, which names the exception class
    and invites a retry the budget may not have. The distinction that matters
    to a model deciding what to say next is *ours or theirs*, and it cannot
    tell from a stack trace.
    """
    return (
        f"error: that tool failed repeatedly ({exc}). This is a fault in the "
        "interview app or the service it called, not something the candidate "
        "did. Do not retry it. Say plainly that you could not complete the "
        "lookup, and continue the interview without it."
    )


class InterviewAgent:
    """One interviewer, driving one turn at a time."""

    def __init__(
        self,
        api_key,
        model,
        reasoning_effort=None,
        base_url=OPENROUTER_BASE_URL,
        typing_delay=TYPING_DELAY_SECONDS,
        chat_model=None,
        identity=None,
        budget=None,
    ):
        self.model = model
        # Kept for the R22.3 pricing ladder below, which needs the catalog when
        # OpenRouter reports no cost for a turn.
        self._api_key = api_key
        # The proof of authorization (R21.13). Kept as given rather than
        # coerced: `stream_reply` refuses anything that is not an `Identity`
        # saying it is authorized, so a truthy stand-in cannot be mistaken for
        # one. Defaults to None so a caller that never heard of authorization
        # fails closed instead of spending.
        self.identity = identity
        # Like the identity, and refused in the same place: the turn, not the
        # construction (R22.5). A real model with no budget named is refused
        # outright rather than silently uncapped — see `resolve_budget`: the
        # arming must not be the caller's to forget. An injected model spends
        # nothing, so a test needs no budget.
        if chat_model is None:
            budget = resolve_budget(budget, "this turn")
        self.budget = budget or UNCAPPED
        self.reasoning_effort = reasoning_effort
        self.typing_delay = typing_delay
        extra_body = {"usage": {"include": True}}
        if reasoning_effort:
            extra_body["reasoning"] = {"effort": reasoning_effort}
        # Injectable so tests can drive a scripted model.
        self._chat_model = chat_model or ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            # Explicitly, not via model_kwargs: passed the other way the
            # request still succeeds but OpenRouter's reported cost never
            # reaches response_metadata, and spend silently becomes an
            # estimate.
            extra_body=extra_body,
        )
        self._reset()

    def _reset(self):
        self.last_usage = None
        self.last_cost = None
        #: Tokens from hops that billed but reported no cost of their own.
        #: A turn is one model call per hop and each bills separately, so a
        #: turn where *some* hops report a cost and others do not is the
        #: under-counting case: taking `last_cost` alone silently prices the
        #: rest at nothing. Kept apart from `last_usage`, which holds only the
        #: final hop, so these can be priced and added rather than replaced.
        self._unpriced_prompt_tokens = 0
        self._unpriced_completion_tokens = 0
        #: What this turn cost, down R22.3's ladder — the figure recorded
        #: against the ledger and displayed by the page. None until a turn ends.
        self.turn_cost_usd = None
        self.last_reasoning_tokens = None
        self.last_tool_calls = []
        self.answer_text = ""
        self.answered = False
        self.final_state = {}

    def _middleware(self, policy, tools, mcp_names, seen_terms, code_corpus, on_warning):
        return [
            content_policy_middleware(
                policy,
                mcp_names=mcp_names,
                seen_terms=seen_terms,
                code_corpus=code_corpus,
                on_warning=on_warning,
            ),
            announce_exhausted_tools,
            catch_typed_tool_call(tools),
            ToolRetryMiddleware(max_retries=2, on_failure=tool_failure_message),
            # "continue" blocks the exhausted tool and lets the model keep
            # talking — survivable only because announce_exhausted_tools tells
            # it what it actually obtained. Also the only option that runs:
            # "end" raises NotImplementedError when the exhausting message
            # carries other pending tool calls, which a batching model produces
            # routinely, and "error" aborts the turn (ADR-0170).
            ToolCallLimitMiddleware(
                run_limit=TOOL_CALL_BUDGET, exit_behavior="continue"
            ),
        ]

    def _require_authorized(self):
        """Refuse a turn without an authorized identity (R21.12, R21.13).

        Delegates to the shared guard so the wording and the `isinstance` rule
        are identical across all six paid clients. Unlike the other five, this
        one fires on the *turn* rather than in `__init__`: constructing an
        agent costs nothing, `create_agent` and the stream are what spend.
        """
        require_authorized(self.identity, "this turn")

    def stream_reply(
        self,
        system_prompt,
        messages,
        tools=(),
        policy=None,
        mcp_names=(),
        seen_terms=None,
        code_corpus=None,
        on_warning=None,
        on_progress=None,
    ):
        """Yield the reply token by token; hops stay invisible to the caller.

        ``on_progress`` is called from *this* thread as the stream is consumed,
        so a Streamlit caller can paint into its own slot safely.

        Not itself a generator: the authorization check has to run when this is
        *called*, not when the first token is pulled. Otherwise a caller that
        builds the stream and abandons it would appear guarded while only a
        caller that iterates is actually checked.
        """
        self._require_authorized()
        # Money before tokens: the cap is checked once per turn, against the
        # next-prompt estimate the caller put in the budget (R22.6), and here
        # rather than in the page because `evals/` and `tests/` reach this
        # method without passing through `chat_bot.py`.
        self.budget.require(self.identity)
        return self._stream_reply(
            system_prompt,
            messages,
            tools=tools,
            policy=policy,
            mcp_names=mcp_names,
            seen_terms=seen_terms,
            code_corpus=code_corpus,
            on_warning=on_warning,
            on_progress=on_progress,
        )

    def _stream_reply(
        self,
        system_prompt,
        messages,
        tools=(),
        policy=None,
        mcp_names=(),
        seen_terms=None,
        code_corpus=None,
        on_warning=None,
        on_progress=None,
    ):
        self._reset()
        agent = create_agent(
            model=self._chat_model,
            tools=list(tools),
            system_prompt=system_prompt,
            state_schema=InterviewState,
            middleware=(
                self._middleware(
                    policy, tools, mcp_names, seen_terms, code_corpus, on_warning
                )
                if tools
                else []
            ),
        )
        conversation = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]

        # `finally`, not a trailing statement, and this is the whole of R22.4
        # for the biggest number in the turn. The page `break`s out of this
        # generator the moment the guardrail returns a jailbreak verdict
        # (chat_bot polls the future between tokens), and an APIError mid-hop
        # propagates through this frame — in both cases a trailing statement is
        # skipped while the hops that already ran have been billed. Abandoning
        # the generator drops its refcount, `close()` raises `GeneratorExit` at
        # the `yield`, and a `finally` still runs. Measured before the fix:
        # $0.42 spent, $0.00 recorded.
        #
        # What that means for *when* the accrual lands: on generator
        # finalisation. For the page that is immediate — its `for` loop holds
        # the only reference and drops it on the `break` — but a caller that
        # keeps the generator in a variable defers the recording until that
        # variable does. Nothing in this app does; a future driver that did
        # would see the spend recorded late, not lost.
        try:
            for mode, chunk in agent.stream(
                {"messages": conversation},
                stream_mode=["messages", "custom", "values"],
            ):
                if mode == "custom":
                    if on_progress and isinstance(chunk, dict) and "progress" in chunk:
                        on_progress(chunk["progress"])
                    continue
                if mode == "values":
                    self.final_state = chunk
                    continue
                message, _meta = chunk
                if not isinstance(message, AIMessage):
                    continue
                self._record_usage(message)
                text = message.text
                if text:
                    yield text
        finally:
            self._finish(system_prompt, messages)

    def _turn_cost(self, system_prompt, messages):
        """This turn's cost, down R22.3's ladder: actual, reported, estimated.

        The whole ladder lives here rather than half here and half in the page,
        because only this object is reachable from every exit path — a turn the
        guardrail blocks never returns to the page's accounting at all, and a
        turn OpenRouter reported no cost for would then record nothing.

        The catalog lookup is skipped when nothing is counting: it is a network
        round trip, and dozens of tests drive this agent with a scripted model
        that never spends a cent.
        """
        unpriced = self._unpriced_prompt_tokens or self._unpriced_completion_tokens
        if self.last_cost is not None and not unpriced:
            return self.last_cost
        if not self.budget.counts:
            return self.last_cost
        pricing = price_of(self.model, self._api_key)
        if self.last_cost is not None:
            # Some hops reported, some did not. Charge what was reported and
            # price the rest from their own tokens (R22.3's second rung),
            # rather than letting the reported figure stand for the whole turn.
            return self.last_cost + turn_cost(
                pricing,
                self._unpriced_prompt_tokens,
                self._unpriced_completion_tokens,
            )
        if self.last_usage is not None:
            return turn_cost(
                pricing,
                self.last_usage.prompt_tokens or 0,
                self.last_usage.completion_tokens or 0,
            )
        return turn_cost(
            pricing,
            estimate_prompt_tokens(system_prompt, messages),
            estimate_text_tokens(self.answer_text),
        )

    def _finish(self, system_prompt="", messages=()):
        """Take the answer and the turn's bookkeeping from the final state.

        The reply is the last assistant message that asked for no tools —
        earlier ones are preamble ("let me read that file"), which is where a
        misbehaving model writes raw tool-call JSON, and must be streamed but
        never stored.
        """
        state_messages = self.final_state.get("messages") or []
        for message in reversed(state_messages):
            if isinstance(message, AIMessage) and not getattr(
                message, "tool_calls", None
            ):
                self.answer_text = message.text
                self.answered = True
                break
        self.last_tool_calls = _tool_call_log(state_messages)
        # Recorded by the operation that spent it, on every exit path. Tool
        # sub-completions (`extra_cost`) are deliberately not added: the web
        # researcher records its own, and adding it twice would cap everyone at
        # half their budget.
        #
        # Guarded, and here more than anywhere: this runs in a `finally`, so an
        # exception raised while pricing the turn would replace whatever was
        # already propagating — including the `GeneratorExit` that a blocked
        # turn abandons the stream with.
        with billing(self.model):
            self.turn_cost_usd = self._turn_cost(system_prompt, messages)
            self.budget.record(self.identity, self.turn_cost_usd or 0.0)

    def _record_usage(self, chunk):
        """Accumulate spend across hops, as every hop bills separately."""
        usage = getattr(chunk, "usage_metadata", None)
        if usage:
            # Normalised to the OpenAI SDK's attribute names, which is what the
            # caller's cost fallback reads.
            self.last_usage = _Usage(
                prompt_tokens=usage.get("input_tokens") or 0,
                completion_tokens=usage.get("output_tokens") or 0,
            )
            reasoning = (usage.get("output_token_details") or {}).get("reasoning")
            if reasoning:
                self.last_reasoning_tokens = (
                    self.last_reasoning_tokens or 0
                ) + reasoning
        token_usage = (chunk.response_metadata or {}).get("token_usage") or {}
        cost = token_usage.get("cost")
        if cost is not None:
            self.last_cost = (self.last_cost or 0.0) + cost
        elif usage:
            # This hop billed and said nothing about what it cost. Its tokens
            # are remembered separately so `_turn_cost` can price them and add
            # them to the hops that did report — rather than the ladder seeing
            # a non-None `last_cost` and stopping on the first rung, which
            # charges a multi-hop turn for whichever hops happened to be
            # chatty about their billing.
            self._unpriced_prompt_tokens += usage.get("input_tokens") or 0
            self._unpriced_completion_tokens += usage.get("output_tokens") or 0

    @property
    def extra_cost(self):
        """USD spent by tool sub-completions this turn."""
        return self.final_state.get("extra_cost") or 0.0

    @property
    def evaluations(self):
        return self.final_state.get("evaluations") or []

    @property
    def citations(self):
        return self.final_state.get("citations") or []

    @property
    def mcp_calls(self):
        return self.final_state.get("mcp_calls") or 0


class _Usage:
    """The two token counts the caller's pricing fallback reads."""

    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


def _tool_call_log(messages):
    """Every (tool payload, final response) pair, for the developer panel.

    The single artefact that separates "the model lied" from "the tool broke",
    so it is rebuilt from the transcript rather than inferred from the stream.
    """
    results = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }
    log = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            result = results.get(call.get("id"))
            log.append(
                {
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("args", {})),
                    "result": getattr(result, "content", "") if result else "",
                }
            )
    return log
