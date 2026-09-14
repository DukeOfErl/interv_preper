"""The harness's own logic, tested apart from a running graph.

Both of these are anti-fabrication machinery whose *wording* and *thresholds*
matter, so they get direct tests; that they actually reach the model is
asserted at loop level in ``test_agent.py``.
"""

from __future__ import annotations

from interview_prep.middleware import final_hop_note, looks_like_typed_tool_call
from interview_prep.tools import build_tools

TOOLS = build_tools(researcher=None, cache={})


# --- the note that makes honest surrender available --------------------------


def test_no_note_before_any_tool_ran():
    assert final_hop_note({}) == ""
    assert final_hop_note({"tool_calls_made": 0}) == ""


def test_a_turn_that_read_nothing_is_told_so_plainly():
    note = final_hop_note({"tool_calls_made": 3, "mcp_calls": 3, "files_read": []})
    assert "no tool calls left" in note
    assert "obtained no file contents" in note
    assert "which file to look at" in note


def test_a_turn_that_read_something_is_told_what():
    note = final_hop_note(
        {"tool_calls_made": 3, "mcp_calls": 3, "files_read": ["o/r/core.py"]}
    )
    assert "You read: o/r/core.py" in note
    assert "obtained no file contents" not in note


def test_a_local_only_turn_gets_the_generic_note():
    note = final_hop_note({"tool_calls_made": 1})
    assert "Answer from what the tools already returned" in note


def test_every_note_forbids_acting_out_the_rest_of_the_exploration():
    """The specific failure this exists to prevent: a cornered model writing
    out the tool calls it would have made, with imagined results."""
    for state in (
        {"tool_calls_made": 1},
        {"tool_calls_made": 3, "mcp_calls": 3, "files_read": []},
        {"tool_calls_made": 3, "mcp_calls": 3, "files_read": ["a.py"]},
    ):
        note = final_hop_note(state)
        assert "do not describe what a further call would have returned" in note.lower()


# --- a tool call written as prose --------------------------------------------


def test_detects_a_tool_call_written_as_prose():
    assert looks_like_typed_tool_call(
        'Let me look: {"query": "acme", "topic": "other", "verbal_feedback": "x"}',
        TOOLS,
    )


def test_ordinary_prose_and_small_examples_are_not_typed_tool_calls():
    """Deliberately narrow — a false positive costs a hop and a re-answer."""
    assert not looks_like_typed_tool_call("I could call a tool here.", TOOLS)
    assert not looks_like_typed_tool_call('A JSON object like {"a": 1}.', TOOLS)
    # Two real parameters is under the threshold.
    assert not looks_like_typed_tool_call('{"query": "x", "topic": "other"}', TOOLS)
    # Three keys, but not all of them are tool parameters.
    assert not looks_like_typed_tool_call(
        '{"query": "x", "topic": "other", "colour": "red"}', TOOLS
    )


def test_no_tools_means_nothing_to_mistake_for_a_call():
    assert not looks_like_typed_tool_call('{"a": 1, "b": 2, "c": 3}', [])
    assert not looks_like_typed_tool_call("", TOOLS)
