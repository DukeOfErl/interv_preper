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

**`chat_bot.py` is the entry point** — a thin Streamlit shell; the logic lives in the `interview_prep/` package.

## Architecture

`chat_bot.py:main()` wires together the `interview_prep` package and drives the chat loop. Each module has one job:

- `config.py` — constants, paths (`PROMPTS_DIR`, `DEFAULT_PROMPT_SOURCE`, `IGNORE_TAG`, `DEFAULT_MODEL`), and `load_api_key()`
- `prompts.py` — `PromptSource` / `PromptLibrary` / `PromptFile`: discover, load, and compose the markdown prompt
- `context.py` — token estimation + `get_model_context_window()`; `compute_context_usage()` returns a `ContextUsage`
- `llm.py` — `InterviewLLM`: OpenRouter client, `stream_reply()` yields tokens
- `ui.py` — `render_prompt_selector()` / `render_sidebar()` / `render_history()`

**System prompt is composed from markdown files, and the user picks which set.** The sidebar has a **System prompt** selector; each option is a *prompt source* discovered automatically from `prompts/` (no hardcoded file lists). A source is either:

1. a single top-level `.md` file (e.g. `simple_interviewer.md`, `few-shot_interviewer.md`), or
2. a subdirectory of `.md` files that are concatenated into one prompt (e.g. `multi-role interviewer/`).

`chat_bot.main()` calls `discover_sources()`, resolves the selected source (session key `prompt_source_key`, defaulting to `config.DEFAULT_PROMPT_SOURCE`), and builds the prompt with `PromptLibrary.from_source(...)`. To change interviewer behavior, edit the markdown in `prompts/` — not the Python. To add an interviewer, drop a new `.md` file or a new folder into `prompts/`; it appears in the selector on the next run.

Two filename conventions govern discovery (both defined and explained in `config.py`):

- **`IGNORE_TAG` (`.ignore`)** — a file whose name ends with `.ignore.md` is hidden from the selector. Use it for markdown in `prompts/` that isn't an interviewer persona. `guardrail.ignore.md` (the pre-send guardrail classifier prompt, loaded separately by `guardrails.py`) carries this tag.
- **Numeric filename prefixes** — files inside a subdirectory are concatenated in the order of a leading number (`10_…`, `20_…`, `30_…`). We use **gap numbering** (10, 20, 30 rather than 1, 2, 3) so a new file can be slotted between two existing ones (e.g. `15_…`) without renumbering everything after it. Files with no numeric prefix sort last.

The default source, `prompts/multi-role interviewer/`, holds the four staged prompts:
- `10_main_system_prompt.md` — role, core behavior, `{context_variable}` placeholders
- `20_info_intake.md` — Phase 1 (intake questions)
- `30_mock_interview.md` — Phase 2 (conducting the interview)
- `40_feedback_stage.md` — Phase 3 (per-answer scoring rubric)

**Model handling.** The model is fixed in `st.session_state["openai_model"]` (default `config.DEFAULT_MODEL` = `"GPT-5-Mini"`); there is no UI to switch it. The sidebar shows an estimated context-usage bar: `get_model_context_window()` first queries OpenRouter's `/models` endpoint (cached 1h via `st.cache_data`) and falls back to the static `MODEL_CONTEXT_WINDOWS` dict. Token counts are rough heuristics (`estimate_text_tokens` = chars/4), not real tokenization.

**Reasoning effort.** When the active model is a reasoning model, the sidebar shows a **Reasoning → Effort** selector (`config.REASONING_EFFORTS` = low/medium/high, default `DEFAULT_REASONING_EFFORT`). Whether to show it is decided by `context.model_supports_reasoning()`, which checks for `reasoning` in the model's OpenRouter `supported_parameters` (returns `False` — selector hidden — when the model is unknown or the catalog is unavailable). The chosen effort lives in `st.session_state["reasoning_effort"]` and is passed to `InterviewLLM(reasoning_effort=...)`, which sends it as `extra_body={"reasoning": {"effort": ...}}`; non-reasoning models send no reasoning param.

**Streaming.** `InterviewLLM.stream_reply()` yields the completion token-by-token with a `TYPING_DELAY_SECONDS` (0.05s) per-token delay for a typewriter effect via `st.write_stream`.

## Evals

`deepeval` and `pytest` (with xdist/rerunfailures) are dev dependencies for evaluating interviewer quality. `user_prompt_for_eval.txt` is a sample intake input used as an eval fixture.

**`evals/` is a standalone prompt-evaluation package — not part of the chatbot** (the Streamlit app never imports it). It loads the real composed system prompt via `interview_prep.prompts.PromptLibrary`, generates a reply per scenario with an app-under-test caller, and judges each reply with DeepEval metrics through OpenRouter. Run it with `uv run python -m evals` (see README for flags), or via the `integration`-marked tests in `tests/test_evals_integration.py`.

- `evals/judge.py` — `OpenRouterJudge(DeepEvalBaseLLM)` (the judge wrapper) and `AppUnderTest` (the non-streaming one-turn caller that mirrors what `chat_bot.py` sends, prompt verbatim)
- `evals/dataset.py` — interview scenarios as `Golden`s; `key_aspects` → `Golden.context`, `category`/`difficulty`/`id` → `additional_metadata`
- `evals/metrics.py` — the 6 metrics keyed by `METRIC_NAMES`; `behavior_rules` is a `GEval` built dynamically from the bullet rules extracted from the system prompt (`extract_behavior_rules`)
- `evals/runner.py` — `evaluate_prompt(...)` orchestrates generate → judge → aggregate into an `EvalReport`; pass `system_prompt=` to A/B test an edited prompt
- `evals/__main__.py` — the `python -m evals` CLI (`--limit`, `--metrics`, `--threshold`, `--fail-under`, model overrides)

To change what "good" means, edit the `GEval` criteria in `metrics.py`; to change what is tested, edit `SCENARIOS` in `dataset.py`. Score convention is DeepEval's: a float in `[0, 1]`, higher is better.
