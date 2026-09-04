# Requirements — Interview Preparation Chatbot

A behavioral specification of the app, written so it could be refactored or re-implemented from scratch. It describes *what* the app does, not *how* the current code does it (implementation notes appear only where the behavior depends on them). Sections 1–15 describe the **current app** (§15 is document RAG, implemented — except R15.14, still pending); section 16 is **planned scope** (web-sourced knowledge) and is written as target behavior; sections 17-19 (curated knowledge base, evaluation cards, GitHub portfolio tools over MCP) describe the **current app**.

## 1. Purpose

A web chatbot that runs mock job interviews. It intakes the user's target role / job ad / resume, conducts a realistic interview one question at a time, and scores each answer.

Two separation-of-concerns principles govern the design:

- **Behavior lives in markdown.** All interviewer behavior is defined in markdown prompt files — the application code never encodes interviewing behavior.
- **Knowledge lives in documents** (§15; web sources planned in §16). What the interviewer *knows about this candidate and role* — resume, job ad, cover letter, and later web-sourced material — comes from ingested documents. Responsibilities split three ways: **retrieval policy** (what text gets fetched: parsing, chunking, query construction, top-k) lives in code and config; **usage behavior** (what the interviewer does with the fetched text) lives in the markdown prompts; the two meet at an explicit **context-block contract** (R15.8) so neither side can silently break the other.

The application code is a thin shell around both.

## 2. Platform & runtime

- **R2.1** Single-page chat UI built on Streamlit; entry point runs the whole app (currently `chat_bot.py`, launched via `streamlit run`).
- **R2.2** Python 3.12, dependencies managed with **uv** (`uv sync`, `uv run ...`).
- **R2.3** All LLM traffic goes through **OpenRouter** (`https://openrouter.ai/api/v1`) using the OpenAI-compatible chat-completions API — never directly to OpenAI.
- **R2.4** Chat state (history, spend, selections) lives in the browser session only; nothing is persisted to disk between sessions.

## 3. API key handling

- **R3.1** The OpenRouter key is read from `OPENROUTER_API_KEY` — from the environment or a local `.env` file (an `.env.example` documents this).
- **R3.2** If no key is found in the environment, the sidebar shows a password-type input where the user can paste a key. The key is kept in session memory only and never written to disk.
- **R3.3** With no key from either source, the app shows guidance and stops (fail fast) — no chat UI is offered.
- **R3.4** If a request fails with an authentication error mid-stream, the partial reply is discarded and a warning tells the user how to fix the key — the hint differs depending on whether the key came from the environment (fix `.env`/env and restart) or the sidebar (enter a different key and retry).
- **R3.5** Any other API error mid-stream also discards the partial reply and shows a warning with the error detail; nothing is persisted for that turn.

## 4. System prompt composition (behavior-in-markdown)

- **R4.1** The interviewer's entire behavior is defined by markdown files under `prompts/`; changing behavior must never require Python changes.
- **R4.2** A **prompt source** is either (a) a single top-level `.md` file, or (b) a subdirectory whose `.md` files are concatenated (non-empty contents joined with blank lines) into one system prompt.
- **R4.3** Sources are **discovered automatically** from `prompts/` — no hardcoded file lists. Dropping in a new file or folder makes it appear in the selector on the next run.
- **R4.4** Files whose name ends with `.ignore.md` are excluded from discovery (used for non-persona markdown such as the guardrail prompt).
- **R4.5** Files inside a source directory are ordered by a leading numeric filename prefix (`10_…`, `20_…`); gap numbering allows insertion without renumbering. Unnumbered files sort last (alphabetically among themselves).
- **R4.6** The sidebar has a **System prompt selector** listing every discovered source (directories marked with a folder icon). The selection is stored in session state; changing it rebuilds the prompt on the same run.
- **R4.7** The default source is configurable (currently the `multi-role interviewer/` directory); if the stored selection goes stale (source renamed/removed) it resets to the default. If the configured default is absent, the first discovered source is used.
- **R4.8** If no sources exist, or the selected source has no usable markdown content, the app shows an error and stops.
- **R4.9** The default interviewer is staged in four files: main system prompt (role + core behavior, with `{context_variable}` placeholders), Phase 1 info intake, Phase 2 mock interview, Phase 3 per-answer feedback/scoring rubric.

## 5. Chat loop

- **R5.1** Standard chat UI: history is replayed from session state on every rerun; user types into a chat input box.
- **R5.2** Before the first user message, the chat input shows placeholder text suggesting the user paste the job ad and resume/CV; afterwards the placeholder is empty.
- **R5.3** Each request sends the full composed system prompt plus the entire conversation history plus the new user message (stateless API; history is resent every turn).
- **R5.4** The assistant reply **streams** into the chat with a typewriter effect (~0.05 s delay per token), painted into a replaceable placeholder so it can be interrupted (a manual loop, not a fire-and-forget stream widget).
- **R5.5** A turn is a **single pass**: only after a successful, allowed reply are the user and assistant messages appended to history and a single rerun fired — which is what refreshes the sidebar (context usage + spend), deliberately after the answer completes rather than when the prompt is sent.
- **R5.6** The model is fixed in session state (default `GPT-5-Mini`); there is **no UI to switch models**.

