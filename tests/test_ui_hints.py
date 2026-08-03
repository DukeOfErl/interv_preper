"""Tests for the sidebar-guidance helpers in ``ui`` (no Streamlit runtime).

Only the pure decision functions are covered; the rendering functions around
them need a live Streamlit script run and are exercised by using the app.
"""

from interview_prep.ui import GITHUB_EFFORT_HINT, github_effort_hint


def test_hint_after_a_repository_turn_below_high_effort():
    assert github_effort_hint(mcp_calls=2, reasoning_effort="medium") == (
        GITHUB_EFFORT_HINT
    )
    assert github_effort_hint(mcp_calls=1, reasoning_effort="low") == (
        GITHUB_EFFORT_HINT
    )


def test_no_hint_when_effort_is_already_high():
    assert github_effort_hint(mcp_calls=3, reasoning_effort="high") is None


def test_no_hint_when_the_turn_did_not_touch_github():
    assert github_effort_hint(mcp_calls=0, reasoning_effort="medium") is None


def test_no_hint_when_the_model_has_no_reasoning_control():
    # The tip names a sidebar selector that is hidden for such models.
    assert github_effort_hint(mcp_calls=2, reasoning_effort=None) is None


def test_hint_names_the_control_and_the_coming_model_choice():
    assert "Effort" in GITHUB_EFFORT_HINT
    assert "high" in GITHUB_EFFORT_HINT
    assert "model" in GITHUB_EFFORT_HINT
