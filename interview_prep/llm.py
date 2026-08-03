"""OpenRouter (OpenAI-compatible) client and streamed responses."""

from __future__ import annotations

import re
import time

from openai import OpenAI

from .config import MAX_TOOL_HOPS, OPENROUTER_BASE_URL, TYPING_DELAY_SECONDS

# A brace-delimited object literal, and the quoted keys inside one.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]{0,800}\}", re.DOTALL)
_JSON_KEY_RE = re.compile(r'"(\w+)"\s*:')
# Enough keys that an ordinary sentence containing braces cannot qualify.
_MIN_TYPED_CALL_KEYS = 3


def looks_like_typed_tool_call(text, specs):
    """True when a reply contains a tool call written out as prose.

    Deliberately narrow: an object literal is only read as a typed tool call
    when it has at least ``_MIN_TYPED_CALL_KEYS`` keys and *every* key is a
    parameter of a tool the model was offered. A reply that merely discusses
    JSON, or shows a two-field example, does not qualify — the cost of a false
    positive is a wasted hop and a re-answer.
    """
    if not text or "{" not in text:
        return False
    parameters = set()
    for spec in specs or []:
        function = spec.get("function") or {}
        schema = function.get("parameters") or {}
        parameters |= set(schema.get("properties") or {})
    if not parameters:
        return False
    for blob in _JSON_OBJECT_RE.findall(text):
        keys = set(_JSON_KEY_RE.findall(blob))
        if len(keys) >= _MIN_TYPED_CALL_KEYS and keys <= parameters:
            return True
    return False


