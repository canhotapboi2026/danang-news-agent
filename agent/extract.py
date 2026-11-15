"""Lấy nội dung bài báo (tiêu đề, nội dung, ảnh) từ trang HTML."""
import hashlib
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


DHASH_MAX_DIFF = 8     # số bit khác nhau tối đa (trên 64) để coi là cùng một ảnh


def _img_name_key(url):
    """Tên file ảnh đã bỏ đuôi và hậu tố kích thước. CDN báo thường giữ nguyên tên file cho mọi cỡ ảnh."""
    name = urlparse(url).path.rsplit("/", 1)[-1].lower()
    name = re.sub(r"\.(jpe?g|png|webp)$", "", name)
    name = re.sub(r"[-_]?\d{2,4}x\d{2,4}$", "", name)
    return name if len(name) >= 15 else None   # tên quá ngắn/chung chung thì không dùng để so


def _dhash(data):
    """Dấu vân tay hình ảnh 64 bit: ảnh cùng nội dung nhưng khác cỡ/độ nén cho dấu vân tay gần như giống nhau.
    Trả None nếu thiếu Pillow hoặc không đọc được ảnh (khi đó chỉ so bằng nội dung chính xác + tên file)."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        px = im.tobytes()
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
        return bits
    except Exception:
        return None


CROP_MAX_DIFF = 6      # số bit lệch tối đa (trên 48 bit so sánh) khi ảnh này là một phần cắt của ảnh kia
CROP_SCALES = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3)
CROP_GRID = 7


def _fingerprint(data):
    """Trả (dhash 64 bit, ảnh xám thu nhỏ để dò ảnh cắt). (None, None) nếu thiếu Pillow/không đọc được."""
    try:
        import io
        from PIL import Image, ImageStat
        th = Image.open(io.BytesIO(data)).convert("L")
        th.thumbnail((160, 160))
        flat = ImageStat.Stat(th).stddev[0] < 12          # ảnh gần như một màu: không đủ chi tiết để so khung cắt
        return _dhash_img(th), (None if flat else th)
    except Exception:
        return None, None


def _rows(im):
    """Dấu vân tay theo từng hàng (8 hàng x 8 bit) của ảnh xám thu về 9x8."""
    from PIL import Image
    px = im.resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    return [sum((px[r * 9 + c] > px[r * 9 + c + 1]) << (7 - c) for c in range(8)) for r in range(8)]


def _dhash_img(im):
    r = _rows(im)
    bits = 0
    for x in r:
        bits = (bits << 8) | x
    return bits


def _rows_dist(a, b):
    """Khoảng cách bit, bỏ qua 2 hàng trên hoặc 2 hàng dưới (băng rôn/logo của báo thường dán ở mép)."""
    d = [bin(x ^ y).count("1") for x, y in zip(a, b)]
    return min(sum(d[:6]), sum(d[2:]))


def _contains(big, small):
    """True nếu ảnh `small` là một khung cắt (có thể phóng to, có băng rôn ở mép) của ảnh `big`.
    Trượt cửa sổ cùng tỉ lệ khung với `small` qua `big` ở nhiều cỡ và vị trí, so dấu vân tay."""
    if big is None or small is None:
        return False
    wb, hb = big.size
    ws, hs = small.size
    if min(wb, hb, ws, hs) < 16:
        return False
    target = _rows(small)
    ar = ws / hs
    for f in CROP_SCALES:
        if ar > wb / hb:
            ww = wb * f
            wh = ww / ar
        else:
            wh = hb * f
            ww = wh * ar
        for iy in range(CROP_GRID):
            for ix in range(CROP_GRID):
                x0 = (wb - ww) * ix / (CROP_GRID - 1)
                y0 = (hb - wh) * iy / (CROP_GRID - 1)
                win = big.crop((int(x0), int(y0), int(x0 + ww) + 1, int(y0 + wh) + 1))
                if _rows_dist(_rows(win), target) <= CROP_MAX_DIFF:
                    return True
    return False


def _relation(a, b):
    """So ảnh mới `a` với ảnh đã giữ `b`: "same" (cùng ảnh), "new_in_old" (a là phần cắt của b),
    "old_in_new" (b là phần cắt của a) hoặc None (khác ảnh)."""
    if a["sha"] == b["sha"]:
        return "same"
    if a["name"] and a["name"] == b["name"]:
        return "same"
    if a["dh"] is not None and b["dh"] is not None:
        if bin(a["dh"] ^ b["dh"]).count("1") <= DHASH_MAX_DIFF:
            return "same"
        if _contains(b.get("th"), a.get("th")):
            return "new_in_old"
        if _contains(a.get("th"), b.get("th")):
            return "old_in_new"
    return None


def _same_image(a, b):
    return _relation(a, b) is not None


def download_images(urls, referer, max_images=4, min_bytes=8000, max_bytes=8_000_000):
    """Tải ảnh về. Trả list {url, mime, data}. Bỏ ảnh quá nhỏ / không phải ảnh thật / ảnh TRÙNG nội dung
    (cùng một ảnh ở nhiều đường dẫn hay kích thước: giữ bản dung lượng lớn nhất)."""
    out, prints, tried = [], [], 0
    for u in urls:
        if len(out) >= max_images or tried >= max_images * 3:
            break
        try:
            r = util.http(u, headers={"Referer": referer, "Accept": "image/*"},
                          retries=1, max_bytes=max_bytes)
        except util.HttpError:
            continue
        mime = util.sniff_image(r.body)
        if not mime or len(r.body) < min_bytes:
            continue
        tried += 1
        dh, th = _fingerprint(r.body)
        fp = {"sha": hashlib.sha256(r.body).hexdigest(), "name": _img_name_key(u), "dh": dh, "th": th}
        im = {"url": u, "mime": mime, "data": r.body}
        j, rel = None, None
        for k, f in enumerate(prints):
            rel = _relation(fp, f)
            if rel:
                j = k
                break
        if j is None:
            out.append(im)
            prints.append(fp)
        elif rel == "same":
            util.log(f"Bỏ ảnh trùng: ...{u[-50:]}")
            if len(r.body) > len(out[j]["data"]):     # giữ bản chất lượng cao hơn
                out[j], prints[j] = im, fp
        elif rel == "new_in_old":
            util.log(f"Bỏ ảnh là phần cắt của ảnh đã có: ...{u[-50:]}")
        else:                                          # ảnh đã giữ lại là phần cắt của ảnh mới -> giữ ảnh đầy đủ
            util.log(f"Thay ảnh cắt bằng ảnh đầy đủ: ...{u[-50:]}")
            out[j], prints[j] = im, fp
    return out
