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
        base_url=OPENROUTER_BASE_URL,
        typing_delay=TYPING_DELAY_SECONDS,
    ):
        self.model = model
        self.typing_delay = typing_delay
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        # Token usage from the most recent stream_reply() call (OpenAI-style
        # usage object with .prompt_tokens / .completion_tokens), or None if the
        # provider didn't report it. Read this after the generator is consumed.
        self.last_usage = None

    def stream_reply(self, system_prompt, messages):
        """Yield the assistant reply token-by-token (for ``st.write_stream``).

        A small per-token delay produces the typewriter effect. The final chunk
        carries token usage (requested via ``include_usage``); it is stashed on
        ``self.last_usage`` for cost accounting once the stream is exhausted.
        """
        self.last_usage = None
        chat_messages = [
            {"role": "system", "content": system_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in messages],
        ]
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=chat_messages,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if getattr(chunk, "usage", None):
                self.last_usage = chunk.usage
            token = ""
            if chunk.choices and chunk.choices[0].delta:
                token = chunk.choices[0].delta.content or ""
            if token:
                yield token
                time.sleep(self.typing_delay)