class InterviewLLM:
    """Thin wrapper around the OpenRouter chat-completions API."""

    def __init__(
        self,
        api_key,
        model,
        reasoning_effort=None,
        base_url=OPENROUTER_BASE_URL,
        typing_delay=TYPING_DELAY_SECONDS,
    ):
        self.model = model
        # Reasoning effort ("low"/"medium"/"high") for reasoning models, or None
        # to send no reasoning param (non-reasoning models).
        self.reasoning_effort = reasoning_effort
        self.typing_delay = typing_delay
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        # Metadata from the most recent stream_reply() call, populated from the
        # final usage chunk and read after the generator is consumed:
        #   last_usage             — OpenAI-style usage object (token counts)
        #   last_cost              — actual USD cost OpenRouter charged, or None
        #   last_reasoning_tokens  — reasoning tokens billed as output, or None
        #   last_tool_calls        — trace of tools run this turn (for the UI)
        # Each stays None if the provider didn't report it. With tools in play a
        # single turn makes several API calls, so cost and reasoning tokens are
        # SUMS across them, while ``last_usage`` holds only the final call's
        # object (it backs a fallback path that OpenRouter's reported cost
        # normally pre-empts — see chat_bot.py).
        self.last_usage = None
        self.last_cost = None
        self.last_reasoning_tokens = None
        self.last_tool_calls = []
        #   answer_text — the reply minus preamble from tool-calling hops
        self.answer_text = ""
        #   typed_tool_call_retries — hops that wrote a tool call as prose
        #   answered — whether a hop produced the final answer (it may be "")
        self.typed_tool_call_retries = 0
        self.answered = False

    def stream_reply(self, system_prompt, messages, toolbox=None):
        """Yield the assistant reply token-by-token (for a typewriter effect).

        Without ``toolbox`` this is one streamed API call. With one, it becomes
        the tool-calling loop: stream a call, and if the model finished by
        requesting tools instead of talking, run them, append the results to the
        conversation, and stream again. Text from every hop is yielded, so the
        caller keeps consuming a flat stream of tokens and never sees the hops.

        Bounded by ``MAX_TOOL_HOPS``. On the final hop the tools are withheld,
        which forces a text answer rather than truncating mid-loop.

        Everything is yielded as it arrives, so a hop that talks before calling
        a tool ("let me look that up") shows immediately. But such text is
        PREAMBLE, not the answer, and a model sometimes emits tool-call JSON
        there instead of prose — so ``answer_text`` accumulates only the text of
        hops that requested no tools. Callers persist that, not the raw stream.
        """
        self.last_usage = None
        self.last_cost = None
        self.last_reasoning_tokens = None
        self.last_tool_calls = []
        self.answer_text = ""
        self.typed_tool_call_retries = 0
        self.answered = False
        chat_messages = [
            {"role": "system", "content": system_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in messages],
        ]

        for hop in range(MAX_TOOL_HOPS):
            offer_tools = toolbox is not None and hop < MAX_TOOL_HOPS - 1
            if toolbox is not None and not offer_tools:
                # Last hop: the answer is about to be forced with no tools
                # left. Let the toolbox state what the turn failed to obtain,
                # so an honest "I couldn't read it" beats inventing one.
                note = toolbox.final_hop_note()
                if note:
                    chat_messages.append({"role": "system", "content": note})
            text, tool_calls = yield from self._stream_once(
                chat_messages, toolbox if offer_tools else None
            )
            if not offer_tools:
                # Tools were withheld (final hop, or no toolbox at all), so
                # this text is the answer — even when empty. Any tool_calls a
                # provider still echoes are ignored deliberately: running one
                # would bill a call whose result no later hop could read.
                self.answer_text += text
                self.answered = True
                return
            if not tool_calls:
                # A model sometimes *types* a tool call instead of making one.
                # The hop then looks like a finished answer, so the loop would
                # end and that JSON would become the reply. Correct it and let
                # it try again while hops remain.
                if looks_like_typed_tool_call(text, toolbox.specs):
                    self.typed_tool_call_retries += 1
                    chat_messages.append({"role": "assistant", "content": text})
                    chat_messages.append(
                        {
                            "role": "system",
                            "content": (
                                "SYSTEM NOTE: that reply contained a tool call "
                                "written as text. Text is never delivered to a "
                                "tool. Request the tool properly through the "
                                "tool interface, or, if you do not need one, "
                                "answer the candidate in plain prose with no "
                                "JSON."
                            ),
                        }
                    )
                    continue
                self.answer_text += text
                self.answered = True
                return
            # The assistant's tool-request message must be replayed verbatim —
            # every tool result is matched to it by tool_call_id, and the API
            # rejects a tool message with no preceding request.
            chat_messages.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"],
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                result = toolbox.run(call["name"], call["arguments"])
                self.last_tool_calls.append(
                    {
                        "name": call["name"],
                        "arguments": call["arguments"],
                        "result": result,
                    }
                )
                chat_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )

    def _stream_once(self, chat_messages, toolbox):
        """Stream one API call; yield its text, return ``(text, tool_calls)``.

        The generator's return value (not its yields) carries the tool calls, so
        ``stream_reply`` can ``yield from`` this and still get them back.
        """
        # Ask OpenRouter for usage accounting so the final chunk carries the real
        # cost; the reasoning param (if any) rides in the same extra_body.
        extra_body = {"usage": {"include": True}}
        if self.reasoning_effort:
            extra_body["reasoning"] = {"effort": self.reasoning_effort}
        create_kwargs = {
            "model": self.model,
            "messages": chat_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": extra_body,
        }
        if toolbox is not None:
            create_kwargs["tools"] = toolbox.specs
        stream = self._client.chat.completions.create(**create_kwargs)
        parts = []
        pending_calls = {}
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                self.last_usage = usage
                self._record_usage_extras(usage)
            if not chunk.choices:
                # Usage-only chunk (it carries no choices at all).
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue
            # getattr, not attribute access: a provider streaming a plain text
            # reply may omit the field entirely rather than send null.
            tool_deltas = getattr(delta, "tool_calls", None)
            if tool_deltas:
                self._accumulate_tool_calls(pending_calls, tool_deltas)
            token = delta.content or ""
            if token:
                parts.append(token)
                yield token
                time.sleep(self.typing_delay)
        text = "".join(parts)
        # Ordered by the index the provider assigned, which is the order the
        # model asked for them in.
        calls = [pending_calls[i] for i in sorted(pending_calls)]
        return text, [c for c in calls if c["id"] and c["name"]]

    @staticmethod
    def _accumulate_tool_calls(pending, deltas):
        """Reassemble streamed tool-call fragments in place.

        A tool call does not arrive whole: ``id`` and ``function.name`` come in
        an early chunk, then ``function.arguments`` streams in as a series of
        JSON fragments that are only valid once concatenated. ``index``
        identifies which call a fragment belongs to when the model requests
        several at once — it is the only thing tying the pieces together, since
        later fragments repeat neither the id nor the name.
        """
        for delta in deltas:
            call = pending.setdefault(
                delta.index, {"id": "", "name": "", "arguments": ""}
            )
            if delta.id:
                call["id"] = delta.id
            function = getattr(delta, "function", None)
            if function is None:
                continue
            if function.name:
                call["name"] = function.name
            if function.arguments:
                call["arguments"] += function.arguments

    def _record_usage_extras(self, usage):
        """Add OpenRouter's actual cost and reasoning-token count off ``usage``.

        Both are extras beyond the standard OpenAI usage fields — ``cost`` is a
        top-level attribute (stored in the SDK model's extras), and reasoning
        tokens live under ``completion_tokens_details``. Anything missing or
        unparseable contributes nothing. Values accumulate rather than overwrite
        because one turn can span several API calls; a turn whose every call
        reported nothing therefore still ends at None.
        """
        cost = getattr(usage, "cost", None)
        if cost is None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost")
        try:
            if cost is not None:
                self.last_cost = (self.last_cost or 0.0) + float(cost)
        except (TypeError, ValueError):
            pass

        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None) if details else None
        try:
            if reasoning is not None:
                self.last_reasoning_tokens = (
                    self.last_reasoning_tokens or 0
                ) + int(reasoning)
        except (TypeError, ValueError):
            pass
