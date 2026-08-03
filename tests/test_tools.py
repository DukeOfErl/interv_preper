"""Tests for the tool-calling loop in ``InterviewLLM`` and the ``ToolBox``.

The loop is exercised against a fake chat-completions client replaying
hand-built chunk sequences (fragmented tool-call deltas are hard to trigger on
demand against a live model). The ToolBox is exercised with fake researcher /
guard / index injected through the constructor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from interview_prep.config import MAX_TOOL_HOPS
from interview_prep.guardrails import GuardrailResult
from interview_prep.llm import InterviewLLM
from interview_prep.tools import ToolBox
from interview_prep.web_research import Citation, ResearchResult


# --- fakes for the streaming loop -------------------------------------------------


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


class FakeStreamCompletions:
    """Replays one canned chunk sequence per call, recording the requests."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return iter(self._responses.pop(0))


def build_llm(responses):
    llm = InterviewLLM(api_key="test", model="test-model", typing_delay=0)
    completions = FakeStreamCompletions(responses)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm, completions


# --- fakes for the ToolBox ---------------------------------------------------------


class FakeResearcher:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def research(self, query):
        self.queries.append(query)
        return self.result


class FakeGuard:
    """Returns scripted verdicts in order, one per check_document call."""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def check_document(self, text, kind="document"):
        self.calls.append({"text": text, "kind": kind})
        return self.verdicts.pop(0)


class FakeIndex:
    def __init__(self, exc=None):
        self.docs = []
        self._exc = exc

    def add_document(self, doc):
        if self._exc:
            raise self._exc
        self.docs.append(doc)
        return 1


GOOD_RESULT = ResearchResult(
    bullets="- Acme raised $40M. [acme.com](https://acme.com/news)",
    citations=[Citation(url="https://acme.com/news", title="Acme news")],
    raw_text="## Acme news\nSource: https://acme.com/news\n\nAcme raised $40M.",
    cost=0.007,
)

ALLOWED = GuardrailResult(allowed=True)
FLAGGED = GuardrailResult(allowed=False, reason="embedded AI-directed instruction")
ERRORED = GuardrailResult(allowed=True, errored=True)


def make_toolbox(result=GOOD_RESULT, verdicts=(ALLOWED, ALLOWED), index=None, **kwargs):
    researcher = FakeResearcher(result)
    guard = FakeGuard(verdicts)
    index = index if index is not None else FakeIndex()
    box = ToolBox(researcher=researcher, guard=guard, index=index, **kwargs)
    return box, researcher, guard, index


# --- streaming loop ------------------------------------------------------------------


def test_streams_plain_text_when_no_tool_is_called():
    llm, completions = build_llm([[text_chunk("Hello"), text_chunk(" there")]])
    box, *_ = make_toolbox()
    out = "".join(llm.stream_reply("sys", [], toolbox=box))
    assert out == "Hello there"
    assert llm.last_tool_calls == []
    assert len(completions.requests) == 1


def test_tool_call_round_trip_feeds_the_result_back():
    responses = [
        # Hop 1: the model requests the tool, in fragments.
        [
            tool_chunk(0, id="call_1", name="web_research"),
            tool_chunk(0, arguments='{"query": "Acme Corp news"'),
            tool_chunk(0, arguments=', "topic": "company research"}'),
            usage_chunk(0.001),
        ],
        # Hop 2: with the result in hand, it answers.
        [text_chunk("Acme recently raised $40M."), usage_chunk(0.002)],
    ]
    llm, completions = build_llm(responses)
    box, researcher, _, index = make_toolbox()
    out = "".join(llm.stream_reply("sys", [], toolbox=box))

    # The caller sees a flat token stream — the hop is invisible.
    assert out == "Acme recently raised $40M."
    assert researcher.queries == ["Acme Corp news"]
    assert len(index.docs) == 1

    # The second request replays the assistant's tool request plus the result.
    assert len(completions.requests) == 2
    replayed = completions.requests[1]["messages"]
    assert replayed[-2]["role"] == "assistant"
    assert replayed[-2]["tool_calls"][0]["id"] == "call_1"
    assert replayed[-1]["role"] == "tool"
    assert replayed[-1]["tool_call_id"] == "call_1"
    assert "$40M" in replayed[-1]["content"]

    # Cost and reasoning tokens summed across the turn's API calls (the
    # sub-completion's cost lives on the toolbox, not here).
    assert llm.last_cost == pytest.approx(0.003)
    assert llm.last_reasoning_tokens == 14
    assert [c["name"] for c in llm.last_tool_calls] == ["web_research"]


def test_interleaved_fragments_reassemble_per_index():
    """Two concurrent calls: index is the only thing tying fragments together."""
    responses = [
        [
            tool_chunk(0, id="a", name="web_research"),
            tool_chunk(1, id="b", name="web_research"),
            tool_chunk(0, arguments='{"query": "one",'),
            tool_chunk(1, arguments='{"query": "two",'),
            tool_chunk(0, arguments=' "topic": "other"}'),
            tool_chunk(1, arguments=' "topic": "other"}'),
        ],
        [text_chunk("done")],
    ]
    llm, completions = build_llm(responses)
    box, researcher, guard, _ = make_toolbox(
        verdicts=(ALLOWED, ALLOWED, ALLOWED, ALLOWED)
    )
    "".join(llm.stream_reply("sys", [], toolbox=box))
    # Second request: [system, assistant(two tool_calls), tool, tool].
    requested = completions.requests[1]["messages"][-3]["tool_calls"]
    assert [c["id"] for c in requested] == ["a", "b"]
    assert researcher.queries == ["one", "two"]


