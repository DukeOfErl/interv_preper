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

## Git project management

**All work happens on a branch, scoped to an explicit work package.** A work package is a one- or two-sentence statement of the goal and what "done" looks like — small enough to live on a single branch and merge as a unit.

- **Branch; don't work on `dev`/`main`.** Each work package gets a dedicated branch off `dev` (or off a daughter branch, for sub-work that builds on still-unmerged work). Integration happens only through the merge flow, which requires the user's explicit approval (a `git merge` / `gh pr merge` guardrail lives in the user's settings).
- **No branch → stop and advise, don't start editing.** When changes are requested but nothing is open to hold them — the current branch is `dev`/`main`, or a branch whose work package doesn't cover the request — first help the user define a clear work package (goal + done-criteria) and open a dedicated branch for it (propose a name and scope). Begin the changes only once that branch exists.
- **Drift → flag it, don't silently absorb it.** While a work package is in progress, watch for scope drift: an unrelated fix, a second feature creeping in, or growth well beyond the stated goal. When it happens, say so explicitly ("this is drifting from *&lt;work package&gt;*") and propose how to proceed — typically one of: (a) move the extra work to its own branch off `dev`, (b) consciously widen the current work package if the addition genuinely belongs to it, or (c) defer/stash the tangent. Let the user choose.
- **Keep branches focused and short-lived** so they stay reviewable and merge cleanly.

## Documentation upkeep

When a change alters the architecture or user-visible functionality in a major way (new module, new pipeline, new UI capability, changed data flow), update **all four** docs in the same change — they serve different readers and go stale independently:

- **`README.md`** — what the app does and how to use it (for users and new developers)
- **`docs/diagrams/`** — the user/developer diagrams (currently `docs/diagrams/architecture.md`), updated **following the principles in `docs/diagrams/DIAGRAMS.md`** (one diagram per story, sequence diagrams for temporal flows, ~7±2 boxes each), together with the references to these diagrams from `README.md`
- **`docs/REQUIREMENTS.md`** — the behavioral spec (what the app must do, kept implementation-agnostic)
- **`CLAUDE.md`** — this file's Architecture section (how the code is organized, for coding agents)

Small fixes and internal refactors that don't change behavior or structure don't need this.

**Architecture Decision Records (`docs/decisions/`).** When a decision shapes the project in a way worth remembering — a non-obvious technical choice, an accepted trade-off, a convention, a reversal — record it as an ADR. Copy `docs/decisions/0000-decision-template.md` to `docs/decisions/NNNN-concise-kebab-name.md`, where `NNNN` is the highest existing id **plus 10** (the first real ADR is `0010-…`; `0000` is the reserved template). Gap numbering leaves room to slot a later decision between two existing ones. Fill in status, date, the pull request, and the context / decision / trade-off. Write the ADR as part of the same change that makes the decision — not retroactively.

**Changelog (`CHANGELOG.md`).** Keep `CHANGELOG.md` (project root, Keep a Changelog format) current as work progresses: for any user-visible or otherwise notable change (new capability, changed behavior, removal, fix), add a bullet under `## [Unreleased]` in the appropriate Added / Changed / Removed / Fixed group. Move those bullets into a versioned section when a release is tagged.

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
- `llm.py` — `InterviewLLM`: OpenRouter client, `stream_reply()` yields tokens; with a `toolbox` it becomes the tool-calling loop (stream → run tools → stream again, capped by `MAX_TOOL_HOPS`, cost accumulated across hops)
- `tools.py` — `ToolBox`: tool schemas + dispatcher (`run()` never raises — failures return error strings). One tool: `web_research(query, topic)`, consent-gated in its description
- `web_research.py` — `WebResearcher`: quarantined sub-completion over OpenRouter's `web` plugin; returns cited bullets + verbatim excerpts; citation links validated in code (ADR-0100)
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

**Grounding contract (must not break).** A prompt source **opts into** document RAG by containing the `{retrieved_context}` placeholder (`PromptLibrary.is_grounding_aware`); sources without it behave as if RAG didn't exist (and are offered no tools). The retrieved block is substituted with **`str.replace`, never `str.format`** — the other `{placeholders}` in the markdown are the model's to fill conversationally and must survive. `format_context_block()` in `retrieval.py` is the single definition of that block's shape (including the `### web search — <topic>: …` provenance header for web-research chunks); persona markdown is written against it.

**Subsystem behavior & rationale live in the docs, not here** (so this file stays lean and the detail stays in one canonical place):

- **`docs/REQUIREMENTS.md`** — the behavioral spec: document RAG, the guardrails, query condensation, cost/token accounting, reasoning effort, streaming.
- **`docs/decisions/`** — the ADRs (0010–0090): *why* each of those was built the way it was (e.g. fail-closed windowed document guardrail vs. fail-open concurrent chat guardrail; condense queries + defer LangGraph; LangChain for retrieval, raw SDK for chat).
- **`docs/diagrams/architecture.md`** — the chat-turn, ingestion, and module-map diagrams.

Read the relevant doc when a task touches that subsystem; keep it and the code in sync per **Documentation upkeep** above.

## Evals

`evals/` is a **standalone prompt-evaluation package, not part of the chatbot** (the app never imports it): it loads the real composed prompt and judges replies with DeepEval via OpenRouter. The spec is **`docs/REQUIREMENTS.md` § 12** (and § R15.14 for the still-pending grounding/retrieval metrics); edit `SCENARIOS` in `evals/dataset.py` to change what's tested and the `GEval` criteria in `evals/metrics.py` to change what "good" means.

