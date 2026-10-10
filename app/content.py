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
            # a page without its own anchor texts uses the site's ones
            candidates.append((p["url"], p.get("keywords") or site.keywords or [], p.get("anchors") or site.anchors or []))
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

# Persian prompts for Persian sites: small local models follow instructions in the target language much better
SOCIAL_SYSTEM_FA = """تو مدیر شبکه‌های اجتماعی یک کسب‌وکار ایرانی هستی و فارسی روان، طبیعی و بی‌غلط می‌نویسی.
پست‌هایت کوتاه، مشخص و مفید است؛ نه تبلیغ اغراق‌آمیز و نه جمله‌های کلی و تکراری.
فقط و فقط یک شیء JSON خروجی بده با کلیدهای text و hashtags."""

SOCIAL_USER_FA = """یک پست برای {platform} بنویس.
کسب‌وکار: {site_name} — {description}
موضوع این پست (فقط درباره‌ی همین بنویس): {focus}
زاویه‌ی پست: {angle}
{style}
قوانین:
- ۲ تا ۴ جمله‌ی کوتاه، روی‌هم حداکثر {max_chars} کاراکتر.
- یک پیام روشن: یک نکته یا مزیت مشخص برای مخاطب، و در آخر یک دعوت کوتاه به اقدام.
- داخل text هیچ لینک، آدرس سایت یا هشتگی ننویس؛ لینک خودکار بعد از متن اضافه می‌شود.
- هیچ جمله یا عبارتی را تکرار نکن. از جمله‌های کلی مثل «این سایت یک پلتفرم مناسب است» استفاده نکن.
- حداکثر ۲ ایموجی.
- hashtags: ۳ تا ۵ کلمه‌ی فارسی مرتبط، بدون #، کلمه‌های چندتایی را با _ به هم بچسبان.
نمونه‌ی قالب خروجی:
{{"text": "دنبال آپارتمان اجاره‌ای نزدیک مترو هستی؟ با فیلتر محله و بودجه، در چند دقیقه گزینه‌های مناسب را ببین و مستقیم با صاحب‌خانه تماس بگیر. همین امروز جست‌وجو را شروع کن 🏠", "hashtags": ["اجاره_آپارتمان", "ملکجت", "خانه"]}}"""

ARTICLE_SYSTEM_FA = """تو نویسنده‌ی حرفه‌ای محتوا و متخصص سئو هستی و فارسی روان و طبیعی می‌نویسی.
مقاله‌هایت واقعاً به خواننده کمک می‌کند، ساختار روشن دارد و شبیه آگهی نیست. هرگز نگو که متن را هوش مصنوعی نوشته.
فقط یک شیء JSON خروجی بده با کلیدهای title، excerpt، tags (آرایه‌ی ۳ تا ۶ کلمه) و body_markdown."""

ARTICLE_USER_FA = """موضوع کلی: {niche}
سایتی که معرفی می‌شود: {site_name} ({site_url}) — {description}
کلمات کلیدی (طبیعی به کار ببر، نه تکراری): {keywords}
این عنوان‌ها قبلاً نوشته شده، تکرارشان نکن: {recent}
{style}
یک مقاله‌ی ۶۰۰ تا ۹۰۰ کلمه‌ای به Markdown بنویس: تیترهای ##، پاراگراف‌های کوتاه، و جایی که طبیعی است یک فهرست.
دقیقاً یک بار، در یک جمله‌ی طبیعی در میانه‌ی مقاله، همین لینک Markdown را بگذار:
[{anchor}]({link_url})
هیچ لینک دیگری نگذار. پاراگراف‌ها و جمله‌ها را تکرار نکن. JSON را داخل ``` نگذار."""

ANGLES_FA = ["یک نکته کاربردی", "یک سؤال از مخاطب", "یک آمار یا واقعیت جالب", "یک اشتباه رایج", "معرفی کوتاه", "پیشنهاد امروز"]
ANGLES_EN = ["a practical tip", "a question to the audience", "an interesting fact", "a common mistake", "a short intro", "today's pick"]


def _style(site: Site, extra: str) -> str:
    return "\n".join(x for x in (site.style, extra) if x)


def _lang(site: Site) -> str:
    return LANG_NAMES.get(site.language, site.language)


def generate_article(llm: LLM, site: Site, recent_titles: list[str], extra: str = "") -> Article:
    link_url, keywords, anchors = pick_link(site)
    anchor = pick_anchor(site, link_url, keywords, anchors)
    persian = site.language == "fa"
    prompt = (ARTICLE_USER_FA if persian else ARTICLE_USER).format(
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
    data = parse_json(llm.complete(ARTICLE_SYSTEM_FA if persian else ARTICLE_SYSTEM, prompt, json_mode=True))
    body = clean_article(str(data.get("body_markdown", "")).strip(), link_url)
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


# ---------------------------------------------------------------- cleaning and scoring AI output

_URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(?:com|ir|net|org|io|co)(?:/\S*)?)", re.I)
_TAG_RE = re.compile(r"#([\w\u200c]+)")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_SENT_SPLIT = re.compile(r"(?<=[.!?؟…])\s+|\n+")


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s).lower()


