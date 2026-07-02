"""Application configuration and shared constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root is the parent of this package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT_DIR / "prompts"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "GPT-5-Mini"
TYPING_DELAY_SECONDS = 0.05
MODELS_CACHE_TTL_SECONDS = 3600

# Markdown prompt files, concatenated in this order to build the system prompt.
PROMPT_FILE_NAMES = [
    "main_system_prompt.md",
    "info_intake.md",
    "mock_interview.md",
    "feedback_stage.md",
]

# Static fallback context windows (in tokens), used when the OpenRouter lookup
# does not resolve a model.
MODEL_CONTEXT_WINDOWS = {
    "gpt-3.5-turbo": 16385,
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
}
DEFAULT_CONTEXT_WINDOW = None


def load_api_key():
    """Load a local ``.env`` (if present) and return the OpenRouter API key.

    Returns ``None`` when the key is not set so callers can fail fast.
    """
    load_dotenv()
    return os.getenv("OPENROUTER_API_KEY")
