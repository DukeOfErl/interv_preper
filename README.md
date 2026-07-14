# interv-preper

A Streamlit chatbot that runs realistic mock job interviews. It gathers context about your target role (from a pasted job ad and resume, or a few intake questions), conducts an interview one question at a time, and scores each answer based on predetermined criteria: relevance, structure, specificity, evidence, judgment, and communication.

The interviewer's behavior is defined entirely in the markdown prompt files under `prompts/`. The sidebar has a **System prompt** selector that lets you switch between interviewers; each option is discovered automatically from `prompts/` and is either a single `.md` file or a folder of `.md` files concatenated into one prompt:

- `prompts/multi-role interviewer/` (default) — the full staged interviewer, built by concatenating its files in numeric-prefix order:
  - `10_main_system_prompt.md` — role and core behavior
  - `20_info_intake.md` — intake questions (Phase 1)
  - `30_mock_interview.md` — conducting the interview (Phase 2)
  - `40_feedback_stage.md` — per-answer scoring rubric (Phase 3)
- `prompts/simple_interviewer.md`, `prompts/few-shot_interviewer.md` — single-file alternatives

**Adding an interviewer:** drop a new `.md` file or a new folder of `.md` files into `prompts/` — it shows up in the selector on the next run, no code change needed. Two filename conventions apply:

- Files inside a folder are concatenated by a **leading number** in the filename (`10_…`, `20_…`). Use **gap numbering** (10, 20, 30 rather than 1, 2, 3) so you can insert a file between two others (e.g. `15_…`) without renumbering the rest. Unnumbered files sort last.
- A file whose name ends with **`.ignore.md`** is hidden from the selector (used for non-persona prompts such as `guardrail.ignore.md`).

Application code lives in the `interview_prep/` package (`config`, `prompts`, `context`, `llm`, `ui`); `chat_bot.py` is the thin Streamlit entry point that wires them together. See [`architecture.md`](architecture.md) for a diagram of how the pieces fit together.

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

Then open http://localhost:8501. For best results, paste the job ad and your resume/CV into the chat when prompted.

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
