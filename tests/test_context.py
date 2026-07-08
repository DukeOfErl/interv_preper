import pytest

from interview_prep import context
from interview_prep.context import (
    ContextUsage,
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    get_model_context_window,
)


# --- token estimation -------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        (None, 0),
        ("a", 1),  # max(1, 0)
        ("abcd", 1),  # 4 chars // 4
        ("a" * 40, 10),
    ],
)
def test_estimate_text_tokens(text, expected):
    assert estimate_text_tokens(text) == expected


def test_estimate_prompt_tokens_counts_overhead_and_content():
    # Empty prompt + no history: 0 + 4 (system overhead) + 2 (trailer).
    assert estimate_prompt_tokens("", []) == 6

    # "abcd" system (1 token) + one message role="user" (1) content="abcd" (1),
    # plus 4 system overhead, 4 per-message overhead, 2 trailer.
    history = [{"role": "user", "content": "abcd"}]
    assert estimate_prompt_tokens("abcd", history) == 1 + 4 + 4 + 1 + 1 + 2


# --- model context window ---------------------------------------------------


CATALOG = [
    {"id": "openai/gpt-4o", "context_length": 128000},
    {"id": "anthropic/claude-x", "top_provider": {"context_length": 200000}},
    {"id": "vendor/model-z", "canonical_slug": "vendor/slug-z", "context_length": 42},
]


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(context, "fetch_openrouter_models", lambda api_key: CATALOG)


def test_exact_id_match_uses_openrouter(catalog):
    assert get_model_context_window("openai/gpt-4o", "key") == (128000, "OpenRouter")


def test_suffix_match_on_provider_prefixed_id(catalog):
    assert get_model_context_window("gpt-4o", "key") == (128000, "OpenRouter")


def test_falls_back_to_top_provider_context_length(catalog):
    assert get_model_context_window("claude-x", "key") == (200000, "OpenRouter")


def test_canonical_slug_match(catalog):
    assert get_model_context_window("slug-z", "key") == (42, "OpenRouter")


def test_unknown_model_uses_static_fallback(catalog):
    # Not in catalog, but present in the static table.
    assert get_model_context_window("gpt-4o-mini", "key") == (128000, "static_fallback")


def test_unknown_everywhere_returns_default(catalog):
    assert get_model_context_window("totally-unknown", "key") == (None, "static_fallback")


def test_blank_model_id_short_circuits(catalog):
    assert get_model_context_window("", "key") == (None, "static_fallback")


# --- ContextUsage -----------------------------------------------------------


def test_context_usage_known_window():
    usage = ContextUsage(model="m", window=1000, source="OpenRouter", used_tokens=250)
    assert usage.has_known_window
    assert usage.used_percentage == 25.0
    assert usage.progress == 0.25
    assert usage.window_label == "1,000 tokens"


def test_context_usage_progress_is_clamped():
    usage = ContextUsage(model="m", window=100, source="s", used_tokens=250)
    assert usage.used_percentage == 250.0
    assert usage.progress == 1.0  # clamped to 1.0


def test_context_usage_unknown_window():
    usage = ContextUsage(model="m", window=None, source="static_fallback", used_tokens=10)
    assert not usage.has_known_window
    assert usage.used_percentage is None
    assert usage.progress == 0.0
    assert usage.window_label == "unknown"


def test_compute_context_usage(monkeypatch):
    monkeypatch.setattr(
        context, "get_model_context_window", lambda model, key: (1000, "OpenRouter")
    )
    usage = compute_context_usage("m", "key", "abcd", [])
    assert usage.window == 1000
    assert usage.source == "OpenRouter"
    assert usage.used_tokens == estimate_prompt_tokens("abcd", [])
