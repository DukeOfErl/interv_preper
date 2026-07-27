# ADR-0090: Adopt Architecture Decision Records and a changelog

- **Status:** accepted
- **Date:** 2026-07-24
- **Pull request:** commits `83bb8d2` (docs restructure) and this ADR batch, on `chore/claude-workflow-restructure`

## Context

The project had no durable record of *why* decisions were made, nor a user-facing history of changes. As the codebase and its conventions grow, that rationale is easy to lose, and onboarding or revisiting a choice means re-deriving it from the diff.

## Decision

Record significant decisions as ADRs in `docs/decisions/`, named `NNNN-concise-kebab-name.md` with ids incrementing by 10 (`0000` is the reserved template; gap numbering leaves room to slot a later decision between two existing ones). Keep a root `CHANGELOG.md` (Keep a Changelog format) current under `[Unreleased]`. Both obligations are written into CLAUDE.md's Documentation-upkeep section. As a one-time step, **back-fill** the key prior decisions of this session (ADR-0010 through ADR-0080) so the record starts complete; going forward, an ADR is written as part of the change that makes the decision.

## Trade-off accepted

Ongoing discipline: every decision-shaping change now also writes an ADR and a changelog entry. The one-time back-fill (0010–0080) is retroactive — contrary to the "write it with the change" rule — and its PR/commit references are approximate. In exchange the project gains a durable, ordered decision log and a readable change history.
