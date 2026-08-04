---
name: tool-loop-honesty
description: Use when designing, reviewing, or debugging an LLM that calls tools — function calling, MCP servers, or any agentic loop — so that it admits failure instead of inventing results. Load when a tool-using agent invents data or file contents, writes out tool calls and their results as prose, claims a task succeeded that did not, or blames a policy or privacy rule for what was really a technical failure. Also load when designing tool result shapes, step or hop budgets, or checks on what an agent claims.
---

# Tool-loop honesty

Keep a tool-using model admitting failure instead of filling the gap. This is
about the **model's prose and final answer** — not about it calling tools that
do not exist.

## The failure

When a tool returns nothing usable, the model has three moves, and it picks a
dishonest one more often than not. Measured over 396 trajectories against tools
that fail silently — HTTP 200 carrying an empty, null, malformed, or truncated
payload:

| Outcome | What it looks like | Rate |
|---|---|---|
| **Honest surrender** | "I was unable to retrieve the record — the system returned no data." | 43.2% |
| **Fabrication** | Presents invented data as though the tool returned it. | **56.6%** |
| **Unfaithful safety refusal** | "I cannot access this due to privacy restrictions" — when no such rule exists. | 0.25%, rising to 3.95% under generic safety prompting |

Two things follow. Fabrication is the **default response to a broken tool**, not
an occasional lapse. And both dishonest outcomes **mask infrastructure faults**:
what presents as a lying model is usually a defective harness.

It helps to remember *why* tools invite this when plain chat does not. A
tool-use transcript is a rigid format — intent, call, result, intent, call,
result, answer — and a model generating inside that format can continue it from
its own weights, with the environment no longer participating. Nothing in the
format distinguishes a result you handed it from a result it wrote. (Framing,
not a measured finding.)

## 1. Diagnose the harness before the model

Work in this order. Most fabrication has a defect upstream of it.

1. **Did the tool return success carrying nothing?** Inspect the raw payload,
   never the model's summary of it. This is the single most common trigger.
2. **Can you even see the raw payloads?** Log every `(tool payload, final
   response)` pair. Without that you cannot separate "the model lied" from "the
   tool broke" — a fabricated transcript and a real one read identically.
3. **Did calls fail on malformed arguments?** Check whether you advertise
   parameters the model does not need, or whose values you override and discard.
   Every unnecessary knob is a chance to garble the call.
4. **Did the model run out of tool calls mid-task?** Count the rounds actually
   available, not the cap.
5. **Did the model write a tool call as prose instead of calling it?** If your
   loop treats a turn with no tool call as a finished answer, that text becomes
   the reply.
6. **Only now** consider model capability and reasoning effort — see §4.

## 2. Make failure unmistakable in the tool result

**Never return success with an empty payload.** Test these four shapes
deliberately; all of them read as success and all of them provoke invention:

- `{"status": 200, "data": []}` — empty but valid
- `{"status": 200, "data": null}` — null field
- `{"status": 200, "message": "error_code_x74"}` — malformed, no data key
- `{"status": 200, "data": [{"id": "rec_001"}]}` — truncated, substantive fields missing

Then:

- **State absence in words the model can repeat.** "No records matched this
  query" is a *result*; a bare empty list is an ambiguity the model resolves by
  guessing. If the request partially succeeded, say which part did not.
- **Make errors actionable** and include an example of a correctly formed call.
  A traceback tells the model nothing it can act on.
- **Return high-signal fields only.** Strip opaque identifiers (`uuid`,
  `mime_type`, URL templates) in favour of semantic ones; resolving identifiers
  to meaningful names measurably reduces hallucination. Verbose noise also
  teaches the model that results are JSON it could have written itself.
- **Truncate and paginate with stated limits**, and say what was cut and how to
  get the rest — otherwise the model treats the fragment as the whole.
- **Do not advertise parameters you override or ignore.** A knob whose value you
  discard is a decision that is not the model's to make.
- **Prefer explicit, narrowly named tools over generic ones.** Agents choose
  correctly far more often when the right tool is obvious from its name.

## 3. Keep honesty reachable in the loop

- **Never demand an answer while withdrawing the means to ground it.** A loop
  that withholds tools on its final step and still requires output has removed
  the only honest action and left invention as the sole way to comply. Any
  forced-output mechanism does this: step caps, retry limits, rigid response
  formats, timeouts. *(Reasoned hypothesis — see references/evidence.md.)*
- **If you must stop tool access, say so in that turn**, and name what was
  actually obtained: "no further calls are available; you retrieved X and Y;
  work only from those, and say plainly what you could not get."
- **Keep the control channel separate from prose, and check the separation.** If
  a turn requests no tool but its text contains what looks like a call, correct
  it and retry rather than accepting it as the answer. *(Reasoned hypothesis.)*
- **Budget for failure-inclusive traversal.** Real exploration spends rounds on
  paths that do not exist. Size the budget for the traversal you expect *plus*
  the wasted attempts, not the happy path.
- **Text from a step that also called a tool is preamble, not the answer.** Keep
  it out of the transcript; that is where narration and stray protocol land.
- **Make recovery possible, not just failure rare.** Reliable recovery from
  errors predicts success better than avoiding errors does, so return results a
  model can retry against.

## 4. Verify cheaply, and instruct surrender specifically

**Do not use an LLM judge as your primary detector.** On detecting false
completion claims, the best judge reached 0.640 AUROC and no configuration
exceeded 0.65; checklists, stepwise reasoning, and richer inputs all failed to
help, because judges anchor on surface signals of completion. Cheap
deterministic detectors reached 0.825–0.953, caught 4–8× more at the same flag
rate, and ran roughly 3,300× faster.

- **Check claims against what the tools actually returned.** You hold the
  ground truth, so verification is a text comparison, not a judgement: names,
  paths, identifiers, and quoted content that appear in no result are
  fabrication candidates. Retrieval-grounded output is checkable output.
- **Treat every flag as triage, never a verdict.** At a 10% flag rate, expect
  around 50% precision, and expect confident-sounding prose to evade detection
  in roughly one case in five. Surface flags to a human; do not auto-suppress.
- **Calibrate hard against false positives.** A check that cries wolf trains
  people to ignore it, which is worse than having no check. Match by basename as
  well as full path, ignore names that are also common technology names, and set
  length or specificity thresholds so incidental mentions do not fire.
- **Instruct surrender specifically and behaviourally.** State what to do when
  data is missing, in the terms of the task: *"if the file could not be read,
  say so and ask which file to open"*, *"if the record is absent, treat the
  answer as zero"*. An explicit missing-data rule of this kind removed a
  substitution failure entirely in one study and lifted a task from 53% to 88%.
- **Never reach for generic safety boilerplate.** Adding language like
  "prioritise user privacy and data security" amplified invented policy excuses
  **15.6×**, concentrated on tools with sensitive-sounding names. Safety
  vocabulary hands the model a ready-made way to dress up an infrastructure
  failure. Be specific about the behaviour you want; say nothing vague about
  safety.
- **Watch for the policy excuse as its own symptom.** A response citing privacy,
  authorisation, or compliance when your prompt and tool specs define no such
  rule is a technical failure wearing a costume. Flagging responses that pair an
  empty payload with policy language catches it at the message boundary, with no
  model access required.

## Applying this

Read `references/evidence.md` before citing any number, and to see which claims
here are measured, which are first-party guidance, and which are reasoned
hypotheses from a single worked example.

When reviewing an existing loop, walk §1 as a checklist against real logs. When
building a new one, §2 and §3 are the design review; §4 is what you add once it
works, because it only pays off against a harness that is already honest.
