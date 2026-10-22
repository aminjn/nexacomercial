"""AI image generation on the user's own image server, for Instagram / Pinterest posts.

Supported servers (chosen in Settings):
  a1111   — Stable Diffusion WebUI (AUTOMATIC1111 / Forge / SD.Next):  POST /sdapi/v1/txt2img
  comfyui — ComfyUI: a workflow exported with "Save (API Format)" containing {{prompt}} placeholders
  openai  — any OpenAI-compatible images API:                          POST <base>/images/generations
"""
from __future__ import annotations

import base64
import io
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import settings
from .llm import LLM, parse_json

log = logging.getLogger(__name__)


class ImageError(RuntimeError):
    pass


def enabled() -> bool:
    return settings.image_provider in ("a1111", "comfyui", "openai")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.image_api_key}"} if settings.image_api_key else {}


def _base() -> str:
    if not settings.image_base_url:
        raise ImageError("آدرس سرور تولید تصویر در تنظیمات خالی است")
    return settings.image_base_url.rstrip("/")


# ---------------------------------------------------------------- providers → raw image bytes


def _a1111(prompt: str) -> bytes:
    body: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": settings.image_negative_prompt,
        "width": settings.image_width,
        "height": settings.image_height,
        "steps": settings.image_steps,
        "batch_size": 1,
        "n_iter": 1,
    }
    if settings.image_model:
        body["override_settings"] = {"sd_model_checkpoint": settings.image_model}
    r = httpx.post(f"{_base()}/sdapi/v1/txt2img", json=body, headers=_headers(), timeout=settings.image_timeout_sec)
    if r.status_code >= 400:
        raise ImageError(f"HTTP {r.status_code}: {r.text[:300]}")
    images = r.json().get("images") or []
    if not images:
        raise ImageError("سرور تصویری برنگرداند")
    return base64.b64decode(images[0].split(",", 1)[-1])


def _openai(prompt: str) -> bytes:
    body: dict[str, Any] = {"prompt": prompt, "n": 1, "size": f"{settings.image_width}x{settings.image_height}",
                            "response_format": "b64_json"}
    if settings.image_model:
        body["model"] = settings.image_model
    r = httpx.post(f"{_base()}/images/generations", json=body, headers=_headers(), timeout=settings.image_timeout_sec)
    if r.status_code >= 400:
        raise ImageError(f"HTTP {r.status_code}: {r.text[:300]}")
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return httpx.get(item["url"], timeout=settings.image_timeout_sec, follow_redirects=True).content
    raise ImageError("سرور تصویری برنگرداند")


def _fill(node: Any, values: dict[str, Any]) -> Any:
    """Replace {{prompt}}, {{negative}}, {{seed}}, {{width}}, {{height}}, {{steps}} inside a workflow."""
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, values) for v in node]
    if isinstance(node, str):
        for key, val in values.items():
            token = "{{" + key + "}}"
            if node == token:
                return val  # whole-string placeholder keeps its type (numbers stay numbers)
            node = node.replace(token, str(val))
    return node


def _comfyui(prompt: str) -> bytes:
    if not settings.image_comfy_workflow.strip():
        raise ImageError("برای ComfyUI باید workflow (API Format) را در تنظیمات بگذاری")
    try:
        workflow = json.loads(settings.image_comfy_workflow)
    except json.JSONDecodeError as e:
        raise ImageError(f"workflow معتبر نیست: {e}") from None
    graph = _fill(workflow, {
        "prompt": prompt, "negative": settings.image_negative_prompt, "seed": secrets.randbelow(2**31),
        "width": settings.image_width, "height": settings.image_height, "steps": settings.image_steps,
    })
    base, h = _base(), _headers()
    r = httpx.post(f"{base}/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex}, headers=h, timeout=30)
    if r.status_code >= 400:
        raise ImageError(f"HTTP {r.status_code}: {r.text[:300]}")
    prompt_id = r.json()["prompt_id"]
    deadline = time.monotonic() + settings.image_timeout_sec
    while time.monotonic() < deadline:
        hist = httpx.get(f"{base}/history/{prompt_id}", headers=h, timeout=30).json().get(prompt_id)
        if hist:
            for out in hist.get("outputs", {}).values():
                for img in out.get("images", []):
                    v = httpx.get(f"{base}/view", headers=h, timeout=60, params={
                        "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output")})
                    v.raise_for_status()
                    return v.content
            raise ImageError("workflow اجرا شد ولی تصویری خروجی نداد (نود Save Image دارد؟)")
        time.sleep(2)
    raise ImageError("زمان تولید تصویر تمام شد")


