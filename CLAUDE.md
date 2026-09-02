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
- `agent.py` — `InterviewAgent`: the turn, as a LangChain `create_agent` graph. `stream_reply()` yields tokens; it asks the stream for `["messages", "custom", "values"]` at once, so the reply comes from the graph's **final state** (not reconstructed from chunks), progress arrives on the consuming thread, and spend accumulates across hops. The only loop (ADR-0170)
- `middleware.py` — the harness: `InterviewState` (turn-scoped `files_read`, `citations`, `evaluations`, counters, `extra_cost`), `content_policy_middleware` (screens **every** tool result and records provenance only for what it admits), `announce_exhausted_tools`, `catch_typed_tool_call`, `final_hop_note`
- `policy.py` — `ContentPolicy` / `ToolOutcome`: the fail-closed screen-and-index rules, deliberately importing no agent framework (ADR-0150)
- `permissions.py` — `Role` / `Permission` / `has()` / `resolve_role()` / `current_role()`: the role model as a pure function, importing neither Streamlit nor any agent framework, so the same check serves the page, the agent path and the tests. Fail-closed to `Role.USER` on every input it cannot interpret. `current_role(lookup)` is the **identity port** — its one adapter is `chat_bot.role_lookup` (Streamlit secrets, then environment; never the browser). No authentication yet (ADR-0190, REQUIREMENTS § 20)
- `tools.py` — the tools themselves, as independent LangChain tools in a list: `web_research(query, topic)` (consent-gated), `record_evaluation(...)` (always-call after scoring — the structured-output channel behind the Evaluations tab, ADR-0120; returns a `Command` that writes state), plus one relaying tool per discovered MCP tool (ADR-0130). **Tools screen nothing** — they report what they got as a `ToolOutcome` on the artifact channel and the middleware decides what the model sees
- `web_research.py` — `WebResearcher`: quarantined sub-completion over OpenRouter's `web` plugin; returns cited bullets + verbatim excerpts; citation links validated in code (ADR-0100)
- `github_mcp.py` — `GitHubMCP`: MCP client of the official GitHub remote MCP server (streamable HTTP + `GITHUB_PAT`); discovers tools at runtime, forwards a read-only allowlist with a consent suffix, relays calls one `asyncio.run` connection at a time (forcing compact-output args), returns an `MCPResult` that splits a fetched file's body from the inline text; discovery cached per session, fails soft. Also the after-the-fact fabrication checks — `unverified_references()` (invented names) and `unquoted_code_blocks()` (code blocks matching no fetched file) (ADR-0130)
- `ingest.py` — `parse_document()` (PDF/DOCX/TXT/MD → text), `infer_doc_type()`, `should_ingest()` (the fail-closed screening policy)
- `retrieval.py` — `DocumentIndex` (LangChain `InMemoryVectorStore` + `OpenAIEmbeddings` via OpenRouter), `format_context_block()` / `fill_retrieved_context()` (the context-block contract)
- `knowledgebase.py` — `KnowledgeBase`: persistent curated reference material. Seeds live in `knowledgebase/*.md` (YAML frontmatter: category/tags, versioned in git); the derived SQLite file `data/knowledgebase.db` (gitignored) caches chunks + per-model embeddings, reconciled at startup by per-file content hash; cosine retrieval returns `RetrievedChunk`s that merge into the same context block (ADR-0110)
- `query_rewrite.py` — `QueryCondenser`: rewrites a follow-up message into a standalone retrieval query (fails open)
- `grounding.py` — `ground_turn()` / `GroundedTurn`: the turn's grounding **policy** — condense (only on a follow-up), retrieve from documents and knowledge base **independently fail-open**, render the context block, fill the slot. Imports no Streamlit: the condenser and warning sink are injected, spinners stay with the caller. `query is None` means nothing was searched, which is how `chat_bot` avoids blanking the last-retrieval panel on an ungrounded turn
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

**Grounding contract (must not break).** A prompt source **opts into** document RAG by containing the `{retrieved_context}` placeholder (`PromptLibrary.is_grounding_aware`); sources without it behave as if RAG didn't exist (and are offered no tools). The retrieved block is substituted with **`str.replace`, never `str.format`** — the other `{placeholders}` in the markdown are the model's to fill conversationally and must survive. `format_context_block()` in `retrieval.py` is the single definition of that block's shape (including the `### web search — <topic>: …` and `### knowledge base — <category>: …` provenance headers for web-research and knowledge-base chunks); persona markdown is written against it.

**Subsystem behavior & rationale live in the docs, not here** (so this file stays lean and the detail stays in one canonical place):

- **`docs/REQUIREMENTS.md`** — the behavioral spec: document RAG, the guardrails, query condensation, cost/token accounting, reasoning effort, streaming.
- **`docs/decisions/`** — the ADRs (0010–0090): *why* each of those was built the way it was (e.g. fail-closed windowed document guardrail vs. fail-open concurrent chat guardrail; condense queries + defer LangGraph; LangChain for retrieval, raw SDK for chat).
- **`docs/diagrams/architecture.md`** — the chat-turn, tool-turn (web research / GitHub MCP), **agent-graph**, ingestion, and module-map diagrams.

Read the relevant doc when a task touches that subsystem; keep it and the code in sync per **Documentation upkeep** above.

## Evals

`evals/` is a **standalone prompt-evaluation package, not part of the chatbot** (the app never imports it): it loads the real composed prompt and judges replies with DeepEval via OpenRouter. The spec is **`docs/REQUIREMENTS.md` § 12** (and § R15.14 for the still-pending grounding/retrieval metrics); edit `SCENARIOS` in `evals/dataset.py` to change what's tested and the `GEval` criteria in `evals/metrics.py` to change what "good" means.

