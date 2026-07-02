# interv-preper

A Streamlit chatbot that runs realistic mock job interviews. It gathers context about your target role (from a pasted job ad and resume, or a few intake questions), conducts an interview one question at a time, and scores each answer based on predetermined criteria: relevance, structure, specificity, evidence, judgment, and communication.

The interviewer's behavior is defined entirely in the markdown prompt files under `prompts/`, which are concatenated into the system prompt at startup:

- `prompts/main_system_prompt.md` — role and core behavior
- `prompts/info_intake.md` — intake questions (Phase 1)
- `prompts/mock_interview.md` — conducting the interview (Phase 2)
- `prompts/feedback_stage.md` — per-answer scoring rubric (Phase 3)

Application code lives in the `interview_prep/` package (`config`, `prompts`, `context`, `llm`, `ui`); `chat_bot.py` is the thin Streamlit entry point that wires them together.

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
