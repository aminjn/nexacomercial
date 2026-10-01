"""LLM abstraction: OpenAI-compatible (Ollama/vLLM/OpenRouter), official Claude API, or a fake."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Protocol

import httpx

from .config import settings

log = logging.getLogger(__name__)


class LLM(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...


class OpenAICompatLLM:
    """Works with Ollama (`/v1`), vLLM, LM Studio, OpenRouter, OpenAI, ..."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.8,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AnthropicLLM:
    """Official Anthropic SDK. Streaming so long articles never hit HTTP timeouts."""

    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        import anthropic  # imported lazily so the package is optional

        self.client = anthropic.Anthropic(api_key=api_key or None, timeout=timeout)
        self.model = model

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        if json_mode:
            system += "\nReply with a single JSON object only. No markdown fences, no prose."
        with self.client.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError("Claude refused the request")
        return "".join(b.text for b in msg.content if b.type == "text")


class FakeLLM:
    """Deterministic output for tests and dry-runs. Never calls the network."""

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        h = hashlib.sha1(user.encode()).hexdigest()[:6]
        if "SOCIAL" in user:
            return json.dumps({"text": f"پست آزمایشی {h} — به سایت ما سر بزنید", "hashtags": ["تست"]}, ensure_ascii=False)
        return json.dumps(
            {
                "title": f"مقاله آزمایشی {h}",
                "excerpt": "خلاصه‌ی مقاله‌ی آزمایشی.",
                "tags": ["تست", "نمونه"],
                "body_markdown": (
                    f"## مقدمه\n\nاین یک مقاله‌ی آزمایشی ({h}) است.\n\n"
                    "## نکته اول\n\nمتن نمونه برای بررسی جریان انتشار.\n\n"
                    "## جمع‌بندی\n\nپایان."
                ),
            },
            ensure_ascii=False,
        )


def get_llm() -> LLM:
    p = settings.llm_provider.lower()
    if p == "fake":
        return FakeLLM()
    if p == "anthropic":
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model, settings.llm_timeout_sec)
    return OpenAICompatLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_sec)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction: strips fences and grabs the outermost {...}."""
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise
