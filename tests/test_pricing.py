import pytest

from interview_prep import pricing
from interview_prep.pricing import (
    ModelPricing,
    format_spend,
    get_model_pricing,
    turn_cost,
)


# OpenRouter reports prices as strings in USD per token.
CATALOG = [
    {
        "id": "openai/gpt-4o",
        "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
    },
    {
        "id": "vendor/model-z",
        "canonical_slug": "vendor/slug-z",
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    },
    {
        "id": "vendor/no-pricing",  # model present but no pricing block
    },
    {
        "id": "vendor/bad-pricing",
        "pricing": {"prompt": "not-a-number", "completion": None},
    },
]


@pytest.fixture
def catalog(monkeypatch):
    # pricing.py imports fetch_openrouter_models by name, so patch it there.
    monkeypatch.setattr(pricing, "fetch_openrouter_models", lambda api_key: CATALOG)


# --- get_model_pricing ------------------------------------------------------


def test_exact_id_match_uses_openrouter(catalog):
    p = get_model_pricing("openai/gpt-4o", "key")
    assert p == ModelPricing(
        model="openai/gpt-4o",
        prompt_price=0.0000025,
        completion_price=0.00001,
        source="OpenRouter",
    )
    assert p.is_known


def test_suffix_match_on_provider_prefixed_id(catalog):
    p = get_model_pricing("gpt-4o", "key")
    assert p.prompt_price == 0.0000025
    assert p.completion_price == 0.00001
    assert p.source == "OpenRouter"


def test_canonical_slug_match(catalog):
    p = get_model_pricing("slug-z", "key")
    assert p.prompt_price == 0.000001
    assert p.completion_price == 0.000002
    assert p.source == "OpenRouter"


def test_unknown_model_is_zero_cost_unknown(catalog):
    p = get_model_pricing("totally-unknown", "key")
    assert p == ModelPricing(
        model="totally-unknown",
        prompt_price=0.0,
        completion_price=0.0,
        source="unknown",
    )
    assert not p.is_known


def test_blank_model_id_is_unknown(catalog):
    p = get_model_pricing("", "key")
    assert p.source == "unknown"


def test_model_without_pricing_block_is_unknown(catalog):
    p = get_model_pricing("no-pricing", "key")
    assert p.source == "unknown"
    assert p.prompt_price == 0.0
    assert p.completion_price == 0.0


def test_malformed_prices_default_to_zero(catalog):
    # The model is found (source is OpenRouter) but unparseable prices → 0.0.
    p = get_model_pricing("bad-pricing", "key")
    assert p.source == "OpenRouter"
    assert p.prompt_price == 0.0
    assert p.completion_price == 0.0


# --- turn_cost --------------------------------------------------------------


def test_turn_cost_multiplies_tokens_by_rates():
    p = ModelPricing(
        model="m", prompt_price=0.001, completion_price=0.002, source="OpenRouter"
    )
    # 100 * 0.001 + 50 * 0.002 = 0.1 + 0.1 = 0.2
    assert turn_cost(p, 100, 50) == pytest.approx(0.2)


def test_turn_cost_is_zero_for_unknown_pricing():
    p = ModelPricing(model="m", prompt_price=0.0, completion_price=0.0, source="unknown")
    assert turn_cost(p, 1000, 1000) == 0.0


# --- format_spend -----------------------------------------------------------


@pytest.mark.parametrize(
    "amount, expected",
    [
        (0.0, "0.00¢"),
        (0.0012, "0.12¢"),
        (0.45, "45.00¢"),
        (0.9999, "99.99¢"),
        (1.0, "$1.00"),  # crosses over to dollars at exactly $1
        (1.23, "$1.23"),
        (1234.5, "$1,234.50"),
    ],
)
def test_format_spend_switches_at_one_dollar(amount, expected):
    assert format_spend(amount) == expected
