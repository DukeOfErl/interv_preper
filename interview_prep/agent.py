"""The tool-calling loop as a LangChain agent, instead of hand-rolled.

Same job as ``llm.InterviewLLM.stream_reply`` and deliberately the same
surface — a generator of text tokens plus ``answer_text`` / ``answered`` /
``last_cost`` / ``last_tool_calls`` read after it is consumed — so ``chat_bot``
swaps one line and both implementations answer to one contract (and to one
fault-injection suite).

Why this exists at all: the hand-rolled loop is not broken. The value is that
every anti-fabrication mitigation we derived the hard way turns out to have a
named home here, which makes them legible instead of buried in a for-loop:

    hop budget              ToolCallLimitMiddleware
    "your tools are gone"   @before_model
    typed tool call         @after_model
    bounded tool retry      ToolRetryMiddleware
    tool-call logging       @wrap_tool_call
    preamble ≠ answer       message roles in graph state (free)

``create_agent`` returns a compiled LangGraph ``StateGraph``; LangChain 1.x has
no separate agent runtime, so this is the graph with its routing prebuilt.

One trap worth naming: ``ToolCallLimitMiddleware``'s default ``exit_behavior``
is ``"continue"``, which blocks the exhausted tool and lets the model keep
talking — the exact forced-answer-with-no-tools state that produced fabricated
repositories in testing. We keep ``"continue"`` deliberately (a canned stop
would lose the closing answer) and pair it with the ``@before_model`` note that
tells the model its tools are gone and what it actually obtained.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    after_model,
    before_model,
    wrap_tool_call,
)
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from .config import MAX_TOOL_HOPS, OPENROUTER_BASE_URL, TYPING_DELAY_SECONDS
from .llm import looks_like_typed_tool_call

# The loop offers tools on every hop but the last; the limit middleware counts
# calls rather than hops, so this is the budget in the units it uses.
TOOL_CALL_BUDGET = MAX_TOOL_HOPS - 1

TYPED_CALL_CORRECTION = (
    "SYSTEM NOTE: that reply contained a tool call written as text. Text is "
    "never delivered to a tool. Request the tool properly through the tool "
    "interface, or, if you do not need one, answer the candidate in plain "
    "prose with no JSON."
)


def _tools_from(toolbox):
    """Adapt ``ToolBox``'s OpenAI-shape specs into LangChain tools.

    The toolbox keeps its dispatcher: every call still goes through
    ``ToolBox.run(name, json_arguments)``, which is where screening, indexing,
    provenance recording and the never-raise contract live. Only the loop is
    being replaced, so none of that is reimplemented here.
    """
    tools = []
    for spec in toolbox.specs:
        function = spec["function"]
        name = function["name"]

        def _call(_name=name, **kwargs):
            return toolbox.run(_name, json.dumps(kwargs))

        tools.append(
            StructuredTool(
                name=name,
                description=function.get("description", ""),
                # A raw JSON Schema is accepted directly — the discovered MCP
                # schemas need no Pydantic translation.
                args_schema=function.get("parameters")
                or {"type": "object", "properties": {}},
                func=_call,
            )
        )
    return tools


class AgentLLM:
    """Drop-in replacement for ``InterviewLLM`` backed by ``create_agent``."""

    def __init__(
        self,
        api_key,
        model,
        reasoning_effort=None,
        base_url=OPENROUTER_BASE_URL,
        typing_delay=TYPING_DELAY_SECONDS,
        chat_model=None,
    ):
        self.model = model
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
        self.last_reasoning_tokens = None
        self.last_tool_calls = []
        self.answer_text = ""
        self.answered = False
        self.typed_tool_call_retries = 0
        # Message ids middleware rejected — never part of the stored answer.
        self._discarded = set()

    def _middleware(self, toolbox):
        recorder = self

        @wrap_tool_call
        def log_tool_calls(request, handler):
            """Every (tool payload, final response) pair, for the UI and §1.

            The single artefact that separates "the model lied" from "the tool
            broke", so it is wrapped rather than inferred from the stream.
            """
            response = handler(request)
            recorder.last_tool_calls.append(
                {
                    "name": request.tool_call.get("name"),
                    "arguments": json.dumps(request.tool_call.get("args", {})),
                    "result": getattr(response, "content", str(response)),
                }
            )
            return response

        @before_model
        def announce_exhausted_tools(state, runtime):
            """Tell a cornered model that its tools are gone, and what it got.

            Without this the model must answer, cannot fetch, and invents. The
            note is the toolbox's to write — it knows which files were read.
            """
            used = sum(
                len(m.tool_calls)
                for m in state["messages"]
                if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
            )
            if used < TOOL_CALL_BUDGET:
                return None
            note = toolbox.final_hop_note()
            return {"messages": [SystemMessage(note)]} if note else None

        @after_model(can_jump_to=["model"])
        def catch_typed_tool_call(state, runtime):
            """A call written as prose is not an answer; correct and retry.

            Native tool calling has no observation marker to stop on, so the
            ReAct-era fix does not apply and the harness has to detect it.

            In the hand-rolled loop the retry was a ``continue`` statement. In
            a graph it is an edge, and an edge only exists if it is declared
            (``can_jump_to``) and taken (``jump_to``) — otherwise this hook
            appends its correction to a run that has already ended.
            """
            message = state["messages"][-1]
            if not isinstance(message, AIMessage) or getattr(
                message, "tool_calls", None
            ):
                return None
            if not looks_like_typed_tool_call(message.text, toolbox.specs):
                return None
            recorder.typed_tool_call_retries += 1
            # The stream accumulator has this message open and would otherwise
            # store it as the answer; the id is how the two views of the run
            # agree that it was rejected.
            recorder._discarded.add(message.id)
            return {
                "messages": [SystemMessage(TYPED_CALL_CORRECTION)],
                "jump_to": "model",
            }

        return [
            log_tool_calls,
            announce_exhausted_tools,
            catch_typed_tool_call,
            ToolRetryMiddleware(max_retries=2),
            # See the module docstring: "continue" is the squeeze, and is
            # survivable only because of announce_exhausted_tools above.
            ToolCallLimitMiddleware(
                run_limit=TOOL_CALL_BUDGET, exit_behavior="continue"
            ),
        ]

    def stream_reply(self, system_prompt, messages, toolbox=None):
        """Yield the reply token-by-token; hops stay invisible to the caller."""
        self._reset()
        agent = create_agent(
            model=self._chat_model,
            tools=_tools_from(toolbox) if toolbox else [],
            system_prompt=system_prompt,
            middleware=self._middleware(toolbox) if toolbox else [],
        )
        conversation = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]

        # Chunks are grouped by message id rather than by a provider's
        # finish_reason, which not every provider sends: one assistant message
        # may arrive as many chunks sharing an id (real streaming) or as one
        # (a non-streaming model). Text from a message that also requested
        # tools is preamble — streamed so the UI stays alive, but never stored
        # as the answer, since that is where narration and stray tool-call
        # JSON land.
        message_id, parts, requested_tools = None, [], False

        def close_message():
            if message_id is None or requested_tools:
                return
            if message_id in self._discarded:
                return
            self.answer_text += "".join(parts)
            self.answered = True

        for chunk, _meta in agent.stream(
            {"messages": conversation}, stream_mode="messages"
        ):
            if not isinstance(chunk, AIMessage):
                continue
            if chunk.id != message_id:
                close_message()
                message_id, parts, requested_tools = chunk.id, [], False
            self._record_usage(chunk)
            if getattr(chunk, "tool_calls", None) or getattr(
                chunk, "tool_call_chunks", None
            ):
                requested_tools = True
            text = chunk.text
            if text:
                parts.append(text)
                yield text
        close_message()

    def _record_usage(self, chunk):
        """Accumulate spend across hops, as the hand-rolled loop does."""
        usage = getattr(chunk, "usage_metadata", None)
        if usage:
            # Normalised to the OpenAI SDK's attribute names, which is what
            # the caller's cost fallback reads. LangChain reports a dict keyed
            # input_tokens/output_tokens; left as-is this raises AttributeError
            # mid-turn, and only on the path where OpenRouter reported no cost
            # — the one least likely to come up in manual testing.
            self.last_usage = SimpleNamespace(
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
