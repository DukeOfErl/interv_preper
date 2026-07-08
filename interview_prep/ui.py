"""Streamlit rendering helpers."""

from __future__ import annotations

import streamlit as st

from .context import ContextUsage
from .pricing import ChatSpend, format_spend
from .prompts import PromptLibrary


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
