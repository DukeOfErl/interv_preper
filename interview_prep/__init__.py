"""Interview-prep chatbot package.

The Streamlit entry point lives in ``chat_bot.py`` at the repo root and wires
these modules together:

- ``config``   - constants, paths, and API-key loading
- ``prompts``  - loading and composing the markdown system prompt
- ``context``  - token estimation and model context-window lookup
- ``llm``      - the OpenRouter (OpenAI-compatible) client and streaming
- ``ui``       - Streamlit rendering helpers
"""
