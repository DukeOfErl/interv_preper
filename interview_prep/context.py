"""Model context-window lookup and rough token estimation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import HTTPException
from urllib import request

import streamlit as st

from .config import (
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CONTEXT_WINDOWS,
    MODELS_CACHE_TTL_SECONDS,
    OPENROUTER_BASE_URL,
)


class _CatalogUnavailable(Exception):
    """A catalog fetch that failed, raised so the failure is not cached.

    `st.cache_data` stores return values and **not** exceptions, which is the
    whole mechanism here. Returning `[]` from inside the cached function cached
    the emptiness for the full hour TTL, and an empty catalog is not neutral
    downstream: `price_of` reports the model unpriceable, and
    `JailbreakGuard._scan_estimate` refuses every document scan outright
    (R22.21) while a tool turn dies on the same refusal. One eight-second
    network blip therefore hard-refused every upload for an hour.

    Raising instead means the next call retries, so an outage lasts as long as
    the outage.
    """


def fetch_openrouter_models(api_key):
    """Fetch the OpenRouter model catalog (cached). Returns [] on any failure.

    The success path is cached; the failure path is not — see
    `_CatalogUnavailable`.
    """
    if not api_key:
        return []
    try:
        return _fetch_openrouter_models_cached(api_key)
    except _CatalogUnavailable:
        return []


@st.cache_data(ttl=MODELS_CACHE_TTL_SECONDS)
def _fetch_openrouter_models_cached(api_key):

    req = request.Request(
        f"{OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with request.urlopen(req, timeout=8) as api_result_handle:
            payload = json.loads(api_result_handle.read().decode("utf-8"))
    except (OSError, HTTPException, ValueError):
        # Caught by base class deliberately, not by enumeration. The three
        # bases cover every way this call has been seen to fail: transport
        # (URLError, HTTPError, TimeoutError, ConnectionResetError — all
        # OSError), a truncated chunked response (IncompleteRead, an
        # HTTPException and *not* an OSError), and an unreadable body
        # (JSONDecodeError, UnicodeDecodeError — both ValueError). An earlier
        # hand-listed tuple missed the last two families while the docstring
        # already promised "any failure", so a flaky connection crashed the
        # page instead of degrading it per R14.2.
        raise _CatalogUnavailable("the model catalog could not be read")

    return payload.get("data", []) if isinstance(payload, dict) else []


def fetch_model_endpoints(model_id, api_key):
    """Fetch one model's provider endpoints. Returns [] on any failure.

    Success cached, failure not — see `_CatalogUnavailable`.
    """
    if not api_key or not model_id:
        return []
    try:
        return _fetch_model_endpoints_cached(model_id, api_key)
    except _CatalogUnavailable:
        return []


@st.cache_data(ttl=MODELS_CACHE_TTL_SECONDS)
def _fetch_model_endpoints_cached(model_id, api_key):
    """Fetch one model's provider endpoints (cached). Returns [] on any failure.

    A second route, because the catalog ``/models`` returns is the **chat**
    catalog: none of ``config.EMBEDDING_MODELS`` appears in it, so a price
    looked up there is not "unknown pricing" (R22.3's named hole) but a
    published price this app was reading from the wrong place. ``/models/{id}/
    endpoints`` carries it — verified against the live API for all three
    configured embedding models.

    Same fail-soft contract, and the same three exception bases, as
    ``fetch_openrouter_models``: a pricing lookup must never take the page down.
    """
    if not api_key or not model_id:
        return []

    req = request.Request(
        f"{OPENROUTER_BASE_URL}/models/{model_id}/endpoints",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with request.urlopen(req, timeout=8) as api_result_handle:
            payload = json.loads(api_result_handle.read().decode("utf-8"))
    except (OSError, HTTPException, ValueError):
        raise _CatalogUnavailable("the model's endpoints could not be read")

    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None
    return endpoints if isinstance(endpoints, list) else []


# The cache lives on the inner functions now, but `.clear()` is part of this
# module's surface — tests reset it between cases, and a caller should not have
# to know which half of the pair holds the cache.
fetch_openrouter_models.clear = _fetch_openrouter_models_cached.clear
fetch_model_endpoints.clear = _fetch_model_endpoints_cached.clear


def find_model(models, model_id):
    """Return the OpenRouter catalog entry matching ``model_id``, or ``None``.

    Matches on either ``id`` or ``canonical_slug``, allowing a bare model name
    (e.g. ``gpt-5-mini``) to match a namespaced id (e.g. ``openai/gpt-5-mini``).
    """
    normalized_model_id = str(model_id or "").strip().lower()
    if not normalized_model_id:
        return None

    for model in models:
        if not isinstance(model, dict):
            continue

        candidate_id = str(model.get("id", "")).strip().lower()
        canonical_slug = str(model.get("canonical_slug", "")).strip().lower()

        is_match = (
            candidate_id == normalized_model_id
            or candidate_id.endswith(f"/{normalized_model_id}")
            or canonical_slug == normalized_model_id
            or canonical_slug.endswith(f"/{normalized_model_id}")
        )
        if is_match:
            return model

    return None


def model_supports_reasoning(model_id, api_key) -> bool:
    """True if the OpenRouter catalog lists ``reasoning`` for this model.

    Returns ``False`` when the model isn't found or the catalog is unavailable
    (e.g. offline), so the reasoning-effort selector simply stays hidden rather
    than being offered for a model that would reject it.
    """
    model = find_model(fetch_openrouter_models(api_key), model_id)
    if model is None:
        return False
    supported = model.get("supported_parameters") or []
    return "reasoning" in supported


def get_model_context_window(model_id, api_key):
    """Return ``(context_window, source)`` for a model id.

    Prefers a live value from OpenRouter, then falls back to the static
    ``MODEL_CONTEXT_WINDOWS`` table, then to ``DEFAULT_CONTEXT_WINDOW``.
    """
    model = find_model(fetch_openrouter_models(api_key), model_id)
    if model is not None:
        direct_context = model.get("context_length")
        provider_context = model.get("top_provider", {}).get("context_length")
        resolved_ctx_len = direct_context or provider_context
        if isinstance(resolved_ctx_len, int) and resolved_ctx_len > 0:
            return resolved_ctx_len, "OpenRouter"

    normalized_model_id = str(model_id or "").strip().lower()
    return (
        MODEL_CONTEXT_WINDOWS.get(normalized_model_id, DEFAULT_CONTEXT_WINDOW),
        "static_fallback",
    )


def estimate_text_tokens(text):
    # Rough approximation: ~1 token per 4 characters for English text.
    return max(1, len(text) // 4) if text else 0


def estimate_prompt_tokens(system_prompt, history_items):
    total = estimate_text_tokens(system_prompt) + 4
    for entry in history_items:
        total += 4
        total += estimate_text_tokens(entry.get("role", ""))
        total += estimate_text_tokens(entry.get("content", ""))
    return total + 2


def _average_content_tokens(messages, role):
    """Mean estimated content tokens across messages of ``role``, or None."""
    counts = [
        estimate_text_tokens(m.get("content", ""))
        for m in messages
        if m.get("role") == role
    ]
    return sum(counts) / len(counts) if counts else None


def _average_reasoning_tokens(messages):
    """Mean reasoning tokens recorded across assistant messages, or 0.0.

    Reasoning tokens are billed as output but never appear in the visible
    content, so they can't be estimated from text length — we average the counts
    OpenRouter reported for past turns (0 when none were recorded).
    """
    counts = [
        int(m.get("reasoning_tokens", 0))
        for m in messages
        if m.get("role") == "assistant"
    ]
    return sum(counts) / len(counts) if counts else 0.0


def _average_context_tokens(messages):
    """Mean retrieved-context tokens recorded across assistant messages, or 0.0.

    When documents are uploaded, each turn injects a retrieved-context block
    into the system prompt. The block is per-turn (not part of the stored
    history), so — like reasoning tokens — it is recorded per assistant message
    and averaged for projections.
    """
    counts = [
        int(m.get("context_tokens", 0))
        for m in messages
        if m.get("role") == "assistant"
    ]
    return sum(counts) / len(counts) if counts else 0.0


def predict_next_call_tokens(system_prompt, messages):
    """Predict ``(input_tokens, output_tokens)`` for the next LLM call.

    The next call resends the system prompt plus the full existing history, then
    one more user message (whose length we extrapolate from the average user
    message so far); the model replies with one assistant message (extrapolated
    from the average assistant message so far, plus the average reasoning tokens
    it spent, which are billed as output but never show up in the content).

    Returns ``None`` when there is no completed exchange to extrapolate from
    (e.g. the very first prompt of a chat), so callers can display "N/A".
    """
    avg_user = _average_content_tokens(messages, "user")
    avg_assistant = _average_content_tokens(messages, "assistant")
    if avg_user is None or avg_assistant is None:
        return None

    input_tokens = (
        estimate_prompt_tokens(system_prompt, messages)
        + 4
        + avg_user
        + _average_context_tokens(messages)
    )
    output_tokens = avg_assistant + _average_reasoning_tokens(messages)
    return int(round(input_tokens)), int(round(output_tokens))


@dataclass(frozen=True)
class ContextUsage:
    """Estimated context-window usage for display in the sidebar."""

    model: str
    window: int | None
    source: str
    used_tokens: int

    @property
    def has_known_window(self) -> bool:
        return isinstance(self.window, int) and self.window > 0

    @property
    def used_percentage(self):
        if not self.has_known_window:
            return None
        return (self.used_tokens / self.window) * 100

    @property
    def progress(self) -> float:
        pct = self.used_percentage
        return min(max(pct / 100, 0.0), 1.0) if pct is not None else 0.0

    @property
    def window_label(self) -> str:
        return f"{self.window:,} tokens" if self.has_known_window else "unknown"


def compute_context_usage(model, api_key, system_prompt, messages) -> ContextUsage:
    window, source = get_model_context_window(model, api_key)
    used_tokens = estimate_prompt_tokens(system_prompt, messages) + int(
        round(_average_context_tokens(messages))
    )
    return ContextUsage(
        model=model, window=window, source=source, used_tokens=used_tokens
    )
