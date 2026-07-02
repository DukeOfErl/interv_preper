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

`OPENROUTER_API_KEY` must be set in the environment before running — the app talks to OpenRouter via the OpenAI SDK (`base_url="https://openrouter.ai/api/v1"`), not to OpenAI directly.

`main.py` is an unused stub. **`chat_bot.py` is the application.**

## Architecture

**System prompt is composed from markdown files at startup.** `chat_bot.py` reads `PROMPT_FILE_NAMES` and concatenates them (order matters) into `SYSTEM_PROMPT`, which is prepended to every LLM call. To change interviewer behavior, edit the markdown — not the Python.

- `main_system_prompt.md` — role, core behavior, `{context_variable}` placeholders
- `info_intake.md` — Phase 1 (intake questions)
- `mock_interview.md` — Phase 2 (conducting the interview)
- `feedback_stage.md` — Phase 3 (per-answer scoring rubric)

**Model handling.** The model is fixed in `st.session_state["openai_model"]` (default `"GPT-5-Mini"`); there is no UI to switch it. The sidebar shows an estimated context-usage bar: `get_model_context_window()` first queries OpenRouter's `/models` endpoint (cached 1h) and falls back to the static `MODEL_CONTEXT_WINDOWS` dict. Token counts are rough heuristics (`estimate_text_tokens` = chars/4), not real tokenization.

**Streaming.** `response_emulator()` wraps the streamed completion and adds a 0.05s per-token delay for a typewriter effect via `st.write_stream`.

## Evals

`deepeval` and `pytest` (with xdist/rerunfailures) are dev dependencies for evaluating interviewer quality. `user_prompt_for_eval.txt` is a sample intake input used as an eval fixture.
