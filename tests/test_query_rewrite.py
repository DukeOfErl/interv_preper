from types import SimpleNamespace

from interview_prep.query_rewrite import QueryCondenser


class FakeCompletions:
    """Stand-in for client.chat.completions with a scripted response."""

    def __init__(self, content=None, exc=None):
        self._content = content
        self._exc = exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, content=None, exc=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(content, exc))


def make_condenser(content=None, exc=None):
    return QueryCondenser(
        api_key="key",
        instructions="rewrite instructions",
        client=FakeClient(content=content, exc=exc),
    )


def test_returns_rewritten_query_stripped():
    condenser = make_condenser(content="  Kubernetes migration at Acme  ")
    result = condenser.condense(
        "tell me more about that",
        [{"role": "assistant", "content": "You mentioned a Kubernetes migration."}],
    )
    assert result == "Kubernetes migration at Acme"


def test_exception_falls_open_to_original_question():
    condenser = make_condenser(exc=RuntimeError("api down"))
    result = condenser.condense("tell me more about that", [{"role": "user", "content": "hi"}])
    assert result == "tell me more about that"


def test_blank_response_falls_open_to_original_question():
    condenser = make_condenser(content="   ")
    assert condenser.condense("what about my leadership?", []) == "what about my leadership?"


def test_none_response_falls_open():
    condenser = make_condenser(content=None)
    assert condenser.condense("original question", []) == "original question"


def test_sends_instructions_and_history_and_question():
    condenser = make_condenser(content="rewritten")
    condenser.condense(
        "tell me more about it",
        [
            {"role": "user", "content": "Ask me about my last role."},
            {"role": "assistant", "content": "What did you do at Acme?"},
        ],
    )
    sent = condenser._client.chat.completions.calls[0]
    assert sent["messages"][0] == {"role": "system", "content": "rewrite instructions"}
    user_content = sent["messages"][1]["content"]
    assert sent["messages"][1]["role"] == "user"
    # The recent history and the latest message are both handed to the model.
    assert "Ask me about my last role." in user_content
    assert "What did you do at Acme?" in user_content
    assert "tell me more about it" in user_content


def test_no_history_sends_bare_question():
    condenser = make_condenser(content="rewritten")
    condenser.condense("what roles fit me?", [])
    user_content = condenser._client.chat.completions.calls[0]["messages"][1]["content"]
    assert user_content == "what roles fit me?"
