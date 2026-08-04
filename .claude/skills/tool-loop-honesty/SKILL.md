---
name: tool-loop-honesty
description: Use when building or testing the harness around an LLM that calls tools — function calling, MCP clients, agentic loops — so the code makes it hard for the model to pass invention off as fact. Covers designing tool results, loop control flow, provenance checks on what the model claims, and fault-injection tests for all of it. Load when writing or reviewing that harness code, and when debugging an agent that invents data or file contents, narrates tool calls it never made, or reports success a tool never returned.
---

# Tool-loop honesty

This is about **harness code**, not prompt wording: the shape of your tool
results, the control flow of your loop, the checks you run on what the model
claims, and the tests that prove all three work. One prompt-side item earns its
place (§5) because no amount of harness will supply it.

Scope is deliberately narrow: **fabrication in the model's prose and final
answer, after tool calls have failed or run out.** Tool-name hallucination,
unnecessary tool use, and tool-choice quality are different failures with their
own literature and their own fixes.

## The failure

When a tool returns nothing usable, the model has three moves. Measured over 396
trajectories against tools failing silently — HTTP 200 carrying an empty, null,
malformed, or truncated payload:

| Outcome | Rate |
|---|---|
| **Honest surrender** — says the tool returned nothing | 43.2% |
| **Fabrication** — presents invented data as the tool's result | **56.6%** |
| **Unfaithful safety refusal** — blames a nonexistent policy or privacy rule | 0.25%, rising to 3.95% under generic safety prompting |

Fabrication is the *majority* response to a broken tool, not an occasional lapse.
And it arrives quietly: fault-injection studies of MCP agents report that "the
failures are usually silent, confident use of incorrect tool outputs rather than
crashes," and that semantic failures of this kind "propagate silently without
raising runtime exceptions." Your exception handlers will not see this. Your
tests will not see it either, unless you build them to (§6).

## 1. Triage: suspect the harness before the model

Four of five fabrication episodes in the worked example had a defect upstream of
the model. Work in this order.

1. **Did a tool return success carrying nothing?** Inspect the raw payload, never
   the model's summary of it. This is the most common trigger.
2. **Can you see raw payloads at all?** If you are not logging every
   `(tool payload, final response)` pair, you cannot distinguish "the model
   lied" from "the tool broke" — a fabricated transcript reads exactly like a
   real one.
3. **Did calls fail on malformed arguments?** Check whether you advertise
   parameters the model does not need, or whose values you override anyway.
4. **Did the model run out of tool calls mid-task?** Count rounds actually
   available, not the configured cap.
5. **Did the model write a call as prose instead of calling it?** If your loop
   treats a turn with no tool call as a finished answer, that text becomes the
   reply.
6. **Only now** consider the model, and see the deferred note at the end before
   reaching for reasoning effort.

## 2. Design tool results so failure cannot be mistaken for data

**Give every result an explicit status.** Return
`{status: "success" | "failure" | "empty", result, error}` rather than a bare
payload, so "nothing came back" is a value the model can read and repeat instead
of an absence it has to interpret. Never return `success` with an empty body.

**Say absence in words.** "No records matched this query" is a result; `[]` is an
ambiguity the model resolves by guessing. If a request partly succeeded, name the
part that did not.

**Make errors actionable.** Include what was wrong and an example of a correctly
formed call. A traceback gives the model nothing to act on.

**Return high-signal fields only.** Strip opaque identifiers (`uuid`,
`mime_type`, URL templates) in favour of semantic ones — resolving identifiers to
meaningful names measurably reduces hallucination. Verbose noise also teaches the
model that results are JSON it could have written itself.

**Truncate and paginate with stated limits**, and say what was cut and how to
reach the rest, or the model treats the fragment as the whole.

**Do not advertise parameters you override or ignore.** A knob whose value you
discard is not the model's decision, and every extra knob is a chance to garble
the call.

**With third-party tools you own neither the schema nor the result shape.** Probe
the live server for the real tool names, the real payload shapes, and where the
content actually sits, then re-probe on version bumps. Schema drift — a renamed
or removed field — is a standard injected fault for a reason.

## 3. Design the loop so honesty is a legal move

**Make deferral a first-class action.** If the only actions available are "call a
tool" and "answer", a model with nothing to say must invent. Expose explicit
alternatives in the action space — something like `report_blocked(reason)` or
`ask_user(question)` — so stopping is a move the harness can see and act on
rather than prose it has to parse. *(The action-space idea comes from work whose
measured gains required fine-tuning; the affordance is the transferable part, and
offering it without retraining is untested.)*

**Never demand output while withdrawing the means to ground it.** A loop that
withholds tools on its final step and still requires an answer has removed the
honest action and left invention as the only way to comply. Any forced-output
mechanism does this: step caps, retry limits, rigid response formats, timeouts.
*(Reasoned hypothesis — see references/evidence.md.)*

**If tools must run out, say so in that turn** and name what was obtained: "no
further calls are available; you retrieved X and Y; work only from those and say
plainly what you could not get."

**Retry at the tool layer, not the agent layer**, to keep control flow intact
during transient failures — and bound it. Two or three attempts is the
practitioner consensus; beyond that, returns diminish and repair attempts get
creative. When repair fails, escalate rather than forcing a best-effort answer.
Injected-fault testing shows retry converts timeouts and API errors almost
completely, and does nothing at all for stale or wrong data — so retry is for
transport faults, not content faults.

