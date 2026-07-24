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

# Reasoning effort presets, offered in the sidebar only when the active model is
# a reasoning model. Ordered low→high; sent to OpenRouter as
# ``reasoning.effort``. These three levels are accepted for every reasoning
# model OpenRouter exposes (provider-specific extras like "minimal" are not).
REASONING_EFFORTS = ["low", "medium", "high"]
DEFAULT_REASONING_EFFORT = "medium"

# Pre-send guardrail: a small, fast classifier that screens user prompts for
# jailbreak / prompt-injection attempts before they reach the interviewer LLM.
# Deliberately a non-reasoning model — this is a tiny binary classification and
# reasoning models add latency the pre-send path can't afford.
GUARDRAIL_MODEL = "openai/gpt-4.1-nano"
# The guardrail prompt is not an interviewer persona, so it carries the
# ``IGNORE_TAG`` to keep it out of the prompt selector (see below).
GUARDRAIL_PROMPT_FILE = "guardrail.ignore.md"
# Document screening uses a mid-size model, unlike the per-turn chat check:
# documents are scanned off the latency-critical path, and the window size a
# model stays reliable at scales with model size. nano needed 600-char windows
# (33 calls for a 7-page CV); mini holds 6/6 detection at 4000-char windows
# (5 calls) with no observed false positives.
GUARDRAIL_DOC_MODEL = "openai/gpt-4.1-mini"
# Documents are screened in overlapping windows of this size: a classifier
# reliably catches an injected line in a window sized to its capacity but
# starts missing it once surrounding benign resume content dilutes the signal
# (verified empirically — a whole-document nano scan passed a planted
# injection 3/3 times). The overlap keeps a phrase that straddles a window
# boundary visible in one piece; windows are scanned concurrently.
GUARDRAIL_SCAN_WINDOW_CHARS = 4000
GUARDRAIL_SCAN_OVERLAP_CHARS = 300
GUARDRAIL_SCAN_CONCURRENCY = 8

# --- Document RAG ---------------------------------------------------------------
#
# Uploaded documents (resume, job ad, cover letter) are chunked, embedded, and
# indexed in session memory; each turn retrieves the top-k chunks and injects
# them into the ``RETRIEVED_CONTEXT_PLACEHOLDER`` slot of the system prompt. A
# prompt source opts into grounding by containing that placeholder — sources
# without it behave exactly as before.
#
# Embeddings go through OpenRouter's OpenAI-compatible ``/embeddings`` endpoint.
# The model is user-selectable in the sidebar; switching it re-embeds the whole
# corpus (vectors from different models are not comparable).
EMBEDDING_MODELS = [
    "openai/text-embedding-3-small",
    "qwen/qwen3-embedding-8b",
    "openai/text-embedding-3-large",
]
DEFAULT_EMBEDDING_MODEL = EMBEDDING_MODELS[0]
CHUNK_SIZE_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150
TOP_K = 6
RETRIEVED_CONTEXT_PLACEHOLDER = "{retrieved_context}"
DOCUMENT_TYPES = ["resume", "job ad", "cover letter", "other"]
UPLOAD_FILE_TYPES = ["pdf", "docx", "txt", "md"]

# --- Prompt selection ---------------------------------------------------------
#
# The system prompt is chosen at runtime from ``prompts/``. Each selectable
# "source" is one of:
#   1. a single top-level ``.md`` file, or
#   2. a subdirectory of ``.md`` files, concatenated into one prompt.
#
# Discovery is fully automatic (no hardcoded file lists) — drop a file or a
# folder into ``prompts/`` and it appears in the selector. Two conventions keep
# that behavior predictable:
#
#   * IGNORE_TAG — any file whose name (before ``.md``) ends with this tag is
#     hidden from the selector. Use it for markdown that lives in ``prompts/``
#     but is not an interviewer persona (e.g. the guardrail classifier prompt).
#
#   * Numeric filename prefixes — files inside a subdirectory are concatenated
#     in the order of a leading number in each filename (``10_…``, ``20_…``).
#     We use *gap* numbering (10, 20, 30, …) rather than 1, 2, 3 so a new file
#     can be slotted in between two others (e.g. ``15_…``) without renumbering
#     every file after it. Files with no numeric prefix sort last.
IGNORE_TAG = ".ignore"

# The prompt source selected by default (a subdirectory name or a top-level
# ``.md`` filename). Falls back to the first discovered source if absent.
DEFAULT_PROMPT_SOURCE = "multi-role interviewer"

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
