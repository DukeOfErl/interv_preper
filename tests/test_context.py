import pytest

from interview_prep import context
from interview_prep.context import (
    ContextUsage,
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    get_model_context_window,
    model_supports_reasoning,
    predict_next_call_tokens,
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


# --- next-call prediction ---------------------------------------------------


def test_predict_next_call_returns_none_on_empty_chat():
    # No history to extrapolate from -> caller shows "N/A".
    assert predict_next_call_tokens("system", []) is None


def test_predict_next_call_returns_none_without_an_assistant_reply():
    # A lone unanswered user prompt is still not a completed exchange.
    messages = [{"role": "user", "content": "hello"}]
    assert predict_next_call_tokens("system", messages) is None


def test_predict_next_call_uses_average_lengths():
    # user contents: 40 chars -> 10 tokens, 80 chars -> 20 tokens; avg = 15.
    # assistant contents: 40 -> 10, 120 -> 30; avg = 20.
    messages = [
        {"role": "user", "content": "a" * 40},
        {"role": "assistant", "content": "b" * 40},
        {"role": "user", "content": "a" * 80},
        {"role": "assistant", "content": "b" * 120},
    ]
    input_tokens, output_tokens = predict_next_call_tokens("sys", messages)

    # input = current history estimate + 4 (per-message overhead) + avg user (15)
    expected_input = estimate_prompt_tokens("sys", messages) + 4 + 15
    assert input_tokens == expected_input
    assert output_tokens == 20  # avg assistant length


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


# --- reasoning support detection --------------------------------------------


REASONING_CATALOG = [
    {"id": "openai/gpt-5-mini", "supported_parameters": ["reasoning", "tools"]},
    {"id": "openai/gpt-4o-mini", "supported_parameters": ["tools"]},
    {"id": "vendor/no-params"},  # missing supported_parameters entirely
]


@pytest.fixture
def reasoning_catalog(monkeypatch):
    monkeypatch.setattr(
        context, "fetch_openrouter_models", lambda api_key: REASONING_CATALOG
    )


def test_reasoning_model_detected(reasoning_catalog):
    assert model_supports_reasoning("gpt-5-mini", "key") is True


def test_non_reasoning_model_detected(reasoning_catalog):
    assert model_supports_reasoning("gpt-4o-mini", "key") is False


def test_model_without_params_is_not_reasoning(reasoning_catalog):
    assert model_supports_reasoning("no-params", "key") is False


def test_unknown_model_is_not_reasoning(reasoning_catalog):
    assert model_supports_reasoning("totally-unknown", "key") is False


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
