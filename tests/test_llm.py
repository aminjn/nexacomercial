import json

import httpx

from app.llm import OllamaLLM


def test_ollama_cloud_request(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, proxy=None):
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": '{"a": 1}'}})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = OllamaLLM("https://ollama.com/v1", "key123", "gpt-oss:120b", 10).complete("sys", "hi", json_mode=True)
    assert out == '{"a": 1}'
    assert seen["url"] == "https://ollama.com/api/chat"
    assert seen["headers"] == {"Authorization": "Bearer key123"}
    assert seen["body"]["format"] == "json" and seen["body"]["stream"] is False
    assert json.loads(out) == {"a": 1}
