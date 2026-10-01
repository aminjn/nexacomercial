"""Content generation: SEO articles carrying contextual backlinks, and social posts."""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import markdown as md

from .models import Site
from .llm import LLM, parse_json

log = logging.getLogger(__name__)

LANG_NAMES = {"fa": "Persian (Farsi)", "en": "English", "ar": "Arabic", "tr": "Turkish", "de": "German"}


@dataclass
class Article:
    title: str
    body_markdown: str
    excerpt: str
    tags: list[str]
    link_url: str
    anchor: str
    site_id: int

    @property
    def body_html(self) -> str:
        return md.markdown(self.body_markdown, extensions=["extra"])


@dataclass
class SocialPost:
    text: str
    link_url: str
    hashtags: list[str] = field(default_factory=list)
    title: str = ""
    image_url: str = ""
    site_id: int = 0

    def render(self, max_len: int | None = None, with_hashtags: bool = True) -> str:
        parts = [self.text.strip()]
        if with_hashtags and self.hashtags:
            parts.append(" ".join("#" + h.replace(" ", "_").lstrip("#") for h in self.hashtags))
        parts.append(self.link_url)
        out = "\n\n".join(parts)
        if max_len and len(out) > max_len:
            room = max_len - len(self.link_url) - 3
            out = self.text.strip()[: max(room, 0)].rstrip() + "…\n\n" + self.link_url
        return out


# ---------------------------------------------------------------- choosing what to link


def pick_link(site: Site) -> tuple[str, list[str], list[str]]:
    """Return (url, keywords, anchors) — the home page or one of the deep pages."""
    candidates: list[tuple[str, list[str], list[str]]] = [(site.url, site.keywords or [], site.anchors or [])]
    for p in site.pages or []:
        if isinstance(p, str):
            p = {"url": p}
        if p.get("url"):
            candidates.append((p["url"], p.get("keywords") or site.keywords or [], p.get("anchors") or []))
    url, kws, anchors = random.choice(candidates)
    return url, kws, anchors


def pick_anchor(site: Site, url: str, keywords: list[str], anchors: list[str]) -> str:
    """Natural anchor mix: brand / keyword / naked URL / generic — avoids over-optimised profiles."""
    domain = urlparse(url).netloc.replace("www.", "")
    generic_fa = ["اینجا", "این سایت", "منبع", "بیشتر بخوانید"]
    generic_en = ["here", "this site", "source", "read more"]
    generic = generic_fa if site.language == "fa" else generic_en
    pool: list[tuple[str, float]] = [(site.name, 0.35), (domain, 0.15), (random.choice(generic), 0.15)]
    if anchors:
        pool.append((random.choice(anchors), 0.25))
    if keywords:
        pool.append((random.choice(keywords), 0.25))
    texts, weights = zip(*pool)
    return random.choices(texts, weights=weights, k=1)[0]


# ---------------------------------------------------------------- prompts

ARTICLE_SYSTEM = """You are a senior content writer and SEO specialist.
Write genuinely useful, original, well-structured articles that a real reader would enjoy.
Never sound like an advertisement. Never mention that the text is AI-generated.
Output ONLY a JSON object with keys: title, excerpt, tags (array of 3-6 short strings), body_markdown."""

ARTICLE_USER = """Language: {lang}
Topic niche: {niche}
Site being promoted: {site_name} ({site_url})
About the site: {description}
Target keywords (use naturally, do not stuff): {keywords}
Avoid these already-written titles: {recent}
{style}

Write an article of 600–900 words in Markdown (use ## headings, short paragraphs, a list where natural).
Exactly once, in a natural sentence somewhere in the middle of the article, include this exact Markdown link:
[{anchor}]({link_url})
Do not add any other links. Do not wrap the JSON in code fences."""

SOCIAL_SYSTEM = """You are a social media manager. Write short, engaging, non-spammy posts.
Output ONLY a JSON object with keys: text (the post, without hashtags and without the link), hashtags (array of 3-5 words, no # sign)."""

