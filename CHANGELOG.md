# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow it going forward. Dates are ISO 8601 (YYYY-MM-DD).

## [Unreleased]

### Added
- Data-privacy **gate**. When the provider's data-usage policy can't be
  established, user content never leaves the process: no uploader renders, no
  message can be sent, and no client capable of transmitting is even constructed.
  Not overridable — a warning after a resume has been embedded is useless.
- Data-privacy enforcement and disclosure. Every provider call now carries the
  request parameters that prevent training on your data where the provider
  offers them (OpenRouter's `data_collection: deny`), across all call sites —
  interview stream, chat and document guardrails, query rewriter, document
  embeddings, and the eval harness. A colour-coded one-line status at the top of
  the Interview tab states the posture on every run: no data-training risk
  (green), provider-stated with a last-verified date (blue), or not ensured
  (red).
  Unrecognised providers are flagged rather than assumed safe.
- Document RAG: drag-and-drop resume / job ad / cover letter, screened by the
  safety guardrail (fail-closed per document, scanned in overlapping windows),
  then chunked, embedded via OpenRouter, and retrieved each turn into a
  grounding-aware system prompt. Follow-up messages are condensed into a
  standalone search query before retrieval.
- Actual-cost accounting: per-turn spend from OpenRouter's reported cost, with
  reasoning tokens included in the next-prompt estimate.
- Tabbed sidebar (Interview / Developer / Warnings) with a document uploader,
  an ingested-document panel, an embedding-model selector, context-usage and
  spend readouts, and a last-retrieval debug panel.
- Developer documentation under `docs/`: three focused architecture diagrams
  and their authoring principles (`docs/diagrams/`), Architecture Decision
  Records (`docs/decisions/`), and this changelog.

### Changed
- Document embeddings now go through a small raw-SDK embedder instead of
  LangChain's `OpenAIEmbeddings`, so the privacy parameters are an explicit
  argument that cannot silently stop being sent on a dependency upgrade.
- The jailbreak guardrail now runs concurrently with the streamed reply instead
  of blocking before it starts.
- Retrieval and query-rewrite failures fail open transparently: the turn still
  completes, with a warning shown near the chat input and logged in the
  Warnings tab.
- Project docs reorganized: `REQUIREMENTS.md` and the architecture diagrams
  moved under `docs/`.
