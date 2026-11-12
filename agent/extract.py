"""Lấy nội dung bài báo (tiêu đề, nội dung, ảnh) từ trang HTML."""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import feeds, util

IGNORE_TAGS = {"script", "style", "nav", "footer", "aside", "header", "form",
               "noscript", "svg", "iframe", "button", "select"}
VOID_TAGS = {"img", "meta", "link", "br", "hr", "input", "source", "area", "base", "col",
             "embed", "param", "track", "wbr"}
VIDEO_EXT = re.compile(r"\.(mp4|webm|mov|m3u8)(\?|#|$)", re.I)
BAD_IMG = re.compile(
    r"(logo|icon|avatar|sprite|pixel|blank|placeholder|banner|/ads?/|adserver|tracking|"
    r"\.svg(\?|$)|\.gif(\?|$)|^data:)", re.I)


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.meta_multi = {}
        self.canonical = ""
        self.title_tag = ""
        self.paras = []          # (text, trong_article)
        self.imgs = []           # (url, trong_article)
        self.videos = []         # link file video trực tiếp (mp4/webm/mov/m3u8)
        self.jsonld = []
        self._ignore = 0
        self._article = 0
        self._in_title = False
        self._p = None
        self._ld = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            val = a.get("content")
            if key and val:
                self.meta.setdefault(key, val)
                self.meta_multi.setdefault(key, []).append(val)
            return
        if tag == "link":
            if "canonical" in (a.get("rel") or "").lower() and a.get("href"):
                self.canonical = a["href"]
            return
        if tag == "img":
            if not self._ignore:
                src = a.get("data-src") or a.get("data-original") or a.get("src")
                if not src and a.get("srcset"):
                    src = a["srcset"].split(",")[0].strip().split(" ")[0]
                if src:
                    self.imgs.append((src, self._article > 0))
            return
        if tag in ("video", "source") and not self._ignore:
            v = a.get("src") or a.get("data-src")
            if v and VIDEO_EXT.search(v):
                self.videos.append(v)
            if tag == "video" and a.get("poster"):
                self.imgs.append((a["poster"], self._article > 0))
        if tag in VOID_TAGS:
            return
        if tag == "script" and "ld+json" in (a.get("type") or "").lower():
            self._ld = []
        if tag in IGNORE_TAGS:
            self._ignore += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "article":
            self._article += 1
        if tag == "p" and not self._ignore:
            self._p = []

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        if tag in IGNORE_TAGS:
            self._ignore = max(0, self._ignore - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag == "article":
            self._article = max(0, self._article - 1)
        if tag == "p" and self._p is not None:
            t = re.sub(r"\s+", " ", "".join(self._p)).strip()
            if t:
                self.paras.append((t, self._article > 0))
            self._p = None

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._in_title:
            self.title_tag += data
        if self._p is not None and not self._ignore:
            self._p.append(data)


def _jsonld_objects(raw_list):
    objs = []
    for raw in raw_list:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "@graph" in o and isinstance(o["@graph"], list):
                    stack.extend(o["@graph"])
                objs.append(o)
            elif isinstance(o, list):
                stack.extend(o)
    return objs


def _image_urls_from(value):
    out = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        u = value.get("url") or value.get("contentUrl")
        if isinstance(u, str):
            out.append(u)
    elif isinstance(value, list):
        for v in value:
            out.extend(_image_urls_from(v))
    return out


def _video_urls_from(value):
    out = []
    if isinstance(value, dict):
        u = value.get("contentUrl")
        if isinstance(u, str):
            out.append(u)
    elif isinstance(value, list):
        for v in value:
            out.extend(_video_urls_from(v))
    return out


def _ts(s):
    return feeds._parse_date(s) if s else None


def parse_article(page_html, page_url):
    p = _Page()
    try:
        p.feed(page_html)
    except Exception:
        pass
    ld = _jsonld_objects(p.jsonld)
    news_ld = next((o for o in ld if str(o.get("@type", "")).endswith(("NewsArticle", "Article", "ReportageNewsArticle"))), {})

    title = (p.meta.get("og:title") or news_ld.get("headline") or p.title_tag or "").strip()
    title = html.unescape(title)
    site = (p.meta.get("og:site_name") or "").strip() or (urlparse(page_url).hostname or "").replace("www.", "")
    canonical = urljoin(page_url, p.canonical) if p.canonical else page_url
    published = _ts(p.meta.get("article:published_time") or news_ld.get("datePublished"))

    in_art = [t for t, a in p.paras if a and len(t) >= 30]
    body = in_art if sum(len(t) for t in in_art) >= 300 else [t for t, _ in p.paras if len(t) >= 40]
    seen, uniq = set(), []
    for t in body:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    text = "\n".join(uniq)[:12000]

    cand = []
    cand += p.meta_multi.get("og:image", []) + p.meta_multi.get("og:image:url", [])
    cand += p.meta_multi.get("twitter:image", [])
    cand += _image_urls_from(news_ld.get("image"))
    cand += [u for u, a in p.imgs if a]
    images, keys = [], set()
    for u in cand:
        u = urljoin(page_url, u.strip())
        if not util.is_safe_url(u) or BAD_IMG.search(u):
            continue
        k = u.split("?")[0]
        if k in keys:
            continue
        keys.add(k)
        images.append(u)

    vcand = []
    for k in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
        vcand += p.meta_multi.get(k, [])
    vcand += _video_urls_from(news_ld.get("video"))
    vcand += [u for o in ld if o.get("@type") == "VideoObject" for u in _video_urls_from(o)]
    vcand += p.videos
    videos, vkeys = [], set()
    for u in vcand:
        u = urljoin(page_url, u.strip())
        if not util.is_safe_url(u) or not VIDEO_EXT.search(u):   # chỉ nhận file video trực tiếp
            continue
        k = u.split("?")[0]
        if k not in vkeys:
            vkeys.add(k)
            videos.append(u)

    desc = p.meta.get("og:description") or p.meta.get("description") or news_ld.get("description") or ""
    return {"url": canonical, "title": title, "text": text, "images": images,
            "site": site, "published": published, "description": html.unescape(desc),
            "videos": videos}


def fetch_article(url):
    if not util.is_safe_url(url):
        raise ValueError("URL không an toàn")
    r = util.http(url, headers={"Accept": "text/html,application/xhtml+xml"})
    art = parse_article(r.text(), r.url or url)
    art["fetched_url"] = r.url or url
    return art


def download_images(urls, referer, max_images=4, min_bytes=8000, max_bytes=8_000_000):
    """Tải ảnh về. Trả list {url, mime, data}. Bỏ ảnh quá nhỏ / không phải ảnh thật."""
    out = []
    for u in urls:
        if len(out) >= max_images:
            break
        try:
            r = util.http(u, headers={"Referer": referer, "Accept": "image/*"},
                          retries=1, max_bytes=max_bytes)
        except util.HttpError:
            continue
        mime = util.sniff_image(r.body)
        if not mime or len(r.body) < min_bytes:
            continue
        out.append({"url": u, "mime": mime, "data": r.body})
    return out
