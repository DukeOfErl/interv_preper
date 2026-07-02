"""Model context-window lookup and rough token estimation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request

import streamlit as st

from .config import (
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CONTEXT_WINDOWS,
    MODELS_CACHE_TTL_SECONDS,
    OPENROUTER_BASE_URL,
)


@st.cache_data(ttl=MODELS_CACHE_TTL_SECONDS)
def fetch_openrouter_models(api_key):
    """Fetch the OpenRouter model catalog (cached). Returns [] on any failure."""
    if not api_key:
        return []

    req = request.Request(
        f"{OPENROUTER_BASE_URL}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with request.urlopen(req, timeout=8) as api_result_handle:
            payload = json.loads(api_result_handle.read().decode("utf-8"))
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []

    return payload.get("data", []) if isinstance(payload, dict) else []


def get_model_context_window(model_id, api_key):
    """Return ``(context_window, source)`` for a model id.

    Prefers a live value from OpenRouter, then falls back to the static
    ``MODEL_CONTEXT_WINDOWS`` table, then to ``DEFAULT_CONTEXT_WINDOW``.
    """
    models = fetch_openrouter_models(api_key)
    normalized_model_id = str(model_id or "").strip().lower()

    if not normalized_model_id:
        return DEFAULT_CONTEXT_WINDOW, "static_fallback"

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
        if not is_match:
            continue

        direct_context = model.get("context_length")
        provider_context = model.get("top_provider", {}).get("context_length")
        resolved_ctx_len = direct_context or provider_context
        if isinstance(resolved_ctx_len, int) and resolved_ctx_len > 0:
            return resolved_ctx_len, "OpenRouter"

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
    used_tokens = estimate_prompt_tokens(system_prompt, messages)
    return ContextUsage(
        model=model, window=window, source=source, used_tokens=used_tokens
    )
