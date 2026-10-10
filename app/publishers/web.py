"""Publishing as a logged-in user through the headless browser ("<platform>_web" kinds).

No developer app or API token: the account's saved browser session (see app/browser.py) is opened
and the post is written the way a person would. Sites change their pages from time to time; when a
recipe breaks, the error says which step failed and a screenshot is kept in data/sessions/.
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

import httpx

from .. import browser
from ..config import settings
from ..content import Article, SocialPost
from . import register
from .base import Publisher, PublishError, PublishResult

EXPIRED = "نشست این اکانت منقضی شده یا از آن خارج شده‌ای؛ در صفحه‌ی اکانت‌ها دوباره «ورود با مرورگر» را بزن"


class WebPublisher(Publisher):
    """Base: subclasses set `login_url` and implement `post(page, ...)` / `article(page, ...)`."""

    login_url = ""
    # substrings of the address bar that mean "you are on the login page" (= session expired)
    login_markers: tuple[str, ...] = ("/login", "/signin", "/accounts/login", "/i/flow/login")

    @property
    def account_id(self) -> int:
        aid = self.o.get("_account_id")
        if not aid:
            raise PublishError("account id missing")
        return int(aid)

    def run(self, recipe: Callable[[Any], PublishResult]) -> PublishResult:
        try:
            return browser.run_with_session(self.account_id, recipe)
        except browser.BrowserError as e:
            raise PublishError(str(e)) from e
        except PublishError:
            raise
        except Exception as e:  # noqa: BLE001 — playwright timeouts etc.: say which page it was on
            raise PublishError(f"مرورگر: {type(e).__name__}: {str(e).splitlines()[0][:300]} "
                               "(عکس صفحه: در صفحه‌ی اکانت‌ها «عکس صفحه در لحظه‌ی آخرین خطا»)") from e

    def goto(self, page: Any, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2500)
        if any(m in page.url for m in self.login_markers):
            raise PublishError(EXPIRED)

    @staticmethod
    def first(page: Any, *selectors: str, timeout: float = 30_000, state: str = "visible") -> Any:
        """The first of several alternative selectors that shows up (sites ship several layouts)."""
        loc = page.locator(", ".join(selectors)).first
        try:
            loc.wait_for(state=state, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise PublishError(f"این بخش صفحه پیدا نشد: {selectors[0]} — شاید ظاهر سایت عوض شده") from e
        return loc

    @staticmethod
    def button(page: Any, name: str | re.Pattern, scope: Any = None, timeout: float = 30_000) -> Any:
        loc = (scope or page).get_by_role("button", name=name).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise PublishError(f"دکمه‌ی «{getattr(name, 'pattern', name)}» پیدا نشد — شاید ظاهر سایت عوض شده") from e
        return loc

    @staticmethod
    def type_into(page: Any, loc: Any, text: str) -> None:
        loc.click()
        page.keyboard.insert_text(text)

    @staticmethod
    def paste_html(page: Any, html: str) -> None:
        """Rich editors (Medium, Tumblr) keep links and headings when content arrives as a paste."""
        page.evaluate("""(html) => {
            const dt = new DataTransfer();
            dt.setData('text/html', html);
            dt.setData('text/plain', html.replace(/<[^>]+>/g, ''));
            document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
        }""", html)

    @staticmethod
    def square_image(page: Any, path: str, background: str = "#ffffff") -> str:
        """Centre the image on a square canvas (white behind transparent parts), so sites that crop
        to a square (Instagram) show all of it. Done in a blank tab of the same browser."""
        data = base64.b64encode(Path(path).read_bytes()).decode()
        tab = page.context.new_page()
        try:
            out = tab.evaluate("""async ([src, bg]) => {
                const img = new Image(); img.src = src; await img.decode();
                const side = Math.max(img.width, img.height), pad = Math.round(side * 0.06), size = side + 2 * pad;
                const c = document.createElement('canvas'); c.width = c.height = size;
                const g = c.getContext('2d'); g.fillStyle = bg; g.fillRect(0, 0, size, size);
                g.drawImage(img, (size - img.width) / 2, (size - img.height) / 2);
                return c.toDataURL('image/jpeg', 0.92).split(',')[1];
            }""", [f"data:image/*;base64,{data}", background])
        except Exception:  # noqa: BLE001 — unreadable image: upload it as it is
            return path
        finally:
            tab.close()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.write(base64.b64decode(out))
        tmp.close()
        return tmp.name

    @staticmethod
    def image_file(url: str) -> str:
        """Local path of the post image (our own uploads are read from disk)."""
        if Path(url).is_file():
            return url
        if "/media/" in url:
            f = settings.uploads_path / Path(url.rsplit("/media/", 1)[1]).name
            if f.exists():
                return str(f)
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(url.split("?")[0]).suffix or ".jpg")
        tmp.write(r.content)
        tmp.close()
        return tmp.name


# ---------------------------------------------------------------- X (Twitter)


@register
class XWeb(WebPublisher):
    kind = "x_web"
    category = "social"
    max_chars = 280
    login_url = "https://x.com/i/flow/login"

    def publish_social(self, post: SocialPost) -> PublishResult:
        text = post.render(self.max_chars - 23 + len(post.link_url))

        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://x.com/compose/post")
            box = self.first(page, '[data-testid="tweetTextarea_0"]')
            self.type_into(page, box, text)
            if post.image_url:
                page.locator('input[data-testid="fileInput"]').first.set_input_files(self.image_file(post.image_url))
                self.first(page, '[data-testid="attachments"]', timeout=60_000)
            btn = self.first(page, '[data-testid="tweetButton"]:not([aria-disabled="true"])')
            btn.click()
            page.locator('[data-testid="tweetTextarea_0"]').first.wait_for(state="detached", timeout=60_000)
            link = page.locator('[data-testid="toast"] a[href*="/status/"]').first
            try:
                href = link.get_attribute("href", timeout=8_000) or ""
            except Exception:  # noqa: BLE001
                href = ""
            return PublishResult(url=("https://x.com" + href) if href.startswith("/") else href)

        return self.run(recipe)


# ---------------------------------------------------------------- LinkedIn


@register
class LinkedInWeb(WebPublisher):
    kind = "linkedin_web"
    category = "social"
    max_chars = 2900
    login_url = "https://www.linkedin.com/login"
    login_markers = ("/login", "/authwall", "/checkpoint", "/uas/")

    def publish_social(self, post: SocialPost) -> PublishResult:
        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://www.linkedin.com/feed/?shareActive=true")
            editor = self.first(page, 'div.ql-editor[contenteditable="true"]',
                                '[role="dialog"] [role="textbox"][contenteditable="true"]')
            self.type_into(page, editor, post.render(self.max_chars))
            page.wait_for_timeout(1500)  # link preview
            btn = self.first(page, "button.share-actions__primary-action:not([disabled])")
            btn.click()
            editor.wait_for(state="detached", timeout=60_000)
            return PublishResult(url="https://www.linkedin.com/in/me/recent-activity/all/")

        return self.run(recipe)


# ---------------------------------------------------------------- Facebook (profile, or a page you manage)


@register
class FacebookWeb(WebPublisher):
    kind = "facebook_web"
    category = "social"
    max_chars = 5000
    login_url = "https://www.facebook.com/login"
    login_markers = ("/login", "/checkpoint")

    def publish_social(self, post: SocialPost) -> PublishResult:
        target = self.opt("page_url") or "https://www.facebook.com/"

        def recipe(page: Any) -> PublishResult:
            self.goto(page, target)
            opener = page.get_by_role("button", name=re.compile(
                r"What's on your mind|Write something|Create a post|چه چیزی در ذهن", re.I)).first
            try:
                opener.click(timeout=20_000)
            except Exception:  # noqa: BLE001
                page.get_by_text(re.compile(r"What's on your mind|Write something", re.I)).first.click(timeout=20_000)
            dialog = self.first(page, 'div[role="dialog"]:has([role="textbox"][contenteditable="true"])')
            self.type_into(page, dialog.locator('[role="textbox"][contenteditable="true"]').first,
                           post.render(self.max_chars))
            if post.image_url:
                dialog.locator('input[type="file"][accept*="image"]').first.set_input_files(self.image_file(post.image_url))
                page.wait_for_timeout(4000)
            nxt = dialog.get_by_role("button", name=re.compile(r"^(Next|بعدی)$")).first
            if nxt.is_visible():
                nxt.click()
                page.wait_for_timeout(1500)
            self.button(page, re.compile(r"^(Post|ارسال|انتشار)$"), scope=page.locator('div[role="dialog"]').last).click()
            page.locator('div[role="dialog"]:has([role="textbox"][contenteditable="true"])').first.wait_for(
                state="detached", timeout=90_000)
            return PublishResult(url=target)

        return self.run(recipe)


# ---------------------------------------------------------------- Instagram


@register
class InstagramWeb(WebPublisher):
    kind = "instagram_web"
    category = "social"
    max_chars = 2000
    needs_image = True
    login_url = "https://www.instagram.com/accounts/login/"
    login_markers = ("/accounts/login", "/challenge")

    def publish_social(self, post: SocialPost) -> PublishResult:
        image = post.image_url or self.opt("image_url")
        if not image:
            raise PublishError("اینستاگرام تصویر می‌خواهد؛ برای سایت تصویر انتخاب کن")

        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://www.instagram.com/")
            self._dismiss(page)
            # the "create" entry in the side menu; labels depend on the account's language
            create = page.locator(", ".join(f'svg[aria-label="{x}"]' for x in IG_CREATE)).first
            try:
                create.wait_for(state="visible", timeout=20_000)
                create.click()
                page.wait_for_timeout(1500)
                sub = page.locator(", ".join(f'svg[aria-label="{x}"]' for x in IG_POST)).first
                if sub.is_visible():
                    sub.click()
            except Exception:  # noqa: BLE001 — menu not found: open the create dialog by its address
                self.goto(page, "https://www.instagram.com/create/select/")
            self._dismiss(page)
            self.first(page, 'input[type="file"]', state="attached").set_input_files(
                self.square_image(page, self.image_file(image)))
            for _ in range(2):  # crop → filters → caption
                self.button(page, re.compile(r"^(Next|بعدی)$"), timeout=60_000).click()
                page.wait_for_timeout(1500)
            caption = self.first(page, 'div[aria-label="Write a caption..."][contenteditable="true"]',
                                 'div[role="dialog"] [contenteditable="true"]')
            self.type_into(page, caption, post.render(self.max_chars))
            self.button(page, re.compile(r"^(Share|اشتراک‌گذاری|اشتراک گذاری|هم‌رسانی)$")).click()
            self._wait_shared(page)
            return PublishResult(url=self._latest_post(page) or "https://www.instagram.com/")

        return self.run(recipe)

    @staticmethod
    def _wait_shared(page: Any, timeout_sec: int = 180) -> None:
        """Only Instagram's own "post shared" message counts as success; its error text is reported."""
        done = page.locator(f"text=/{IG_SHARED}/i").first
        fail = page.locator(f"text=/{IG_FAILED}/i").first
        for _ in range(timeout_sec):
            if done.is_visible():
                return
            if fail.is_visible():
                raise PublishError("اینستاگرام پست را منتشر نکرد: " + fail.inner_text()[:200])
            page.wait_for_timeout(1000)
        raise PublishError("اینستاگرام پیام «پست منتشر شد» را نشان نداد؛ احتمالاً منتشر نشده — عکس صفحه را ببین")

    def _latest_post(self, page: Any) -> str:
        """Address of the newest post on the profile (needs the account's username in the "handle" field)."""
        handle = str(self.opt("handle") or "").strip().lstrip("@")
        if not handle:
            return ""
        try:
            page.goto(f"https://www.instagram.com/{handle}/", wait_until="domcontentloaded", timeout=60_000)
            href = page.locator('a[href*="/p/"], a[href*="/reel/"]').first.get_attribute("href", timeout=20_000) or ""
        except Exception:  # noqa: BLE001
            return ""
        return "https://www.instagram.com" + href if href.startswith("/") else href

    @staticmethod
    def _dismiss(page: Any) -> None:
        """Close "Turn on notifications?" / "Save login info?" popups."""
        for _ in range(2):
            not_now = page.get_by_role("button", name=re.compile(r"^(Not [Nn]ow|اکنون نه|الان نه|بعداً)$")).first
            try:
                if not_now.is_visible():
                    not_now.click()
                    page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                return


