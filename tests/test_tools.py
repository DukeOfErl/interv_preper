"""Tests for the tool-calling loop in ``InterviewLLM`` and ``ToolBox``.

The loop is exercised against a fake chat-completions client that replays
hand-built chunk sequences, since the interesting behavior (reassembling
fragmented tool-call deltas, replaying the request, feeding results back) all
happens before any network call would matter.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from interview_prep.config import MAX_TOOL_HOPS
from interview_prep.llm import InterviewLLM
from interview_prep.tools import ToolBox


def text_chunk(text):
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def tool_chunk(index, id=None, name=None, arguments=None):
    """One fragment of a streamed tool call, as the provider sends them."""
    call = SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    delta = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def usage_chunk(cost):
    """A usage-only final chunk — it carries no choices at all."""
    usage = SimpleNamespace(
        cost=cost,
        model_extra=None,
        prompt_tokens=10,
        completion_tokens=5,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=7),
    )
    return SimpleNamespace(choices=[], usage=usage)


class FakeCompletions:
    """Replays one canned chunk sequence per call, recording the requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return iter(self._responses.pop(0))


def build_llm(responses):
    llm = InterviewLLM(api_key="test", model="test-model", typing_delay=0)
    completions = FakeCompletions(responses)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


def test_streams_plain_text_when_no_tool_is_called():
    llm, completions = build_llm([[text_chunk("Hello"), text_chunk(" there")]])
    out = "".join(llm.stream_reply("sys", [], toolbox=ToolBox(started_at=0.0)))
    assert out == "Hello there"
    assert llm.last_tool_calls == []
    assert len(completions.requests) == 1


def test_tool_call_round_trip_feeds_the_result_back():
    responses = [
        # Hop 1: the model requests the tool, in fragments.
        [
            tool_chunk(0, id="call_1", name="get_elapsed_time"),
            tool_chunk(0, arguments="{"),
            tool_chunk(0, arguments="}"),
            usage_chunk(0.001),
        ],
        # Hop 2: with the result in hand, it answers.
        [text_chunk("We are 3 minutes in."), usage_chunk(0.002)],
    ]
    llm, completions = build_llm(responses)
    out = "".join(llm.stream_reply("sys", [], toolbox=ToolBox(started_at=0.0)))

    # The caller sees a flat token stream — the hop is invisible.
    assert out == "We are 3 minutes in."

    # Two API calls, and the second replays the request plus the result.
    assert len(completions.requests) == 2
    replayed = completions.requests[1]["messages"]
    assert replayed[-2]["role"] == "assistant"
    assert replayed[-2]["tool_calls"][0]["id"] == "call_1"
    assert replayed[-1]["role"] == "tool"
    assert replayed[-1]["tool_call_id"] == "call_1"
    assert "elapsed_minutes" in replayed[-1]["content"]

    # Cost and reasoning tokens are summed across both calls, not overwritten.
    assert llm.last_cost == pytest.approx(0.003)
    assert llm.last_reasoning_tokens == 14
    assert [c["name"] for c in llm.last_tool_calls] == ["get_elapsed_time"]


def test_interleaved_fragments_reassemble_per_index():
    """Two concurrent calls: index is the only thing tying fragments together."""
    responses = [
        [
            tool_chunk(0, id="a", name="get_elapsed_time"),
            tool_chunk(1, id="b", name="get_elapsed_time"),
            tool_chunk(0, arguments='{"x":'),
            tool_chunk(1, arguments='{"y":'),
            tool_chunk(0, arguments=" 1}"),
            tool_chunk(1, arguments=" 2}"),
        ],
        [text_chunk("done")],
    ]
    llm, completions = build_llm(responses)
    "".join(llm.stream_reply("sys", [], toolbox=ToolBox(started_at=0.0)))
    requested = completions.requests[1]["messages"][-3]["tool_calls"]
    assert [c["id"] for c in requested] == ["a", "b"]
    assert [c["function"]["arguments"] for c in requested] == ['{"x": 1}', '{"y": 2}']


def test_final_hop_withholds_tools_to_force_an_answer():
    """A model that only ever calls tools must still end on a text request."""
    tool_response = [tool_chunk(0, id="c", name="get_elapsed_time", arguments="{}")]
    llm, completions = build_llm([list(tool_response) for _ in range(MAX_TOOL_HOPS)])
    "".join(llm.stream_reply("sys", [], toolbox=ToolBox(started_at=0.0)))
    assert len(completions.requests) == MAX_TOOL_HOPS
    assert "tools" in completions.requests[0]
    assert "tools" not in completions.requests[-1]


def test_no_toolbox_never_offers_tools():
    llm, completions = build_llm([[text_chunk("hi")]])
    "".join(llm.stream_reply("sys", []))
    assert "tools" not in completions.requests[0]


def test_toolbox_reports_elapsed_time():
    box = ToolBox(started_at=0.0)
    result = json.loads(box.run("get_elapsed_time", "{}"))
    assert result["elapsed_minutes"] >= 0
    assert "since the interview began" in result["human"]


def test_toolbox_returns_errors_instead_of_raising():
    box = ToolBox(started_at=0.0)
    assert "not valid JSON" in box.run("get_elapsed_time", "{oops")
    assert "no tool named" in box.run("nonexistent", "{}")
    # A hallucinated argument must come back as text, not raise out of the loop.
    assert "error" in box.run("get_elapsed_time", '{"invented": 1}')
