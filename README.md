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

Application code lives in the `interview_prep/` package (`config`, `prompts`, `context`, `llm`, `pricing`, `guardrails`, `ingest`, `retrieval`, `tools`, `web_research`, `ui`); `chat_bot.py` is the thin Streamlit entry point that wires them together. See [`docs/diagrams/architecture.md`](docs/diagrams/architecture.md) for diagrams of how the pieces fit together.

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

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). The app calls [OpenRouter](https://openrouter.ai/) via the OpenAI SDK, so an API key is needed. Copy the template and add your key ([get one here](https://openrouter.ai/keys)):

```bash
cp .env.example .env      # then edit .env and set OPENROUTER_API_KEY
uv sync
```

The `.env` file is gitignored and loaded automatically at startup. Alternatively, export `OPENROUTER_API_KEY` in your shell. In production, use your host's secrets manager rather than a file.

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
