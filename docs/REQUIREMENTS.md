# Requirements — Interview Preparation Chatbot

A behavioral specification of the app, written so it could be refactored or re-implemented from scratch. It describes *what* the app does, not *how* the current code does it (implementation notes appear only where the behavior depends on them). Sections 1–15 describe the **current app** (§15 is document RAG, implemented — except R15.14, still pending); section 16 is **planned scope** (web-sourced knowledge) and is written as target behavior.

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

- **R16.1** The chat client supports a generic **tool-calling loop**: when a turn's stream ends in tool requests instead of text, the requested tools run locally, their results are appended as `tool` messages (with the assistant's request replayed verbatim), and the model is called again — bounded by a hop cap, with tools withheld on the final hop to force a text answer. The caller consumes one flat token stream; hops are invisible to it. Reported cost and reasoning tokens **accumulate** across a turn's API calls.
- **R16.2** Tool failures never abort a turn: the dispatcher returns error strings (unknown tool, invalid JSON, unexpected arguments, failed search, blocked content) that the model can read and recover from.
- **R16.3** One tool is offered, `web_research(query, topic)`, and only to grounding-aware prompt sources. It runs as a **sub-completion**: a separate, non-streamed OpenRouter call on a cheap model with the `web` plugin (engine `exa`, explicit `plugins` field — never the `:online` suffix). The sub-call's instructions live in `prompts/web_research.ignore.md` and demand extraction (cited fact bullets), not prose. The interviewer never sees raw web pages (dual-LLM quarantine).
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
