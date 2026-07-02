# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit chatbot that runs mock job interviews. It intakes a user's target role/resume, conducts a realistic interview one question at a time, and scores each answer. The interviewer's entire behavior lives in the markdown prompt files, not in Python — the Python is a thin Streamlit + LLM-streaming shell around a system prompt assembled from those files.

## Commands

This project uses **uv** (see `uv.lock`, Python 3.12).

```bash
uv sync                              # install deps
uv run streamlit run chat_bot.py     # run the app (the real entry point)
uv run pytest                        # run evals/tests
uv run pytest path/to/test.py::name  # run a single test
```

`OPENROUTER_API_KEY` must be set before running — via a local `.env` file (see `.env.example`, loaded by `config.load_api_key()`) or the environment. The app talks to OpenRouter via the OpenAI SDK (`base_url="https://openrouter.ai/api/v1"`), not to OpenAI directly. If the key is missing, `chat_bot.py` shows an `st.error` and calls `st.stop()` (fail fast).

`main.py` is an unused stub. **`chat_bot.py` is the entry point** — a thin Streamlit shell; the logic lives in the `interview_prep/` package.

## Architecture

`chat_bot.py:main()` wires together the `interview_prep` package and drives the chat loop. Each module has one job:

- `config.py` — constants, paths (`PROMPTS_DIR`, `PROMPT_FILE_NAMES`, `DEFAULT_MODEL`), and `load_api_key()`
- `prompts.py` — `PromptLibrary` / `PromptFile`: load and compose the markdown prompt
- `context.py` — token estimation + `get_model_context_window()`; `compute_context_usage()` returns a `ContextUsage`
- `llm.py` — `InterviewLLM`: OpenRouter client, `stream_reply()` yields tokens
- `ui.py` — `render_sidebar()` / `render_history()`

**System prompt is composed from markdown files at startup.** `PromptLibrary.load()` reads the files named in `config.PROMPT_FILE_NAMES` from `prompts/` and concatenates them (order matters) into `.system_prompt`, prepended to every LLM call. To change interviewer behavior, edit the markdown in `prompts/` — not the Python.

- `prompts/main_system_prompt.md` — role, core behavior, `{context_variable}` placeholders
- `prompts/info_intake.md` — Phase 1 (intake questions)
- `prompts/mock_interview.md` — Phase 2 (conducting the interview)
- `prompts/feedback_stage.md` — Phase 3 (per-answer scoring rubric)

**Model handling.** The model is fixed in `st.session_state["openai_model"]` (default `config.DEFAULT_MODEL` = `"GPT-5-Mini"`); there is no UI to switch it. The sidebar shows an estimated context-usage bar: `get_model_context_window()` first queries OpenRouter's `/models` endpoint (cached 1h via `st.cache_data`) and falls back to the static `MODEL_CONTEXT_WINDOWS` dict. Token counts are rough heuristics (`estimate_text_tokens` = chars/4), not real tokenization.

**Streaming.** `InterviewLLM.stream_reply()` yields the completion token-by-token with a `TYPING_DELAY_SECONDS` (0.05s) per-token delay for a typewriter effect via `st.write_stream`.

## Evals

`deepeval` and `pytest` (with xdist/rerunfailures) are dev dependencies for evaluating interviewer quality. `user_prompt_for_eval.txt` is a sample intake input used as an eval fixture.