IG_CREATE = ("New post", "Create", "پست جدید", "ایجاد", "ساختن")
IG_SHARED = r"post has been shared|Post shared|reel has been shared|Reel shared|به اشتراک گذاشته شد|هم‌رسانی شد|منتشر شد"
IG_FAILED = (r"couldn't be shared|could not be shared|Something went wrong|Try again later|"
             r"We restrict certain activity|مشکلی پیش آمد|منتشر نشد|بعداً دوباره امتحان")
IG_POST = ("Post", "پست")


# ---------------------------------------------------------------- Threads


@register
class ThreadsWeb(WebPublisher):
    kind = "threads_web"
    category = "social"
    max_chars = 500
    login_url = "https://www.threads.net/login"

    def publish_social(self, post: SocialPost) -> PublishResult:
        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://www.threads.net/intent/post?" + urlencode({"text": post.render(self.max_chars)}))
            dialog = self.first(page, 'div[role="dialog"]')
            self.button(page, re.compile(r"^Post$"), scope=dialog).click()
            dialog.wait_for(state="detached", timeout=60_000)
            return PublishResult(url="https://www.threads.net/")

        return self.run(recipe)


# ---------------------------------------------------------------- Pinterest


@register
class PinterestWeb(WebPublisher):
    kind = "pinterest_web"
    category = "social"
    max_chars = 500
    needs_image = True
    login_url = "https://www.pinterest.com/login/"

    def publish_social(self, post: SocialPost) -> PublishResult:
        image = post.image_url or self.opt("image_url")
        board = self.opt("board", required=True)

        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://www.pinterest.com/pin/create/button/?" + urlencode(
                {"url": post.link_url, "media": image, "description": post.render(self.max_chars)}))
            row = self.first(page, f'[data-test-id="board-row-{board}"]', f'div[title="{board}"]',
                             f'text="{board}"', timeout=60_000)
            row.hover()
            save = row.locator("xpath=ancestor-or-self::*[.//button][1]").get_by_role("button", name="Save").first
            (save if save.is_visible() else row).click()
            self.first(page, "text=/Saved to|Saved!/", timeout=60_000)
            return PublishResult(url="https://www.pinterest.com/")

        return self.run(recipe)


