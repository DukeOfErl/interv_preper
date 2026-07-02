"""Streamlit rendering helpers."""

from __future__ import annotations

import streamlit as st

from .context import ContextUsage
from .prompts import PromptLibrary


def render_sidebar(library: PromptLibrary, usage: ContextUsage) -> None:
    """Render the model-context and prompt-config sidebar."""
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
