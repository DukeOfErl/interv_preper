"""The LangChain agent loop, held to the same contract as the hand-rolled one.

Driven by a scripted fake chat model so the assertions are about *our* loop —
budget, preamble handling, typed-call correction, spend accounting — and not
about a live model's mood.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from interview_prep.agent import TOOL_CALL_BUDGET, AgentLLM
from interview_prep.guardrails import GuardrailResult
from interview_prep.tools import ToolBox

ALLOWED = GuardrailResult(allowed=True)


class ScriptedModel(BaseChatModel):
    """Replays canned AIMessages, one per model call, recording its prompts.

    Not ``GenericFakeChatModel``: that one streams by chunking message
    *content*, so a tool-call message — which carries no content — raises "No
    generations found in stream". Returning whole messages also keeps
    ``tool_calls``, ``response_metadata`` and ``usage_metadata`` intact, which
    the loop reads for spend accounting.
    """

    replies: list = Field(default_factory=list)
    prompts: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(list(messages))
        message = self.replies.pop(0)
        return ChatResult(generations=[ChatGeneration(message=message)])


def scripted(*replies):
    return ScriptedModel(replies=list(replies))


class FakeMCP:
    def __init__(self, reply="listing"):
        self.specs = [
            {
                "type": "function",
                "function": {
                    "name": "get_file_contents",
                    "description": "read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "path": {"type": "string"},
                        },
                    },
                },
            }
        ]
        self._reply = reply
        self.calls = []

    @property
    def tool_names(self):
        return {"get_file_contents"}

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        from interview_prep.github_mcp import MCPResult

        return MCPResult(text=self._reply)


def make_toolbox(**kwargs):
    return ToolBox(
        researcher=SimpleNamespace(research=lambda q: None),
        guard=SimpleNamespace(check_document=lambda text, kind="document": ALLOWED),
        index=SimpleNamespace(add_document=lambda doc: 1),
        mcp=FakeMCP(),
        **kwargs,
    )


def call_message(path="src/core.py"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_file_contents",
                "args": {"owner": "o", "repo": "r", "path": path},
                "id": "call_1",
            }
        ],
    )


def run(llm, toolbox, prompt="tell me about my repo"):
    return "".join(
        llm.stream_reply("sys", [{"role": "user", "content": prompt}], toolbox=toolbox)
    )


# --- the contract ----------------------------------------------------------


def test_plain_answer_streams_and_is_the_answer():
    llm = AgentLLM(api_key="k", model="m", chat_model=scripted(AIMessage("Hello.")))
    out = run(llm, make_toolbox())
    assert out == "Hello."
    assert llm.answer_text == "Hello." and llm.answered
    assert llm.last_tool_calls == []


def test_tool_call_round_trip_reaches_the_toolbox():
    box = make_toolbox()
    llm = AgentLLM(
        api_key="k",
        model="m",
        chat_model=scripted(call_message(), AIMessage("Your parser is neat.")),
    )
    out = run(llm, box)

    assert box._mcp.calls, "the toolbox dispatcher was bypassed"
    name, arguments = box._mcp.calls[0]
    assert name == "get_file_contents"
    # ToolBox parses the JSON before dispatching, so the MCP client sees a dict.
    assert arguments["path"] == "src/core.py"
    assert out.endswith("Your parser is neat.")
    assert llm.answer_text == "Your parser is neat."


def test_tool_calls_are_recorded_for_the_developer_panel():
    """The (payload, response) log is what makes harness-first triage possible."""
    box = make_toolbox()
    llm = AgentLLM(
        api_key="k", model="m", chat_model=scripted(call_message(), AIMessage("done"))
    )
    run(llm, box)
    assert [c["name"] for c in llm.last_tool_calls] == ["get_file_contents"]
    assert "src/core.py" in llm.last_tool_calls[0]["arguments"]
    assert llm.last_tool_calls[0]["result"]


def test_preamble_from_a_tool_calling_turn_is_not_the_answer():
    """Narration before a tool call streams, but must not be stored.

    This is where raw tool-call JSON lands when a model misbehaves, so the
    distinction is a correctness property, not tidiness.
    """
    preamble = AIMessage(
        content="I will read the file next.",
        tool_calls=[
            {"name": "get_file_contents", "args": {"path": "a.py"}, "id": "c1"}
        ],
    )
    llm = AgentLLM(
        api_key="k",
        model="m",
        chat_model=scripted(preamble, AIMessage("Real question about it?")),
    )
    out = run(llm, make_toolbox())

    assert "I will read the file next." in out, "preamble should still stream"
    assert llm.answer_text == "Real question about it?"


def test_a_typed_tool_call_is_corrected_rather_than_answered():
    typed = AIMessage(
        content='{"owner": "o", "repo": "r", "path": "src/core.py"}'
    )
    llm = AgentLLM(
        api_key="k",
        model="m",
        chat_model=scripted(typed, AIMessage("Which file should I open?")),
    )
    run(llm, make_toolbox())
    assert llm.typed_tool_call_retries == 1
    assert llm.answer_text == "Which file should I open?"
    assert "{" not in llm.answer_text


def test_exhausted_budget_announces_itself_before_the_forced_answer():
    """The anti-squeeze, at loop level.

    After the budget is spent the model is handed a note naming what the turn
    obtained, instead of being asked for an answer with tools silently gone.

    Asserted against the prompt the model actually received: that the toolbox
    *has* a note to offer is a fact about the toolbox, and stays true while the
    loop quietly fails to deliver it — which is the bug this guards.
    """
    box = make_toolbox()
    replies = [call_message(f"f{i}.py") for i in range(TOOL_CALL_BUDGET)]
    replies.append(AIMessage("I could not read your code — which file?"))
    model = scripted(*replies)
    llm = AgentLLM(api_key="k", model="m", chat_model=model)
    run(llm, box)

    assert len(box._mcp.calls) == TOOL_CALL_BUDGET
    notes = [
        m.text
        for m in model.prompts[-1]
        if isinstance(m, SystemMessage) and "SYSTEM NOTE" in m.text
    ]
    assert len(notes) == 1, "the note must reach the model, not merely exist"
    assert llm.answered


def test_spend_and_reasoning_tokens_accumulate_across_hops():
    def priced(text, cost, reasoning, finish="stop", **kw):
        return AIMessage(
            content=text,
            response_metadata={
                "finish_reason": finish,
                "token_usage": {"cost": cost},
            },
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "output_token_details": {"reasoning": reasoning},
            },
            **kw,
        )

    hop1 = priced(
        "",
        0.001,
        7,
        finish="tool_calls",
        tool_calls=[{"name": "get_file_contents", "args": {"path": "a"}, "id": "c1"}],
    )
    hop2 = priced("Answer.", 0.002, 7)
    llm = AgentLLM(api_key="k", model="m", chat_model=scripted(hop1, hop2))
    run(llm, make_toolbox())

    assert llm.last_cost == 0.003
    assert llm.last_reasoning_tokens == 14
    assert llm.answer_text == "Answer."
    # The caller's cost fallback reads these attribute names off last_usage.
    # LangChain reports a dict keyed input_tokens/output_tokens, so leaving it
    # unnormalised raises AttributeError — but only when OpenRouter reports no
    # cost, which is why the swap looks fine right up until it isn't.
    assert llm.last_usage.prompt_tokens == 10
    assert llm.last_usage.completion_tokens == 5
