"""Ảnh dự phòng khi bài gốc không có ảnh dùng được.

1) frame_from_videos: chụp 1 khung hình đại diện từ video bài gốc (cần ffmpeg).
2) make_card: tự vẽ "thẻ tin" có tiêu đề (cần Pillow + một font có tiếng Việt).

Cả hai đều "êm": thiếu công cụ thì trả None, agent tự bỏ qua, không làm sập cả lần chạy.
Thẻ tin KHÔNG phải ảnh thật của sự việc nên không vẽ cảnh giả, chỉ có chữ trên nền màu.
"""
import glob
import io
import os
import shutil
import subprocess
import tempfile

from . import util

DEFAULT_PAGE_NAME = "Bản tin về Đà Nẵng"
FFMPEG_TIMEOUT = 60

# ---------- 1. Chụp khung hình từ video ----------


def frame_from_video(video_url, referer="", min_bytes=8000):
    """Chụp 1 khung hình đại diện (bỏ ~1 giây đầu, chọn khung 'đại diện' nhất trong 20 giây đầu).
    Trả {url, mime, data} hoặc None."""
    if not shutil.which("ffmpeg") or not util.is_safe_url(video_url):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "frame.jpg")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-protocol_whitelist", "http,https,tcp,tls,crypto",
               "-user_agent", util.UA,
               "-rw_timeout", "20000000",
               "-ss", "1", "-t", "20"]
        if referer:
            cmd += ["-headers", f"Referer: {referer}\r\n"]
        cmd += ["-i", video_url,
                "-vf", "thumbnail=120,scale='min(1280,iw)':-2",
                "-frames:v", "1", "-q:v", "3", out]
        try:
            subprocess.run(cmd, check=True, timeout=FFMPEG_TIMEOUT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with open(out, "rb") as f:
                data = f.read()
        except Exception as e:  # ffmpeg lỗi, hết giờ, không có file...
            util.log(f"Chụp khung hình lỗi: {type(e).__name__}")
            return None
    if util.sniff_image(data) != "image/jpeg" or len(data) < min_bytes:
        return None
    return {"url": "frame:" + video_url, "mime": "image/jpeg", "data": data}


def frame_from_videos(urls, referer="", max_try=2):
    """Thử tối đa max_try video. Trả (ảnh, video_url) hoặc (None, None)."""
    for u in list(urls or [])[:max_try]:
        im = frame_from_video(u, referer)
        if im:
            return im, u
    return None, None


# ---------- 2. Thẻ tin ----------

_BOLD_FONTS = ["DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "LiberationSans-Bold.ttf",
               "Roboto-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"]
_REG_FONTS = ["DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf",
              "Roboto-Regular.ttf", "Arial.ttf", "arial.ttf"]


def _find_font(names, env_key):
    p = os.environ.get(env_key)
    if p and os.path.exists(p):
        return p
    roots = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
             "/System/Library/Fonts", "C:/Windows/Fonts"]
    for n in names:
        for r in roots:
            hits = glob.glob(os.path.join(r, "**", n), recursive=True)
            if hits:
                return hits[0]
    return None


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_card(title, page_name=DEFAULT_PAGE_NAME, label="TIN ĐÀ NẴNG"):
    """Vẽ thẻ tin 1200x630. Trả {url, mime, data} hoặc None nếu thiếu Pillow/font."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        util.log("Thiếu Pillow nên không vẽ được thẻ tin")
        return None
    bold, reg = _find_font(_BOLD_FONTS, "AGENT_FONT_BOLD"), _find_font(_REG_FONTS, "AGENT_FONT")
    if not bold:
        util.log("Không tìm thấy font có tiếng Việt nên không vẽ được thẻ tin")
        return None
    reg = reg or bold
    W, H, PAD = 1200, 630, 80
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    top, bot = (11, 60, 93), (22, 104, 140)          # nền xanh đậm chuyển dần
    for y in range(H):
        k = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)))
    px.rectangle([0, 0, 16, H], fill=(255, 183, 3))  # vạch nhấn màu vàng bên trái
    px.text((PAD, 60), label, font=ImageFont.truetype(reg, 30), fill=(255, 183, 3))

    title = " ".join((title or "").split())
    box_w, box_h = W - 2 * PAD, 360
    lines, font = [], None
    for size in range(72, 39, -4):
        font = ImageFont.truetype(bold, size)
        lines = _wrap(px, title, font, box_w)
        if len(lines) * int(size * 1.3) <= box_h:
            break
    else:  # tiêu đề quá dài: giữ cỡ nhỏ nhất và cắt bớt dòng
        size = 40
        lines = lines[:max(1, box_h // int(size * 1.3))]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    lh = int(size * 1.3)
    y = 120 + (box_h - lh * len(lines)) // 2
    for ln in lines:
        px.text((PAD, y), ln, font=font, fill=(255, 255, 255))
        y += lh
    px.line([(PAD, H - 90), (W - PAD, H - 90)], fill=(255, 255, 255), width=2)
    px.text((PAD, H - 70), page_name or DEFAULT_PAGE_NAME, font=ImageFont.truetype(reg, 30),
            fill=(235, 240, 245))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return {"url": "card:", "mime": "image/jpeg", "data": buf.getvalue()}
