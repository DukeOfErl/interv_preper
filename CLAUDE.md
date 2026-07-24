# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## LLM Coding Guidelines (Karpathy-inspired)

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Documentation upkeep

When a change alters the architecture or user-visible functionality in a major way (new module, new pipeline, new UI capability, changed data flow), update **all four** docs in the same change — they serve different readers and go stale independently:

- **`README.md`** — what the app does and how to use it (for users and new developers)
- **`architecture.md`** — the mermaid diagram + walkthrough (module boundaries, data flow, external calls)
- **`REQUIREMENTS.md`** — the behavioral spec (what the app must do, kept implementation-agnostic)
- **`CLAUDE.md`** — this file's Architecture section (how the code is organized, for coding agents)

Small fixes and internal refactors that don't change behavior or structure don't need this.

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
- `ingest.py` — `parse_document()` (PDF/DOCX/TXT/MD → text), `infer_doc_type()`, `should_ingest()` (the fail-closed screening policy)
- `retrieval.py` — `DocumentIndex` (LangChain `InMemoryVectorStore` + `OpenAIEmbeddings` via OpenRouter), `format_context_block()` / `fill_retrieved_context()` (the context-block contract)
- `query_rewrite.py` — `QueryCondenser`: rewrites a follow-up message into a standalone retrieval query (fails open)
- `ui.py` — `render_prompt_selector()` / `render_sidebar()` / `render_history()` / document uploader + panels

**System prompt is composed from markdown files, and the user picks which set.** The sidebar has a **System prompt** selector; each option is a *prompt source* discovered automatically from `prompts/` (no hardcoded file lists). A source is either:

1. a single top-level `.md` file (e.g. `simple_interviewer.md`, `few-shot_interviewer.md`), or
2. a subdirectory of `.md` files that are concatenated into one prompt (e.g. `multi-role interviewer/`).

`chat_bot.main()` calls `discover_sources()`, resolves the selected source (session key `prompt_source_key`, defaulting to `config.DEFAULT_PROMPT_SOURCE`), and builds the prompt with `PromptLibrary.from_source(...)`. To change interviewer behavior, edit the markdown in `prompts/` — not the Python. To add an interviewer, drop a new `.md` file or a new folder into `prompts/`; it appears in the selector on the next run.

Two filename conventions govern discovery (both defined and explained in `config.py`):

- **`IGNORE_TAG` (`.ignore`)** — a file whose name ends with `.ignore.md` is hidden from the selector. Use it for markdown in `prompts/` that isn't an interviewer persona. `guardrail.ignore.md` (the pre-send guardrail classifier prompt, loaded separately by `guardrails.py`) carries this tag.
- **Numeric filename prefixes** — files inside a subdirectory are concatenated in the order of a leading number (`10_…`, `20_…`, `30_…`). We use **gap numbering** (10, 20, 30 rather than 1, 2, 3) so a new file can be slotted between two existing ones (e.g. `15_…`) without renumbering everything after it. Files with no numeric prefix sort last.

The default source, `prompts/multi-role interviewer/`, holds the staged prompts:
- `10_main_system_prompt.md` — role, core behavior, `{context_variable}` placeholders
- `15_grounding.md` — the `{retrieved_context}` slot + rules for using uploaded documents
- `20_info_intake.md` — Phase 1 (intake questions)
- `30_mock_interview.md` — Phase 2 (conducting the interview)
- `40_feedback_stage.md` — Phase 3 (per-answer scoring rubric)

**Document RAG.** Users drag-and-drop resume / job ad / cover letter files into a sidebar uploader (`ingest.parse_document` → text). Each document is screened by `JailbreakGuard.check_document` **before** ingestion — in **overlapping windows scanned concurrently** (`GUARDRAIL_SCAN_WINDOW_CHARS` / `GUARDRAIL_SCAN_CONCURRENCY`), because a whole-document scan reliably misses an injected line diluted by pages of benign resume text. Document scans use the mid-size `GUARDRAIL_DOC_MODEL` (reliable at much larger windows than the per-turn nano; off the latency-critical path). This screening **fails closed per document** (`ingest.should_ingest`): a flagged document *or* a failed scan rejects that document (with a sidebar warning), while the chat itself stays available — the opposite polarity of the fail-open per-turn chat guardrail. Clean documents are chunked and embedded into a session-scoped `retrieval.DocumentIndex` (LangChain `InMemoryVectorStore`; embeddings via OpenRouter's `/embeddings` endpoint, model user-selectable from `config.EMBEDDING_MODELS` — switching re-embeds everything, since vectors from different models aren't comparable). Each turn, `chat_bot.main()` builds the retrieval query — on follow-up turns via `query_rewrite.QueryCondenser`, a cheap `QUERY_REWRITE_MODEL` call that rewrites the message into a standalone search query (markdown-driven, fails open to the raw message, skipped on the first turn) — retrieves the top-`TOP_K` chunks, and substitutes a labeled context block into the system prompt's `{retrieved_context}` placeholder — **with `str.replace`, never `str.format`** (the other `{placeholders}` in the markdown are the model's to fill conversationally and must survive). A prompt source **opts into grounding** by containing that placeholder (`PromptLibrary.is_grounding_aware`); sources without it behave exactly as before. `format_context_block()` in `retrieval.py` is the single definition of the injected block's shape — persona markdown is written against it. The injected tokens are recorded per assistant message (`context_tokens`, same pattern as `reasoning_tokens`) so context/cost projections account for them.

