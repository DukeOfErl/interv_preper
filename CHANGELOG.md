# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow it going forward. Dates are ISO 8601 (YYYY-MM-DD).

## [Unreleased]

### Added
- Web research on request: the interviewer can call a `web_research` tool
  (consent-gated — only when the user asks or agrees to an offer) that runs a
  quarantined sub-completion over OpenRouter's web-search plugin and returns
  cited fact bullets; citations render as clickable links with hover summaries,
  and a Web Sources panel lists the sources. The full excerpts are screened
  fail-closed and indexed like an uploaded document (type *web search*, with
  the search topic as provenance) so follow-up turns retrieve them without
  re-searching. (ADR-0100)
- Generic tool-calling loop in the chat client (bounded hops, errors returned
  to the model as strings, sub-call costs added to the turn's spend) and a
  Last Tool Calls panel in the Developer tab.
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
- The safety classifier's scope broadened from jailbreak detection to
  injection/manipulation generally (AI-directed instructions inside documents
  and web pages, tool-usage manipulation, rubric overrides), with framing per
  content kind so ordinary web boilerplate isn't flagged.
- The grounding prompt now distinguishes candidate documents from web-sourced
  excerpts (labeled with their search topic) and ranks web content below the
  candidate's own documents.
- The jailbreak guardrail now runs concurrently with the streamed reply instead
  of blocking before it starts.
- Retrieval and query-rewrite failures fail open transparently: the turn still
  completes, with a warning shown near the chat input and logged in the
  Warnings tab.
- Project docs reorganized: `REQUIREMENTS.md` and the architecture diagrams
  moved under `docs/`.