def test_final_hop_withholds_tools_to_force_an_answer():
    """A model that only ever calls tools must still end on a text request."""
    tool_response = [
        tool_chunk(0, id="c", name="web_research",
                   arguments='{"query": "q", "topic": "other"}')
    ]
    llm, completions = build_llm([list(tool_response) for _ in range(MAX_TOOL_HOPS)])
    box, *_ = make_toolbox(verdicts=[ALLOWED] * (2 * MAX_TOOL_HOPS))
    "".join(llm.stream_reply("sys", [], toolbox=box))
    assert len(completions.requests) == MAX_TOOL_HOPS
    assert "tools" in completions.requests[0]
    assert "tools" not in completions.requests[-1]


def test_no_toolbox_never_offers_tools():
    llm, completions = build_llm([[text_chunk("hi")]])
    "".join(llm.stream_reply("sys", []))
    assert "tools" not in completions.requests[0]


# --- ToolBox dispatch ---------------------------------------------------------------


def test_successful_research_returns_bullets_and_indexes_raw_text():
    box, researcher, guard, index = make_toolbox()
    out = box.run("web_research", '{"query": "Acme Corp", "topic": "company research"}')

    assert out == GOOD_RESULT.bullets
    # Both tiers screened, both with the web framing.
    assert [c["kind"] for c in guard.calls] == ["web", "web"]
    assert guard.calls[0]["text"] == GOOD_RESULT.bullets
    assert guard.calls[1]["text"] == GOOD_RESULT.raw_text
    # Raw text indexed with provenance.
    (doc,) = index.docs
    assert doc.doc_type == "web search"
    assert doc.topic == "company research"
    assert doc.name.startswith("web: Acme Corp")
    # Cost and citations surfaced for the caller.
    assert box.extra_cost == pytest.approx(0.007)
    assert box.citations == GOOD_RESULT.citations


def test_cache_hit_skips_research_entirely():
    box, researcher, guard, _ = make_toolbox()
    args = '{"query": "Acme Corp", "topic": "other"}'
    first = box.run("web_research", args)
    second = box.run("web_research", args)
    assert first == second
    assert len(researcher.queries) == 1  # second call never hit the researcher
    assert len(guard.calls) == 2  # and never re-scanned


def test_flagged_bullets_fail_closed_with_warning():
    warnings = []
    box, _, _, index = make_toolbox(
        verdicts=(FLAGGED,),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert out.startswith("error:")
    assert "safety" in out
    assert index.docs == []  # nothing indexed
    assert box.citations == []  # nothing surfaced
    assert warnings and warnings[0][1] == "web research blocked"


def test_errored_bullet_scan_fails_closed():
    """Fail-closed means a scan outage blocks web content, like documents."""
    box, *_ = make_toolbox(verdicts=(ERRORED,))
    out = box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert out.startswith("error:")


def test_flagged_raw_text_degrades_to_bullets_only():
    warnings = []
    box, _, _, index = make_toolbox(
        verdicts=(ALLOWED, FLAGGED),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert out == GOOD_RESULT.bullets  # tier one still delivered
    assert index.docs == []  # tier two withheld
    assert warnings and warnings[0][1] == "web research not indexed"


def test_researcher_error_returns_error_string_and_warns_with_reason():
    warnings = []
    box, *_ = make_toolbox(
        result=ResearchResult(errored=True, error_reason="RuntimeError: api down"),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert out == "error: web research is unavailable right now"
    # The captured reason reaches the warnings log for diagnosability.
    assert warnings == [("Acme", "web research", "RuntimeError: api down")]


def test_indexing_exception_degrades_with_warning():
    warnings = []
    box, *_ = make_toolbox(
        index=FakeIndex(exc=RuntimeError("embeddings down")),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert out == GOOD_RESULT.bullets
    assert warnings


def test_successful_index_registers_document_with_caller():
    registered = []
    box, _, _, index = make_toolbox(on_document=registered.append)
    box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert registered == index.docs


def test_invalid_topic_is_coerced_to_other():
    box, _, _, index = make_toolbox()
    box.run("web_research", '{"query": "Acme", "topic": "made-up-topic"}')
    assert index.docs[0].topic == "other"


def test_dispatch_errors_are_strings_never_raises():
    box, *_ = make_toolbox()
    assert "not valid JSON" in box.run("web_research", "{oops")
    assert "no tool named" in box.run("nonexistent", "{}")
    assert "error" in box.run("web_research", '{"query": "q", "invented": 1}')
    assert "non-empty" in box.run("web_research", '{"query": "  ", "topic": "other"}')
