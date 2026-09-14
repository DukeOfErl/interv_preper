# ADR-0170: LangChain's agent is the only loop

- **Status:** accepted
- **Date:** 2026-08-15
- **Pull request:** TBD — `feat/middleware-native-tools`
- **Supersedes:** the "raw SDK for chat" half of ADR-0020, and ADR-0050's
  deferral of LangGraph

## Context

ADR-0020 chose LangChain for retrieval and the raw OpenAI SDK for chat;
ADR-0050 deferred LangGraph as more machinery than a linear chat turn needed.
Both were right for a loop that called one tool. The GitHub MCP work
(ADR-0130) made the turn genuinely agentic — several dependent reads, a
budget, a screening policy, retries — and five fabrication episodes in testing
were traced to defects in that hand-rolled loop rather than to model dishonesty.

Porting it to `create_agent` was initially done for learning value, keeping the
old loop selectable. That turned out to be untenable: `ToolBox` — schemas,
JSON-string dispatch, turn state, provenance, counters — existed only because
the raw SDK required it. Everything in it now has a first-party equivalent
(runtime injection, `stream_writer`, `state_schema`, `Command`,
`ToolRetryMiddleware`), and the `_tools_from` adapter that translated our
representation back into the framework's was the tell.

## Decision

`InterviewAgent` is the only loop. `InterviewLLM`, `ToolBox`, `llm.py` and the
Developer-tab loop toggle are deleted.

The layering is now:

- **`tools.py`** — independent tools in a list. They report what they got as a
  `ToolOutcome` on the `content_and_artifact` channel and screen nothing.
- **`middleware.py`** — the harness: `InterviewState`, the content policy
  applied to every tool result, the exhaustion note, the typed-call correction.
- **`policy.py`** — admission rules, deliberately importing no agent framework.
- **`agent.py`** — the model, the stream, the accounting.

Three consequences worth naming:

- **Progress goes through `runtime.stream_writer`** and is rendered from the
  stream on the consuming thread. Tools run on a ThreadPoolExecutor; reaching
  sideways into Streamlit from there raised `NoSessionContext` and was reported
  to the model as three GitHub failures that never happened.
- **The reply is read from the graph's final state**, not reconstructed by
  grouping stream chunks by message id. `stream_mode=["messages","custom",
  "values"]` yields tokens, progress and final state from one pass.
- **Every framework default we depend on is pinned explicitly** —
  `exit_behavior`, `on_failure`, `max_retries` — rather than inherited.

**Every field of `InterviewState` must carry a reducer.** A model may request
several tools in one message (`MAX_TOOL_HOPS` already counts on it), and their
middleware then runs concurrently in a single graph step. Without a reducer
LangGraph refuses the second write outright; with a read-modify-write of the
whole value it would be worse — both calls read the same base and one silently
wins, and a file quietly missing from `files_read` is exactly what the
exhaustion note then reports to the model as fact. Each call contributes only
its own delta. Replace-by-question for evaluation cards therefore lives in the
`merge_cards` reducer rather than in the tool, because only the reducer sees
two cards recorded in the same hop.

One practical note for changing any of this: `InterviewState` is consumed at
graph-construction time and bound by name into `agent.py`, so edits to it need
a Streamlit **restart**, not a rerun. Edits to the middleware bodies, the tools
or the policy survive a hot reload.

## The argument against, and our answer

[12-Factor Agents](https://github.com/humanlayer/12-factor-agents) Factor 8 says
plainly: *"You should own the `while` loop. Don't let a framework hide the logic
of when an agent retries, pauses, or terminates."* This ADR does the opposite,
and we have already paid the predicted cost twice:

- An `@after_model` hook that appended a correction did nothing, because in a
  graph the retry is an *edge* that must be declared (`can_jump_to`) and taken
  (`jump_to`). In a for-loop it had been a `continue` statement.
- `ToolCallLimitMiddleware`'s `exit_behavior` defaults to `"continue"` — block
  the tool, let the model keep talking. That is exactly the
  forced-answer-with-no-tools state that produced fabricated repositories.

We accept the trade for two reasons. First, the hooks make each safeguard a
named, separately testable object rather than a branch buried in a loop, and
the fault-injection suite now exercises them through a real turn. Second, the
opacity is manageable precisely *because* of what those two bugs taught: we pin
the defaults we rely on, and we assert on what the model was actually sent
rather than on what our code intended to send.

**`exit_behavior="continue"` is also the only workable option, not just the
kindest.** We keep it because `"end"` stops the run with no closing reply and
`"error"` aborts the turn — but `"end"` additionally raises
`NotImplementedError` when the exhausting message carries **other pending tool
calls**, which our model does routinely (see the reducer requirement above). So
the setting that reads as the framework shipping our fabrication squeeze as a
default is, for a batching model, the only one that runs at all. The hazard was
never the setting; it is the setting *without* `announce_exhausted_tools`.

Two related notes on hook choice, since the same question recurs.
`ToolCallLimitMiddleware` is an `after_model` hook rather than a
`wrap_tool_call` one because its decision needs the **whole batch** — with four
requested calls and two of budget left, something must choose which two, and a
per-call wrapper sees no siblings and has no defined ordering — and because
ending or erroring is control flow, which only a node-style hook can express.
Our own `content_policy_middleware` is the mirror image and is correctly a
wrapper: it acts on one *result* at a time, independently, and never routes.
The rule of thumb is the smallest unit the decision must see.

Where the same document supports the change, it is worth recording too: Factor
5 (unify execution and business state) and Factor 12 (stateless reducer) are
arguments for `InterviewState`, and Factor 9 (compact errors into context) is
what the exhaustion note and `tool_failure_message` do.

## Trade-off accepted

**No fallback loop.** A LangChain regression is now a whole-app regression.
Mitigated by pinned versions and by a fault-injection suite that runs against
the real graph.

**Consent stays prompt-enforced.** `HumanInTheLoopMiddleware` would make the
web-research and GitHub gates structural, and was deliberately not adopted: it
needs a checkpointer and restructures the turn around interrupt/resume, and an
interviewer asking conversationally is a better experience than a modal. The
cost is that consent remains an instruction the model can ignore.

**One thread hazard survives.** Warnings and document registrations are still
raised from inside `wrap_tool_call`, on a worker thread, so `chat_bot`'s
`in_script_thread` remains for those two. Routing them through the custom
stream as progress now is would remove it.

**Schemas are written, not derived.** Tools are built with `StructuredTool` and
explicit JSON Schema rather than inferred from type hints, because those
schemas are prompt text tuned against live model behaviour — the consent
policy, the rubric's nested score object, the tolerance for integral floats.
Letting a signature regenerate them would silently change what the model reads.
