"""Tests for the tool-calling loop in ``InterviewLLM`` and the ``ToolBox``.

The loop is exercised against a fake chat-completions client replaying
hand-built chunk sequences (fragmented tool-call deltas are hard to trigger on
demand against a live model). The ToolBox is exercised with fake researcher /
guard / index injected through the constructor.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import json

from interview_prep.config import (
    EVALUATION_DIMENSIONS,
    GITHUB_FILE_INLINE_CHARS,
    MAX_TOOL_HOPS,
)
from interview_prep.github_mcp import MCPResult
from interview_prep.guardrails import GuardrailResult
from interview_prep.llm import InterviewLLM, looks_like_typed_tool_call
from interview_prep.tools import ToolBox, looks_like_feedback
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


# --- record_evaluation dispatch -----------------------------------------------------


GOOD_SCORES = {d: i % 5 + 1 for i, d in enumerate(EVALUATION_DIMENSIONS)}


def evaluation_args(**overrides):
    args = {
        "question": "Tell me about a conflict you handled.",
        "question_type": "behavioral",
        "scores": GOOD_SCORES,
        "verbal_feedback": "Strong structure; add measurable outcomes.",
    }
    args.update(overrides)
    return json.dumps(args)


def test_valid_evaluation_is_recorded_and_acknowledged():
    box, *_ = make_toolbox()
    out = box.run("record_evaluation", evaluation_args())
    assert out == "evaluation recorded"
    # Accumulated on the box for the caller's post-verdict commit.
    (card,) = box.evaluations
    assert card["scores"] == GOOD_SCORES
    assert card["question_type"] == "behavioral"
    assert card["verbal_feedback"].startswith("Strong structure")


def test_repeat_call_for_same_question_replaces_the_card():
    """A model occasionally re-records with revised scores on the next hop;
    same question means correction, so it replaces rather than appends."""
    box, *_ = make_toolbox()
    box.run("record_evaluation", evaluation_args())
    out = box.run(
        "record_evaluation",
        evaluation_args(scores=dict(GOOD_SCORES, relevance=5)),
    )
    assert "replaced" in out
    (card,) = box.evaluations  # still exactly one
    assert card["scores"]["relevance"] == 5


def test_different_questions_in_one_turn_both_keep_their_cards():
    """One message can answer two outstanding questions — two cards."""
    box, *_ = make_toolbox()
    box.run("record_evaluation", evaluation_args())
    out = box.run(
        "record_evaluation", evaluation_args(question="Describe a system you built.")
    )
    assert out == "evaluation recorded"
    assert [c["question"] for c in box.evaluations] == [
        "Tell me about a conflict you handled.",
        "Describe a system you built.",
    ]


def test_integral_float_scores_are_accepted():
    """JSON Schema's 'integer' accepts 4.0; rejecting it would make a
    schema-conforming call fail and burn hops on identical retries."""
    box, *_ = make_toolbox()
    scores = {d: float(v) for d, v in GOOD_SCORES.items()}
    out = box.run("record_evaluation", evaluation_args(scores=scores))
    assert out == "evaluation recorded"
    assert box.evaluations[0]["scores"] == GOOD_SCORES  # stored as ints


def test_non_string_arguments_return_errors_not_crashes():
    """JSON-valid but wrongly-typed args must come back as error strings —
    an AttributeError here would escape run() and kill the whole turn."""
    box, *_ = make_toolbox()
    assert box.run("record_evaluation", evaluation_args(question=12)).startswith(
        "error:"
    )
    assert box.run(
        "record_evaluation", evaluation_args(verbal_feedback=["good"])
    ).startswith("error:")
    assert box.run("web_research", '{"query": 12, "topic": "other"}').startswith(
        "error:"
    )
    assert box.evaluations == []


def test_evaluation_missing_dimension_is_an_error():
    box, *_ = make_toolbox()
    scores = {d: 3 for d in EVALUATION_DIMENSIONS[:-1]}
    out = box.run("record_evaluation", evaluation_args(scores=scores))
    assert out.startswith("error:")
    assert EVALUATION_DIMENSIONS[-1] in out
    assert box.evaluations == []


def test_evaluation_out_of_range_or_non_integer_score_is_an_error():
    box, *_ = make_toolbox()
    bad = dict(GOOD_SCORES, relevance=6)
    assert box.run("record_evaluation", evaluation_args(scores=bad)).startswith("error:")
    bad = dict(GOOD_SCORES, judgment="high")
    assert box.run("record_evaluation", evaluation_args(scores=bad)).startswith("error:")
    assert box.evaluations == []


def test_evaluation_unknown_question_type_coerced_to_other():
    box, *_ = make_toolbox()
    box.run("record_evaluation", evaluation_args(question_type="rhetorical"))
    assert box.evaluations[0]["question_type"] == "other"


def test_two_tool_dispatch_still_routes_web_research():
    box, researcher, _, _ = make_toolbox()
    box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert researcher.queries == ["Acme"]


def test_specs_offer_both_tools():
    box, *_ = make_toolbox()
    names = [spec["function"]["name"] for spec in box.specs]
    assert names == ["web_research", "record_evaluation"]


def test_looks_like_feedback_detects_scored_replies():
    scored = (
        "Quick feedback:\n- Relevance: 4/5 — on point.\n"
        "- Structure: 3/5 — loose ending.\n- Evidence: 2/5 — no metrics.\n"
        "Next question: tell me about scale."
    )
    assert looks_like_feedback(scored)


def test_looks_like_feedback_is_conservative():
    # Ordinary interview prose mentioning a dimension or two must not trip it.
    assert not looks_like_feedback("Can you give more structure to that answer?")
    assert not looks_like_feedback(
        "Your communication was clear. Let's move to question 3 of 5."
    )
    assert not looks_like_feedback("")
    # A rubric ANNOUNCEMENT lists ranges, not scores — no card is correct.
    assert not looks_like_feedback(
        "I will score each answer on Relevance (1-5), Structure (1-5), "
        "Specificity (1-5), Evidence (1-5) before we move on."
    )
    # A session RECAP quotes decimal averages, not per-answer scores.
    assert not looks_like_feedback(
        "So far: relevance averaged 4.2, structure 3.8, evidence 2.9 — "
        "let's keep practicing."
    )


def test_looks_like_feedback_matches_real_formats():
    # Bold-labeled and dash-separated variants seen in live replies.
    assert looks_like_feedback(
        "**Relevance**: 4\n**Structure** — 3\n**Evidence**: 2/5\nNext question:"
    )
# --- MCP tools (remote, e.g. GitHub) --------------------------------------------------


class FakeMCP:
    """Duck-type of github_mcp.GitHubMCP: specs + tool_names + call."""

    def __init__(
        self,
        names=("get_file_contents",),
        exc=None,
        result=None,
        params=("owner", "repo", "path", "ref", "sha"),
    ):
        self.specs = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "d",
                    # Real schemas declare properties; the loop's typed-call
                    # detection reads them, so the fake must too.
                    "parameters": {
                        "type": "object",
                        "properties": {key: {"type": "string"} for key in params},
                    },
                },
            }
            for name in names
        ]
        self._exc = exc
        self._result = result if result is not None else MCPResult(text="[]")
        self.calls = []

    @property
    def tool_names(self):
        return {spec["function"]["name"] for spec in self.specs}

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if self._exc:
            raise self._exc
        return self._result


def file_result(body="def run():\n    return 1\n", path="src/core.py"):
    return MCPResult(
        text="successfully downloaded text file (SHA: abc)",
        file_text=body,
        source=f"octocat/hello/{path}",
        terms={path, "run"},
    )


# --- schema merging & routing ---------------------------------------------------------


def local_tool_names():
    """The names a ToolBox offers with no MCP client attached."""
    box, *_ = make_toolbox()
    return [spec["function"]["name"] for spec in box.specs]


def test_specs_merge_mcp_tools_after_local_ones():
    box, *_ = make_toolbox(mcp=FakeMCP(names=("get_file_contents", "search_code")))
    names = [spec["function"]["name"] for spec in box.specs]
    # Derived rather than hard-coded, so adding a local tool cannot break this.
    assert names == local_tool_names() + ["get_file_contents", "search_code"]


def test_specs_skip_mcp_tool_colliding_with_local_name():
    box, *_ = make_toolbox(mcp=FakeMCP(names=("web_research", "search_code")))
    names = [spec["function"]["name"] for spec in box.specs]
    assert names == local_tool_names() + ["search_code"]
    assert names.count("web_research") == 1
    # And dispatch routes the shared name to the local tool, not the MCP one.
    mcp = box._mcp
    box.run("web_research", '{"query": "Acme", "topic": "other"}')
    assert mcp.calls == []


def test_no_mcp_is_a_noop():
    box, *_ = make_toolbox()
    assert [spec["function"]["name"] for spec in box.specs] == local_tool_names()
    assert "no tool named" in box.run("get_file_contents", "{}")


def test_unknown_name_still_errors_with_mcp_attached():
    box, *_ = make_toolbox(mcp=FakeMCP())
    assert "no tool named" in box.run("create_issue", "{}")


def test_mcp_call_exception_becomes_error_string():
    warnings = []
    box, *_ = make_toolbox(
        mcp=FakeMCP(exc=RuntimeError("connection refused")),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("get_file_contents", "{}")
    assert out.startswith("error:")
    assert "connection refused" in out
    assert warnings == [("get_file_contents", "github tool", "connection refused")]


def test_mcp_call_fires_progress_hook():
    progress = []
    box, *_ = make_toolbox(mcp=FakeMCP(), on_progress=progress.append)
    box.run("get_file_contents", "{}")
    assert any("get_file_contents" in message for message in progress)


# --- listings stay inline -------------------------------------------------------------


def test_listing_returns_inline_and_is_not_indexed():
    listing = '[{"name":"core.py","path":"src/core.py","type":"file"}]'
    seen = set()
    box, _, guard, index = make_toolbox(
        mcp=FakeMCP(result=MCPResult(text=listing, terms={"src/core.py"})),
        seen_terms=seen,
    )
    out = box.run("get_file_contents", '{"owner": "octocat", "repo": "hello"}')
    assert out == listing
    assert index.docs == []
    assert guard.calls == []  # nothing to screen
    assert seen == {"src/core.py"}


def test_errored_result_passes_its_message_through():
    box, _, _, index = make_toolbox(
        mcp=FakeMCP(result=MCPResult(text="error: nope", errored=True))
    )
    assert box.run("get_file_contents", "{}") == "error: nope"
    assert index.docs == []


# --- a fetched file takes the document route -------------------------------------------


def test_file_is_screened_as_code_indexed_and_excerpted():
    seen = set()
    box, _, guard, index = make_toolbox(
        mcp=FakeMCP(result=file_result()), verdicts=(ALLOWED,), seen_terms=seen
    )
    out = box.run(
        "get_file_contents",
        '{"owner": "octocat", "repo": "hello", "path": "src/core.py"}',
    )
    # Screened with the code framing, indexed as a github document...
    assert guard.calls[0]["kind"] == "code"
    assert index.docs[0].doc_type == "github"
    assert index.docs[0].name == "octocat/hello/src/core.py"
    assert index.docs[0].text == "def run():\n    return 1\n"
    # ...and the model still sees the real contents this turn.
    assert "octocat/hello/src/core.py" in out
    assert "def run():" in out
    assert seen == {"src/core.py", "run"}


def test_flagged_file_fails_closed_and_is_not_indexed():
    warnings = []
    seen = set()
    box, _, _, index = make_toolbox(
        mcp=FakeMCP(result=file_result()),
        verdicts=(FLAGGED,),
        seen_terms=seen,
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("get_file_contents", '{"path": "src/core.py"}')
    assert out.startswith("error:")
    assert "def run():" not in out
    assert index.docs == []
    # Nothing was shown, so nothing counts as verified.
    assert seen == set()
    assert warnings[0][1] == "github file blocked"


def test_errored_scan_fails_closed_too():
    box, _, _, index = make_toolbox(
        mcp=FakeMCP(result=file_result()), verdicts=(ERRORED,)
    )
    assert box.run("get_file_contents", "{}").startswith("error:")
    assert index.docs == []


def test_large_file_is_truncated_inline_but_indexed_whole():
    body = "x" * (GITHUB_FILE_INLINE_CHARS + 500)
    box, _, _, index = make_toolbox(
        mcp=FakeMCP(result=file_result(body=body)), verdicts=(ALLOWED,)
    )
    out = box.run("get_file_contents", "{}")
    assert "Truncated at" in out
    assert len(out) < len(body)
    assert index.docs[0].text == body  # the index keeps every character


def test_file_indexing_failure_degrades_to_the_excerpt():
    warnings = []
    box, *_ = make_toolbox(
        mcp=FakeMCP(result=file_result()),
        verdicts=(ALLOWED,),
        index=FakeIndex(exc=RuntimeError("embeddings down")),
        on_warning=lambda name, kind, reason="": warnings.append((name, kind, reason)),
    )
    out = box.run("get_file_contents", "{}")
    assert "def run():" in out
    assert warnings


def test_indexed_file_is_registered_with_the_caller():
    registered = []
    box, _, _, index = make_toolbox(
        mcp=FakeMCP(result=file_result()),
        verdicts=(ALLOWED,),
        on_document=registered.append,
    )
    box.run("get_file_contents", "{}")
    assert registered == index.docs


# --- preamble from tool-calling hops is not the answer ---------------------------------


def test_answer_text_excludes_preamble_from_a_tool_calling_hop():
    # Hop 1 talks *and* calls a tool — a model sometimes emits raw tool-call
    # JSON there. That text streams (so the UI stays alive) but must not
    # become the persisted reply.
    llm, _ = build_llm(
        [
            [
                text_chunk('{"owner": "octocat", "repo": "hello"}'),
                tool_chunk(0, id="c1", name="web_research"),
                tool_chunk(0, arguments='{"query": "Acme", "topic": "other"}'),
            ],
            [text_chunk("Real answer about Acme.")],
        ]
    )
    box, *_ = make_toolbox()
    streamed = "".join(llm.stream_reply("sys", [], toolbox=box))
    # Everything was streamed for visibility...
    assert '{"owner"' in streamed
    # ...but only the final hop's text is the answer.
    assert llm.answer_text == "Real answer about Acme."


def test_answer_text_is_the_whole_reply_when_no_tools_run():
    llm, _ = build_llm([[text_chunk("Hello"), text_chunk(" there")]])
    box, *_ = make_toolbox()
    assert "".join(llm.stream_reply("sys", [], toolbox=box)) == "Hello there"
    assert llm.answer_text == "Hello there"


# --- the final-hop note ----------------------------------------------------------------


def test_no_final_hop_note_before_any_tool_ran():
    box, *_ = make_toolbox(mcp=FakeMCP(result=MCPResult(text="[]")))
    assert box.final_hop_note() == ""


def test_final_hop_note_when_github_ran_but_read_nothing():
    box, *_ = make_toolbox(mcp=FakeMCP(result=MCPResult(text="[]")))
    box.run("get_file_contents", '{"path": "/"}')  # a listing only
    note = box.final_hop_note()
    assert "no tool calls left" in note
    assert "obtained no file contents" in note
    assert "which file to look at" in note


def test_final_hop_note_names_what_was_read_when_exploration_ran_out():
    # The failure this covers: a model that read one file, still wanted more,
    # ran out of hops, and wrote the remaining calls out as prose.
    box, *_ = make_toolbox(mcp=FakeMCP(result=file_result()), verdicts=(ALLOWED,))
    box.run("get_file_contents", '{"path": "src/core.py"}')
    assert box.files_read == ["octocat/hello/src/core.py"]
    note = box.final_hop_note()
    assert "no tool calls left" in note
    assert "Do NOT write tool calls" in note
    assert "octocat/hello/src/core.py" in note


def test_final_hop_note_is_generic_when_only_local_tools_ran():
    box, *_ = make_toolbox(mcp=FakeMCP())
    box.run("web_research", '{"query": "Acme", "topic": "other"}')
    note = box.final_hop_note()
    assert "no tool calls left" in note
    assert "GitHub" not in note


def test_fetched_file_text_lands_in_the_code_corpus():
    corpus = []
    box, *_ = make_toolbox(
        mcp=FakeMCP(result=file_result()), verdicts=(ALLOWED,), code_corpus=corpus
    )
    box.run("get_file_contents", "{}")
    assert corpus == ["def run():\n    return 1\n"]


def test_blocked_file_never_reaches_the_code_corpus():
    corpus = []
    box, *_ = make_toolbox(
        mcp=FakeMCP(result=file_result()), verdicts=(FLAGGED,), code_corpus=corpus
    )
    box.run("get_file_contents", "{}")
    assert corpus == []


def test_loop_injects_the_final_hop_note_before_forcing_an_answer():
    tool_response = [
        tool_chunk(0, id="c", name="get_file_contents", arguments='{"path": "/"}')
    ]
    llm, completions = build_llm(
        [list(tool_response) for _ in range(MAX_TOOL_HOPS - 1)]
        + [[text_chunk("I could not read your code — which file should I open?")]]
    )
    box, *_ = make_toolbox(mcp=FakeMCP(result=MCPResult(text="[]")))
    "".join(llm.stream_reply("sys", [], toolbox=box))

    final_request = completions.requests[-1]
    assert "tools" not in final_request  # tools withheld on the last hop
    system_notes = [
        m["content"]
        for m in final_request["messages"]
        if m["role"] == "system" and "SYSTEM NOTE" in (m["content"] or "")
    ]
    assert len(system_notes) == 1
    assert "obtained no file contents" in system_notes[0]


# --- a tool call typed as prose ---------------------------------------------------------


GH_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_file_contents",
            "parameters": {
                "properties": {
                    "owner": {}, "repo": {}, "path": {}, "ref": {}, "sha": {}
                }
            },
        },
    }
]


def test_detects_a_tool_call_written_as_prose():
    # Exactly what a live session produced instead of calling the tool.
    typed = (
        "I will read the top-level package files next.\n\n"
        '{"owner":"DukeOfErl","path":"src/cytocalc/__init__.py",'
        '"ref":"refs/heads/main","repo":"Cytocalc","sha":""}'
    )
    assert looks_like_typed_tool_call(typed, GH_SPECS)


def test_ordinary_prose_and_small_examples_are_not_typed_tool_calls():
    assert not looks_like_typed_tool_call("Walk me through your parser.", GH_SPECS)
    # Two keys is below the threshold, so discussing a payload stays safe.
    assert not looks_like_typed_tool_call('e.g. {"owner": "a", "repo": "b"}', GH_SPECS)
    # Keys outside the tool's parameters mean it is not a call.
    assert not looks_like_typed_tool_call(
        '{"name": "x", "age": 3, "city": "y"}', GH_SPECS
    )
    assert not looks_like_typed_tool_call("no braces here", [])


def test_typed_tool_call_is_corrected_and_retried_not_answered():
    llm, completions = build_llm(
        [
            # Hop 1: types the call instead of making it, requests no tool.
            [
                text_chunk("I will read the package next.\n"),
                text_chunk(
                    '{"owner":"o","path":"src/__init__.py","ref":"main",'
                    '"repo":"r","sha":""}'
                ),
            ],
            # Hop 2: after the correction, answers properly.
            [text_chunk("Walk me through how you structured the package.")],
        ]
    )
    box, *_ = make_toolbox(mcp=FakeMCP(names=("get_file_contents",)))
    "".join(llm.stream_reply("sys", [], toolbox=box))

    assert llm.typed_tool_call_retries == 1
    # The typed JSON never becomes the stored reply.
    assert llm.answer_text == "Walk me through how you structured the package."
    assert "{" not in llm.answer_text
    # The model was told what it did wrong before retrying.
    notes = [
        m["content"]
        for m in completions.requests[1]["messages"]
        if m["role"] == "system" and "tool call written as text" in (m["content"] or "")
    ]
    assert len(notes) == 1


def test_a_plain_answer_is_never_retried():
    llm, completions = build_llm([[text_chunk("What did you optimize and why?")]])
    box, *_ = make_toolbox(mcp=FakeMCP())
    "".join(llm.stream_reply("sys", [], toolbox=box))
    assert llm.typed_tool_call_retries == 0
    assert len(completions.requests) == 1


def test_unknown_tool_name_does_not_count_as_a_tool_call():
    # Otherwise final_hop_note would claim tools ran on a turn where none did.
    box, *_ = make_toolbox(mcp=FakeMCP())
    box.run("totally_made_up", "{}")
    assert box.tool_calls_made == 0
    assert box.final_hop_note() == ""


def test_withheld_final_hop_ignores_echoed_tool_calls():
    # Some providers echo a call from history even when tools are withheld;
    # running it would bill a call whose result no later hop can read.
    llm, completions = build_llm(
        [list([tool_chunk(0, id="c", name="get_file_contents",
                          arguments='{"path": "/"}')]) for _ in range(MAX_TOOL_HOPS - 1)]
        + [[text_chunk("Answer."), tool_chunk(0, id="z", name="get_file_contents",
                                              arguments='{"path": "/"}')]]
    )
    mcp = FakeMCP(result=MCPResult(text="[]"))
    box, *_ = make_toolbox(mcp=mcp)
    "".join(llm.stream_reply("sys", [], toolbox=box))
    assert llm.answered
    assert llm.answer_text == "Answer."
    # One call per tool-offering hop, and none from the withheld final hop.
    assert len(mcp.calls) == MAX_TOOL_HOPS - 1


def test_answered_flag_distinguishes_an_empty_answer_from_no_answer():
    llm, _ = build_llm([[text_chunk("")]])
    box, *_ = make_toolbox()
    "".join(llm.stream_reply("sys", [], toolbox=box))
    assert llm.answered
    assert llm.answer_text == ""
