"""Đọc RSS/Atom và giải link Google News về link bài thật."""
import base64
import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

from . import util


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:          # "-0000" nghĩa là UTC, không phải giờ máy
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return None


def parse_feed(data):
    """Trả về list {title, link, published, summary, source_name, source_url, guid}."""
    if b"<!ENTITY" in data[:5000] or b"<!entity" in data[:5000]:
        raise ValueError("Feed chứa ENTITY - từ chối vì lý do an toàn")
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "published": None, "summary": "",
             "source_name": "", "source_url": "", "guid": ""}
        for c in el:
            n = _local(c.tag)
            txt = (c.text or "").strip()
            if n == "title":
                d["title"] = _clean(txt)
            elif n == "link":
                if name == "entry":
                    if c.get("rel", "alternate") == "alternate" and c.get("href"):
                        d["link"] = c.get("href").strip()
                else:
                    d["link"] = txt
            elif n in ("pubDate", "published", "updated", "date"):
                d["published"] = d["published"] or _parse_date(txt)
            elif n in ("description", "summary", "encoded", "content"):
                if not d["summary"]:
                    d["summary"] = _clean(txt)
            elif n == "source":
                d["source_name"] = _clean(txt)
                d["source_url"] = c.get("url", "")
            elif n in ("guid", "id"):
                d["guid"] = txt
        if d["title"] and d["link"]:
            out.append(d)
    return out


# ---------- Google News ----------

def _extract_url_from_bytes(b):
    m = re.search(rb"https?://[\x21-\x7e]+", b)
    if not m:
        return None
    url = m.group(0).decode("ascii", "ignore")
    # cắt tại ký tự điều khiển protobuf thường gặp
    url = re.split(r"[\x00-\x20\"<>\\^`{|}]", url)[0]
    return url if util.is_safe_url(url) else None


def _gn_id(link):
    p = urlparse(link)
    m = re.search(r"/articles/([^/?]+)", p.path)
    return m.group(1) if m else None


def google_news_real_url(link):
    """Giải link news.google.com về link bài gốc. Trả None nếu không giải được.

    Cách 1: giải mã base64 (link kiểu cũ có nhúng URL thật).
    Cách 2: gọi endpoint batchexecute của Google (chưa kiểm chứng trên mạng thật;
            nếu thất bại sẽ trả None và tin bị bỏ qua, có ghi log).
    """
    host = urlparse(link).hostname or ""
    if "news.google.com" not in host:
        return link
    gid = _gn_id(link)
    if not gid:
        return None
    try:
        pad = "=" * (-len(gid) % 4)
        raw = base64.urlsafe_b64decode(gid + pad)
        u = _extract_url_from_bytes(raw)
        if u:
            return u
    except Exception:
        pass
    try:
        page = util.http(f"https://news.google.com/rss/articles/{gid}", retries=1).text()
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return None
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None,
              None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            gid, int(ts.group(1)), sg.group(1)])
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = util.http(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            data=("f.req=" + quote(freq)).encode(), retries=1)
        body = resp.text().split("\n\n", 1)[-1]
        arr = json.loads(body)
        payload = json.loads(arr[0][2])
        u = payload[1]
        return u if isinstance(u, str) and util.is_safe_url(u) else None
    except Exception as e:
        util.log(f"Google News: không giải được link ({type(e).__name__})")
        return None