# ---------------------------------------------------------------- Reddit (old.reddit.com: plain, stable form)


@register
class RedditWeb(WebPublisher):
    kind = "reddit_web"
    category = "social"
    max_chars = 300
    login_url = "https://old.reddit.com/login"

    def publish_social(self, post: SocialPost) -> PublishResult:
        sub = self.opt("subreddit", required=True).removeprefix("r/").strip("/")
        title = (post.title or post.text)[:290]

        def recipe(page: Any) -> PublishResult:
            self.goto(page, f"https://old.reddit.com/r/{sub}/submit?" + urlencode(
                {"url": post.link_url, "title": title, "resubmit": "true"}))
            if page.locator("form#login_login-main, #login-form").first.is_visible():
                raise PublishError(EXPIRED)
            self.first(page, 'form#newlink button[name="submit"]', 'form#newlink button[type="submit"]').click()
            try:
                page.wait_for_url(re.compile(r"/comments/"), timeout=45_000)
            except Exception as e:  # noqa: BLE001
                err = page.locator("form#newlink .error:visible").all_inner_texts()
                raise PublishError("ردیت پست را قبول نکرد: " + (" | ".join(x for x in err if x.strip()) or "بدون پیام")) from e
            return PublishResult(url=page.url)

        return self.run(recipe)


