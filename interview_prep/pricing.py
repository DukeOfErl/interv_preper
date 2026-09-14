"""Model pricing lookup and running chat-cost accounting.

Prices come from the same OpenRouter ``/models`` catalog used for context
windows (see :mod:`interview_prep.context`). OpenRouter reports pricing as USD
*per token* (as strings), split into ``prompt`` (input) and ``completion``
(output) rates.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

from .context import (
    estimate_text_tokens,
    fetch_model_endpoints,
    fetch_openrouter_models,
    find_model,
)


@dataclass(frozen=True)
class ModelPricing:
    """USD-per-token input/output rates for a single model."""

    model: str
    prompt_price: float
    completion_price: float
    source: str  # "OpenRouter" when resolved from the catalog, else "unknown"

    @property
    def is_known(self) -> bool:
        """Whether this model can actually be priced.

        Both halves matter. The catalog is the source, *and* it has to have
        produced a rate: `_to_price` coerces a missing or non-numeric
        `prompt`/`completion` to 0.0, so an entry that exists but publishes no
        pricing used to come back "known" at zero. Downstream that is worse
        than "unknown" — `JailbreakGuard._scan_estimate` skips
        `refuse_unpriceable` on a known price and returns $0.00, and `decide`
        admits a zero estimate against any non-empty budget. A user with one
        cent left could then launch the 5,668-call scan the estimate exists to
        refuse.
        """
        return self.source == "OpenRouter" and bool(
            self.prompt_price or self.completion_price
        )


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


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def billing(model):
    """Bookkeeping that must never raise into the operation it is counting.

    Billing happens *after* the decision its caller is making, and a pricing
    lookup or a ledger write that fails must cost the operator an unrecorded
    call — never change what the caller returns.

    This is stated as a rule because it was broken in the one place it mattered
    most: the guardrail billed inside the same `try` whose `except` fails a
    classification **open**, so any exception while pricing a call returned
    "allowed" for a prompt the classifier had just flagged as a jailbreak. An
    accounting change had been wired into a safety control's failure path.

    Logged rather than swallowed: spend that went unrecorded is a real loss —
    the cap under-counts by that call — and an operator who cannot see it will
    not know the ledger drifted.
    """
    try:
        yield
    except Exception as exc:
        logger.warning("spend on %s was not recorded: %s", model, exc)


def bill_call(budget, identity, response, model, api_key, texts=()) -> None:
    """Record what one completion cost. Returns None, raises nothing.

    `response is None` means the call never completed, so there is nothing to
    bill; the pricing ladder is skipped entirely when nothing is counting,
    because rungs two and three mean a catalog lookup.
    """
    if response is None or not getattr(budget, "counts", False):
        return
    with billing(model):
        budget.record(identity, call_cost(response, model, api_key, texts))


def bill_embedding(budget, identity, texts, model, api_key) -> None:
    """Record what one embedding call cost. Returns None, raises nothing."""
    if not getattr(budget, "counts", False):
        return
    with billing(model):
        budget.record(identity, embedding_cost(texts, model, api_key))


def reported_cost(usage) -> float | None:
    """OpenRouter's actual USD cost off a response's usage object, or None.

    An SDK-extra field: OpenRouter adds ``cost`` to the OpenAI usage schema
    when the request asks for ``usage: {include: true}``, and the SDK parks
    unknown fields in ``model_extra``. None means the provider reported
    nothing, which is the caller's cue to fall down the R22.3 ladder rather
    than to bill zero — every paid client now has to answer this question, so
    it is answered once here (R22.2).
    """
    cost = getattr(usage, "cost", None)
    if cost is None and getattr(usage, "model_extra", None):
        cost = usage.model_extra.get("cost")
    try:
        return float(cost) if cost is not None else None
    except (TypeError, ValueError):
        return None


def turn_cost(pricing: ModelPricing, prompt_tokens, completion_tokens) -> float:
    """USD cost of one request given its token counts and the model's rates."""
    return (
        prompt_tokens * pricing.prompt_price
        + completion_tokens * pricing.completion_price
    )


