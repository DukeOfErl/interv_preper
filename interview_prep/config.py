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

# Tool calling: how many times one turn may round-trip through tools before we
# stop feeding results back. A cap is required, not defensive — the loop is
# "call model → run tools → call model again", and a model that keeps requesting
# tools (e.g. retrying a failing one) would otherwise spin indefinitely at full
# token cost. On the final hop the tools are withheld, forcing a text answer.
MAX_TOOL_HOPS = 4

# --- Web research tool ----------------------------------------------------------
#
# The interviewer can call a ``web_research(query, topic)`` tool. It runs as a
# SUB-COMPLETION: a separate, non-streamed OpenRouter call on a cheap model with
# OpenRouter's ``web`` plugin attached (there is no bare search endpoint — the
# plugin only exists as a modifier on chat completions). That sub-call reads the
# raw pages and returns extracted fact bullets with citations; the interviewer
# never sees unprocessed web text (the dual-LLM / quarantined-model pattern).
# The raw excerpts are additionally indexed for RAG after a fail-closed
# guardrail scan, so later turns can retrieve fuller detail without a new
# search.
WEB_RESEARCH_MODEL = "openai/gpt-4.1-mini"
WEB_RESEARCH_PROMPT_FILE = "web_research.ignore.md"
WEB_SEARCH_MAX_RESULTS = 5
# Explicit engine choice; "exa" works with any chat model (OpenRouter injects
# results server-side), unlike "native" which needs provider search support.
WEB_SEARCH_ENGINE = "exa"
# Why the model is searching, supplied by the model per call and shown later as
# the label on retrieved web excerpts (provenance for the grounding prompt).
WEB_TOPICS = [
    "company research",
    "role & interview questions",
    "technical reference",
    "interview best practices",
    "other",
]

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
DOCUMENT_TYPES = ["resume", "job ad", "cover letter", "web search", "other"]
UPLOAD_FILE_TYPES = ["pdf", "docx", "txt", "md"]

# --- Curated knowledge base -----------------------------------------------------
#
# Coach-side reference material (interview best practices, question banks tagged
# by role, legal guidelines, bias-reduction tips, …) that persists across
# sessions. The source of truth is the seed folder — markdown files with YAML
# frontmatter, versioned in git; the SQLite file under ``data/`` is a derived
# artifact (gitignored) holding the chunked text plus cached embeddings,
# reconciled against the seeds by per-file content hash (see ADR-0110).
#
# The knowledge base follows the sidebar embedding selector. Embeddings are
# cached in the DB per (chunk, model), so switching back to a previously used
# model re-embeds nothing; only chunks never embedded under the active model
# are backfilled.
KNOWLEDGEBASE_DIR = ROOT_DIR / "knowledgebase"
KNOWLEDGEBASE_DB_PATH = ROOT_DIR / "data" / "knowledgebase.db"
KB_TOP_K = 4

# Before retrieval on a follow-up turn, a small model rewrites the user's
# message into a standalone search query (resolving "that", "it", etc.) so
# vector search isn't handed an anaphoric fragment. Cheap/fast because it sits
# on the turn's critical path (before the reply streams); skipped on the first
# turn and whenever we're not retrieving. Fails open to the raw message.
QUERY_REWRITE_MODEL = "openai/gpt-4.1-nano"
QUERY_REWRITE_PROMPT_FILE = "query_rewrite.ignore.md"
QUERY_REWRITE_HISTORY_TURNS = 4

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