def clean_social(text: str, max_chars: int) -> tuple[str, list[str]]:
    """Remove links the model wrote anyway, move inline #hashtags out, drop repeated sentences,
    keep at most 3 emoji and cut on a sentence boundary. Returns (text, hashtags found inline)."""
    tags = [t for t in _TAG_RE.findall(text)]
    text = _URL_RE.sub("", _TAG_RE.sub("", text))
    seen, sentences = set(), []
    for sent in _SENT_SPLIT.split(text):
        sent = re.sub(r"\s+", " ", sent).strip(" -–—:|،,")
        key = _norm(sent)
        if len(key) < 3 or key in seen or any(key in k or k in key for k in seen if len(k) > 25):
            continue  # empty, or the same sentence again (small models love repeating themselves)
        seen.add(key)
        sentences.append(sent)
    out, emoji = "", 0
    for sent in sentences:
        kept = []
        for ch in sent:
            if _EMOJI_RE.match(ch):
                emoji += 1
                if emoji > 3:
                    continue
            kept.append(ch)
        sent = "".join(kept).strip()
        if out and len(out) + 1 + len(sent) > max_chars:
            break
        out = f"{out} {sent}".strip()
    if len(out) > max_chars:  # one very long sentence: cut on a word
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip("،, ") + "…"
    return out, tags


def clean_article(body: str, keep_url: str) -> str:
    """Drop links to other sites (keep their text) and paragraphs that repeat an earlier one."""
    body = re.sub(r"\[([^\]]+)\]\((?!" + re.escape(keep_url) + r"\))[^)]*\)", r"\1", body)
    seen, paras = set(), []
    for para in body.split("\n\n"):
        key = _norm(para)
        if key and key in seen:
            continue
        seen.add(key)
        paras.append(para)
    return "\n\n".join(paras)


def score_text(text: str, persian: bool, max_chars: int) -> float:
    """Higher is better: right length, right script, varied words."""
    if not text:
        return -100
    score = 0.0
    letters = [c for c in text if c.isalpha()]
    if persian and letters:
        latin = sum(c.isascii() for c in letters) / len(letters)
        score -= latin * 40  # English / gibberish mixed into a Persian post
    words = [w for w in re.findall(r"\w+", text) if len(w) > 2]
    if words:
        score -= (1 - len(set(words)) / len(words)) * 30  # repeated words
    if len(text) < 60:
        score -= 15
    if len(text) > max_chars * 0.4:
        score += 5
    score -= text.count("این سایت") * 4 + text.count("پلتفرم") * 2
    return score


def _is_rtl(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


def generate_social(llm: LLM, site: Site, platform: str, max_chars: int = 240, extra: str = "",
                    candidates: int | None = None) -> SocialPost:
    """Write a few versions, clean each one (no links / inline hashtags / repeated sentences) and keep the best."""
    from .config import settings
    link_url, keywords, anchors = pick_link(site)
    persian = site.language == "fa"
    angles = ANGLES_FA if persian else ANGLES_EN
    focus = "، ".join((anchors or [])[:1] + (keywords or [])[:4]) or site.niche or site.name
    best: tuple[float, str, list[str]] | None = None
    for _ in range(max(1, candidates or settings.ai_candidates)):
        if persian:
            prompt = SOCIAL_USER_FA.format(platform=platform, site_name=site.name, description=site.description or "-",
                                           focus=focus, angle=random.choice(angles), style=_style(site, extra),
                                           max_chars=max_chars)
            system = SOCIAL_SYSTEM_FA
        else:
            prompt = SOCIAL_USER.format(lang=_lang(site), platform=platform, site_name=site.name, site_url=site.url,
                                        description=site.description or "-", keywords=", ".join(keywords) or "-",
                                        link_url=link_url, angle=random.choice(angles), style=_style(site, extra),
                                        max_chars=max_chars)
            system = SOCIAL_SYSTEM
        try:
            data = parse_json(llm.complete(system, prompt, json_mode=True))
        except ValueError:
            continue  # unreadable answer: try another version
        text, inline_tags = clean_social(str(data.get("text", "")), max_chars)
        tags = [str(h).strip().lstrip("#").replace(" ", "_") for h in data.get("hashtags", []) if str(h).strip()]
        tags = list(dict.fromkeys(tags + inline_tags))[:5]
        score = score_text(text, persian, max_chars)
        if best is None or score > best[0]:
            best = (score, text, tags)
    if best is None or not best[1]:
        raise ValueError("هوش مصنوعی متن قابل استفاده‌ای نداد")
    _, text, hashtags = best
    return SocialPost(
        text=text,
        link_url=link_url,
        hashtags=hashtags,
        title=site.name,
        image_url=site.image_url,
        site_id=site.id,
    )
