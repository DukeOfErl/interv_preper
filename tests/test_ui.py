"""Tests for the pure (non-rendering) helpers in ``ui.py``."""

from interview_prep.config import EVALUATION_DIMENSIONS
from interview_prep.ui import summarize_cards


def card(**scores):
    full = {d: 3 for d in EVALUATION_DIMENSIONS}
    full.update(scores)
    return {
        "question": "q",
        "question_type": "other",
        "scores": full,
        "verbal_feedback": "",
    }


def test_summarize_cards_empty():
    assert summarize_cards([]) == {}


def test_summarize_cards_means_per_dimension():
    cards = [card(relevance=5), card(relevance=2)]
    means = summarize_cards(cards)
    assert means["relevance"] == 3.5
    assert means["structure"] == 3.0
    # Rubric order preserved for display.
    assert list(means) == EVALUATION_DIMENSIONS