## 6. Pre-send jailbreak guardrail

- **R6.1** Every user prompt is screened by a small, fast, non-reasoning classifier model (currently `openai/gpt-4.1-nano`) for jailbreak / prompt-injection attempts only — off-topic handling is left to the interviewer persona.
- **R6.2** The classifier's instructions live in a markdown file in `prompts/` carrying the ignore tag (`guardrail.ignore.md`), consistent with behavior-in-markdown.
- **R6.3** The classifier is asked for a JSON object (`is_jailbreak` boolean, optional `reason`) via JSON response format.
- **R6.4** The guardrail runs **concurrently** with the interview stream (background thread): the stream starts immediately and the guardrail future is polled between tokens, never blocking token painting.
- **R6.5** On a jailbreak verdict — whenever it lands, mid-stream or after the stream ends — the stream stops, the painted text is cleared, nothing is persisted, and the user sees a warning (including the classifier's reason when given).
- **R6.6** If the stream finishes before the verdict, the app waits for the verdict before committing the turn.
- **R6.7** The guardrail **fails open**: any classifier error, timeout, or unparseable response allows the prompt through, flagged as errored. Such turns are persisted with a marker, and the history view shows a caption on that message noting the prompt wasn't screened.

## 7. Reasoning effort

- **R7.1** When the active model supports reasoning (per the `supported_parameters` of its OpenRouter catalog entry), the sidebar shows a **Reasoning → Effort** selector with low/medium/high (default medium).
- **R7.2** When the model is unknown or the catalog is unavailable, the selector stays hidden and no reasoning parameter is sent (fail toward hiding).
- **R7.3** The chosen effort is sent as `{"reasoning": {"effort": ...}}` in the request body; non-reasoning models send no reasoning parameter.

## 8. Model metadata (OpenRouter catalog)

- **R8.1** The app fetches OpenRouter's `/models` catalog (authenticated), cached for 1 hour; any failure yields an empty catalog and graceful degradation everywhere it's used.
- **R8.2** Model lookup matches on `id` or `canonical_slug`, case-insensitively, and allows a bare model name (e.g. `gpt-5-mini`) to match a namespaced id (`openai/gpt-5-mini`).
- **R8.3** The catalog supplies, per model: context window length, pricing (USD per token, prompt/completion), and supported parameters (for the reasoning check).

## 9. Context-usage display

- **R9.1** The sidebar shows the active model, its context window, the source of that number, and a progress bar of estimated usage (`used %`, `used/window` token counts).
- **R9.2** Context window resolution order: live OpenRouter value (direct or top-provider) → static fallback table → unknown. When unknown, the bar is replaced by a "unknown (N tokens so far)" caption.
- **R9.3** Token counts are **rough heuristics** — ~1 token per 4 characters plus small per-message overheads — not real tokenization; the UI labels them as estimates.

## 10. Cost accounting

- **R10.1** The sidebar shows two figures: **total accrued spend this chat** and an **estimate for the next prompt**, plus the model's per-1M-token input/output rates (or "pricing unavailable").
- **R10.2** Amounts under $1 display in cents (e.g. `12.34¢`), $1 and over in dollars.
- **R10.3** Each request asks OpenRouter for usage accounting (`extra_body={"usage": {"include": True}}` together with `stream_options={"include_usage": True}`); the final stream chunk carries token counts, the **actual USD cost**, and reasoning-token counts.
- **R10.4** Accrued spend uses, in fallback order: (1) OpenRouter's reported actual `cost`; (2) reported token counts × catalog prices; (3) chars/4 token estimates × catalog prices. Unknown pricing degrades to zero-cost rather than raising.
- **R10.5** The next-prompt **estimate** projects: input = system prompt + full history + one more user message of average-user-message length; output = average assistant message length **plus the running average of reasoning tokens** (billed as output but absent from visible content). Reasoning-token counts are recorded per assistant message for this purpose.
- **R10.6** Before any completed exchange exists, the next-prompt estimate shows "N/A".

## 11. Developer dashboard (sidebar)

- **R11.1** A "Prompt Config" section shows the active source (name, file/folder kind, file count) and each file's name with found/missing status, in composition order.
- **R11.2** A collapsed "Prompt Preview" expander shows the first ~8 lines of each file (or "(empty file)" / a missing-file warning).

## 12. Evals (standalone; not part of the app)

- **R12.1** A separate `evals/` package — never imported by the app — evaluates interviewer prompt quality using DeepEval with an OpenRouter-backed judge.
- **R12.2** It loads the real composed system prompt (verbatim, via the same prompt-library code), generates one non-streaming reply per scenario with a caller that mirrors what the app sends, and judges each reply.
- **R12.3** Scenarios are defined as data (`dataset.py` Goldens with key aspects, category, difficulty); metrics are 6 named GEval criteria, one built dynamically from the behavior rules extracted from the system prompt itself.
- **R12.4** Runnable via `uv run python -m evals` (flags: `--limit`, `--metrics`, `--threshold`, `--fail-under`, model overrides) and via `integration`-marked pytest tests. Supports A/B testing an edited prompt by passing `system_prompt=`.
- **R12.5** Scores follow DeepEval's convention: float in [0, 1], higher is better.

## 13. Testing

- **R13.1** Unit tests (pytest, with xdist/rerunfailures available) cover config, prompts, context/token estimation, pricing, LLM wrapper, and guardrails; network-touching eval/guardrail tests are marked `integration`.
- **R13.2** External clients (OpenAI client, etc.) are injectable for testing (e.g. the guardrail accepts an injected client).

## 14. Non-functional requirements

- **R14.1** Fail fast on missing prerequisites (no API key, no prompts) with clear user-facing messages.
- **R14.2** Degrade gracefully on external failures: catalog unavailable → hidden reasoning selector, unknown context window, zero-price fallback; guardrail failure → fail open with a visible flag; stream failure → warn, persist nothing.
- **R14.3** Keep the Python shell thin: interviewer behavior belongs in markdown; each module has one job (config/constants, prompt discovery+composition, context estimation, pricing, LLM client, guardrail, UI rendering, entry-point wiring).
- **R14.4** Never require a restart to pick up new prompt files — discovery happens at render time.

---

## 15. Document ingestion & RAG grounding

**Why true RAG (and not just prompt-stuffing):** a resume + job ad + cover letter would fit whole in the context window, and full-text injection would be simpler. Retrieval is chosen deliberately because (a) this is a learning exercise in building a RAG pipeline, and (b) the corpus will grow to include web-sourced material (§16) that will not fit in context. The pipeline is therefore built as if the corpus is unbounded, even though it currently holds only a few small documents.

### Ingestion

- **R15.1** Users can upload documents via **drag-and-drop** in the UI (multiple files at once; Streamlit's `st.file_uploader` provides this natively). The resume is the primary document; job ad and cover letter are optional.
- **R15.2** Accepted formats: PDF, DOCX, TXT, MD — each parsed to plain text. A parse failure warns for that file only and never blocks the chat.
- **R15.3** Each document carries a **type tag** (resume / job ad / cover letter / other), inferred where possible and confirmable by the user. The tag travels with the document's chunks as metadata, so the interviewer prompt can treat sources differently ("probe gaps in the *resume*", "check requirements from the *job ad*").
- **R15.4** Documents and everything derived from them (chunks, embeddings, index) are **session-scoped and in-memory** — extends R2.4. Individual documents can be removed; the index updates accordingly. Nothing is persisted to disk.
- **R15.5** At upload time, each document is **chunked and embedded**; chunk size/overlap are named constants (config-level). Embeddings go through OpenRouter's OpenAI-compatible embeddings endpoint (`/api/v1/embeddings`), consistent with R2.3. The embedding model is **user-selectable from a sidebar dropdown** over a curated config list (`EMBEDDING_MODELS`: `openai/text-embedding-3-small`/`-large`, `qwen/qwen3-embedding-8b`); switching models **re-embeds the entire corpus** (vectors from different models are not comparable). The RAG plumbing is built on LangChain (`OpenAIEmbeddings` + `InMemoryVectorStore` + `RecursiveCharacterTextSplitter`).
- **R15.6** Uploaded document text is screened by the **guardrail before ingestion** (extends R6 — an uploaded "job ad" is a prompt-injection vector). Screening happens in **overlapping windows** small enough for the classifier to stay reliable, framed as document data rather than chat input — a single whole-document scan demonstrably misses an injected line diluted by benign content; any flagged window rejects the document. Windows are scanned **concurrently** and with a **larger model than the per-turn chat guardrail** (document scans are off the latency-critical path, and window size scales with model capacity), keeping multi-page uploads at interactive latency. Unlike the per-turn chat guardrail (R6.7), document screening **fails closed at the document level**: a document is ingested only after a successful, clean scan. If the scan flags the document or cannot complete (classifier error, timeout, unparseable response), the document is rejected — not chunked, not embedded, not indexed — and a warning tells the user which document was not accepted and why (flagged vs. couldn't be scanned; a rejected-for-scan-failure upload can be retried). The **app itself stays available** either way: the chat continues without the rejected document, per R15.9.

### Retrieval & grounding

- **R15.7** On each turn, a query retrieves the **top-k chunks** from the index; k is a named constant. Retrieved chunks are formatted into a clearly labeled context block, each with provenance metadata (document name, type, chunk id), and injected per the contract in R15.8. If retrieval **fails**, the turn falls open — the reply is generated without document context — and the fallback is surfaced **transparently** (a one-shot chat warning plus a Warnings-tab log entry), never silently.
- **R15.16** **Query condensation.** On follow-up turns the retrieval query is not the raw message (an anaphoric fragment like "tell me more about that" retrieves poorly). A small, fast model rewrites the message — given recent conversation — into a **standalone search query**, using markdown instructions (behavior-in-markdown, `.ignore`-tagged like the guardrail). It **fails open but transparently**: any error or blank rewrite falls back to the raw message *and* surfaces a warning (chat flash + Warnings-tab log), so the user knows search ran on the un-rewritten message. It is **skipped on the first turn** (no referents to resolve) and whenever retrieval isn't happening (not grounding-aware, or no documents). The resolved query is shown in the developer dashboard's last-retrieval panel.
- **R15.8** **Context-block contract & grounding opt-in.** A prompt source declares itself grounding-aware by containing a **`{retrieved_context}` placeholder** (extending the R4.9 placeholder convention); the context block is injected only into that slot. The block's shape — the placeholder name, section labeling, document types, provenance fields — is an explicit contract defined and documented in exactly one place in code; persona markdown is written against it. Sources without the placeholder receive no injected context and behave byte-identically to today. When documents are uploaded but the active source is not grounding-aware, the UI says so plainly (the documents are indexed but unused) rather than failing silently.
- **R15.9** **Grounding requirement.** When documents are present and the active source is grounding-aware, the interviewer's questions and feedback must be based on them. The division of labor follows §1: *how* retrieved context is used (weighting sources, probing gaps, not inventing facts beyond the documents) is defined in the prompt markdown; *what* gets retrieved is code/config, and its quality is verified by the retrieval evals (R15.14) — a prompt cannot compensate for a retrieval miss. With **no documents uploaded**, behavior is unchanged from §5: Phase 1 collects the same information conversationally. Uploading is an accelerator, never a gate.

### Amendments to current-state requirements

- **R15.10** (amends **R5.3**) Message assembly becomes: composed system prompt — with the retrieved-context block substituted into the `{retrieved_context}` slot for grounding-aware sources (R15.8) — + full history + new user message. The block therefore sits inside the system prompt and is recomputed each turn.
- **R15.11** (amends **R9**) Context-usage estimates include the injected retrieved chunks.
- **R15.12** (amends **R10.5**) The next-prompt cost estimate accounts for retrieved-context tokens — since top-k injection makes input size roughly stable per turn, use the average injected size across past turns (same technique as reasoning tokens).
- **R15.13** (amends **R11**) The developer dashboard gains an **ingestion panel**: uploaded documents with type, parse status, chunk and token counts — and a **last-retrieval panel**: the chunks retrieved for the most recent turn with their scores and provenance. The Prompt Config section additionally shows whether the active source is grounding-aware (contains `{retrieved_context}`).
- **R15.14** (amends **R12**) *(still pending — not yet implemented; the `evals/` package is unchanged so far)* Evals will gain document fixtures (sample resume / job ad alongside `user_prompt_for_eval.txt`) and two new metric families: **groundedness** (are questions derived from the documents; does feedback avoid contradicting the resume — DeepEval faithfulness / contextual-relevancy metrics apply directly) and **retrieval quality** (do the retrieved chunks contain the information the turn needed).
- **R15.15** (amends **R13, R14.3**) Parsing, chunking, and retrieval live in their own single-job modules (e.g. `ingest.py`, `retrieval.py`) with injectable clients and unit tests, following the existing guardrail pattern.

## 16. Web research (tool calling)

*(Design rationale: ADR-0100.)*

- **R16.1** The chat client supports a generic **tool-calling loop**: when a turn's stream ends in tool requests instead of text, the requested tools run locally, their results are appended as `tool` messages (with the assistant's request replayed verbatim), and the model is called again — bounded by a hop cap, with tools withheld on the final hop to force a text answer. The caller consumes one flat token stream; hops are invisible to it. A hop that requests no tools but whose text **contains a tool call written as prose** (an object literal whose keys are all parameters of an offered tool) is not treated as an answer: the model is told that text is never delivered to a tool and the hop is retried while the budget allows, so a typed call cannot silently become the reply. **Text emitted by a hop that also requested tools is preamble, not the answer**: it is streamed for responsiveness but excluded from the stored reply, which is repainted once the turn ends (a model that narrates its intentions — or emits raw tool-call JSON — must not leave that in the transcript). Reported cost and reasoning tokens **accumulate** across a turn's API calls.
- **R16.2** Tool failures never abort a turn: a failure (unknown tool, invalid arguments, failed search, blocked content, a repeatedly failing remote) reaches the model as a readable error it can recover from, and a tool that keeps failing is reported as a fault in the app or the service it called — not as something the candidate did.
- **R16.2a** **Every tool result is screened by default** (R16.5), whatever the tool. The only exception is a tool whose output is the model's own words coming back — currently just `record_evaluation` — and that exemption is declared explicitly rather than being the consequence of a tool not asking for a scan. Adding a tool that reaches outside therefore cannot ship unscreened by omission.
- **R16.3** One *local* tool is offered, `web_research(query, topic)` (see § 19 for the discovered GitHub tools), and only to grounding-aware prompt sources. It runs as a **sub-completion**: a separate, non-streamed OpenRouter call on a cheap model with the `web` plugin (engine `exa`, explicit `plugins` field — never the `:online` suffix). The sub-call's instructions live in `prompts/web_research.ignore.md` and demand extraction (cited fact bullets), not prose. The interviewer never sees raw web pages (dual-LLM quarantine).
- **R16.4** **Consent:** the tool description and persona rules require an explicit user request — or a yes to the interviewer's offer — before any search; the interviewer must offer, not search, when it merely believes a search would help. Enforcement is prompt-level (auditable in the dashboard), not code-level.
- **R16.5** Web content is **untrusted and screened fail-closed** (unlike the fail-open chat guardrail): the bullets must pass a clean guardrail scan before the tool returns them, and the raw excerpts must pass the same windowed scan before indexing — a flagged or errored scan withholds that tier. The classifier's scope covers injection/manipulation generally (AI-directed instructions in content, fake delimiters, tool-usage manipulation, rubric overrides, exfiltration), with framing per content kind so web boilerplate (ads, banners, marketing imperatives) is not flagged.
- **R16.6** **Two-tier context:** the screened bullets return as the tool result (a `tool` message, never the system role); the verbatim excerpts are indexed as a `web search` document through the same pipeline as uploads, carrying the model-supplied `topic` as provenance. Retrieved web chunks are distinguishable in the context block (`### web search — <topic>: …`) and the dashboard, and the grounding rules rank them below the candidate's documents.
- **R16.7** **Citations:** sources come from the response's `url_citation` annotations; every markdown link in the bullets is validated in code against those annotations (unknown links reduced to plain text). The interviewer is instructed to preserve citation links verbatim (`[domain](url "note")` — the title renders as a hover tooltip). A **Web Sources** panel lists the last research call's sources with excerpts.
- **R16.8** Research is cached per session by normalized query; sub-completion costs are added to the turn's spend; a progress line is painted into the reply slot during tool hops (which otherwise stream nothing for many seconds). A **Last Tool Calls** panel in the Developer tab shows each call's name, arguments, and result.

## 17. Curated knowledge base

*(Design rationale: ADR-0110.)*

- **R17.1** The app maintains a **persistent, curated knowledge base** of coach-side reference material — interview best practices, question banks tagged by role/seniority, legal guidelines on what must not be asked, bias-reduction guidance, competency expectations per seniority tier. Unlike uploads (R15.4), it survives sessions.
- **R17.2** **Seeds in git, DB derived.** The source of truth is a folder of markdown seed files (`knowledgebase/*.md`), each with optional YAML frontmatter carrying a `category` and free-form `tags` (e.g. roles, seniority, countries). The runtime store is a single SQLite file under `data/` — a **derived, gitignored artifact** that any clone rebuilds on first run. The **primary motivation is distribution**: the database never has to be hosted or downloaded from anywhere — the repo alone is sufficient to run the app anywhere. Secondary benefits: the content is versioned and code-reviewed like the prompt files, and git doubles as the backup.
- **R17.3** **Startup reconciliation by content hash.** On startup the DB is reconciled against the seed folder per file: unchanged files (same content hash) are skipped, new/changed files are re-chunked and re-embedded, deleted files are removed. A warm start makes **zero network calls** for the knowledge base. A schema version stamp lets an incompatible DB be dropped and rebuilt rather than migrated.
- **R17.4** **Per-model embedding cache.** Embeddings are stored per (chunk, embedding model). The knowledge base follows the sidebar embedding selector (R15.5); changing models backfills **only** the chunks lacking a vector under the newly active model, so returning to a previously used model re-embeds nothing.
- **R17.5** **Retrieval & grounding.** Each grounded turn retrieves the top-k knowledge-base chunks (k a named constant, separate from the uploads' k) with the same condensed query as R15.16, and injects them into the same context block under distinguishable provenance (`### knowledge base — <category>: …`). Tags travel inside the chunk text, visible to both the embedding and the interviewer. The grounding rules treat knowledge-base excerpts as guidance for the interviewer — never as facts about the candidate — and legal-guidelines excerpts as hard constraints on what may be asked.
- **R17.6** **Fails open, transparently.** A knowledge-base load or retrieval failure never blocks the chat: the session (or turn) proceeds without the knowledge base, with the standard warning surfacing (chat flash + Warnings tab).
- **R17.7** **Trusted content.** Seed files are curated in-repo, so — unlike uploads (R15.6) and web content (R16.5) — they are not guardrail-screened at ingestion. Any future path that writes non-curated content into the knowledge base (e.g. saving a web source) must bring its own fail-closed screening.
- **R17.8** (amends **R11**) The developer dashboard gains a **Knowledge Base panel** (documents with category and chunk counts); knowledge-base chunks appear in the last-retrieval panel like any other chunk.
## 18. Evaluation cards (structured feedback)

*(Design rationale: ADR-0120.)*

- **R18.1** When the interviewer scores an answer (Phase 3), it also records the evaluation as structured data by calling a `record_evaluation(question, question_type, scores, verbal_feedback)` tool in the same turn — the same scores it states in chat. The schema requires all six rubric dimensions (`relevance`, `structure`, `specificity`, `evidence`, `judgment`, `communication`) as integers 1–5 and a `question_type` from {behavioral, technical, other}; the card is re-validated in code (providers enforce JSON Schema unevenly) and a malformed one comes back as an error the model can correct.
- **R18.2** The tool's invocation policy is **always-call after answer feedback** (stated in the tool description and `40_feedback_stage.md`) — the opposite of `web_research`'s consent gating; policy lives per-tool. No guardrail scan (the content is the model's own output) and no consent (no external effects).
- **R18.3** Cards commit to the session only when the turn is allowed and persisted — a guardrail-blocked turn contributes no card. If a completed reply reads like scored feedback (conservative heuristic: several rubric dimension names each actually assigned a 1–5 score) but recorded no card, a warning is logged.
- **R18.4** An **Evaluations** sidebar tab accumulates the cards, newest first: question, type, and the six scores always visible; the verbal feedback collapsed under an expander so several cards can be scanned at once. A summary header shows per-dimension means across the interview so far, **computed by Python from the cards** — aggregates are never asked of the model.
- **R18.5** Cards are session-scoped. Cross-interview comparison requires persistence to disk and is deliberately out of scope (future work package + ADR).
## 19. GitHub portfolio tools (MCP)

*(Design rationale: ADR-0130.)*

- **R19.1** The app can act as an **MCP client** of the official GitHub remote MCP server (streamable HTTP): it discovers the server's tools at runtime (`tools/list`), converts them to the chat-completions schema shape, and relays each invocation (`tools/call`). The tool schemas are the server's, not the app's.
- **R19.2** Only a configured **read-only allowlist** of the server's tools is forwarded to the interviewer (repo search, file reading); everything else is dropped at discovery time, and a readonly request header is sent as defense-in-depth.
- **R19.3** The feature requires a GitHub PAT (`GITHUB_PAT`) and **fails soft**: no PAT, an unreachable server, or a failed discovery leaves the app running exactly as without the feature (a warning is logged; discovery is not retried every turn). Discovery runs once per session; its schemas are cached, while calls stay live.
- **R19.4** **Consent** follows the web-research model (R16.4): a consent-policy sentence is appended to every forwarded tool description, and the persona offers the portfolio deep-dive at intake — browsing happens only after the user shares a username/repo or accepts the offer.
- **R19.5** GitHub tools ride the same tool-calling loop and failure contracts as R16.1–R16.2a: remote failures are returned to the model as error strings, never raised, and calls appear in the Last Tool Calls panel. A local tool name always shadows a remote one.
- **R19.6** **A fetched file is treated as a document, not as conversation.** Listings and searches return inline, but a file body must pass a fail-closed guardrail scan (framed for source code, so docstrings, CLI help text, and a project's own prompt templates are not flagged), is then indexed as a `github` document named `<owner>/<repo>/<path>`, and is returned to the model only as a bounded excerpt. Retrieved repository chunks are distinguishable in the context block (`### github: <owner>/<repo>/<path>`); a blocked file is withheld entirely and the interviewer is told to say it could not read it.
- **R19.7** **Results are trimmed before the model sees them:** the client forces the server's compact output options (overriding the model's own choice), because verbose metadata crowds the context window without telling the interviewer anything it can act on. Parameters the client overrides are also **stripped from the forwarded schema** — a knob whose value is discarded should not be offered as a decision.
- **R19.8** **Fabricated code references are surfaced.** Every repository path a tool result reveals, and every identifier in a file the interviewer actually received, is remembered for the session. After each reply, file names and compound identifiers presented as real but absent from that record are reported to the user — both in the durable warnings log and as a one-shot flash in the chat window. The check must be conservative enough that ordinary suggestions (naming a library, a generic `main()`) never trigger it.
- **R19.9** **Quoted code is verified verbatim.** Because every fetched file is held for the session, a multi-line code block the reply presents as the candidate's code is checked by plain text matching against what was actually read; a block whose lines are essentially absent is reported like R19.8. The check tolerates re-indentation and elision, and ignores snippets short enough to be the interviewer's own illustration.
- **R19.10** **The forced answer is told that its tools are gone.** When the hop cap withholds tools (R16.1) and any tool ran during the turn, a note is added before the final generation: no calls remain, do not write tool calls or invent their results, and work only from what was received — naming the files actually read, or stating that none were and to ask which file to open. Without it the model is cornered (must answer, cannot fetch) and has been observed both inventing a repository and writing out the exploration it *would* have done as if it had happened.
- **R19.11** **The tool-round budget must fit repository exploration.** A deep-dive needs several sequential rounds (find the repo, list directories, read files) and normally wastes one on a guessed path that does not exist. The cap must leave room for that; sized only for a single web search, turns ran out mid-exploration, which is what triggered the fabrication above.
- **R19.12** **The user is told what makes a deep-dive work.** Repository exploration degrades markedly at low reasoning effort (guessed paths instead of listings; narrated tool calls instead of real ones), so after a turn that used the GitHub tools with effort below the maximum, the app shows a one-shot informational notice — not a warning — pointing at the effort control, and noting that a more capable model helps too. Shown once per session, and never for models that expose no reasoning control.
- **R19.13** **A failed read must be admitted, not filled in.** Prompt rules require the interviewer to discuss only files and symbols it has actually received (a listing proves a file exists, not what is in it), and to say plainly that it could not read the code — asking which file to look at — rather than inventing names or implementations. This is the behavioral counterpart to R16.1's final-hop tool withholding, which otherwise pressures a model with no content into answering anyway.

---

## 20. User roles & permissions

*(Design rationale: ADR-0190.)*

**Why a permission model and not just hidden tabs:** the app is headed for a shared Streamlit Cloud deployment where an interviewee and a developer reach the same URL. Hiding the diagnostic tabs is presentation. What actually needs protecting is spend, and later whose API key pays for a turn — and `evals/` and `tests/` already call the agent without passing through `chat_bot.py` at all. A guard that lives in the page therefore protects exactly one of the app's three callers. So the model is built as a permission check any caller can ask, and the UI becomes one of its consumers rather than its enforcement point.

### Roles & permissions

- **R20.1** Every session resolves to exactly one **role**. Two exist: `user` (the interviewee; the default) and `dev`.
- **R20.2** A **permission** is a named capability, never a UI element. One is defined initially: *view diagnostics*.
- **R20.3** Resolution and checking are **fail closed**: an unknown role name, an absent identity, a malformed override, or any exception raised while resolving yields `user` — never `dev`. Least privilege is the error case, and the cheap direction to be wrong in: a wrongly denied permission produces a visible complaint, a wrongly granted one produces nothing at all.
- **R20.4** The check is a **pure function of (role, permission)** living in its own module that imports neither Streamlit nor any agent framework — so it is callable from a unit test, from the agent path, and from the page alike. This is the separation `policy.py` already keeps (ADR-0150), for the same reason.
- **R20.5** **A hidden tab is not a permission.** Gating presentation is expected, but any operation that must be restricted is guarded inside the function that performs it, not in the caller that renders it.
- **R20.6** Withholding diagnostics must not withhold **actionable** information. The one-shot flash warnings in the chat body stay visible to every role — the document-rejection warning (R15.6), the retrieval-fallback notice (R15.7), and the unverified-reference flash (R19.8) — because they tell a user something about their own turn that they can act on. Only the durable Warnings *tab* is gated.
- **R20.7** Role resolution sits behind a single **identity port** — one seam, one function — with one implementation initially: an override read from the environment or Streamlit secrets. With the role resolved to `dev`, the app behaves exactly as it does today.
- **R20.8** The override must **not be influenceable from the browser**. A role taken from a URL query parameter, or from a `session_state` key any page code can write, is explicitly out of scope: anything in `session_state` is reachable by anything in the process.
- **R20.9** The role is resolved **from the port on each run**, not cached across runs in a form that outlives a change to the underlying identity.

### Amendments to current-state requirements

- **R20.10** (amends **R11**) The Developer dashboard and the Warnings tab render only for a role holding *view diagnostics*. Without it, the sidebar offers the Interview and Evaluations tabs only.
- **R20.11** (amends **R13**) The permission module carries unit tests covering every (role, permission) pair and every fail-closed input. A deliberate defect seeded into a copy must fail exactly those tests (per the `fault-seeding` discipline), because a wrongly granted permission raises no error, logs nothing, and produces output that looks ordinary.

### Deferred (each its own work package and ADR)

- **R20.12** *(deferred)* **Authentication.** Replacing the override with a real identity provider. Until it lands there is no authentication: the role is configuration, and the deployment is only as private as its URL. This must be stated wherever the deployment is documented.
- **R20.13** *(deferred)* **Per-role API key.** A `dev` role may spend the app's own `OPENROUTER_API_KEY`; a `user` role supplies its own through the existing sidebar input. Amends R3.1–R3.3.
- **R20.14** *(deferred)* **Per-identity spend cap**, enforced where spend is accrued rather than where it is displayed. It requires durable storage that survives a container restart, which runs against ADR-0110's stated principle that no database ever needs hosting — so it is its own decision, not a detail of this one.

## 21. Authentication & authorization

*(Implements R20.12. Design rationale: ADR-0200.)*

**Why authentication and an allowlist are two separate things.** OIDC answers *who is this*; it does not answer *may they be here*. Signing in with Google proves an email address and nothing else, and every Google account holder in the world can do it. Since every turn of this app spends the operator's own OpenRouter credit, a successful login is not sufficient grounds to serve anyone — so identity and authorization are decided separately, and the second is an explicit allowlist rather than a property of the first.

### Identity

- **R21.1** Authentication uses Streamlit's native OIDC support (`st.login` / `st.user` / `st.logout`), configured through `[auth]` in `secrets.toml`. No credential is ever handled by this app's code.
- **R21.2** **Login is required.** An unauthenticated visitor is offered a sign-in control and nothing else: no chat input, no document upload, no model selector, and no turn may be taken.
- **R21.3** The authenticated identity is the verified **email claim** of the OIDC token. An authenticated session with no email claim is treated as unauthenticated (fail closed), because the allowlist is keyed on email and an identity the allowlist cannot be applied to is not an identity this app can act on.
- **R21.4** **The session is not bounded by the ID token's lifetime, and must not be.** An ID token is a one-time signed assertion that the person authenticated at `iat`; its `exp` bounds how long a relying party should accept it *as proof of a fresh login*, not how long they may stay. The standard flow — which Streamlit already implements — verifies the token once at the OAuth callback and then mints its own session cookie (`_streamlit_user`, `Max-Age` 30 days). `st.user` reads that cookie once at session start, so `exp` is frozen at login and never refreshes; re-checking it on each run converts Google's ~1-hour token lifetime into a hard 1-hour cap that discards a candidate's transcript, uploaded documents and evaluations mid-interview. No service behaves that way, and an earlier implementation of this requirement did.
- **R21.5** **Revocation is the allowlist's job, and is immediate.** The role table is re-read from secrets on *every* run (R20.9), so removing an address locks that person out on their next interaction whatever cookie their browser holds. This is what the expiry check was reaching for, and it is both stronger and operator-controlled.
- **R21.6** Accepted, documented trade-off of the above: a stolen browser session authenticates for up to 30 days without contacting the provider again. Bounding that requires an **absolute session age** measured from `iat` — a deliberate policy, not a side effect of a token lifetime — and is deferred (R21.18).

### Authorization

- **R21.7** A single table in secrets maps **authorized email → role**. Presence in the table is authorization; **absence is refusal**. There is one source of truth, so an allowlist and a role table cannot disagree.
- **R21.8** Email comparison is case-insensitive and whitespace-stripped on both sides. Address casing is not a security boundary and treating it as one only produces mystifying refusals.
- **R21.9** An authorized email whose role name is unrecognized resolves to `user` (per R20.3) and **remains authorized**. The two failures are graded differently on purpose: a typo in a role name should cost privilege, not access, because losing access to a paid-for account is the expensive direction for a legitimate user.
- **R21.10** A table that is **absent, unreadable, or not a mapping authorizes nobody.** This is the one place the fail-closed default is expensive rather than cheap — a misconfigured deployment serves no one — and that is deliberate: the alternative is a deployment that silently admits the world to the operator's credit.
- **R21.11** A user who is authenticated but not authorized is told plainly that their account is not authorized, is shown the address they signed in as (so they can report it or switch accounts), and is offered sign-out. No detail of the app is rendered to them, and **no turn may be taken**.

### Where the refusal lives

- **R21.12** The refusal is enforced **inside the operation that spends**, not only in the page that renders it (R20.5). `chat_bot.py` refuses early so the message is clean, but an unauthorized identity reaching the agent directly is refused there too — `evals/` and `tests/` call the agent without passing through the page, and a page-only guard would protect one of three callers.
- **R21.13** The agent therefore requires an authorized identity to be handed to it, and raises rather than calling any model without one. A caller added later cannot spend by accident; making it spend requires passing something that says, in as many words, that it is authorized.
- **R21.14** Authorization is **not** a `Permission`. Permissions grade what an authorized role may do; authorization decides whether there is a role at all. Modelling refusal as a missing permission would make "no identity" and "identity with few rights" the same state, and they fail in opposite directions.

### Consequences for what already exists

- **R21.15** (amends **R20.7**) The identity port's implementation becomes the authenticated session rather than an environment variable. The port itself — one seam, one function — does not change shape, which is the point of having had one.
- **R21.16** (amends **R20.1**) A session no longer always resolves to a role: it resolves to a role **or to a refusal**. `user` remains the default *among authorized roles*, not the default for an unidentified visitor.
- **R21.17** (amends **R20.12**) The statement that "there is no authentication and the deployment is only as private as its URL" is withdrawn from the documentation when this lands, and replaced by a statement of what is now true: access requires a Google sign-in **and** an entry in the operator's allowlist.
- **R21.18** Local development and the test suite must not require a live OIDC provider. The identity port is injectable, so a test supplies an identity directly and never performs a login.

### Deferred

- **R21.20** *(deferred)* **Absolute session age.** Re-authenticate after a fixed period measured from the token's `iat`, bounding R21.6's 30-day window. Deliberately separate from R21.4: a session length the operator chooses, not one inherited from whatever lifetime a provider happens to give its ID tokens.
- **R21.19** *(deferred)* Per-identity audit of spend, which becomes possible once every turn has a stable email attached to it. It is the natural foundation for R20.14's spend cap and should be decided with it, not before.
