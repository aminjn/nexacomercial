"""AI connections: many text / image servers, a default per kind, per-campaign choice and automatic failover."""
from __future__ import annotations

import logging
from typing import Any

from sqlmodel import select

from . import images
from . import models as m
from .config import settings
from .llm import LLM, AnthropicLLM, FakeLLM, OllamaLLM, OpenAICompatLLM, list_models, ollama_models

log = logging.getLogger(__name__)

TEXT_PROVIDERS = {
    "ollama": "Ollama (سرور خودت)",
    "openai": "سازگار با OpenAI (vLLM، LM Studio، OpenRouter، ...)",
    "anthropic": "Claude (API رسمی Anthropic)",
    "fake": "آزمایشی — متن ثابت",
}
IMAGE_PROVIDERS = {
    "a1111": "Stable Diffusion WebUI (AUTOMATIC1111 / Forge / SD.Next)",
    "comfyui": "ComfyUI",
    "openai": "سازگار با OpenAI Images API",
}


# ---------------------------------------------------------------- building clients


def make_llm(c: m.AIConnection, timeout: float | None = None) -> LLM:
    t = timeout or float(c.opt("timeout_sec", settings.llm_timeout_sec))
    if c.provider == "fake":
        return FakeLLM()
    if c.provider == "anthropic":
        return AnthropicLLM(c.api_key, c.model or settings.anthropic_model, t)
    if c.provider == "ollama":
        return OllamaLLM(c.base_url, c.api_key, c.model, t)
    return OpenAICompatLLM(c.base_url, c.api_key, c.model, t)


def image_cfg(c: m.AIConnection) -> images.ImageCfg:
    return images.ImageCfg(
        provider=c.provider, base_url=c.base_url, api_key=c.api_key, model=c.model,
        width=int(c.opt("width", 1024)), height=int(c.opt("height", 1024)), steps=int(c.opt("steps", 25)),
        negative_prompt=c.opt("negative_prompt", "") or "", style=c.opt("style", "") or "",
        comfy_workflow=c.opt("comfy_workflow", "") or "", timeout_sec=float(c.opt("timeout_sec", 300)),
    )


# ---------------------------------------------------------------- choosing / failover


def chain(kind: str, preferred_id: int | None = None) -> list[m.AIConnection]:
    """Enabled connections of a kind in the order to try: campaign's choice, default, then the rest."""
    with m.session() as s:
        rows = [c for c in s.exec(select(m.AIConnection).where(m.AIConnection.kind == kind)).all() if c.enabled]
    rows.sort(key=lambda c: (c.id != preferred_id, not c.is_default, c.id))
    return rows


class FailoverLLM:
    """Tries each connection in order; moves on when one is unreachable or errors."""

    def __init__(self, conns: list[m.AIConnection]) -> None:
        self.conns = conns
        self.used: m.AIConnection | None = None

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        errors = []
        for c in self.conns:
            try:
                out = make_llm(c).complete(system, user, json_mode=json_mode)
                self.used = c
                return out
            except Exception as e:  # noqa: BLE001
                log.warning("AI connection %s (%s) failed: %s", c.id, c.name, e)
                errors.append(f"«{c.name}»: {type(e).__name__}: {e}")
        raise RuntimeError(" | ".join(errors) or "no AI connection")


def llm_for(preferred_id: int | None = None) -> LLM | None:
    conns = chain("text", preferred_id)
    return FailoverLLM(conns) if conns else None


def image_renderer(preferred_id: int | None = None):
    """Callable(prompt) -> bytes that tries image connections in order; None when none is configured."""
    conns = chain("image", preferred_id)
    if not conns:
        return None

    def render(prompt: str) -> bytes:
        errors = []
        for c in conns:
            try:
                return images.generate_bytes(prompt, image_cfg(c))
            except Exception as e:  # noqa: BLE001
                log.warning("image connection %s (%s) failed: %s", c.id, c.name, e)
                errors.append(f"«{c.name}»: {type(e).__name__}: {e}")
        raise images.ImageError(" | ".join(errors))
    return render


# ---------------------------------------------------------------- testing


