"""Streamlit entry point for the interview-prep chatbot.

Run with:  uv run streamlit run chat_bot.py

This module only wires together the pieces in the ``interview_prep`` package
and drives the Streamlit chat loop.
"""

import streamlit as st
from openai import APIError, AuthenticationError

from interview_prep.config import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    REASONING_EFFORTS,
    load_api_key,
)
from interview_prep.context import (
    compute_context_usage,
    estimate_prompt_tokens,
    estimate_text_tokens,
    model_supports_reasoning,
    predict_next_call_tokens,
)
from interview_prep.guardrails import JailbreakGuard
from interview_prep.llm import InterviewLLM
from interview_prep.pricing import ChatSpend, get_model_pricing, turn_cost
from interview_prep.prompts import (
    PromptLibrary,
    default_source,
    discover_sources,
)
from interview_prep.ui import (
    render_api_key_input,
    render_history,
    render_prompt_selector,
    render_reasoning_selector,
    render_sidebar,
)


def main() -> None:
    # Prefer a key from the environment/.env; otherwise let the user paste one
    # into the sidebar (kept in session only). Fail fast until we have a key.
    env_key = load_api_key()
    key_from_env = bool(env_key)
    api_key = env_key or render_api_key_input()
    if not api_key:
        st.info(
            "Enter your OpenRouter API key in the sidebar to get started. "
            "Alternatively, set OPENROUTER_API_KEY in a .env file (see "
            ".env.example) or your environment and restart."
        )
        st.stop()

    sources = discover_sources()
    if not sources:
        st.error(
            "No prompt sources found in prompts/, so interview prep is not "
            "available."
        )
        st.stop()

    st.session_state.setdefault("openai_model", DEFAULT_MODEL)
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("total_cost", 0.0)
    st.session_state.setdefault("prompt_source_key", default_source(sources).key)
    # A stored key can go stale if a source is renamed/removed between runs;
    # reset it before the widget renders, since the selectbox requires its bound
    # value to be one of the current options.
    source_keys = {s.key for s in sources}
    if st.session_state["prompt_source_key"] not in source_keys:
        st.session_state["prompt_source_key"] = default_source(sources).key

    # Render the selector first so a change is reflected on the same run.
    render_prompt_selector(sources)
    selected = next(
        s for s in sources if s.key == st.session_state["prompt_source_key"]
    )

    library = PromptLibrary.from_source(selected)
    if library.is_empty:
        st.error(
            "The selected prompt source has no usable markdown content, so "
            "interview prep is not available."
        )
        st.stop()

    model = st.session_state["openai_model"]
    system_prompt = library.system_prompt
    messages = st.session_state["messages"]

    # Reasoning effort is only meaningful for reasoning models, so the selector
    # is shown (and the param sent) only when the active model supports it.
    supports_reasoning = model_supports_reasoning(model, api_key)
    reasoning_effort = None
    if supports_reasoning:
        st.session_state.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
        render_reasoning_selector(REASONING_EFFORTS)
        reasoning_effort = st.session_state["reasoning_effort"]

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
        llm = InterviewLLM(
            api_key=api_key, model=model, reasoning_effort=reasoning_effort
        )
        try:
            with st.chat_message("assistant"):
                assistant_reply = st.write_stream(
                    llm.stream_reply(system_prompt, messages)
                )
        except AuthenticationError:
            # The key was rejected. Keep the pending prompt so the reply is
            # generated automatically once a working key is supplied.
            hint = (
                "Enter a different OpenRouter API key in the sidebar and try "
                "again."
                if not key_from_env
                else "Check the OPENROUTER_API_KEY in your environment/.env, "
                "then restart the app."
            )
            st.warning(f"⚠️ That API key didn't work (authentication failed). {hint}")
            st.stop()
        except APIError as exc:
            # Any other OpenRouter/API failure (rate limit, connection, etc.).
            detail = getattr(exc, "message", None) or str(exc)
            st.warning(
                "⚠️ The request to OpenRouter failed, so no reply was generated. "
                f"Please try again. ({detail})"
            )
            st.stop()

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
