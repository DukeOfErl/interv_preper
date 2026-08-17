"""The tool layer: what each tool reports, before any policy is applied.

Tools no longer screen, index or record anything, so what is testable here is
narrow and pure: the validation a schema cannot express, how a relayed MCP
result is *described*, and which tools get built. Everything the policy then
does with those descriptions lives in ``test_policy.py``; everything the loop
does with the tools lives in ``test_agent.py``.
"""

from __future__ import annotations

import pytest

from interview_prep.github_mcp import MCPResult
from interview_prep.tools import (
    _describe_mcp_result,
    build_tools,
    looks_like_feedback,
    record_evaluation_spec,
    validate_evaluation_card,
    web_research_spec,
)

DIMENSIONS = [
    "relevance",
    "structure",
    "specificity",
    "evidence",
    "judgment",
    "communication",
]


def scores(value=4, **overrides):
    out = {d: value for d in DIMENSIONS}
    out.update(overrides)
    return out


# --- the validation a JSON Schema cannot be trusted to do --------------------


def test_a_valid_card_comes_back_clean():
    card, error = validate_evaluation_card("Q?", "behavioral", scores(), "Good.")
    assert error is None
    assert card["question"] == "Q?" and card["scores"]["relevance"] == 4


def test_integral_float_scores_are_accepted():
    """JSON Schema's "integer" accepts 4.0, so rejecting it would fail a
    schema-conforming call — and models retry such rejections verbatim until
    the hops run out."""
    card, error = validate_evaluation_card(
        "Q?", "behavioral", scores(value=4.0), "Good."
    )
    assert error is None and card["scores"]["relevance"] == 4


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"question": ""}, "question must be"),
        ({"question": 7}, "question must be"),
        ({"verbal_feedback": 7}, "verbal_feedback must be"),
        ({"scores": "high"}, "scores must be an object"),
        ({"scores": {"relevance": 4}}, "missing dimensions"),
        ({"scores": {**{d: 4 for d in DIMENSIONS}, "relevance": 9}}, "between 1 and 5"),
        ({"scores": {**{d: 4 for d in DIMENSIONS}, "relevance": True}}, "between 1 and 5"),
    ],
)
def test_a_malformed_card_is_refused_with_a_correctable_message(kwargs, fragment):
    args = {
        "question": "Q?",
        "question_type": "behavioral",
        "scores": scores(),
        "verbal_feedback": "Good.",
    }
    args.update(kwargs)
    card, error = validate_evaluation_card(**args)
    assert card is None
    assert fragment in error


def test_an_unknown_question_type_is_coerced_rather_than_refused():
    card, error = validate_evaluation_card("Q?", "invented", scores(), "Good.")
    assert error is None and card["question_type"] == "other"


# --- describing what came back from the MCP server ---------------------------


def test_a_file_body_becomes_a_document_whose_scan_governs_the_excerpt():
    """``inline=None`` means the model's text is a slice of this document, so
    one scan decides both."""
    outcome = _describe_mcp_result(
        MCPResult(
            text="ok",
            file_text="def run(): ...",
            source="o/r/core.py",
            terms={"core.py"},
        ),
        "get_file_contents",
    )
    assert outcome.inline is None
    assert outcome.document.text == "def run(): ..."
    assert outcome.document.doc_type == "github"
    assert outcome.blocked_as == "github file blocked"
    assert outcome.terms == ("core.py",)


def test_a_listing_goes_inline_but_is_still_described_as_screenable():
    """A repository the candidate does not control can put an instruction in a
    file *name*, so a listing is not exempt."""
    outcome = _describe_mcp_result(
        MCPResult(text='[{"name":"core.py"}]', terms={"core.py"}), "get_file_contents"
    )
    assert outcome.inline == '[{"name":"core.py"}]'
    assert outcome.document is None
    assert outcome.trusted is False, "a listing is external text"
    assert outcome.blocked_as == "github listing blocked"


def test_a_server_error_is_our_text_and_is_not_screened():
    outcome = _describe_mcp_result(
        MCPResult(text="error: 404 not found", errored=True), "get_file_contents"
    )
    assert outcome.trusted is True
    assert outcome.inline == "error: 404 not found"


# --- which tools get offered -------------------------------------------------


class FakeMCP:
    def __init__(self, *names):
        self.specs = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in names
        ]

    @property
    def tool_names(self):
        return {spec["function"]["name"] for spec in self.specs}

    def call(self, name, arguments):
        return MCPResult(text="[]")


def tool_names(tools):
    return [tool.name for tool in tools]


def test_the_local_tools_are_always_offered():
    assert tool_names(build_tools(researcher=None, cache={})) == [
        "web_research",
        "record_evaluation",
    ]


def test_mcp_tools_join_the_same_list():
    tools = build_tools(researcher=None, cache={}, mcp=FakeMCP("search_code"))
    assert tool_names(tools) == ["web_research", "record_evaluation", "search_code"]


def test_a_remote_tool_cannot_shadow_a_local_name():
    """Two tools with one name would make the model's choice ambiguous."""
    tools = build_tools(researcher=None, cache={}, mcp=FakeMCP("web_research"))
    assert tool_names(tools) == ["web_research", "record_evaluation"]


def test_the_schemas_the_model_sees_survive_verbatim():
    """These are prompt text, not derived types: the consent policy and the
    rubric's nested score object are tuned against live model behaviour."""
    tools = {t.name: t for t in build_tools(researcher=None, cache={})}
    assert "CONSENT POLICY" in tools["web_research"].description
    assert tools["web_research"].args_schema == web_research_spec()["parameters"]
    assert (
        tools["record_evaluation"].args_schema
        == record_evaluation_spec()["parameters"]
    )
    # runtime is injected, so it must never appear as a model-facing argument.
    for tool in tools.values():
        assert "runtime" not in tool.args_schema["properties"]


# --- the skipped-card heuristic ----------------------------------------------


def test_looks_like_feedback_detects_scored_replies():
    assert looks_like_feedback(
        "Relevance: 4/5\nStructure: 3/5\nSpecificity: 4/5\nNice work."
    )


def test_looks_like_feedback_is_conservative():
    """A false 'you skipped a card' warning is worse than a missed one."""
    assert not looks_like_feedback("I'll score you on relevance (1-5) shortly.")
    assert not looks_like_feedback("Your relevance averaged 4.2 across answers.")
    assert not looks_like_feedback("")