def call_cost(response, model, api_key, texts=()) -> float:
    """What one completion cost, down R22.3's whole ladder.

    Rung one is OpenRouter's reported `cost`; rung two is the token counts it
    reported priced from the catalog; rung three is estimated tokens priced the
    same way. Written once, here, because the three sub-completion clients had
    only rung one — `reported_cost(...) or 0.0` — and a response carrying token
    counts but no cost billed exactly zero, with no warning and no flag. One
    provider quirk or one dropped `extra_body` away from three of the six paid
    clients silently counting nothing.

    `texts` are what the call sent and received, for rung three.
    """
    usage = getattr(response, "usage", None)
    actual = reported_cost(usage)
    if actual is not None:
        return actual

    pricing = price_of(model, api_key)
    prompt_tokens = getattr(usage, "prompt_tokens", None) or 0
    completion_tokens = getattr(usage, "completion_tokens", None) or 0
    if prompt_tokens or completion_tokens:
        return turn_cost(pricing, prompt_tokens, completion_tokens)
    # Rung three. Priced entirely as input: these clients send a prompt and get
    # back a sentence or a small JSON object, so charging the estimate at the
    # (higher) completion rate would be the larger error.
    return turn_cost(pricing, sum(estimate_text_tokens(t) for t in texts), 0)


def get_endpoint_pricing(model_id, api_key) -> ModelPricing:
    """Pricing from the per-model ``/endpoints`` route, for models ``/models`` omits.

    Needed because ``/models`` is the *chat* catalog: every model in
    ``config.EMBEDDING_MODELS`` is absent from it, so `get_model_pricing`
    returns ``source="unknown"`` and a price of zero for all three — which
    looks exactly like R22.3's "unknown pricing degrades to zero" hole while
    actually being a published price read from the wrong route. The embedding
    paths therefore recorded $0.00 forever, and the code around them claimed
    they were counted.

    **The highest-priced endpoint wins.** OpenRouter may route a request to any
    provider serving the model (qwen3-embedding-8b is offered at both
    $0.00000001 and $0.00000004 per token), and this call cannot know which one
    answered. Under-counting spend is the expensive direction for a cap — it
    uncaps quietly — so the ambiguity is resolved against the user's budget
    rather than against the operator's wallet.
    """
    prices = []
    for endpoint in fetch_model_endpoints(model_id, api_key):
        pricing = endpoint.get("pricing") if isinstance(endpoint, dict) else None
        if isinstance(pricing, dict):
            prices.append(
                (_to_price(pricing.get("prompt")), _to_price(pricing.get("completion")))
            )
    if not prices:
        return ModelPricing(
            model=model_id, prompt_price=0.0, completion_price=0.0, source="unknown"
        )
    return ModelPricing(
        model=model_id,
        prompt_price=max(prompt for prompt, _ in prices),
        completion_price=max(completion for _, completion in prices),
        source="OpenRouter",
    )


def price_of(model_id, api_key) -> ModelPricing:
    """The model's rates, from the chat catalog or, failing that, its endpoints.

    One ladder rather than two lookups scattered around: chat models resolve on
    the first route, embedding models only on the second, and a caller that
    does not know which kind it holds should not have to.
    """
    pricing = get_model_pricing(model_id, api_key)
    if pricing.is_known:
        return pricing
    return get_endpoint_pricing(model_id, api_key)


def embedding_cost(texts, model, api_key) -> float:
    """USD cost of embedding ``texts``, estimated (R22.3's bottom rung).

    The embedding endpoint reports usage, but LangChain's ``OpenAIEmbeddings``
    returns vectors and keeps the response to itself, so neither an actual cost
    nor a reported token count reaches us. Estimated tokens × published price is
    what is left, and it is what R22.3's ladder descends to — worth stating
    plainly, because an estimate that silently reads as an actual would make
    the two embedding paths look better counted than they are.

    The price comes through `price_of`, **not** `get_model_pricing`: the chat
    catalog lists none of this app's embedding models, so the direct lookup
    returned zero for every call and the paths recorded nothing at all.
    """
    tokens = sum(estimate_text_tokens(text) for text in texts)
    return tokens * price_of(model, api_key).prompt_price


def format_spend(amount) -> str:
    """Render a USD amount as cents below $1, switching to dollars at/above $1.

    Keeps tiny chat costs legible (e.g. ``12.34¢``) instead of rounding to
    ``$0.00``, while larger totals read naturally as dollars (e.g. ``$1.23``).
    """
    if abs(amount) < 1:
        return f"{amount * 100:,.2f}¢"
    return f"${amount:,.2f}"
