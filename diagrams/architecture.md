# Architecture

> **Viewing these diagrams:** in VS Code, install the **"Markdown Preview Mermaid
> Support"** extension (publisher `bierner`), then open this file and press
> `Ctrl+Shift+V`. Or paste a code block into <https://mermaid.live>.
>
> Diagrams here follow the principles in [`DIAGRAMS.md`](DIAGRAMS.md): one story
> per diagram, sequence diagrams for temporal flows, ~7±2 boxes each.

Three views, from most dynamic to most static. Shared conventions: **dotted
arrows** = network calls to OpenRouter, **solid arrows** = in-process;
**orange** = external API, **blue** = markdown prompt files.

## 1. A chat turn

*What happens between the user pressing Enter and the reply being saved?*

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant CB as chat loop<br/>(chat_bot.py)
    participant R as DocumentIndex<br/>(retrieval.py)
    participant G as JailbreakGuard<br/>(guardrails.py)
    participant L as InterviewLLM<br/>(llm.py)

    U->>CB: prompt
    opt documents indexed & prompt source is grounding-aware
        CB->>R: retrieve top-k chunks
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

## 2. Document ingestion

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

## 3. Module map

*What are the parts, and what depends on what?* Static structure only — no
runtime edges (those are diagrams 1 and 2).

```mermaid
flowchart TB
    entry["chat_bot.py<br/>Streamlit entry point — wires everything"]

    subgraph pkg["interview_prep/ (one job per cluster)"]
        direction LR
        promptsC["prompt composition<br/>prompts.py · config.py"]
        rag["document RAG<br/>ingest.py · retrieval.py"]
        safety["guardrail<br/>guardrails.py"]
        llmC["LLM + accounting<br/>llm.py · pricing.py · context.py"]
        uiC["rendering<br/>ui.py"]
    end

    mdfiles["prompts/*.md — interviewer behavior lives here, not in Python<br/>(personas + guardrail classifier prompt)"]
    orouter["OpenRouter API<br/>/chat/completions · /models · /embeddings"]
    evals["evals/ — standalone prompt evals<br/>(reuses prompt loading; never imported by the app)"]

    entry --> pkg
    promptsC --> mdfiles
    safety --> mdfiles
    rag -.-> orouter
    safety -.-> orouter
    llmC -.-> orouter
    evals --> promptsC
    evals -.-> orouter

    classDef ext fill:#f9e0c3,stroke:#b5651d,color:#000
    classDef mdfile fill:#dfeef7,stroke:#2b6a8f,color:#000
    class orouter ext
    class mdfiles mdfile
```

The full module-by-module list (what each file exports) lives in
[`CLAUDE.md`](../CLAUDE.md) and the [`README`](../README.md) — inventories read
better as text than as boxes.
