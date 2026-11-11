"""Chống trùng: theo URL, theo tiêu đề, theo phần mở đầu nội dung, theo caption đã đăng."""
from urllib.parse import parse_qsl, urlparse

from . import util

DAY = 86400
TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "ref", "zarsrc", "src", "amp", "from"}
STOP = set("va cua la co khong nhung duoc trong cho mot cac nay da bi de voi tu den "
           "theo khi ve nhu se cung nen hay con tai tren duoi sau truoc rat nhieu "
           "nguoi viec nam ngay thang".split())


def url_key(url):
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    for pre in ("www.", "m.", "amp."):
        if host.startswith(pre):
            host = host[len(pre):]
    path = p.path.rstrip("/")
    if path.endswith("/amp"):
        path = path[:-4]
    q = sorted((k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING)
    return host + path + ("?" + "&".join(f"{k}={v}" for k, v in q) if q else "")


def tokens(text, limit=None):
    ws = util.words(text)
    if limit:
        ws = ws[:limit]
    return {w for w in ws if w not in STOP and (len(w) >= 3 or w.isdigit())}


def _nums(toks):
    return {t for t in toks if any(c.isdigit() for c in t)}


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check(state, url, title, text, now, title_thr=0.6, lead_thr=0.5, window_h=48):
    """Trả (trùng?, lý do)."""
    k = url_key(url)
    if k in state["seen"]:
        return True, "trùng URL"
    tt, lt = tokens(title), tokens(text, limit=80)
    cut = now - window_h * 3600
    for s in state["seen"].values():
        if s.get("t", 0) < cut:
            continue
        st, sl = set(s.get("title", [])), set(s.get("lead", []))
        # cùng sự việc nhưng khác con số (ngày, giờ, số người...) -> không coi là trùng
        n1, n2 = _nums(tt), _nums(st)
        if n1 and n2 and n1 != n2:
            continue
        if jaccard(tt, st) >= title_thr:
            return True, "trùng tiêu đề với tin đã xử lý"
        if len(lt) >= 15 and jaccard(lt, sl) >= lead_thr:
            return True, "trùng phần mở đầu nội dung với tin đã xử lý"
    return False, ""


def remember(state, url, title, text, now):
    state["seen"][url_key(url)] = {
        "t": now, "url": url,
        "title": sorted(tokens(title)),
        "lead": sorted(tokens(text, limit=80)),
    }


def caption_is_duplicate(state, caption, now, thr=0.6, window_h=48):
    ct = tokens(caption)
    cut = now - window_h * 3600
    for c in state["posted_captions"]:
        if c.get("t", 0) >= cut and jaccard(ct, c.get("tok", [])) >= thr:
            return True
    return False


def remember_caption(state, caption, now):
    state["posted_captions"].append({"t": now, "tok": sorted(tokens(caption))})
