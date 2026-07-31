# ADR-0030: Grounding opt-in via a `{retrieved_context}` placeholder

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/interview-rag` → `dev` (merge `424c123`) — back-filled

## Context

Interviewer behavior lives in markdown prompt sources, and there are several (multi-role, simple, few-shot). Retrieved document context must reach the prompt without hard-coding it into every persona, and non-RAG personas must be unaffected. The code fills the slot, but the markdown already contains `{other_placeholders}` the model fills conversationally.

## Decision

A prompt source declares itself grounding-aware by containing a `{retrieved_context}` placeholder; retrieved chunks are injected only there. `retrieval.format_context_block()` is the single definition of the injected block's shape — the contract personas are written against — and substitution uses `str.replace`, never `str.format` (which would choke on the other braces).

## Trade-off accepted

Personas must be written against the block's shape, and the code↔markdown contract has to be kept in sync (documented in one place). In exchange, grounding is opt-in per source, non-RAG personas behave byte-identically to before, and filling the slot can't accidentally break the model's own placeholders.
