# Open items

Known-unfinished work and unresolved judgement calls, kept here so they survive
the session that created them. Distinct from `CHANGELOG.md` (what happened) and
`docs/decisions/` (what was decided and why): this is what is *still owed*.

Remove an item when it is closed, and if closing it involved a decision worth
remembering, write the ADR instead of leaving a note here.

## Provider data privacy (ADR-0100, ADR-0110)

- [ ] **Verify a live request end-to-end.** The privacy work was built and tested
      without network access, so no real call has ever been made with
      `provider: {data_collection: deny}` attached. Two things are unconfirmed:
      that OpenRouter accepts the parameter on `/chat/completions` as documented,
      and that the new raw-SDK embedder (`PrivateOpenAIEmbeddings`, replacing
      LangChain's `OpenAIEmbeddings` — ADR-0110) actually works against the real
      `/embeddings` endpoint. Run `uv run streamlit run chat_bot.py`, upload a
      document, and complete one interview turn. If the default embedding model
      is served only by providers that store data, the request will fail by
      design — that is the restriction working, not a bug; try another model.

- [ ] **User's own end-to-end test.** Repo owner to exercise the app manually
      before this branch merges — the automated suite covers the gate and the
      request bodies, but nobody has yet used the thing as a candidate would.

- [ ] **Decide how a local/self-hosted model should be treated.** The privacy
      gate is a hard block with no override (ADR-0110), so pointing the app at a
      local endpoint — Ollama, LM Studio, an internal gateway — makes it refuse to
      run, even though a local model is the *most* private option available: the
      data never leaves the machine. The block is currently escapable only by
      adding a `PROVIDER_POLICIES` entry by hand.

      Options, roughly in order of preference:
      1. Register `localhost` / `127.0.0.1` endpoints as a fourth level meaning
         "never leaves this machine" — arguably stronger than `ENFORCED` and
         honest, since it is verifiable from the URL rather than trusted.
      2. Document the manual registry entry as the supported path and leave the
         behavior as-is.
      3. Reconsider the no-override decision (rejected once already: an override
         is the thing that gets clicked through).

      Deferred rather than guessed — it interacts with the provider selector that
      does not exist yet.