PROVIDERS = {"a1111": _a1111, "openai": _openai, "comfyui": _comfyui}


def generate_bytes(prompt: str) -> bytes:
    if not enabled():
        raise ImageError("تولید تصویر در تنظیمات خاموش است")
    full = ", ".join(x for x in (prompt, settings.image_style) if x)
    return PROVIDERS[settings.image_provider](full)


# ---------------------------------------------------------------- storage / format


def to_jpeg(data: bytes, max_side: int = 1440) -> bytes:
    """Instagram accepts only JPEG; also flattens transparency and limits size."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        img = bg
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def public_url_for(name: str, required: bool = True) -> str:
    if not settings.public_url:
        if not required:
            return f"/media/{name}"  # good enough for previews in the dashboard
        raise ImageError("در «تنظیمات» آدرس عمومی داشبورد را بگذار (مثل https://cm.nojanteb.ir) تا اینستاگرام بتواند تصویر را دانلود کند")
    return f"{settings.public_url.rstrip('/')}/media/{name}"


def store_jpeg(data: bytes, prefix: str = "gen") -> str:
    """Save as JPEG under data/uploads; returns the file name."""
    name = f"{prefix}-{secrets.token_hex(10)}.jpg"
    (settings.uploads_path / name).write_bytes(to_jpeg(data))
    return name


def jpeg_url(url: str) -> str:
    """Make sure an image we host is a JPEG (convert PNG/WEBP uploads once); external URLs pass through."""
    if "/media/" not in url or url.lower().endswith((".jpg", ".jpeg")):
        return url
    src = settings.uploads_path / Path(url.rsplit("/media/", 1)[1]).name
    if not src.exists():
        return url
    dst = src.with_suffix(".jpg")
    if not dst.exists():
        dst.write_bytes(to_jpeg(src.read_bytes()))
    return url.rsplit("/", 1)[0] + "/" + dst.name


# ---------------------------------------------------------------- prompt writing

PROMPT_SYSTEM = """You write prompts for a text-to-image model (Stable Diffusion / Flux).
Describe one concrete, photogenic scene that fits the post. English only, comma-separated phrases,
under 60 words. No text, letters, logos or watermarks in the image.
Output ONLY a JSON object: {"prompt": "..."}"""

PROMPT_USER = """IMAGE PROMPT
Business niche: {niche}
About the business: {description}
Social post (may be Persian): {text}"""


def write_prompt(llm: LLM, niche: str, description: str, text: str) -> str:
    data = parse_json(llm.complete(PROMPT_SYSTEM, PROMPT_USER.format(
        niche=niche or "-", description=description or "-", text=text[:800]), json_mode=True))
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise ImageError("هوش مصنوعی توضیح تصویر ننوشت")
    return prompt


def generate_for_post(llm: LLM, niche: str, description: str, text: str, *,
                      public: bool = True) -> tuple[str, str]:
    """Write a prompt, render it on the image server, store as JPEG. Returns (url, prompt).

    public=True requires the dashboard's public URL (Instagram must be able to download the file)."""
    if public:
        public_url_for("check")  # fail fast, before spending GPU time
    prompt = write_prompt(llm, niche, description, text)
    name = store_jpeg(generate_bytes(prompt))
    return public_url_for(name, required=public), prompt
