"""Tools the interviewer LLM can call, and the dispatcher that runs them.

A *tool* is two things kept deliberately separate here:

  1. a **schema** the model sees (name, description, JSON-Schema parameters) —
     this is prompt text, and it is the only thing that makes the model decide
     to call it, so the description does the real work;
  2. a **local implementation** the model never sees, invoked by ``ToolBox.run``
     with the arguments the model produced.

``get_elapsed_time`` is deliberately trivial (no arguments, no I/O): an LLM has
no clock, so wall-clock time is the smallest piece of information that genuinely
cannot come from the weights or the prompt. It exists to exercise the
tool-calling loop in ``llm.py``, not to be a general pattern for state the
prompt could carry.
"""

from __future__ import annotations

import json
import time


# --- Deliberate sabotage, for observing failure modes ---------------------------
#
# LEARNING SCAFFOLDING — delete with this branch. Selectable in the sidebar's
# Developer tab so a failure can be induced mid-interview without a restart.
# Each mode breaks a *different* link in the chain, and the interesting part is
# what the model does next:
#
#   off             everything works.
#   wrong_name      the schema advertises a name the dispatcher doesn't handle.
#                   The model calls correctly; the dispatch fails. This is the
#                   classic drift bug — rename the method, forget the schema.
#   bad_args        the schema declares a required argument the implementation
#                   won't accept. The model dutifully invents a value, and the
#                   splat into the function fails.
#   garbage_result  the tool succeeds but returns nonsense. Nothing errors
#                   anywhere, which is what makes this the dangerous one.
#   no_hint         the description is stripped of any instruction to use the
#                   tool. Usually the model never calls it and states a time
#                   from nowhere — a silent failure with no error to find.
FAILURE_MODES = ["off", "wrong_name", "bad_args", "garbage_result", "no_hint"]

# The real description earns the call; the vague one usually doesn't.
_REAL_DESCRIPTION = (
    "Return how long the current interview has been running. Call this "
    "whenever pacing matters — before announcing time remaining, deciding "
    "whether to move on to the next question, or answering a question about "
    "elapsed or remaining time. You have no other way to know the time, so "
    "never estimate it."
)
_VAGUE_DESCRIPTION = "Returns timing information."


class ToolBox:
    """The tools available for one turn, bound to that turn's live state.

    Bound at construction rather than passed per call: the model supplies only
    the arguments in its schema, so anything else the implementation needs
    (here, when the interview started) has to be closed over by the caller.
    """

    def __init__(self, started_at, failure_mode="off"):
        # Monotonic timestamp of the interview's first user message.
        self.started_at = started_at
        self.failure_mode = failure_mode

    @property
    def specs(self):
        """The tool schemas, in the shape the chat-completions API expects."""
        # Only ``run`` knows the real name, so advertising a different one here
        # is enough to break dispatch while leaving the model blameless.
        name = (
            "get_elapsed_time_v2"
            if self.failure_mode == "wrong_name"
            else "get_elapsed_time"
        )
        properties = {}
        required = []
        if self.failure_mode == "bad_args":
            properties = {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name, e.g. Europe/Madrid.",
                }
            }
            required = ["timezone"]
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (
                        _VAGUE_DESCRIPTION
                        if self.failure_mode == "no_hint"
                        else _REAL_DESCRIPTION
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def run(self, name, arguments):
        """Execute tool ``name`` with the model's ``arguments`` (a JSON string).

        Always returns a string — the tool message's content — including for
        failures. Raising here would abort the turn; handing the model an error
        string lets it recover or tell the user, which is almost always the
        behavior you want.
        """
        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return "error: arguments were not valid JSON"
        if name != "get_elapsed_time":
            return f"error: no tool named {name!r}"
        try:
            return self._get_elapsed_time(**parsed)
        except TypeError as exc:
            # Models do invent arguments a schema never declared; splatting them
            # would raise out of the tool loop and kill the turn.
            return f"error: {exc}"

    def _get_elapsed_time(self):
        """Elapsed interview time, phrased for a model rather than a parser."""
        if self.failure_mode == "garbage_result":
            # Succeeds loudly and wrongly: no exception, no error string, and
            # the model has no way to tell this from the truth.
            return json.dumps(
                {
                    "elapsed_minutes": 4200,
                    "elapsed_seconds": 0,
                    "human": "4200 min 0 sec since the interview began",
                }
            )
        seconds = int(time.monotonic() - self.started_at)
        minutes, seconds = divmod(seconds, 60)
        return json.dumps(
            {
                "elapsed_minutes": minutes,
                "elapsed_seconds": seconds,
                "human": f"{minutes} min {seconds} sec since the interview began",
            }
        )
