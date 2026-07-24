"""OpenRouter (OpenAI-compatible) client and streamed responses."""

from __future__ import annotations

import time

from openai import OpenAI

from .config import OPENROUTER_BASE_URL, TYPING_DELAY_SECONDS


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
        # Each stays None if the provider didn't report it.
        self.last_usage = None
        self.last_cost = None
        self.last_reasoning_tokens = None

    def stream_reply(self, system_prompt, messages):
        """Yield the assistant reply token-by-token (for a typewriter effect).

        A small per-token delay produces the typewriter effect. The final chunk
        carries usage metadata (requested via ``include_usage`` plus OpenRouter's
        ``usage.include``); the token counts, actual USD ``cost``, and reasoning
        tokens are stashed on ``self.last_*`` for cost accounting once the stream
        is exhausted.
        """
        self.last_usage = None
        self.last_cost = None
        self.last_reasoning_tokens = None
        chat_messages = [
            {"role": "system", "content": system_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in messages],
        ]
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
        stream = self._client.chat.completions.create(**create_kwargs)
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                self.last_usage = usage
                self._record_usage_extras(usage)
            token = ""
            if chunk.choices and chunk.choices[0].delta:
                token = chunk.choices[0].delta.content or ""
            if token:
                yield token
                time.sleep(self.typing_delay)

    def _record_usage_extras(self, usage):
        """Pull OpenRouter's actual cost and reasoning-token count off ``usage``.

        Both are extras beyond the standard OpenAI usage fields — ``cost`` is a
        top-level attribute (stored in the SDK model's extras), and reasoning
        tokens live under ``completion_tokens_details``. Anything missing or
        unparseable leaves the corresponding attribute at None.
        """
        cost = getattr(usage, "cost", None)
        if cost is None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost")
        try:
            self.last_cost = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            self.last_cost = None

        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None) if details else None
        try:
            self.last_reasoning_tokens = (
                int(reasoning) if reasoning is not None else None
            )
        except (TypeError, ValueError):
            self.last_reasoning_tokens = None
