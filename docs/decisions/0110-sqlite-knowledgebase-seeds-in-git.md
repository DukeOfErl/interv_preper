# ADR-0110: Persistent knowledge base on plain SQLite, seeds in git, per-model embedding cache

- **Status:** accepted
- **Date:** 2026-07-31
- **Pull request:** _pending_

## Context

The interviewer needed durable, coach-side reference material — interview best
practices, question banks tagged by role/seniority, legal guidelines on what
not to ask, bias-reduction guidance, competency expectations per tier — that
survives sessions, unlike the session-scoped `DocumentIndex`. Three questions
had to be settled: the storage engine (plain SQLite vs. sqlite-vec vs. reusing
the in-memory LangChain store with a dump file), the relationship between the
curated content and git (commit the DB, curate directly into it, or derive it
from versioned sources), and what happens to persisted vectors when the
user-selectable embedding model changes (the sidebar offers three models, and
vectors from different models are not comparable).

The distribution question dominated the second choice: if the database itself
were the source of truth, anyone cloning the app (a reviewer, a new machine, a
fresh checkout) would need the DB **hosted somewhere and downloaded** — an
artifact store, a release asset, or a binary committed to git — plus a way to
keep that hosted copy in sync with the code. Deriving the DB from files that
already live in the repo makes the repo the only distribution channel.

## Decision

1. **Plain SQLite, no vector extension.** One derived file
   (`data/knowledgebase.db`, gitignored) with three tables — `documents`,
   `chunks`, `embeddings` — and retrieval as brute-force cosine over the
   cached vectors in numpy. `interview_prep/knowledgebase.py` owns all of it.
2. **Seeds in git, DB derived — primarily so no database ever needs hosting.**

   > **Narrowed on this point by [ADR-0210](0210-hosted-postgres-ledger-for-the-spend-cap.md).**
   > "No database ever needs hosting" held for as long as everything this app
   > stored was derived from files in the repo. The per-identity spend cap
   > needs a total that survives a container restart and is shared between
   > concurrent containers, which no derived local file can provide, so it uses
   > a hosted Postgres. The claim below is narrowed rather than withdrawn: it
   > still describes the knowledge base, whose SQLite file remains derived,
   > disposable and local. The ledger is the single exception, and ADR-0210
   > records why it was accepted and what it costs.

   The source of truth is `knowledgebase/*.md` (YAML frontmatter: `category` +
   free-form `tags`); the DB is rebuilt locally from them, so cloning the repo
   is the *complete* setup — there is no hosted artifact to download, publish,
   or keep in sync with the code. Secondary benefits follow: the content is
   versioned, diffable, and code-reviewable like the prompt files, and git
   doubles as its backup. Startup reconciles the DB against the seeds by
   **per-file content hash** — new/changed files are re-chunked, vanished
   files deleted, unchanged files skipped — so a warm start touches the
   network zero times and a reviewer's clone self-builds on first run. A
   schema change just bumps `PRAGMA user_version` and rebuilds; the DB is
   never migrated.
3. **Embeddings cached per (chunk, model).** The knowledge base follows the
   sidebar embedding selector; a model switch backfills only the chunks that
   lack a vector under the newly active model, and switching back to a
   previously used model re-embeds nothing.
4. Retrieved chunks reuse the existing `RetrievedChunk`/`format_context_block`
   contract (`doc_type="knowledge base"`, category as the topic), so the
   context block, grounding prompt, and dashboard needed no new machinery.
   Tags are rendered as a bracket line inside each chunk's text, reaching both
   the embedding and the interviewer.

## Trade-off accepted

- **Brute-force retrieval has a ceiling.** Linear scan is milliseconds at
  curated scale (hundreds to a few thousand chunks) but not at ~50k+; we
  knowingly defer sqlite-vec (or FTS5 hybrid search) until scale demands it.
  The schema keeps that migration cheap — vectors already live in SQLite,
  so upgrading means adding a `vec0` table and rewriting one query.
- **Per-file hashing, not per-chunk:** editing one line of a seed re-embeds
  that whole file. Acceptable because seed files are small by convention.
- **First use of a new embedding model pays a one-time full backfill** (spend
  + a spinner), the price of never rebuilding on later switches; a
  fully-backfilled KB stores one vector set per model (a few MB each).
- **Seeds are trusted content** — curated in-repo and code-reviewed, so they
  skip the guardrail scan that uploads and web content get. Anything that
  enters the KB by another route (e.g. the planned save-web-source feature)
  must bring its own screening.
