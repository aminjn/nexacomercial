import json

import httpx

from app import engine
from app import models as m
from app.content import clean_article, clean_social, generate_social, score_text
from app.publishers.base import Publisher
from tests.test_engine import _mock, add_site


def test_clean_social_removes_links_tags_and_repeats():
    raw = ("خرید آپارتمان سعادت آباد با ملکجت، سهولت و دقت بیشتر! 🏠✨ https://melkjet.com/search?%=kind "
           "خرید آپارتمان سعادت آباد با ملکجت، سهولت و دقت بیشتر! همین امروز melkjet.com را ببین. #ملک #اجاره_آپارتمان")
    text, tags = clean_social(raw, 240)
    assert "http" not in text and "melkjet.com" not in text and "#" not in text
    assert text.count("سهولت و دقت بیشتر") == 1  # the repeated sentence is gone
    assert tags == ["ملک", "اجاره_آپارتمان"]
    long, _ = clean_social("جمله‌ی اول کوتاه است. " + "کلمه " * 100, 80)
    assert len(long) <= 81


def test_clean_article_drops_foreign_links_and_repeated_paragraphs():
    body = "## تیتر\n\nپاراگراف [ملکجت](https://melkjet.com) خوب.\n\nببینید [ویکی](https://wiki.org/x).\n\n## تیتر"
    out = clean_article(body, "https://melkjet.com")
    assert "[ملکجت](https://melkjet.com)" in out and "wiki.org" not in out and "ببینید ویکی." in out
    assert out.count("## تیتر") == 1


def test_best_of_several_versions():
    class TwoAnswers:
        answers = [json.dumps({"text": "best best best best best best this site is platform platform", "hashtags": ["a"]}),
                   json.dumps({"text": "دنبال آپارتمان اجاره‌ای نزدیک مترو هستی؟ با فیلتر محله و بودجه گزینه‌های مناسب را "
                                       "ببین و مستقیم تماس بگیر. همین امروز شروع کن 🏠", "hashtags": ["اجاره", "#ملکجت"]})]

        def complete(self, system, user, json_mode=False):
            assert "داخل text هیچ لینک" in user  # the Persian prompt with the rules
            return self.answers.pop(0)
    site = m.Site(id=1, name="ملکجت", url="https://melkjet.com", language="fa", keywords=["اجاره آپارتمان"])
    post = generate_social(TwoAnswers(), site, "telegram", max_chars=240, candidates=2)
    assert post.text.startswith("دنبال آپارتمان") and post.hashtags == ["اجاره", "ملکجت"]
    assert score_text(post.text, True, 240) > score_text("best best best this site", True, 240)


def test_draft_edit_publish_and_delete(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9, "chat": {"username": "c"}}})
    monkeypatch.setattr(Publisher, "http", _mock(handler))

    class Now:  # run background work right away
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.t, self.a, self.k = target, args, kwargs or {}

        def start(self):
            self.t(*self.a, **self.k)
    import threading
    monkeypatch.setattr(threading, "Thread", Now)
    site = add_site()
    acc = m.Account(label="tg", kind="telegram", category="social")
    acc.creds = {"bot_token": "1:a", "chat_id": "@c"}
    with m.session() as s:
        s.add(acc)
        s.commit()
        s.refresh(acc)
    draft = engine.publish(site, acc, dry_run=True)
    assert draft.status == "dry_run" and draft.draft["text"] and not sent
    other = engine.publish(site, acc, dry_run=True)
    with TestClient(app) as client:
        page = client.get(f"/sites/{site.id}").text
        assert "پیش‌نویس" in page and f"/publications/{draft.id}/draft" in page
        assert "متن پست" in client.get(f"/publications/{draft.id}/draft").text
        r = client.post(f"/publications/{draft.id}/draft", data={"action": "save", "text": "متن ویرایش‌شده‌ی من",
                                                                 "hashtags": "ملکجت، اجاره"})
        assert "ذخیره شد" in r.text and "متن ویرایش‌شده‌ی من" in r.text
        r = client.post(f"/publications/{draft.id}/draft", data={"action": "publish", "text": "متن نهایی",
                                                                 "hashtags": "ملکجت"})
        assert "در حال انتشار" in r.text
        assert "حذف شد" in client.post(f"/publications/{other.id}/draft", data={"action": "delete"}).text
    assert sent[0].startswith("متن نهایی") and "#ملکجت" in sent[0]
    with m.session() as s:
        p = s.get(m.Publication, draft.id)
        assert p.status == "ok" and p.url == "https://t.me/c/9" and p.draft is None
        assert s.get(m.Publication, other.id) is None
        assert s.get(m.Account, acc.id).published_count == 1
