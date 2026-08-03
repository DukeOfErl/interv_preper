# ADR-0060: Present architecture as three focused diagrams

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** `feat/architecture-diagrams` → `dev` (merge `7f00812`, commit `29a774c`) — back-filled

## Context

The architecture was one dense Mermaid flowchart that mixed runtime behavior, static structure, and the standalone evals system. It was hard to read, arrow-heavy, had no clear reading order, and Mermaid packed its subgraphs side-by-side.

## Decision

Split it into three focused diagrams, each answering one question: a chat-turn *sequence* diagram, a document-ingestion *pipeline*, and a static *module map*. Capture the reusable rules in `docs/diagrams/DIAGRAMS.md` (one story per diagram, sequence diagrams for temporal flows, ~7±2 boxes, curated arrows) and follow them for future diagrams.

## Trade-off accepted

Three diagrams to keep in sync with the code instead of one. In exchange each is legible on its own, uses the right diagram type for its content, and future diagrams have explicit principles to follow.