def test(c: m.AIConnection) -> tuple[bool, str, str]:
    """Returns (ok, message, image_url_or_empty) and stores the result on the connection."""
    img = ""
    try:
        if c.kind == "text":
            if c.provider == "ollama":
                models = ollama_models(c.base_url, c.api_key)
                if c.model not in models and f"{c.model}:latest" not in models:
                    raise RuntimeError(f"به سرور وصل شد ولی مدل «{c.model}» روی آن نیست. مدل‌های موجود: "
                                       f"{', '.join(models) or 'هیچ'}")
            reply = make_llm(c, timeout=120).complete("Answer in one short sentence.", "سلام، یک جمله درباره‌ی خودت بگو.")
            ok, msg = True, f"وصل است ✓ جواب: {reply.strip()[:160]}"
        else:
            name = images.store_jpeg(images.generate_bytes("modern apartment living room, warm natural light",
                                                           image_cfg(c)), prefix="test")
            img = f"/media/{name}"
            ok, msg = True, "تصویر ساخته شد ✓"
    except Exception as e:  # noqa: BLE001
        from .engine import explain_error
        ok, msg = False, explain_error("ai" if c.kind == "text" else "image", "", e)
    with m.session() as s:
        row = s.get(m.AIConnection, c.id)
        if row:
            row.last_test_ok, row.last_test_msg, row.last_test_at = ok, msg[:500], m.utcnow()
            s.add(row)
            s.commit()
    return ok, msg, img


def models_on(provider: str, base_url: str, api_key: str) -> list[str]:
    return list_models(provider, base_url, api_key)


# ---------------------------------------------------------------- saving / migration


def set_default(conn_id: int) -> None:
    with m.session() as s:
        target = s.get(m.AIConnection, conn_id)
        if target is None:
            return
        for c in s.exec(select(m.AIConnection).where(m.AIConnection.kind == target.kind)).all():
            c.is_default = c.id == conn_id
            s.add(c)
        s.commit()


def ensure_default(kind: str) -> None:
    """Keep exactly one default per kind when connections exist."""
    with m.session() as s:
        rows = s.exec(select(m.AIConnection).where(m.AIConnection.kind == kind)).all()
        if rows and not any(c.is_default for c in rows):
            rows[0].is_default = True
            s.add(rows[0])
            s.commit()


def seed_from_settings() -> None:
    """First start after upgrading: turn the old single AI / image settings into connections (once)."""
    from .runtime import AppSetting
    with m.session() as s:
        if s.get(AppSetting, "ai_seeded"):
            return
        if not s.exec(select(m.AIConnection)).first():
            if settings.llm_provider in TEXT_PROVIDERS and settings.llm_provider != "fake":
                c = m.AIConnection(name="هوش مصنوعی اصلی", kind="text", provider=settings.llm_provider,
                                   base_url=settings.llm_base_url, model=settings.llm_model,
                                   options={"timeout_sec": settings.llm_timeout_sec}, is_default=True)
                c.api_key = settings.anthropic_api_key if settings.llm_provider == "anthropic" else settings.llm_api_key
                s.add(c)
            cfg = images.cfg_from_settings()
            if cfg:
                c = m.AIConnection(name="سرور تصویر", kind="image", provider=cfg.provider, base_url=cfg.base_url,
                                   model=cfg.model, is_default=True, options={
                                       "width": cfg.width, "height": cfg.height, "steps": cfg.steps,
                                       "style": cfg.style, "negative_prompt": cfg.negative_prompt,
                                       "comfy_workflow": cfg.comfy_workflow, "timeout_sec": cfg.timeout_sec})
                c.api_key = cfg.api_key
                s.add(c)
        s.add(AppSetting(key="ai_seeded", value="1"))
        s.commit()


def summary() -> dict[str, Any]:
    with m.session() as s:
        rows = s.exec(select(m.AIConnection)).all()
    out: dict[str, Any] = {}
    for kind in ("text", "image"):
        ks = [c for c in rows if c.kind == kind]
        out[kind] = {"count": len(ks), "enabled": sum(c.enabled for c in ks),
                     "default": next((c for c in ks if c.is_default), None)}
    return out
