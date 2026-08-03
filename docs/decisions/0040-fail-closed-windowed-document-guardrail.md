# ADR-0040: Screen uploaded documents fail-closed, in concurrent windows

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/interview-rag` → `dev` (merge `424c123`) — back-filled

## Context

Uploaded documents (and later web pages) are prompt-injection vectors — a "job ad" can contain "ignore your instructions…". Empirically, a single whole-document scan with the small classifier reliably misses an injected line diluted by pages of benign résumé text. The per-turn *chat* guardrail deliberately fails open (a classifier outage must not brick the conversation) and runs concurrently with the reply stream.

## Decision

Screen each document with the guardrail **before** ingestion, and **fail closed per document**: index only after a clean scan; a flagged or errored scan rejects the document (with a visible warning) while the chat stays available. Scan in overlapping windows sized to the classifier's reliable range, run concurrently, using a mid-size model (`GUARDRAIL_DOC_MODEL`) since document scans are off the latency-critical path.

## Trade-off accepted

Extra latency and several classifier calls per upload, plus the opposite polarity to the fail-open per-turn chat guardrail (two behaviors to keep straight). In exchange, injections diluted across a long document are caught, and untrusted content can't enter the index unscreened. Not adversarially complete — obfuscated/encoded injections may still pass; a deterministic pre-filter is a possible future hardening.
