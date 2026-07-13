"""Streamlit rendering helpers."""

from __future__ import annotations

import streamlit as st

from .context import ContextUsage
from .pricing import ChatSpend, format_spend
from .prompts import PromptLibrary, PromptSource


def render_api_key_input() -> str:
    """Render a sidebar input for a session-scoped OpenRouter API key.

    Only meant to be shown when no key was found in the environment. The value
    is held in ``st.session_state["user_api_key"]`` for this browser session
    only — it is never written to disk. Returns the entered key (stripped).
    """
    with st.sidebar:
        st.subheader("API key")
        key = st.text_input(
            "OpenRouter API key",
            type="password",
            placeholder="sk-or-...",
            key="user_api_key",
            help=(
                "No OPENROUTER_API_KEY was found in your environment. Enter one "
                "to use the app. It is kept only for this session (in memory) "
                "and never saved to disk."
            ),
        )
    return (key or "").strip()


def render_prompt_selector(sources: list[PromptSource]) -> None:
    """Render the system-prompt source picker.

    The selectbox is bound to ``st.session_state["prompt_source_key"]``; picking
    a new source triggers a rerun and the entry point rebuilds the prompt from
    it. A folder icon marks multi-file (directory) sources.
    """
    with st.sidebar:
        st.subheader("Interviewer")

        def _label(key: str) -> str:
            source = next(s for s in sources if s.key == key)
            return f"📁 {source.label}" if source.is_directory else source.label

        st.selectbox(
            "System prompt",
            options=[s.key for s in sources],
            format_func=_label,
            key="prompt_source_key",
            help=(
                "Pick a single prompt file or a folder of files that are "
                "concatenated in numeric-prefix order."
            ),
        )


def render_reasoning_selector(efforts: list[str]) -> None:
    """Render the reasoning-effort picker.

    Only call this when the active model is a reasoning model — the caller
    decides whether to show it. Bound to ``st.session_state["reasoning_effort"]``.
    """
    with st.sidebar:
        st.subheader("Reasoning")
        st.selectbox(
            "Effort",
            options=efforts,
            key="reasoning_effort",
            help=(
                "How hard the model thinks before answering. Higher effort can "
                "improve quality but adds latency and cost."
            ),
        )


def render_sidebar(
    library: PromptLibrary, usage: ContextUsage, spend: ChatSpend
) -> None:
    """Render the model-context, spend, and prompt-config sidebar."""
    with st.sidebar:
        st.subheader("Model Context")
        st.text(f"Model: {usage.model}")
        st.text(f"Context window: {usage.window_label}")
        st.caption(f"Context source: {usage.source}")

        used_percentage = usage.used_percentage
        if used_percentage is not None:
            st.progress(usage.progress)
            st.caption(
                f"Estimated used: {used_percentage:.1f}% "
                f"({usage.used_tokens:,}/{usage.window:,} tokens)"
            )
        else:
            st.caption(
                f"Estimated used: unknown ({usage.used_tokens:,} tokens so far)"
            )

        st.subheader("Spend")
        total_col, next_col = st.columns(2)
        total_col.metric("Total this chat", format_spend(spend.total_cost))
        next_col.metric(
            "Est. next prompt",
            "N/A"
            if spend.next_estimate is None
            else format_spend(spend.next_estimate),
        )
        pricing = spend.pricing
        if pricing.is_known:
            st.caption(
                f"{pricing.model} rates: "
                f"\\${pricing.prompt_price * 1_000_000:,.2f} / 1M input tokens, "
                f"\\${pricing.completion_price * 1_000_000:,.2f} / 1M output tokens"
            )
        else:
            st.caption(f"Pricing unavailable for {pricing.model}")

        st.title("Developer Dashboard")

        st.subheader("Prompt Config")
        if library.source is not None:
            kind = "folder" if library.source.is_directory else "file"
            st.caption(
                f"Active source: **{library.source.label}** ({kind}) — "
                f"{len(library.files)} markdown file(s), in this order:"
            )
        else:
            st.caption("Markdown prompt files used by the chatbot")
        for prompt_file in library.files:
            status = "found" if prompt_file.exists else "missing"
            st.text(f"- {prompt_file.name} ({status})")

        with st.expander("Prompt Preview", expanded=False):
            for prompt_file in library.files:
                st.markdown(f"**{prompt_file.name}**")
                if prompt_file.content:
                    st.code(library.preview(prompt_file), language="markdown")
                elif prompt_file.exists:
                    st.code("(empty file)", language="markdown")
                else:
                    st.warning("File not found")


def render_history(messages) -> None:
    """Replay the stored chat history on rerun."""
    for entry in messages:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