SOCIAL_USER = """SOCIAL POST
Language: {lang}
Platform: {platform}
Site: {site_name} ({site_url})
About: {description}
Keywords: {keywords}
Link that will be appended after the text: {link_url}
Topic angle: {angle}
{style}
Maximum {max_chars} characters for the text. Vary the style; do not start with the site name."""

ANGLES_FA = ["یک نکته کاربردی", "یک سؤال از مخاطب", "یک آمار یا واقعیت جالب", "یک اشتباه رایج", "معرفی کوتاه", "پیشنهاد امروز"]
ANGLES_EN = ["a practical tip", "a question to the audience", "an interesting fact", "a common mistake", "a short intro", "today's pick"]


def _style(site: Site, extra: str) -> str:
    return "\n".join(x for x in (site.style, extra) if x)


def _lang(site: Site) -> str:
    return LANG_NAMES.get(site.language, site.language)


def generate_article(llm: LLM, site: Site, recent_titles: list[str], extra: str = "") -> Article:
    link_url, keywords, anchors = pick_link(site)
    anchor = pick_anchor(site, link_url, keywords, anchors)
    prompt = ARTICLE_USER.format(
        lang=_lang(site),
        niche=site.niche or site.name,
        site_name=site.name,
        site_url=site.url,
        description=site.description or "-",
        keywords=", ".join(keywords) or "-",
        recent="; ".join(recent_titles[-20:]) or "-",
        style=_style(site, extra),
        anchor=anchor,
        link_url=link_url,
    )
    data = parse_json(llm.complete(ARTICLE_SYSTEM, prompt, json_mode=True))
    body = str(data.get("body_markdown", "")).strip()
    body = ensure_link(body, anchor, link_url)
    tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()][:6]
    return Article(
        title=str(data.get("title", "")).strip() or anchor,
        body_markdown=body,
        excerpt=str(data.get("excerpt", "")).strip(),
        tags=tags or keywords[:4],
        link_url=link_url,
        anchor=anchor,
        site_id=site.id,
    )


def ensure_link(body_markdown: str, anchor: str, url: str) -> str:
    """Guarantee exactly one backlink is present even if the model forgot or hallucinated a URL."""
    link = f"[{anchor}]({url})"
    if link in body_markdown:
        return body_markdown
    # Model may have used a different anchor with the right URL — accept that.
    if re.search(r"\[[^\]]+\]\(" + re.escape(url) + r"\)", body_markdown):
        return body_markdown
    # Otherwise append a natural closing sentence with the link.
    paragraphs = [p for p in body_markdown.split("\n\n") if p.strip()]
    insert_at = max(1, len(paragraphs) // 2)
    sentence = (f"برای اطلاعات بیشتر می‌توانید به {link} مراجعه کنید." if _is_rtl(body_markdown)
                else f"You can find more details at {link}.")
    paragraphs.insert(insert_at, sentence)
    return "\n\n".join(paragraphs)


def _is_rtl(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


def generate_social(llm: LLM, site: Site, platform: str, max_chars: int = 240, extra: str = "") -> SocialPost:
    link_url, keywords, _ = pick_link(site)
    angles = ANGLES_FA if site.language == "fa" else ANGLES_EN
    prompt = SOCIAL_USER.format(
        lang=_lang(site),
        platform=platform,
        site_name=site.name,
        site_url=site.url,
        description=site.description or "-",
        keywords=", ".join(keywords) or "-",
        link_url=link_url,
        angle=random.choice(angles),
        style=_style(site, extra),
        max_chars=max_chars,
    )
    data = parse_json(llm.complete(SOCIAL_SYSTEM, prompt, json_mode=True))
    hashtags = [str(h).strip().lstrip("#") for h in data.get("hashtags", []) if str(h).strip()][:5]
    return SocialPost(
        text=str(data.get("text", "")).strip()[:max_chars],
        link_url=link_url,
        hashtags=hashtags,
        title=site.name,
        image_url=site.image_url,
        site_id=site.id,
    )
