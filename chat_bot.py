import streamlit as st
import time
from openai import OpenAI
import os
import json
from pathlib import Path
from urllib import request, error
from dotenv import load_dotenv

# Load variables from a local .env file (if present) into the environment.
load_dotenv()


MODEL_CONTEXT_WINDOWS = {
    "gpt-3.5-turbo": 16385,
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
}
DEFAULT_CONTEXT_WINDOW = None


@st.cache_data(ttl=3600)
def fetch_openrouter_models(api_key):
    if not api_key:
        return []

    req = request.Request(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    try:
        with request.urlopen(req, timeout=8) as api_result_handle:
            payload = json.loads(api_result_handle.read().decode("utf-8"))
    except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []

    return payload.get("data", []) if isinstance(payload, dict) else []


def get_model_context_window(model_id, api_key):
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

    return MODEL_CONTEXT_WINDOWS.get(normalized_model_id, DEFAULT_CONTEXT_WINDOW), "static_fallback"


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



# Streamed response emulator
def response_emulator(stream_iter):
    for chunk in stream_iter:
        token = ""
        if chunk.choices and chunk.choices[0].delta:
            token = chunk.choices[0].delta.content or ""
        if token:
            yield token
            time.sleep(0.05)
    

# Set OpenRouter API key from environment variable
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    st.error(
        "OPENROUTER_API_KEY is not set. Add it to a .env file (see .env.example) "
        "or export it in your environment, then restart the app."
    )
    st.stop()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Set a default model
if "openai_model" not in st.session_state:
    st.session_state["openai_model"] = "GPT-5-Mini"

# Initialize chat history once so all later reads/writes use the same state key.
st.session_state.setdefault("messages", [])




# Markdown prompt files to load (order matters; files are concatenated).
PROMPT_FILE_NAMES = [ "main_system_prompt.md", "info_intake.md", "mock_interview.md", "feedback_stage.md"]
PROMPT_FILES = [Path(__file__).with_name(name) for name in PROMPT_FILE_NAMES]

prompt_file_content = {}
for prompt_file in PROMPT_FILES:
    if prompt_file.exists():
        prompt_file_content[prompt_file] = prompt_file.read_text(encoding="utf-8").strip()

SYSTEM_PROMPT = "\n\n".join(
    prompt_file_content[prompt_file]
    for prompt_file in PROMPT_FILES
    if prompt_file in prompt_file_content and prompt_file_content[prompt_file]
).strip()

if not SYSTEM_PROMPT:
    st.error(
        "Markdown prompt files could not be loaded, so interview prep is not available."
    )
    st.stop()


st.title("Interview preparation Chatbot")



with st.sidebar:
    current_model = st.session_state["openai_model"]
    context_window, context_source = get_model_context_window(
        current_model,
        OPENROUTER_API_KEY,
    )
    used_tokens = estimate_prompt_tokens(
        SYSTEM_PROMPT,
        st.session_state["messages"],
    )
    has_known_context_window = isinstance(context_window, int) and context_window > 0
    used_percentage = (
        (used_tokens / context_window) * 100 if has_known_context_window else None
    )
    bounded_progress = (
        min(max(used_percentage / 100, 0.0), 1.0)
        if used_percentage is not None
        else 0.0
    )
    context_window_label = (
        f"{context_window:,} tokens" if has_known_context_window else "unknown"
    )

    st.subheader("Model Context")
    st.text(f"Model: {current_model}")
    st.text(f"Context window: {context_window_label}")
    st.caption(f"Context source: {context_source}")
    if used_percentage is not None:
        st.progress(bounded_progress)
        st.caption(
            f"Estimated used: {used_percentage:.1f}% ({used_tokens:,}/{context_window:,} tokens)"
        )
    else:
        st.caption(f"Estimated used: unknown ({used_tokens:,} tokens so far)")

    st.subheader("Prompt Config")
    st.caption("Markdown prompt files used by the chatbot")

    for prompt_file in PROMPT_FILES:
        file_status = "found" if prompt_file.exists() else "missing"
        st.text(f"- {prompt_file.name} ({file_status})")

    with st.expander("Prompt Preview", expanded=False):
        for prompt_file in PROMPT_FILES:
            st.markdown(f"**{prompt_file.name}**")
            if prompt_file in prompt_file_content and prompt_file_content[prompt_file]:
                preview_lines = prompt_file_content[prompt_file].splitlines()[:8]
                st.code("\n".join(preview_lines), language="markdown")
            elif prompt_file.exists():
                st.code("(empty file)", language="markdown")
            else:
                st.warning("File not found")

# Display chat messages from history on app rerun
for chat_entry in st.session_state.messages:
    with st.chat_message(chat_entry["role"]):
        st.markdown(chat_entry["content"])

# Accept user input
has_user_prompt = any(m.get("role") == "user" for m in st.session_state.messages)
chat_input_placeholder = "For best results paste the relevant job ad and resume/CV here" if not has_user_prompt else ""

if prompt := st.chat_input(chat_input_placeholder, key="main_chat_input"):
    # Display user message in chat message container
    with st.chat_message("user"):
        st.markdown(prompt)
    # Add user message to chat history
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Display assistant response in chat message container
    with st.chat_message("assistant"):
        chat_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *[
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages
            ],
        ]
        stream = client.chat.completions.create(
            model=st.session_state["openai_model"],
            messages=chat_messages,
            stream=True,
        )
        assistant_reply = st.write_stream(response_emulator(stream))
    st.session_state.messages.append({"role": "assistant", "content": assistant_reply})