**Stop the model from writing the observation itself.** In text-protocol loops
(ReAct-style) this is a solved problem: set a stop sequence on the observation
marker so generation halts before the model can invent the tool's reply, and
inject real results yourself. **Native function calling removes that seam** —
there is no marker to stop on — and the failure reappears as an object literal
in the content channel. Detect it: if a turn requests no tool but its text
contains a JSON object whose keys are all parameters of an offered tool, correct
it and retry rather than accepting it as the answer. Keep the detector narrow so
a two-field example in ordinary prose cannot trip it.

**Text from a step that also called a tool is preamble, not the answer.** Exclude
it from the stored reply; that is where narration and stray protocol land.

**Budget for failure-inclusive traversal.** Real exploration spends rounds on
paths that do not exist. Size the budget for the traversal you expect *plus* the
wasted attempts. *(Reasoned hypothesis.)*

## 4. Check claims against provenance

The established name for this is **provenance checking**: every factual span in
the answer must trace to something a tool actually returned, and spans that
cannot be traced are treated as hallucinated. Pick the cheapest tier that covers
your risk.

1. **Exact match.** Extract identifiers, paths, names, and quoted blocks from the
   answer and require each to appear literally in a cached tool result. Free,
   deterministic, no model. Sufficient whenever claims are about things with
   names.
2. **Embedding distance.** Split the answer into sentences and compare each to
   the k nearest source chunks; flag sentences beyond a distance threshold.
   Off-the-shelf validators implement this.
3. **Entailment.** Run a small specialised fact-checker over each sentence
   against its source chunks. Models in the 355M–770M range match or beat GPT-4
   at this task, so this tier is cheap in the sense that matters.

**Do not use a general LLM judge as the primary detector.** On detecting false
completion claims the best judge reached 0.640 AUROC with nothing above 0.65, and
checklists, stepwise reasoning, and richer inputs all failed to help, because
judges anchor on surface signals of completion. Cheap detectors reached
0.825–0.953, caught 4–8× more at the same flag rate, and ran ~3,300× faster. A
judge *with* structured provenance access does better than one without, which is
the point: the win comes from the source alignment, not the judge.

**Treat every flag as triage, never a verdict.** Expect ~50% precision at a 10%
flag rate, and expect confident-sounding prose to evade detection about one time
in five. Surface flags; do not auto-suppress.

**Calibrate against false positives, deliberately.** A check that cries wolf
teaches people to ignore it, which is worse than no check. Match by basename as
well as full path; exclude names that are also common technology names; set
length or specificity thresholds so incidental mentions do not fire. Expect this
to take real iteration — the first version of ours shipped three false-positive
bugs.

**Log the pair regardless of tier.** Every `(tool payload, final response)` pair,
retained. It costs nothing, needs no model access, and it is the only artefact
that makes §1 possible.

## 5. The one prompt-side item: license honest surrender

The harness can make honesty *possible*; only the prompt makes it *permitted*.

**Instruct it specifically and behaviourally**, in the terms of the task: "if the
file could not be read, say so and ask which file to open"; "if the record is
absent, treat the answer as zero". An explicit missing-data rule of this kind
eliminated a substitution failure outright in one study and lifted a task from
53% to 88%.

**Never reach for generic safety boilerplate.** Adding "prioritise user privacy
and data security" amplified invented policy excuses **15.6×**, concentrated on
tools with sensitive-sounding names: safety vocabulary hands the model a
ready-made costume for an infrastructure failure. Be specific about the behaviour
you want and say nothing vague about safety.

## 6. Test it by injecting faults

Fabrication does not raise exceptions, so a green suite proves nothing unless the
suite makes tools misbehave on purpose. The established method is
**record → perturb → re-run**: run the agent against real tools and cache every
response; replay with exactly one response replaced by a fault; then replay again
with your mitigation enabled and confirm the failure disappears *against the
identical fault*. Holding the fault constant is what turns "it seemed better"
into a deterministic check.

A fault matrix worth covering, from the published taxonomy for MCP agents:

- **Transport** — timeout, 5xx error, 403 permission denied, **schema drift**
  (field renamed or removed).
- **Data quality** — **silent empty** (no error code), stale data, contradiction
  between two calls, wrong answer for the query asked. *This category is the
  weakest across every agent tested, including the strongest overall.*
- **Adversarial** — instructions hidden in a tool response, a directive planted
  in tool metadata, a fabricated fact embedded in otherwise valid data.

For each, assert on the harness rather than on model prose where you can: that
the status field says failure, that no provenance-less span survives to the
answer, that the loop retried or deferred, that nothing was persisted. Two
cautions: a passing suite shows robustness to *the faults you injected*, nothing
more; and one fault per run leaves compound and cascading failures untested.

## Deferred, not recommended

**Reasoning effort and model tier.** Raising reasoning effort was the single most
effective fix in the worked example, but published work finds that strengthening
reasoning *increases* a different class of tool hallucination, and the two
measurements are not about the same failure. Deliberately excluded from this
MVP; see references/evidence.md for the tension before treating it as a lever.

## Using this

`references/evidence.md` carries the citations, the measured rates, and a tier
mark on every claim — **measured**, **practice**, or **hypothesis**. Read it
before quoting a number.

Reviewing an existing harness: walk §1 against real logs, then audit §2 and §3.
Building a new one: §2, §3, and §6 are the design; add §4 once it works, since
verification only pays off on a harness that is already honest.
