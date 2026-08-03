# Architecture

> **Viewing these diagrams:** in VS Code, install the **"Markdown Preview Mermaid
> Support"** extension (publisher `bierner`), then open this file and press
> `Ctrl+Shift+V`. Or paste a code block into <https://mermaid.live>.
>
> Diagrams here follow the principles in [`DIAGRAMS.md`](DIAGRAMS.md): one story
> per diagram, sequence diagrams for temporal flows, ~7±2 boxes each.

Five views, from most dynamic to most static. Shared conventions: **dotted
arrows** = network calls to OpenRouter, **solid arrows** = in-process;
**orange** = external API, **blue** = markdown prompt files.

## 1. A chat turn

*What happens between the user pressing Enter and the reply being saved?*

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant CB as chat loop<br/>(chat_bot.py)
    participant R as DocumentIndex +<br/>KnowledgeBase
    participant G as JailbreakGuard<br/>(guardrails.py)
    participant L as InterviewLLM<br/>(llm.py)

    U->>CB: prompt
    opt grounding-aware source & anything indexed (uploads or knowledge base)
        CB->>R: retrieve top-k chunks from each
        R-->>R: embed query (OpenRouter /embeddings)
        R->>CB: context block → retrieved_context slot
    end
    par guardrail on a background thread
        CB->>G: check(prompt)
        G-->>G: classify (OpenRouter, fails open)
        G->>CB: verdict
    and reply stream
        CB->>L: stream_reply(system prompt, history)
        L-->>L: OpenRouter /chat/completions (stream)
        L->>U: tokens, typewriter-style
    end
    alt verdict is jailbreak (whenever it lands)
        CB->>U: stop stream, clear text, warn — nothing persisted
    else allowed
        CB->>CB: add actual cost, persist turn, single rerun
    end
```

Key points: retrieval happens *before* the stream; the guardrail runs
*concurrently with* the stream and is polled between tokens; the sidebar
refreshes only on the rerun after the turn completes.

## 2. A web-research turn

*What happens when the user asks the interviewer to research something?* (The
guardrail-vs-stream concurrency and the persist/rerun ending are as in
diagram 1 — this diagram tells only the tool story.)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant L as InterviewLLM<br/>(llm.py)
    participant T as ToolBox<br/>(tools.py)
    participant W as WebResearcher<br/>(web_research.py)
    participant G as JailbreakGuard<br/>(guardrails.py)
    participant R as DocumentIndex<br/>(retrieval.py)

    U->>L: "please research Acme Corp"
    L-->>L: hop 1 (OpenRouter, stream) →<br/>tool call, no text
    L->>T: run web_research(query, topic)
    T->>W: research(query)
    W-->>W: sub-completion with web plugin<br/>(OpenRouter, quarantined model)
    W->>T: cited bullets + raw excerpts
    T->>G: scan bullets (fails closed)
    T->>G: scan raw excerpts (fails closed)
    T->>R: index excerpts as "web search" doc<br/>(topic = provenance)
    T->>L: bullets (or error string)
    L-->>L: hop 2 (OpenRouter, stream)
    L->>U: cited reply, typewriter-style
```

Key points: the interviewer never sees raw web pages — only the sub-call's
screened bullets (dual-LLM quarantine; ADR-0100); both scans fail **closed**
like document ingestion; the indexed excerpts let diagram 1's retrieval serve
follow-up turns without a new search.

## 3. Document ingestion

*What happens when the user drops a file into the sidebar?*

```mermaid
flowchart LR
    drop([User drops<br/>PDF / DOCX / TXT / MD]) --> parse["1. parse to text<br/>(ingest.py)"]
    parse --> scan{"2. guardrail scan<br/>fails closed"}
    scan -->|clean| embed["3. chunk + embed<br/>(retrieval.py)"]
    embed --> index[("4. session-scoped<br/>DocumentIndex")]
    scan -->|flagged or scan failed| reject["rejected: warning shown,<br/>chat continues without it"]
    embed -.OpenRouter /embeddings.-> orouter["OpenRouter"]

    classDef ext fill:#f9e0c3,stroke:#b5651d,color:#000
    class orouter ext
```

Note the polarity: this scan **fails closed** per document (no clean scan → no
ingestion), the opposite of the per-turn chat guardrail, which fails open so a
classifier outage never blocks the conversation.

## 4. Knowledge-base startup sync

*How does the persistent knowledge base stay in step with its seed files?*
(Runs once per session, and again when the embedding model changes.)

```mermaid
flowchart LR
    start([App starts /<br/>embedding model switched]) --> hash{"1. hash each seed in<br/>knowledgebase/*.md"}
    hash -->|unchanged| skip["skip (no calls)"]
    hash -->|new / changed| rechunk["2. re-chunk file<br/>(old vectors dropped)"]
    hash -->|file deleted| remove["remove document"]
    rechunk --> cover{"3. any chunk lacking a vector<br/>under the active model?"}
    skip --> cover
    cover -->|yes| embed["4. embed just those<br/>(cached per chunk × model)"]
    cover -->|no| done([DB ready — retrieval<br/>serves diagram 1])
    embed --> done
    embed -.OpenRouter /embeddings.-> orouter["OpenRouter"]

    classDef ext fill:#f9e0c3,stroke:#b5651d,color:#000
    class orouter ext
```

Key points: the seeds in git are the source of truth and `data/knowledgebase.db`
is a derived artifact (delete it and it rebuilds); a warm start makes zero
network calls; switching back to a previously used embedding model re-embeds
nothing (ADR-0110).

## 5. Module map

*What are the parts, and what depends on what?* Static structure only — no
runtime edges (those are diagrams 1–3).

```mermaid
flowchart TB
    entry["chat_bot.py<br/>Streamlit entry point — wires everything"]

    subgraph pkg["interview_prep/ (one job per cluster)"]
        direction LR
        promptsC["prompt composition<br/>prompts.py · config.py"]
        rag["document RAG + knowledge base<br/>ingest.py · retrieval.py · knowledgebase.py"]
        safety["guardrail<br/>guardrails.py"]
        llmC["LLM + accounting<br/>llm.py · pricing.py · context.py"]
        toolsC["tools<br/>tools.py · web_research.py"]
        uiC["rendering<br/>ui.py"]
    end

    mdfiles["prompts/*.md — interviewer behavior lives here, not in Python<br/>(personas + guardrail classifier prompt)"]
    kbfiles["knowledgebase/*.md — curated seeds (versioned);<br/>data/knowledgebase.db is derived, gitignored"]
    orouter["OpenRouter API<br/>/chat/completions · /models · /embeddings"]
    evals["evals/ — standalone prompt evals<br/>(reuses prompt loading; never imported by the app)"]

    entry --> pkg
    promptsC --> mdfiles
    rag --> kbfiles
    safety --> mdfiles
    toolsC --> mdfiles
    rag -.-> orouter
    safety -.-> orouter
    llmC -.-> orouter
    toolsC -.-> orouter
    evals --> promptsC
    evals -.-> orouter

    classDef ext fill:#f9e0c3,stroke:#b5651d,color:#000
    classDef mdfile fill:#dfeef7,stroke:#2b6a8f,color:#000
    class orouter ext
    class mdfiles mdfile
    class kbfiles mdfile
```

The full module-by-module list (what each file exports) lives in
[`CLAUDE.md`](../../CLAUDE.md) and the [`README`](../../README.md) — inventories read
better as text than as boxes.
