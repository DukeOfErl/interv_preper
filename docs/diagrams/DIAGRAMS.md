# Diagram principles

Guidelines for every diagram in this folder. The goal is diagrams a human can read
without a manual — when a diagram needs a long prose walkthrough to be understood,
it is over budget and should be split or simplified.

## 1. One diagram, one story

A diagram answers exactly one question ("what happens during a chat turn?",
"which modules exist and what do they depend on?"). Never mix runtime behavior,
static structure, and unrelated systems in one graph — each has a different
natural shape, and a graph forced to carry all of them fits none. When a new
concern appears, add a new small diagram rather than growing an existing one.

## 2. Match the diagram type to the story

- **Temporal flows** (ordered steps, concurrency, branching on outcomes) →
  `sequenceDiagram`. Flowcharts have no time axis; steps-in-time drawn as
  structural arrows are the main source of spaghetti.
- **Static structure** (modules, dependencies, data stores) → `flowchart`,
  with **no runtime edges** in it.
- **Pipelines** (data moving through fixed stages) → a short left-to-right
  flowchart.

## 3. Every arrow must earn its place

A diagram is a curated argument, not a call graph — exhaustive truth lives in
the code. Drop edges that add crossings but no insight. Budget per diagram:
**~7±2 boxes, under ~15 edges**. Past that, split the diagram or collapse boxes.

## 4. One dominant reading axis

Pick top→bottom or left→right and make layout follow the story's order.
Mermaid caveat: with `flowchart TB`, *subgraphs* still pack side-by-side to fill
width — keep diagrams small enough that packing doesn't matter, or force rank
order with invisible edges (`A ~~~ B`).

## 5. Collapse detail to the diagram's level

An overview shows conceptual clusters ("document RAG", "LLM + accounting"),
not one box per file. Exhaustive inventories (module lists, file lists) belong
in text (README, CLAUDE.md), where a list is the right form.

## 6. Give the reader an entry point and an order

The actor/user node comes first on the reading axis. When order matters, number
it (`autonumber` in sequence diagrams; `1. upload`-style edge labels in
flowcharts). Keep edge labels to a word or two.

## Conventions used here

- Color encodes *kind of thing*, consistently across diagrams:
  orange = external API, blue = markdown prompt files.
- Dotted arrows = network calls; solid arrows = in-process.
- Each diagram gets a one-sentence caption stating the question it answers.
