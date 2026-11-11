"""Kiểm tra từng nguồn trong config.json có đọc được không (không đăng gì, không tốn tiền Claude).

Chạy:  python -m agent.check_sources
"""
import os
import sys

from . import extract, feeds, util
from .main import ROOT, load_config


def main():
    cfg = load_config(os.environ.get("AGENT_CONFIG", os.path.join(ROOT, "config.json")))
    bad = 0
    for src in cfg["sources"]:
        name = src["name"]
        if src["url"].startswith("DÁN"):
            print(f"⏭  {name}: chưa điền URL RSS")
            continue
        try:
            items = feeds.parse_feed(util.http(src["url"]).body)
        except Exception as e:
            print(f"❌ {name}: không đọc được feed ({type(e).__name__})")
            bad += 1
            continue
        if not items:
            print(f"⚠️  {name}: feed rỗng")
            bad += 1
            continue
        first = items[0]
        line = f"✅ {name}: {len(items)} tin. Mới nhất: {first['title'][:60]}"
        link = first["link"]
        if src["type"] == "google_news":
            real = feeds.google_news_real_url(link)
            line += f" | giải link Google News: {'OK' if real else 'THẤT BẠI'}"
            if not real:
                bad += 1
            link = real
        if link:
            try:
                art = extract.fetch_article(link)
                imgs = extract.download_images(art["images"], art["url"], 1, 1000)
                line += (f" | bài mẫu: {len(art['text'])} ký tự, "
                         f"{'có ảnh' if imgs else 'KHÔNG tải được ảnh'}")
            except Exception as e:
                line += f" | lấy bài mẫu lỗi ({type(e).__name__})"
                bad += 1
        print(line)
    print(f"\nCó {bad} nguồn cần xem lại." if bad else "\nTất cả nguồn đều ổn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
