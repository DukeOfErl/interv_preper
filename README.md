# interv-preper

A Streamlit chatbot that runs realistic mock job interviews. It gathers context about your target role (from uploaded documents, a pasted job ad and resume, or a few intake questions), conducts an interview one question at a time, and scores each answer based on predetermined criteria: relevance, structure, specificity, evidence, judgment, and communication.

You can drag-and-drop your **resume, job ad, and cover letter** (PDF/DOCX/TXT/MD) into the sidebar; the interviewer grounds its questions and feedback in them via retrieval (RAG) — see [Documents](#documents-rag) below.

The interviewer's behavior is defined entirely in the markdown prompt files under `prompts/`. The sidebar has a **System prompt** selector that lets you switch between interviewers; each option is discovered automatically from `prompts/` and is either a single `.md` file or a folder of `.md` files concatenated into one prompt:

- `prompts/multi-role interviewer/` (default) — the full staged interviewer, built by concatenating its files in numeric-prefix order:
  - `10_main_system_prompt.md` — role and core behavior
  - `15_grounding.md` — the `{retrieved_context}` slot + rules for using uploaded documents and web research
  - `20_info_intake.md` — intake questions (Phase 1)
  - `30_mock_interview.md` — conducting the interview (Phase 2)
  - `40_feedback_stage.md` — per-answer scoring rubric (Phase 3)
- `prompts/simple_interviewer.md`, `prompts/few-shot_interviewer.md` — single-file alternatives

**Adding an interviewer:** drop a new `.md` file or a new folder of `.md` files into `prompts/` — it shows up in the selector on the next run, no code change needed. Two filename conventions apply:

- Files inside a folder are concatenated by a **leading number** in the filename (`10_…`, `20_…`). Use **gap numbering** (10, 20, 30 rather than 1, 2, 3) so you can insert a file between two others (e.g. `15_…`) without renumbering the rest. Unnumbered files sort last.
- A file whose name ends with **`.ignore.md`** is hidden from the selector (used for non-persona prompts such as `guardrail.ignore.md`).
- A prompt that contains the **`{retrieved_context}` placeholder** is *grounding-aware*: excerpts retrieved from the uploaded documents are injected there each turn. Prompts without it simply ignore uploaded documents (the sidebar says so).

Application code lives in the `interview_prep/` package (`config`, `prompts`, `context`, `agent`, `middleware`, `policy`, `pricing`, `guardrails`, `ingest`, `retrieval`, `knowledgebase`, `query_rewrite`, `tools`, `web_research`, `github_mcp`, `ui`); `chat_bot.py` is the thin Streamlit entry point that wires them together. See [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) for diagrams of how the pieces fit together — including the agent's graph.

## Documents (RAG)

Drag-and-drop files into the sidebar's **Documents** uploader (PDF, DOCX, TXT, or MD). Each document is:

1. **parsed** to plain text,
2. **screened** by the safety guardrail — a document is only used after a successful, clean scan (a flagged document, or one that couldn't be scanned, is rejected with a warning; the chat continues without it),
3. **chunked, embedded, and indexed** in memory for this session (nothing is written to disk).

Each turn, the most relevant chunks are retrieved and injected into the system prompt, so the interviewer asks about *your* projects and probes gaps between *your* resume and *the* job ad. The **Ingested Documents** panel shows each document's inferred type (resume / job ad / cover letter — correctable), and the **Last Retrieval** panel shows exactly which excerpts the last answer was grounded in.

Embeddings are computed through OpenRouter's `/embeddings` endpoint; the model is selectable in the sidebar (switching re-embeds all documents). With no documents uploaded, the interviewer collects the same context through intake questions as before.

## Web research

Ask the interviewer to research something current — the target company's recent news, up-to-date technologies for the role, salary data — and it can search the web (grounding-aware interviewers only). **It searches only with your consent**: when you explicitly ask, or after you say yes to its offer; it never searches on its own.

Under the hood the interviewer calls a `web_research` tool that runs a separate quarantined model over OpenRouter's web-search plugin. You get back cited fact bullets — each citation is a clickable link whose hover text summarizes the source — and the **Web Sources** panel in the sidebar lists the sources with excerpts. The full excerpts are also screened and indexed like an uploaded document (shown in Ingested Documents as type *web search*), so follow-up questions can draw on them without searching again.

Web content is treated as untrusted: everything is scanned by the safety guardrail **before** the interviewer sees it, and a flagged or unscannable result is withheld (the interviewer says research is unavailable rather than using it). A research turn costs a few cents and takes noticeably longer than a normal reply; the running status is shown in the chat while it works, and the **Last Tool Calls** panel (Developer tab) records exactly what was searched and returned.

## Knowledge base

The interviewer also draws on a **curated knowledge base** that persists across sessions: interview best practices, behavioral and technical question banks (tagged with the roles and seniority tiers they fit), legal guidelines on what an interviewer must not ask, bias-reduction guidance, and competency expectations per seniority tier. Each turn, the most relevant excerpts are retrieved alongside your documents — they shape the interviewer's questions, scoring, and feedback, and appear in the **Last Retrieval** panel under `knowledge base — <category>` headers.

The content is authored as markdown files in `knowledgebase/`, each with a small YAML frontmatter (`category`, plus optional `tags` such as roles, seniority, or countries). This files-in-git design exists **primarily so the database never needs to be hosted anywhere**: cloning the repo is the complete setup — the app builds the database locally from the files, with no artifact to download or keep published in sync. It also means that **to add or edit knowledge, you edit those files** — they are versioned in git and reviewable like any code change. The app derives a local SQLite file (`data/knowledgebase.db`, gitignored) from them on startup: changed files are re-embedded incrementally (detected by content hash), and embeddings are cached per model, so a normal start makes no network calls and switching the embedding model back and forth re-embeds nothing. Deleting `data/` is always safe — it rebuilds from the seeds on the next run. The **Knowledge Base** panel (Developer tab) lists what's loaded.
## Evaluation cards

Every scored answer also lands as a structured **evaluation card** in the sidebar's **Evaluations** tab: the question, its type (behavioral/technical), and the six rubric scores (relevance, structure, specificity, evidence, judgment, communication) stay visible at a glance, while the verbal feedback folds away under an expander so several cards can be compared at once. A summary row at the top shows your per-dimension averages across the interview so far — computed from the cards, so it always agrees with them.

Under the hood the interviewer records each card by calling a `record_evaluation` tool with the same scores it states in the chat (grounding-aware interviewers only). If a feedback reply ever arrives without its card, a warning appears in the Warnings tab. Cards live for the browser session; comparing across interviews (which needs saving to disk) is planned separately.
## GitHub portfolio deep-dive

Share your GitHub username and the interviewer can look at your public repositories — read a project's README and code, then ask grounded interview questions about *your* actual work (grounding-aware interviewers only, and only with your consent: it offers, and browses only after you share a username or say yes).

Under the hood this is an [MCP](https://modelcontextprotocol.io) (Model Context Protocol) integration: instead of a hand-written tool, the app is an MCP *client* of the official GitHub remote MCP server — it discovers the server's tools at runtime and forwards a curated read-only subset (repo search, file reading) to the interviewer, relaying each call. Requires a GitHub personal access token (`GITHUB_PAT`, see Setup); without one the feature simply isn't offered. Calls appear in the **Last Tool Calls** panel like any other tool.

Repository files are handled like uploaded documents rather than pasted into the conversation: each file is screened by the safety guardrail, indexed for retrieval (it appears in **Ingested Documents** as type *github*), and passed to the interviewer as a bounded excerpt, so a long file informs later questions through retrieval instead of filling the context window.

**Set Reasoning → Effort to high for deep-dives.** Reading a repository is a multi-step tool workflow — decide what to open, list it, read a file, decide again — and reasoning effort is what buys that discipline. At lower effort the interviewer tends to guess plausible file names instead of looking them up, and to narrate tool calls instead of making them. The app reminds you once per session if you run a deep-dive below high effort. (Choosing a more capable model helps too, and model selection is planned.)

Because a model that fails to read a repo can invent a plausible one, the app also **checks the interviewer's claims**: every file path and identifier the tools really returned is remembered, and if a reply names a file or function that never appeared, you get a warning in the chat and in the Warnings tab telling you to treat that part as possibly invented. See ADR-0130 for the reasoning and the limits.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). The app calls [OpenRouter](https://openrouter.ai/) via the OpenAI SDK, so an API key is needed. Copy the template and add your key ([get one here](https://openrouter.ai/keys)):

```bash
cp .env.example .env      # then edit .env and set OPENROUTER_API_KEY
uv sync
```

The `.env` file is gitignored and loaded automatically at startup. Alternatively, export `OPENROUTER_API_KEY` in your shell. In production, use your host's secrets manager rather than a file.

Optionally, set `GITHUB_PAT` in the same `.env` to enable the GitHub portfolio deep-dive: create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens) with read-only access to public repositories. Without it, the app runs normally minus that feature.

## Run

```bash
uv run streamlit run chat_bot.py
```

Then open http://localhost:8501. For best results, drag-and-drop your resume and the job ad into the sidebar (or paste them into the chat when prompted).

## Tests

```bash
uv run pytest
```

## Prompt evaluation

The `evals/` package is a standalone tool for evaluating the interview-prep system prompts — it is **not** part of the chatbot (the app never imports it). It loads the *real* composed prompt from `prompts/`, runs it against a dataset of interview scenarios, and judges the responses with [DeepEval](https://deepeval.com) (LLM-as-a-judge), using OpenRouter as the judge.

Six metrics run per scenario: DeepEval's built-in `answer_relevancy`, plus custom `GEval` rubrics — `asks_one_question`, `actionable_feedback`, `refuses_fabrication`, `stays_on_scope`, and `behavior_rules` (which is built dynamically from the behavior rules the markdown itself lists). Editing a prompt file and re-running tells you whether the change helped.

```bash
uv run python -m evals                      # score the current prompt, print a report
uv run python -m evals --limit 3            # cheaper smoke run (first 3 scenarios)
uv run python -m evals --metrics asks_one_question behavior_rules
uv run python -m evals --fail-under 0.7     # non-zero exit if overall mean is below 0.7 (CI)
uv run python -m evals --prompt-files simple_interviewer.md   # evaluate a different prompt set
```

By default the harness evaluates the exact prompt set the chatbot uses (the `config.DEFAULT_PROMPT_SOURCE` source). Pass `--prompt-files` with one or more markdown file names from `prompts/` (order matters; they are concatenated) to evaluate an experimental prompt instead — e.g. `--prompt-files "multi-role interviewer/10_main_system_prompt.md" "multi-role interviewer/20_info_intake.md"`.

The same evaluation is also wired into pytest as `integration`-marked tests (skipped unless `OPENROUTER_API_KEY` is set, since they make real calls):

```bash
uv run pytest -m integration -k evals
```

The default judge is `openai/gpt-4.1-mini` and the app-under-test defaults to `openai/gpt-5-mini`; override with `--judge-model` / `--app-model`. To A/B test an edited prompt programmatically, call `evals.evaluate_prompt(system_prompt=...)`.