# ---------------------------------------------------------------- Mastodon (token read from the logged-in web app)


@register
class MastodonWeb(WebPublisher):
    kind = "mastodon_web"
    category = "social"
    max_chars = 480
    login_markers = ("/auth/sign_in",)

    @property
    def instance(self) -> str:
        base = self.opt("instance", "https://mastodon.social").rstrip("/")
        return base if "://" in base else "https://" + base

    @property
    def login_url(self) -> str:  # type: ignore[override]
        return self.instance + "/auth/sign_in"

    def publish_social(self, post: SocialPost) -> PublishResult:
        def recipe(page: Any) -> PublishResult:
            self.goto(page, self.instance + "/home")
            raw = page.locator("script#initial-state").first.text_content(timeout=20_000) or "{}"
            token = json.loads(raw).get("meta", {}).get("access_token")
            if not token:
                raise PublishError(EXPIRED)
            return token

        token = self.run(recipe)
        with self.http(headers={"Authorization": f"Bearer {token}"}) as c:
            d = self.check(c.post(f"{self.instance}/api/v1/statuses", json={"status": post.render(self.max_chars)}))
        return PublishResult(url=d.get("url", ""), external_id=str(d.get("id", "")), raw=d)


# ---------------------------------------------------------------- Telegram (web.telegram.org, as your own user)


