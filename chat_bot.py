"""Streamlit entry point for the interview-prep chatbot.

Run with:  uv run streamlit run chat_bot.py

This module only wires together the pieces in the ``interview_prep`` package
and drives the Streamlit chat loop.
"""

import streamlit as st

from interview_prep.config import DEFAULT_MODEL, load_api_key
from interview_prep.context import (
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    predict_next_call_tokens,
)
from interview_prep.guardrails import JailbreakGuard
from interview_prep.llm import InterviewLLM
from interview_prep.pricing import ChatSpend, get_model_pricing, turn_cost
from interview_prep.prompts import PromptLibrary
from interview_prep.ui import render_history, render_sidebar


def main() -> None:
    api_key = load_api_key()
    if not api_key:
        st.error(
            "OPENROUTER_API_KEY is not set. Add it to a .env file (see .env.example) "
            "or export it in your environment, then restart the app."
        )
        st.stop()

    library = PromptLibrary.load()
    if library.is_empty:
        st.error(
            "Markdown prompt files could not be loaded, so interview prep is not "
            "available."
        )
        st.stop()

    st.session_state.setdefault("openai_model", DEFAULT_MODEL)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("total_cost", 0.0)

    model = st.session_state["openai_model"]
    system_prompt = library.system_prompt
    messages = st.session_state["messages"]

    st.title("Interview preparation Chatbot")

    usage = compute_context_usage(model, api_key, system_prompt, messages)
    pricing = get_model_pricing(model, api_key)
    next_tokens = predict_next_call_tokens(system_prompt, messages)
    spend = ChatSpend(
        total_cost=st.session_state["total_cost"],
        pricing=pricing,
        next_estimate=(
            turn_cost(pricing, *next_tokens) if next_tokens is not None else None
        ),
    )
    render_sidebar(library, usage, spend)
    render_history(messages)

    has_user_prompt = any(m.get("role") == "user" for m in messages)
    placeholder = (
        ""
        if has_user_prompt
        else "For best results paste the relevant job ad and resume/CV here"
    )

    # If the last message is an unanswered user prompt, generate the reply now.
    # This runs on the rerun triggered right after a prompt is submitted, so the
    # sidebar above has already recomputed usage with the user prompt included.
    if messages and messages[-1]["role"] == "user":
        llm = InterviewLLM(api_key=api_key, model=model)
        with st.chat_message("assistant"):
            assistant_reply = st.write_stream(
                llm.stream_reply(system_prompt, messages)
            )

        # Accrue this turn's cost using the model that produced it, so a chat
        # that switches models later still sums correctly. Prefer the provider's
        # reported token usage; fall back to rough estimates if it's absent.
        usage_report = llm.last_usage
        if usage_report is not None:
            prompt_tokens = usage_report.prompt_tokens or 0
            completion_tokens = usage_report.completion_tokens or 0
        else:
            prompt_tokens = estimate_prompt_tokens(system_prompt, messages)
            completion_tokens = estimate_text_tokens(assistant_reply)
        st.session_state["total_cost"] += turn_cost(
            get_model_pricing(model, api_key), prompt_tokens, completion_tokens
        )

        messages.append({"role": "assistant", "content": assistant_reply})
        # Rerun so usage and spend are recomputed with the assistant reply too.
        st.rerun()

    if prompt := st.chat_input(placeholder, key="main_chat_input"):
        # Screen for jailbreak / prompt-injection before spending a real call.
        # Fails open, so a classifier outage never blocks legitimate prompts.
        verdict = JailbreakGuard(api_key=api_key).check(prompt)
        if verdict.allowed:
            messages.append({"role": "user", "content": prompt})
            # Rerun immediately so usage recomputes with the user prompt included
            # before the assistant reply streams (handled by the block above).
            st.rerun()
        else:
            st.warning(
                "⚠️ That prompt was blocked by the safety guardrail"
                + (f": {verdict.reason}" if verdict.reason else ".")
            )


if __name__ == "__main__":
    main()
