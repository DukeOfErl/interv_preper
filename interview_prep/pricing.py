"""Model pricing lookup and running chat-cost accounting.

Prices come from the same OpenRouter ``/models`` catalog used for context
windows (see :mod:`interview_prep.context`). OpenRouter reports pricing as USD
*per token* (as strings), split into ``prompt`` (input) and ``completion``
(output) rates.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import fetch_openrouter_models, find_model


@dataclass(frozen=True)
class ModelPricing:
    """USD-per-token input/output rates for a single model."""

    model: str
    prompt_price: float
    completion_price: float
    source: str  # "OpenRouter" when resolved from the catalog, else "unknown"

    @property
    def is_known(self) -> bool:
        return self.source == "OpenRouter"


@dataclass(frozen=True)
class ChatSpend:
    """Running spend for the whole chat plus the active model's rates.

    ``next_estimate`` is the projected USD cost of the next prompt, or ``None``
    when there is no history yet to base a prediction on (shown as "N/A").
    """

    total_cost: float
    pricing: ModelPricing
    next_estimate: float | None = None


def _to_price(value) -> float:
    """Coerce an OpenRouter price string to a float, defaulting to 0.0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_model_pricing(model_id, api_key) -> ModelPricing:
    """Return per-token pricing for ``model_id`` from the OpenRouter catalog.

    Falls back to zero-cost ``"unknown"`` pricing when the model or its pricing
    block can't be found, so cost accounting degrades gracefully rather than
    raising.
    """
    model = find_model(fetch_openrouter_models(api_key), model_id)
    if model is not None:
        pricing = model.get("pricing")
        if isinstance(pricing, dict):
            return ModelPricing(
                model=model_id,
                prompt_price=_to_price(pricing.get("prompt")),
                completion_price=_to_price(pricing.get("completion")),
                source="OpenRouter",
            )

    return ModelPricing(
        model=model_id, prompt_price=0.0, completion_price=0.0, source="unknown"
    )


def turn_cost(pricing: ModelPricing, prompt_tokens, completion_tokens) -> float:
    """USD cost of one request given its token counts and the model's rates."""
    return (
        prompt_tokens * pricing.prompt_price
        + completion_tokens * pricing.completion_price
    )


def format_spend(amount) -> str:
    """Render a USD amount as cents below $1, switching to dollars at/above $1.

    Keeps tiny chat costs legible (e.g. ``12.34¢``) instead of rounding to
    ``$0.00``, while larger totals read naturally as dollars (e.g. ``$1.23``).
    """
    if abs(amount) < 1:
        return f"{amount * 100:,.2f}¢"
    return f"${amount:,.2f}"