**Model handling.** The model is fixed in `st.session_state["openai_model"]` (default `config.DEFAULT_MODEL` = `"GPT-5-Mini"`); there is no UI to switch it. The sidebar shows an estimated context-usage bar: `get_model_context_window()` first queries OpenRouter's `/models` endpoint (cached 1h via `st.cache_data`) and falls back to the static `MODEL_CONTEXT_WINDOWS` dict. Token counts are rough heuristics (`estimate_text_tokens` = chars/4), not real tokenization.

**Cost accounting.** The **accrued** spend (`st.session_state["total_cost"]`) is the *actual* USD OpenRouter charges: `stream_reply()` requests usage accounting (`extra_body={"usage": {"include": True}}`) and reads the real `cost` off the final usage chunk into `InterviewLLM.last_cost`; the chat loop adds that per turn, falling back to reported-tokens × price (`turn_cost`) and then to chars/4 estimates only when `cost` is absent. The **next-prompt estimate** (`ChatSpend.next_estimate`) stays a projection: `predict_next_call_tokens()` × `turn_cost`. Because each call resends the whole growing history, the predicted input rises turn over turn (`estimate_prompt_tokens` over full history); the predicted output adds the running average of `reasoning_tokens` (captured per turn into `InterviewLLM.last_reasoning_tokens` from `completion_tokens_details`, stored on each assistant message), since reasoning tokens are billed as output but never appear in the visible content.

**Reasoning effort.** When the active model is a reasoning model, the sidebar shows a **Reasoning → Effort** selector (`config.REASONING_EFFORTS` = low/medium/high, default `DEFAULT_REASONING_EFFORT`). Whether to show it is decided by `context.model_supports_reasoning()`, which checks for `reasoning` in the model's OpenRouter `supported_parameters` (returns `False` — selector hidden — when the model is unknown or the catalog is unavailable). The chosen effort lives in `st.session_state["reasoning_effort"]` and is passed to `InterviewLLM(reasoning_effort=...)`, which adds `{"reasoning": {"effort": ...}}` to the same `extra_body` that already carries the usage-accounting flag; non-reasoning models send no reasoning param.

**Streaming & the concurrent guardrail.** `InterviewLLM.stream_reply()` yields the completion token-by-token with a `TYPING_DELAY_SECONDS` (0.05s) per-token delay for a typewriter effect. A turn is a **single pass**: `chat_bot.main()` shows the user prompt, then launches `JailbreakGuard.check()` on a background thread (`ThreadPoolExecutor`) *concurrently* with the stream, painting tokens into an `st.empty()` placeholder as they arrive (a manual loop, not `st.write_stream`, so it can be interrupted). It polls the guardrail future between tokens; if the verdict is a jailbreak — whenever it lands — the stream stops, the placeholder is cleared, and nothing is persisted. Only on an allowed verdict are the user + assistant messages appended and a **single** `st.rerun()` fired, which is what refreshes the sidebar (context usage + spend) — deliberately *after* the answer completes, not the moment the prompt is sent.

## Evals

`deepeval` and `pytest` (with xdist/rerunfailures) are dev dependencies for evaluating interviewer quality. `user_prompt_for_eval.txt` is a sample intake input used as an eval fixture.

**`evals/` is a standalone prompt-evaluation package — not part of the chatbot** (the Streamlit app never imports it). It loads the real composed system prompt via `interview_prep.prompts.PromptLibrary`, generates a reply per scenario with an app-under-test caller, and judges each reply with DeepEval metrics through OpenRouter. Run it with `uv run python -m evals` (see README for flags), or via the `integration`-marked tests in `tests/test_evals_integration.py`.

- `evals/judge.py` — `OpenRouterJudge(DeepEvalBaseLLM)` (the judge wrapper) and `AppUnderTest` (the non-streaming one-turn caller that mirrors what `chat_bot.py` sends, prompt verbatim)
- `evals/dataset.py` — interview scenarios as `Golden`s; `key_aspects` → `Golden.context`, `category`/`difficulty`/`id` → `additional_metadata`
- `evals/metrics.py` — the 6 metrics keyed by `METRIC_NAMES`; `behavior_rules` is a `GEval` built dynamically from the bullet rules extracted from the system prompt (`extract_behavior_rules`)
- `evals/runner.py` — `evaluate_prompt(...)` orchestrates generate → judge → aggregate into an `EvalReport`; pass `system_prompt=` to A/B test an edited prompt
- `evals/__main__.py` — the `python -m evals` CLI (`--limit`, `--metrics`, `--threshold`, `--fail-under`, model overrides)

To change what "good" means, edit the `GEval` criteria in `metrics.py`; to change what is tested, edit `SCENARIOS` in `dataset.py`. Score convention is DeepEval's: a float in `[0, 1]`, higher is better.

