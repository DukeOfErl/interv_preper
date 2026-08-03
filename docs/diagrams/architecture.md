# Architecture

> **Viewing these diagrams:** in VS Code, install the **"Markdown Preview Mermaid
> Support"** extension (publisher `bierner`), then open this file and press
> `Ctrl+Shift+V`. Or paste a code block into <https://mermaid.live>.
>
> Diagrams here follow the principles in [`DIAGRAMS.md`](DIAGRAMS.md): one story
> per diagram, sequence diagrams for temporal flows, ~7±2 boxes each.

Six views, from most dynamic to most static. Shared conventions: **dotted
arrows** = network calls to OpenRouter, **solid arrows** = in-process;
arrows** = network calls to an external API, **solid arrows** = in-process;
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
        CB->>CB: add actual cost, persist turn<br/>+ evaluation cards, single rerun
    end
```

Key points: retrieval happens *before* the stream; the guardrail runs
*concurrently with* the stream and is polled between tokens; the sidebar
refreshes only on the rerun after the turn completes.

## 2. A tool-calling turn (web research shown)

*What happens when the interviewer calls a tool mid-turn?* (The
guardrail-vs-stream concurrency and the persist/rerun ending are as in
diagram 1 — this diagram tells only the tool story.) The hop loop is the same
for every tool; `web_research` is drawn because it has the richest flow. The
other tool, `record_evaluation` (ADR-0120), follows the same shape with steps
4–9 collapsed to "validate the card, hold it for commit" — no sub-completion,
no scans, no indexing; its cards are persisted with the turn in diagram 1's
final step and rendered in the Evaluations tab.

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

## 3. A GitHub portfolio turn (MCP)

*What happens when the candidate shares their GitHub username?* Unlike
diagram 2's bespoke tool, the tools here belong to the **GitHub remote MCP
server** — the app only discovers and relays them (ADR-0130).

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant L as InterviewLLM<br/>(llm.py)
    participant T as ToolBox<br/>(tools.py)
    participant M as GitHubMCP<br/>(github_mcp.py)
    participant S as GitHub MCP server<br/>(api.githubcopilot.com)
    participant G as JailbreakGuard<br/>(guardrails.py)
    participant R as DocumentIndex<br/>(retrieval.py)

    Note over M,S: once per session, first turn:<br/>tools/list → read-only allowlist → cached specs
    U->>L: "my GitHub is octocat" (after consenting)
    L->>T: run get_file_contents(owner, repo, path)
    T->>M: call(name, args)
    M->>S: tools/call (fresh connection, PAT,<br/>compact-output args forced)
    S->>M: file body (embedded resource)
    M->>T: MCPResult: inline text + file_text + terms
    T->>G: scan file as "code" (fails closed)
    T->>R: index as "github" doc<br/>(owner/repo/path)
    T->>L: bounded excerpt (or error string — never raises)
    L->>U: grounded question about the real code
    Note over L,U: after the reply: names it claims are<br/>checked against terms → warning if invented
```

Key points: the schemas come from the server at runtime, not from Python; only
allowlisted read-only tools are forwarded, each with a consent-policy suffix;
no PAT or a failed discovery degrades to a session without GitHub tools. A
file body never enters the conversation whole — it is screened, indexed, and
excerpted, so diagram 1's retrieval carries the rest into later turns
(ADR-0130).

## 4. Document ingestion

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

## 5. Knowledge-base startup sync

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

## 6. Module map

*What are the parts, and what depends on what?* Static structure only — no
runtime edges (those are diagrams 1–5).

```mermaid
flowchart TB
    entry["chat_bot.py<br/>Streamlit entry point — wires everything"]

    subgraph pkg["interview_prep/ (one job per cluster)"]
        direction LR
        promptsC["prompt composition<br/>prompts.py · config.py"]
        rag["document RAG + knowledge base<br/>ingest.py · retrieval.py · knowledgebase.py"]
        safety["guardrail<br/>guardrails.py"]
        llmC["LLM + accounting<br/>llm.py · pricing.py · context.py"]
        toolsC["tools<br/>tools.py · web_research.py · github_mcp.py"]
        uiC["rendering<br/>ui.py"]
    end

    mdfiles["prompts/*.md — interviewer behavior lives here, not in Python<br/>(personas + guardrail classifier prompt)"]
    kbfiles["knowledgebase/*.md — curated seeds (versioned);<br/>data/knowledgebase.db is derived, gitignored"]
    orouter["OpenRouter API<br/>/chat/completions · /models · /embeddings"]
    ghmcp["GitHub MCP server<br/>tools/list · tools/call"]
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
    toolsC -.-> ghmcp
    evals --> promptsC
    evals -.-> orouter

    classDef ext fill:#f9e0c3,stroke:#b5651d,color:#000
    classDef mdfile fill:#dfeef7,stroke:#2b6a8f,color:#000
    class orouter ext
    class ghmcp ext
    class mdfiles mdfile
    class kbfiles mdfile
```

The full module-by-module list (what each file exports) lives in
[`CLAUDE.md`](../../CLAUDE.md) and the [`README`](../../README.md) — inventories read
better as text than as boxes.
