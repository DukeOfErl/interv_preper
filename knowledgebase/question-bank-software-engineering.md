---
category: question bank
tags:
  kind: technical
  roles: [backend engineer, full-stack engineer, software engineer]
---

# Technical question bank — software engineering

## System design (mid-level and above)

- "Design a URL shortener" (classic warm-up). Strong answers: clarify scale
  and requirements first, discuss ID generation trade-offs (hash vs counter),
  storage schema, cache layer, and redirect latency. Weak: jumps straight to
  a database table with no requirements gathering.
- "Design a rate limiter for a public API." Probe: algorithm choice (token
  bucket vs sliding window), where it lives (gateway vs service), distributed
  coordination, and what happens at the limit (429 semantics, retry headers).
- "How would you evolve a monolith that has become hard to change?" Strong:
  asks *why* it's hard first, proposes incremental extraction along seams with
  measurable checkpoints. Weak: "rewrite it as microservices" as a reflex.

## Coding exercises

- Two-pointer / hash-map warm-up: "Given a list of transactions, find the
  first pair that sums to a target." Expect a working O(n) solution with a
  hash map, clean naming, and edge cases (empty input, duplicates) raised by
  the candidate unprompted.
- Refactoring exercise: hand over a ~40-line function with mixed concerns and
  ask the candidate to make it testable. Signal is in what they *ask* (what
  does it do today? are there tests?) and whether behavior is preserved.
- Debugging exercise: a service intermittently returns stale data (a cache
  invalidation bug). Strong candidates form hypotheses, add observability,
  and bisect the system rather than guessing fixes.

## Practical engineering judgment

- "Walk me through a production incident you handled. What was the root
  cause, and what changed afterwards?" Strong: timeline, mitigation before
  root-causing, a durable prevention (test, alert, process). Weak: hero
  narrative with no follow-through.
- "How do you decide what to test, and at which level (unit / integration /
  end-to-end)?" Strong: risk-based reasoning, testing the contract not the
  implementation. Weak: a coverage percentage as the goal.
- "A dependency you rely on releases a breaking major version. Walk me
  through how you'd approach the upgrade." Probes risk assessment, reading
  changelogs, canarying, and rollback planning.
