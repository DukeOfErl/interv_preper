from types import SimpleNamespace

from interview_prep import llm
from interview_prep.llm import InterviewLLM


def _chunk(content):
    """Build a fake streaming chunk with the given delta content."""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def _build_llm(monkeypatch, chunks):
    """Construct an InterviewLLM whose OpenAI client is a fake stream source."""
    client = _FakeClient(chunks)
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: client)
    instance = InterviewLLM(api_key="key", model="test-model", typing_delay=0)
    return instance, client


def test_stream_reply_yields_only_nonempty_tokens(monkeypatch):
    chunks = [
        _chunk("Hello"),
        _chunk(""),  # empty content -> skipped
        _chunk(None),  # None content -> skipped
        _chunk(" world"),
        SimpleNamespace(choices=[]),  # no choices -> skipped
    ]
    instance, _ = _build_llm(monkeypatch, chunks)

    tokens = list(instance.stream_reply("system", []))
    assert tokens == ["Hello", " world"]


def test_stream_reply_prepends_system_then_history(monkeypatch):
    instance, client = _build_llm(monkeypatch, [_chunk("ok")])
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    list(instance.stream_reply("SYS", history))

    (call,) = client.chat.completions.calls
    assert call["model"] == "test-model"
    assert call["stream"] is True
    assert call["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
