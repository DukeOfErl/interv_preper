# ADR-0010: Ground the interviewer with document RAG

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/interview-rag` → `dev` (merge `424c123`, core commit `d3b6f95`) — back-filled retroactively

## Context

The interviewer should base its questions and feedback on the candidate's actual resume, job ad, and cover letter. Those documents would fit whole in a modern context window, so full-text prompt-stuffing was a viable and simpler option. But (a) this is a deliberate learning exercise in building a real retrieval pipeline, and (b) planned web-sourced material (REQUIREMENTS §16) will not fit in context, so the design must assume an unbounded corpus.

## Decision

Build true RAG: parse uploads to text, chunk and embed them into a session-scoped vector store, and each turn retrieve the top-k chunks to inject into the prompt — rather than stuffing whole documents. Embeddings run through OpenRouter's `/embeddings` endpoint with a user-selectable model.

## Trade-off accepted

More moving parts than stuffing (chunking, embeddings, a vector store, retrieval quality to manage) in exchange for a pipeline that scales to a growing/web-sourced corpus and exercises the intended learning. Retrieval can miss context that full-text injection would have included — mitigated by query condensation (ADR-0050) and to be verified by evals.