@register
class TelegramWeb(WebPublisher):
    kind = "telegram_web"
    category = "social"
    max_chars = 3500
    login_url = "https://web.telegram.org/k/"
    login_markers = ()

    def publish_social(self, post: SocialPost) -> PublishResult:
        name = self.opt("channel", required=True).strip().removeprefix("https://t.me/").removeprefix("@").strip("/")

        def recipe(page: Any) -> PublishResult:
            self.goto(page, f"https://web.telegram.org/k/#@{name}")
            if page.locator(".auth-pages, #auth-pages, .page-sign").first.is_visible():
                raise PublishError(EXPIRED)
            box = self.first(page, '.input-message-input[contenteditable="true"]', timeout=60_000)
            self.type_into(page, box, post.render(self.max_chars))
            page.keyboard.press("Enter")
            page.wait_for_timeout(4000)
            return PublishResult(url=f"https://t.me/{name}")

        return self.run(recipe)


# ---------------------------------------------------------------- articles: Medium, Tumblr, dev.to


@register
class MediumWeb(WebPublisher):
    kind = "medium_web"
    category = "article"
    login_url = "https://medium.com/m/signin"
    login_markers = ("/m/signin", "/signin")

    def publish_article(self, article: Article) -> PublishResult:
        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://medium.com/new-story")
            title = self.first(page, '[data-testid="editorTitleParagraph"]', 'h3.graf--title', 'section [contenteditable="true"] h3')
            self.type_into(page, title, article.title)
            page.keyboard.press("Enter")
            self.paste_html(page, article.body_html)
            page.wait_for_timeout(3000)
            self.button(page, re.compile(r"^Publish$")).click()
            self.first(page, '[data-testid="publishConfirmButton"]', 'button:has-text("Publish now")').click()
            page.wait_for_url(lambda u: "new-story" not in u and "/edit" not in u, timeout=90_000)
            return PublishResult(url=page.url.split("?")[0])

        return self.run(recipe)


@register
class TumblrWeb(WebPublisher):
    kind = "tumblr_web"
    category = "article"
    login_url = "https://www.tumblr.com/login"

    def publish_article(self, article: Article) -> PublishResult:
        blog = (self.opt("blog") or "").strip()

        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://www.tumblr.com/new/text" + (f"?blog={quote(blog)}" if blog else ""))
            title = self.first(page, '[aria-label="Title"]', '[data-placeholder="Title"]', 'h1[contenteditable="true"]')
            self.type_into(page, title, article.title)
            page.keyboard.press("Enter")
            self.paste_html(page, article.body_html)
            page.wait_for_timeout(2000)
            self.button(page, re.compile(r"^(Post now|Post)$")).click()
            page.wait_for_url(lambda u: "/new/" not in u, timeout=60_000)
            return PublishResult(url=f"https://www.tumblr.com/{blog}" if blog else "https://www.tumblr.com/")

        return self.run(recipe)


@register
class DevToWeb(WebPublisher):
    kind = "devto_web"
    category = "article"
    login_url = "https://dev.to/enter"
    login_markers = ("/enter",)

    def publish_article(self, article: Article) -> PublishResult:
        def recipe(page: Any) -> PublishResult:
            self.goto(page, "https://dev.to/new")
            self.first(page, "#article-form-title").fill(article.title)
            self.first(page, "#article_body_markdown").fill(article.body_markdown)
            self.button(page, re.compile(r"^Publish$")).click()
            page.wait_for_url(lambda u: not u.rstrip("/").endswith("/new") and "/edit" not in u, timeout=60_000)
            return PublishResult(url=page.url)

        return self.run(recipe)
