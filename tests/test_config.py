from interview_prep import config


def test_load_api_key_reads_environment(monkeypatch):
    # Neutralize .env loading so the test only sees the environment we set.
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-123")
    assert config.load_api_key() == "secret-123"


def test_load_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert config.load_api_key() is None


def test_load_github_pat_reads_environment(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GITHUB_PAT", "ghp-456")
    assert config.load_github_pat() == "ghp-456"


def test_load_github_pat_returns_none_when_unset(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    assert config.load_github_pat() is None
