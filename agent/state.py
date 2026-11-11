"""Lưu trạng thái giữa các lần chạy (state.json): tin đã thấy, bản nháp, lịch sử đăng."""
import json
import os

DAY = 86400


def default_state():
    return {
        "seen": {},            # url_key -> {t, title:[...], lead:[...], url}
        "drafts": {},          # id -> bản nháp
        "posted_times": [],    # mốc thời gian các bài đã đăng
        "posted_captions": [], # [{t, tok:[...]}] để chống trùng nội dung
        "tg_offset": 0,        # vị trí đọc nút bấm Telegram
        "fail": {},            # url_key -> số lần lỗi tạm thời
    }


def load(path):
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_state()
    base = default_state()
    base.update(st if isinstance(st, dict) else {})
    return base


def save(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def prune(st, now, keep_days=14):
    cut = now - keep_days * DAY
    st["seen"] = {k: v for k, v in st["seen"].items() if v.get("t", 0) >= cut}
    st["drafts"] = {k: v for k, v in st["drafts"].items() if v.get("created", 0) >= cut}
    st["posted_times"] = [t for t in st["posted_times"] if t >= cut]
    st["posted_captions"] = [c for c in st["posted_captions"] if c.get("t", 0) >= cut]
    st["fail"] = {k: v for k, v in st["fail"].items() if k in st["seen"] or v < 3}
