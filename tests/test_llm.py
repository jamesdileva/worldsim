"""Sprint 25: Ollama client, config precedence, graceful degradation.

HTTP is fully mocked by default so CI needs no Ollama server. Live tests
auto-skip unless a server answers.
"""

import json
import urllib.error

import pytest

from worldsim.llm import (
    DEFAULT_CONFIG_PATH,
    LLMConfig,
    LLMResult,
    OllamaClient,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload=None, status=200, headers=None,
                 raise_exc=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}
        self.raise_exc = raise_exc

    def read(self):
        if self.raise_exc:
            raise self.raise_exc
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _generate_payload(text="hello world"):
    return {"response": text}


def test_generate_ok(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))
    monkeypatch.setattr(
        client, "_post",
        lambda ep, pl: FakeResponse(_generate_payload("hi there")),
    )
    result = client.generate("say hi")
    assert result.ok and result.text == "hi there"
    assert result.error == ""


def test_chat_ok(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))
    messages = [{"role": "user", "content": "hi"}]
    monkeypatch.setattr(
        client, "_post",
        lambda ep, pl: FakeResponse({"message": {"content": "reply"}}),
    )
    result = client.chat(messages)
    assert result.ok and result.text == "reply"


def test_unreachable_degrades_cleanly(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))

    def raise_urlerror(ep, pl):
        raise urllib.error.URLError(reason="connection refused")

    monkeypatch.setattr(client, "_post", raise_urlerror)
    result = client.generate("hi")
    assert result.ok is False
    assert "unreachable" in result.error.lower()
    assert result.text == ""


def test_timeout_degrades_cleanly(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x", timeout_s=1))
    def raise_timeout(ep, pl):
        raise TimeoutError()
    monkeypatch.setattr(client, "_post", raise_timeout)
    result = client.generate("hi")
    assert result.ok is False
    assert "timed out" in result.error.lower()


def test_malformed_json_degrades_cleanly(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))
    monkeypatch.setattr(
        client, "_post",
        lambda ep, pl: FakeResponse(payload=None,
                                    raise_exc=json.JSONDecodeError(
                                        "bad", "{", 0)),
    )
    result = client.generate("hi")
    assert result.ok is False
    assert "malformed" in result.error.lower()


def test_post_redirect_followed_once(monkeypatch):
    """Ollama may answer POSTs with 307; urllib won't re-POST — the client
    must follow once preserving method/payload."""
    client = OllamaClient(LLMConfig(host="http://x"))
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 307, "Temporary Redirect",
                {"Location": "http://x:11434/api/generate/"}, None,
            )
        return FakeResponse(_generate_payload("redirected-ok"))

    import worldsim.llm as llm_mod
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    result = client.generate("hi")
    assert result.ok and result.text == "redirected-ok"
    assert len(calls) == 2
    assert calls[1].endswith("/api/generate/")


def test_is_available_and_list_models(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))
    tags = {"models": [{"name": "llama3.1:8b"}, {"name": "gemma2:2b"}]}
    monkeypatch.setattr(client, "_get",
                        lambda ep: FakeResponse(tags))
    assert client.is_available() is True
    assert client.list_models() == ["llama3.1:8b", "gemma2:2b"]


def test_unavailable_server_flags_false(monkeypatch):
    client = OllamaClient(LLMConfig(host="http://x"))
    def boom(ep):
        raise urllib.error.URLError(reason="refused")
    monkeypatch.setattr(client, "_get", boom)
    assert client.is_available() is False
    assert client.list_models() == []


# ----------------------------------------------------------------------
# Config precedence: overrides > JSON file > defaults
# ----------------------------------------------------------------------

def test_config_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("worldsim.llm.DEFAULT_CONFIG_PATH",
                        tmp_path / "missing.json")
    cfg = LLMConfig.load()
    assert cfg.host.startswith("http://127.0.0.1")
    assert cfg.model == "llama3.1:8b"


def test_config_file_overrides_defaults(tmp_path, monkeypatch):
    cfg_path = tmp_path / "llm_config.json"
    cfg_path.write_text(json.dumps({
        "host": "http://10.0.0.5:11434",
        "model": "gemma2:2b",
        "temperature": 0.3,
        "unknown_key": "ignored",
    }), encoding="utf-8")
    monkeypatch.setattr("worldsim.llm.DEFAULT_CONFIG_PATH", cfg_path)
    cfg = LLMConfig.load()
    assert cfg.host == "http://10.0.0.5:11434"
    assert cfg.model == "gemma2:2b"
    assert cfg.temperature == pytest.approx(0.3)


def test_overrides_beat_file(tmp_path, monkeypatch):
    cfg_path = tmp_path / "llm_config.json"
    cfg_path.write_text(json.dumps({"model": "gemma2:2b"}),
                        encoding="utf-8")
    monkeypatch.setattr("worldsim.llm.DEFAULT_CONFIG_PATH", cfg_path)
    cfg = LLMConfig.load(overrides={"model": "qwen3:8b"})
    assert cfg.model == "qwen3:8b"


def test_corrupt_config_degrades_to_defaults(tmp_path, monkeypatch):
    cfg_path = tmp_path / "bad.json"
    cfg_path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr("worldsim.llm.DEFAULT_CONFIG_PATH", cfg_path)
    cfg = LLMConfig.load()
    assert cfg.model == "llama3.1:8b"  # defaults survive


# ----------------------------------------------------------------------
# Live (auto-skipped unless Ollama answers)
# ----------------------------------------------------------------------

def _live_client():
    from worldsim.llm import OllamaClient as C

    client = C()
    if not client.is_available():
        pytest.skip("Ollama not reachable")
    return client


@pytest.mark.slow
def test_live_generate():
    client = _live_client()
    result = client.generate("Reply with exactly: OK")
    assert result.ok
    assert len(result.text) > 0
