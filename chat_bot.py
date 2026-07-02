"""Streamlit entry point for the interview-prep chatbot.

Run with:  uv run streamlit run chat_bot.py

This module only wires together the pieces in the ``interview_prep`` package
and drives the Streamlit chat loop.
"""

import streamlit as st

from interview_prep.config import DEFAULT_MODEL, load_api_key
from interview_prep.context import compute_context_usage
from interview_prep.llm import InterviewLLM
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

    model = st.session_state["openai_model"]
    system_prompt = library.system_prompt
    messages = st.session_state["messages"]

    st.title("Interview preparation Chatbot")

    usage = compute_context_usage(model, api_key, system_prompt, messages)
    render_sidebar(library, usage)
    render_history(messages)

    has_user_prompt = any(m.get("role") == "user" for m in messages)
    placeholder = (
        ""
        if has_user_prompt
        else "For best results paste the relevant job ad and resume/CV here"
    )

    if prompt := st.chat_input(placeholder, key="main_chat_input"):
        with st.chat_message("user"):
            st.markdown(prompt)
        messages.append({"role": "user", "content": prompt})

        llm = InterviewLLM(api_key=api_key, model=model)
        with st.chat_message("assistant"):
            assistant_reply = st.write_stream(
                llm.stream_reply(system_prompt, messages)
            )
        messages.append({"role": "assistant", "content": assistant_reply})


if __name__ == "__main__":
    main()
