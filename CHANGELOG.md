# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow it going forward. Dates are ISO 8601 (YYYY-MM-DD).

## [Unreleased]

### Added
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
- The jailbreak guardrail now runs concurrently with the streamed reply instead
  of blocking before it starts.
- Retrieval and query-rewrite failures fail open transparently: the turn still
  completes, with a warning shown near the chat input and logged in the
  Warnings tab.
- Project docs reorganized: `REQUIREMENTS.md` and the architecture diagrams
  moved under `docs/`.
