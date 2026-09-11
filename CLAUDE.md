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

`.streamlit/secrets.toml` must also exist before the app serves anyone — it holds the `[auth]` OIDC config, the `[roles]` allowlist and the `[spend]` ledger/cap (see `.streamlit/secrets.toml.example`); `Authlib>=1.3.2` and `psycopg[binary]` are required dependencies. **An absent or unreachable `[spend]` ledger refuses every turn** (R22.12), the same fail-closed direction an unusable `[roles]` table takes. The tests need neither: the identity port is injectable, so they pass an `Identity` directly and never perform a login (R21.16).

**`chat_bot.py` is the entry point** — a thin Streamlit shell; the logic lives in the `interview_prep/` package.

**Login is required, and login alone is not access.** Authentication is Streamlit's native OIDC (`st.login()` / `st.user` / `st.logout()`), configured through `[auth]` in `.streamlit/secrets.toml` (`redirect_uri` — which differs between local and deployed — `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`). Authorization is a **separate** `[roles]` table in the same file mapping authorized email → role (`dev` / `user`); an unusable table authorizes nobody, so the app serves no one until both blocks exist. `chat_bot.current_identity()` is the **identity port's adapter** (with `role_table`, `user_claim`, `signed_in`, `token_has_expired`): it reads `st.user` and `st.secrets`, never the query string or `session_state`, requires the verified email claim, and logs out an expired token. The gate at the top of `main()` renders `render_sign_in()` or `render_not_authorized()` and calls `st.stop()` — the legibility of the refusal, not its enforcement, which lives in `authorization.require_authorized` (ADR-0200, REQUIREMENTS § 21).

**And access alone is not unlimited spend.** A third gate follows the first two: `chat_bot.current_budget()` reads `[spend]` and hands every paid client a `Budget`, `main()` refuses the turn before the uploader is even drawn, and each client checks the cap inside the operation that spends (ADR-0210, REQUIREMENTS § 22). The ledger is read **and written every turn** — `st.session_state["total_cost"]` is now a display of this chat's contribution to a durable per-identity total, never the authority (R22.18).

## Architecture

`chat_bot.py:main()` wires together the `interview_prep` package and drives the chat loop. Each module has one job:

- `config.py` — constants, paths (`PROMPTS_DIR`, `DEFAULT_PROMPT_SOURCE`, `IGNORE_TAG`, `DEFAULT_MODEL`), and `load_api_key()`
- `prompts.py` — `PromptSource` / `PromptLibrary` / `PromptFile`: discover, load, and compose the markdown prompt
- `context.py` — token estimation + `get_model_context_window()`; `compute_context_usage()` returns a `ContextUsage`
- `agent.py` — `InterviewAgent`: the turn, as a LangChain `create_agent` graph. `stream_reply()` yields tokens; it asks the stream for `["messages", "custom", "values"]` at once, so the reply comes from the graph's **final state** (not reconstructed from chunks), progress arrives on the consuming thread, and spend accumulates across hops. The only loop (ADR-0170)
- `middleware.py` — the harness: `InterviewState` (turn-scoped `files_read`, `citations`, `evaluations`, counters, `extra_cost`), `content_policy_middleware` (screens **every** tool result and records provenance only for what it admits), `announce_exhausted_tools`, `catch_typed_tool_call`, `final_hop_note`
- `policy.py` — `ContentPolicy` / `ToolOutcome`: the fail-closed screen-and-index rules, deliberately importing no agent framework (ADR-0150)
- `permissions.py` — `Role` / `Permission` / `has()` / `resolve_role()`: the role model as a pure function, importing neither Streamlit nor any agent framework, so the same check serves the page, the agent path and the tests. Grades what an **authorized** role may *do*; fail-closed to `Role.USER` on every input it cannot interpret. It no longer carries the identity port — `current_role()` and its env-var adapter are deleted (ADR-0190, REQUIREMENTS § 20)
- `pricing.py` — model rates and the R22.3 cost ladder: `price_of()` tries the `/models` catalog and falls back to the per-model `/endpoints` route (the only one that publishes prices for `EMBEDDING_MODELS`), `call_cost()` prices one completion (reported cost → reported tokens → estimated tokens), `embedding_cost()` prices an embedding call, which can only ever be an estimate
- `spend.py` — `CapDecision` / `decide()` / `check_budget()` / `require_within_budget()` / `Budget` / `UNCAPPED` / `InMemoryLedger`: the third question, after who they are and whether they may be here — **how much may they spend**. `decide()` is a pure function of role, spend, cap and the *next-prompt estimate*, so the turn that would cross the cap is the one refused (R22.6); `dev` is exempt from the cap but not from the ledger (R22.9). Refuses "at_cap" and "ledger_unavailable" separately, because they are facts about different parties (R22.13). `Budget` is what the six paid clients hold — a store, a ceiling, this turn's estimate — and `UNCAPPED` is the null object they get when nobody is counting. Imports no Streamlit and **no database driver**, asserted by a test (ADR-0210, REQUIREMENTS § 22)
- `ledger_postgres.py` — `PostgresLedger`: the spend port's hosted adapter (`total` / `record`). One row per normalised email, incremented in the database rather than in this process (two containers may serve the same person at once); `prepare_threshold=None` because Supabase's transaction pooler cannot serve prepared statements; every driver failure becomes `LedgerUnavailable`, which the policy fails closed on
- `authorization.py` — `Identity` / `ANONYMOUS` / `authorize()` / `require_authorized()` / `Unauthorized`: the prior question — whether there is a role at all. `authorize(email, table=…, email_verified=…)` decides an authenticated email against the operator's `[roles]` allowlist: presence is authorization, **absence is refusal**, and `email_verified` must be exactly `True` (defaults to `False`, so an unaware caller fails closed). `role is None` means refused and is deliberately not `Role.USER`. An unrecognized role name costs privilege but keeps access; an absent, unreadable or non-mapping table authorizes **nobody**, operator included. Never raises. `require_authorized(identity)` is the guard **inside** each of the six paid clients (`isinstance`, not truthiness), not just in the page. Imports neither Streamlit nor any agent framework (ADR-0200, REQUIREMENTS § 21)
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

