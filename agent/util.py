"""Tiện ích dùng chung: HTTP, xử lý chữ tiếng Việt, multipart, log.

Chỉ dùng thư viện chuẩn của Python (không cần cài thêm gì).
Mọi lời gọi mạng đều đi qua `http()` để dễ kiểm thử bằng dữ liệu giả.
"""
import hashlib
import ipaddress
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zlib
from datetime import datetime, timezone
from urllib.parse import urlparse

UA = "Mozilla/5.0 (compatible; DaNangNewsAgent/1.0)"


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


class HttpError(Exception):
    def __init__(self, status, body=b"", url=""):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status}")


class Resp:
    def __init__(self, status, headers, body, url):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body
        self.url = url

    def text(self, encoding=None):
        enc = encoding or _charset(self.headers.get("content-type", "")) or "utf-8"
        return self.body.decode(enc, errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8", errors="replace"))


def _charset(content_type):
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    return m.group(1) if m else None


def is_safe_url(url):
    """Chỉ cho phép http/https tới máy chủ công khai (chặn localhost / IP nội bộ)."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass  # là tên miền, không phải IP
    return True


def http(url, method="GET", headers=None, data=None, timeout=25, retries=2,
         max_bytes=8_000_000):
    """Gọi HTTP. Trả về Resp. Lỗi -> HttpError (status=0 nếu lỗi mạng)."""
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    h.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise HttpError(413, b"too large", url)
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, max_bytes)
                return Resp(r.status, dict(r.headers), body, r.geturl())
        except urllib.error.HTTPError as e:
            body = e.read()[:20000]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(e.code, body, url)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HttpError(0, str(last).encode(), url)


def multipart(fields, files):
    """fields: {tên: chuỗi}; files: {tên: (tên_file, bytes, mime)} -> (body, content_type)."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    for k, (fname, content, mime) in files.items():
        parts.append(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---------- xử lý chữ tiếng Việt ----------

def strip_accents(s):
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s):
    return re.sub(r"\s+", " ", strip_accents(s).lower()).strip()


def words(s):
    return re.findall(r"[a-z0-9]+", norm(s))


def sha(s, n=12):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def sniff_image(b):
    """Nhận diện loại ảnh bằng chữ ký đầu file. Trả về mime hoặc None."""
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


def now_ts():
    return int(time.time())
