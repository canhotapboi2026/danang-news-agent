"""Điều phối toàn bộ: nhận duyệt Telegram -> quét nguồn -> biên tập -> kiểm tra -> gửi duyệt/đăng.

Chạy:  python -m agent.main            (dùng config.json, state.json)
"""
import json
import os
import re
import sys

from . import dedupe, editor, extract, feeds, media, util
from . import state as st_mod
from .facebook import Facebook, FacebookError
from .telegram import Telegram, TelegramError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUR = 3600


class Ctx:
    def __init__(self, cfg, env, state, now, tg, fb):
        self.cfg, self.env, self.state, self.now, self.tg, self.fb = cfg, env, state, now, tg, fb
        self.s = cfg["settings"]
        self.dry_run = env.get("DRY_RUN") == "1" or bool(self.s.get("dry_run")) or not fb.enabled
        self.cache = {}      # ảnh đã tải trong lần chạy này (tránh tải lại khi đăng bài từ hàng đợi)
        self.stats = {k: 0 for k in (
            "sources_ok", "sources_error", "processed", "blocked", "off_topic", "duplicates", "unresolved",
            "no_image", "too_short", "errors", "sent_for_review", "auto_posted", "queued",
            "posted", "dry_posted", "rejected", "expired", "video_frame", "card_media", "queue_full")}


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("settings", "sources", "keywords"):
        if k not in cfg:
            raise ValueError(f"config.json thiếu mục '{k}'")
    if cfg["settings"]["publish_mode"] not in ("approve", "auto"):
        raise ValueError("publish_mode phải là 'approve' hoặc 'auto'")
    return cfg


def matches_keywords(text, kws):
    n = util.norm(text)
    for k in kws:
        if re.search(r"(?<![a-z0-9])" + re.escape(util.norm(k)) + r"(?![a-z0-9])", n):
            return True
    return False


def mark_light(ctx, key, url, title=""):
    ctx.state["seen"][key] = {"t": ctx.now, "url": url, "title": sorted(dedupe.tokens(title)), "lead": []}


def transient(ctx, key, url, title, why):
    n = ctx.state["fail"].get(key, 0) + 1
    ctx.state["fail"][key] = n
    util.log(f"Lỗi tạm thời ({n}/3): {why}")
    if n >= 3:
        mark_light(ctx, key, url, title)
        ctx.stats["errors"] += 1


# ---------- giới hạn đăng ----------

def can_publish_now(ctx, urgent):
    times = [t for t in ctx.state["posted_times"] if t > ctx.now - 86400]
    cap = ctx.s["max_posts_per_day"] + (5 if urgent else 0)
    if len(times) >= cap:
        return False
    if not urgent and times and ctx.now - max(times) < gap_minutes(ctx, max(times)) * 60:
        return False
    return True


def gap_minutes(ctx, last_post_ts):
    """Khoảng cách tối thiểu tới bài kế tiếp: ngẫu nhiên trong [min_gap_minutes, max_gap_minutes].
    Tính từ mốc bài vừa đăng nên không cần lưu thêm gì vào state."""
    lo = ctx.s["min_gap_minutes"]
    hi = max(lo, ctx.s.get("max_gap_minutes", lo))
    return lo + int(util.sha(str(last_post_ts), 6), 16) % (hi - lo + 1)


def in_active_hours(ctx):
    """Chỉ tự đăng trong khung giờ cho phép, ví dụ "active_hours": [6, 22] (giờ Việt Nam)."""
    rng = ctx.s.get("active_hours")
    if not rng:
        return True
    start, end = rng
    h = ((ctx.now + ctx.s.get("utc_offset_hours", 7) * HOUR) // HOUR) % 24
    return start <= h < end if start < end else (h >= start or h < end)


def queue_full(ctx):
    if ctx.s["publish_mode"] != "auto":
        return False
    n = sum(1 for d in ctx.state["drafts"].values() if d["status"] == "queued")
    return n >= ctx.s.get("queue_max", 5)


# ---------- đăng bài ----------

def restore_media(ctx, d):
    """Dựng lại ảnh của bản nháp khi bấm Duyệt ở lần chạy sau (ảnh không được lưu trong state)."""
    m = d.get("media") or {"kind": "photo"}
    if m["kind"] == "photo":
        return extract.download_images(d["images"], d["url"], ctx.s["max_images"], ctx.s["min_image_bytes"])
    if m["kind"] == "frame":
        im = media.frame_from_video(m.get("video_url", ""), d["url"])
        if im:
            return [im]
        util.log("Chụp lại khung hình lỗi, đổi sang thẻ tin")
    card = media.make_card(d.get("card_title") or d["title"], ctx.cfg.get("page_name", media.DEFAULT_PAGE_NAME))
    return [card] if card else []


def publish_draft(ctx, d, imgs=None, auto=False):
    """Đăng một bản nháp. Trả True nếu xong (kể cả dry-run)."""
    imgs = imgs or restore_media(ctx, d)
    if not imgs:
        d["status"], d["error"] = "failed", "không tải lại được ảnh"
        ctx.tg.notify(f"❌ Không đăng được (mất ảnh): {d['url']}")
        return False
    tag = "🤖 Tự động đăng" if auto else "✅ Đã đăng Page"
    if ctx.dry_run:
        d["status"] = "posted_dry"
        ctx.stats["dry_posted"] += 1
        ctx.state["posted_times"].append(ctx.now)     # mô phỏng đúng nhịp đăng khi chạy thử
        ctx.tg.notify(f"🧪 DRY-RUN (chạy thử, KHÔNG đăng thật lên Page)\n"
                      f"Bài sẽ đăng với {len(imgs)} ảnh:\n\n{d['caption']}")
        return True
    try:
        pid, link = ctx.fb.publish(d["caption"], imgs)
    except FacebookError as e:
        d["error"] = str(e)
        util.log(f"Đăng Facebook lỗi: {e}")
        ctx.tg.notify(f"❌ Đăng Facebook lỗi: {e}\nBài: {d['url']}")
        if auto:
            d["status"] = "failed"
        return False
    d["status"], d["fb_post_id"], d["fb_link"] = "posted", pid, link
    ctx.state["posted_times"].append(ctx.now)
    dedupe.remember_caption(ctx.state, d["caption"], ctx.now)
    ctx.stats["auto_posted" if auto else "posted"] += 1
    ctx.tg.notify(f"{tag}: {link}\n\n📋 DÁN VÀO GROUP (dán link bài Page, hoặc copy nội dung):\n"
                  f"{link}\n\n{d['caption']}")
    return True


def ttl_hours(ctx, d):
    return ctx.s["urgent_ttl_hours"] if d.get("urgent") else ctx.s["pending_ttl_hours"]


def handle_decisions(ctx):
    decisions, off = ctx.tg.poll(ctx.state["tg_offset"])
    for act, did, cq in decisions:
        d = ctx.state["drafts"].get(did)
        mid = cq["message"]["message_id"]
        if not d:
            ctx.tg.answer(cq["id"], "Không tìm thấy bản nháp (đã bị xóa?)")
            continue
        if d["status"] != "pending":
            ctx.tg.answer(cq["id"], f"Đã xử lý trước đó: {d['status']}")
            ctx.tg.clear_buttons(mid)
            continue
        if act == "no":
            d["status"] = "rejected"
            ctx.stats["rejected"] += 1
            ctx.tg.answer(cq["id"], "Đã bỏ tin này")
            ctx.tg.clear_buttons(mid)
        elif ctx.now - d["created"] > ttl_hours(ctx, d) * HOUR:
            d["status"] = "expired"
            ctx.stats["expired"] += 1
            ctx.tg.answer(cq["id"], "Tin đã quá hạn, không đăng nữa")
            ctx.tg.clear_buttons(mid)
        else:
            ok = publish_draft(ctx, d)
            ctx.tg.answer(cq["id"], "Đã xử lý" if ok else "Lỗi, xem tin nhắn bot")
            if ok:
                ctx.tg.clear_buttons(mid)
    ctx.state["tg_offset"] = off


def housekeeping(ctx):
    """Hết hạn bản nháp cũ và tin xếp hàng quá lâu."""
    for d in sorted(ctx.state["drafts"].values(), key=lambda x: x["created"]):
        age_h = (ctx.now - d["created"]) / HOUR
        if d["status"] == "pending" and age_h > ttl_hours(ctx, d):
            d["status"] = "expired"
            ctx.stats["expired"] += 1
            if d.get("tg_msg_id"):
                ctx.tg.clear_buttons(d["tg_msg_id"])
        elif d["status"] == "queued" and age_h > ctx.s.get("queue_ttl_hours", 5):
            d["status"] = "expired"
            ctx.stats["expired"] += 1


def publish_queued(ctx):
    """Chế độ auto: nếu đủ khoảng cách và đúng khung giờ thì đăng TỐI ĐA 1 bài trong lần chạy này.
    Ưu tiên tin HOT, sau đó tin MỚI nhất trước (tin xếp hàng lâu sẽ tự hết hạn thay vì đăng muộn)."""
    if ctx.s["publish_mode"] != "auto" or not in_active_hours(ctx):
        return
    queued = sorted((d for d in ctx.state["drafts"].values() if d["status"] == "queued"),
                    key=lambda x: (x.get("priority") != "hot", -x["created"]))
    if queued and can_publish_now(ctx, queued[0].get("urgent")):
        d = queued[0]
        publish_draft(ctx, d, imgs=ctx.cache.get(d["id"]), auto=True)


# ---------- xử lý từng tin ----------

def process_item(ctx, src, it):
    s = ctx.s
    raw_key = dedupe.url_key(it["link"])
    if raw_key in ctx.state["seen"]:
        return
    kws = src.get("require_keywords") or ctx.cfg["keywords"]
    if matches_keywords(it["title"], ctx.cfg.get("block_keywords", [])):
        mark_light(ctx, raw_key, it["link"], it["title"])
        ctx.stats["blocked"] += 1
        return
    if src.get("prefilter", True) and not matches_keywords(it["title"] + " " + it["summary"], kws):
        mark_light(ctx, raw_key, it["link"], it["title"])
        ctx.stats["off_topic"] += 1
        return

    link = it["link"]
    if src["type"] == "google_news":
        real = feeds.google_news_real_url(link)
        if not real:
            mark_light(ctx, raw_key, link, it["title"])
            ctx.stats["unresolved"] += 1
            return
        link = real
        if dedupe.url_key(link) in ctx.state["seen"]:
            mark_light(ctx, raw_key, link, it["title"])
            return

    ctx.stats["processed"] += 1
    try:
        art = extract.fetch_article(link)
    except Exception as e:
        transient(ctx, raw_key, link, it["title"], f"lấy bài lỗi: {type(e).__name__} {link}")
        return
    if art["published"] and art["published"] < ctx.now - s["max_age_hours"] * 2 * HOUR:
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["off_topic"] += 1
        return
    if len(art["text"]) < s["min_text_chars"]:
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["too_short"] += 1
        return
    if not matches_keywords(art["title"] + " " + art["text"][:4000], kws):
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["off_topic"] += 1
        return
    dup, why = dedupe.check(ctx.state, art["url"], art["title"], art["text"], ctx.now)
    if dup:
        util.log(f"Bỏ (trùng): {why}: {art['title'][:70]}")
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["duplicates"] += 1
        return

    imgs = extract.download_images(art["images"], art["url"], s["max_images"], s["min_image_bytes"])
    mdesc = {"kind": "photo"}
    if not imgs and art.get("videos"):      # chỉ có video -> chụp 1 khung hình làm ảnh minh họa
        frame, vurl = media.frame_from_videos(art["videos"], art["url"])
        if frame:
            imgs, mdesc = [frame], {"kind": "frame", "video_url": vurl}

    try:
        res = editor.edit_article(ctx.cfg, ctx.env, art, imgs)
    except Exception as e:
        transient(ctx, raw_key, link, art["title"], f"biên tập lỗi: {type(e).__name__}")
        return
    if res.get("relevant") is False:
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["off_topic"] += 1
        return
    if dedupe.caption_is_duplicate(ctx.state, res["caption"], ctx.now):
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["duplicates"] += 1
        return

    card_title = editor.clean_text(res.get("title")) or art["title"]
    if not imgs:                            # không ảnh, không video dùng được -> tự vẽ thẻ tin
        card = media.make_card(card_title, ctx.cfg.get("page_name", media.DEFAULT_PAGE_NAME))
        if not card:
            mark_light(ctx, raw_key, link, art["title"])
            ctx.stats["no_image"] += 1      # không tạo được ảnh dự phòng
            util.log(f"Bỏ (không có ảnh): {art['title'][:70]}")
            return
        imgs, mdesc = [card], {"kind": "card"}
    ctx.stats["video_frame"] += mdesc["kind"] == "frame"
    ctx.stats["card_media"] += mdesc["kind"] == "card"

    cat = res.get("category") if res.get("category") in editor.CATEGORIES else "khac"
    prio = res.get("priority") if res.get("priority") in ("khan", "hot", "thuong") else "thuong"
    if cat == "khan" and prio != "khan":
        cat = "khac"  # nhãn khẩn chỉ do priority quyết định
    if prio == "khan":
        cat = cat if cat != "khan" else "khac"
    d = {
        "id": util.sha(art["url"], 8), "url": art["url"], "title": art["title"], "site": art["site"],
        "caption": editor.final_caption(ctx.cfg, res, art),
        "images": [i["url"] for i in imgs] if mdesc["kind"] == "photo" else [],
        "media": mdesc, "card_title": card_title,
        "category": cat, "priority": prio, "urgent": prio == "khan",
        "flags": res["flags"], "created": ctx.now, "status": "pending", "source": src["name"],
    }
    auto = (s["publish_mode"] == "auto" and (s.get("auto_all_sources", False) or src.get("auto_ok", False))
            and not d["flags"] and (not d["urgent"] or s.get("auto_urgent", False)))
    try:
        if auto:
            d["status"] = "queued"          # chờ tới lượt đăng (publish_queued)
            ctx.state["drafts"][d["id"]] = d
            ctx.cache[d["id"]] = imgs
            ctx.stats["queued"] += 1
        else:
            d["tg_msg_id"] = ctx.tg.send_draft(d, imgs)
            ctx.state["drafts"][d["id"]] = d
            ctx.stats["sent_for_review"] += 1
    except TelegramError as e:
        transient(ctx, raw_key, link, art["title"], f"gửi Telegram lỗi: {e}")
        return
    dedupe.remember(ctx.state, art["url"], art["title"], art["text"], ctx.now)
    if raw_key not in ctx.state["seen"]:   # link RSS khác link chuẩn: ghi nhớ cả hai, không ghi đè bản đầy đủ
        mark_light(ctx, raw_key, link, art["title"])


def run_source(ctx, src):
    try:
        r = util.http(src["url"])
        items = feeds.parse_feed(r.body)
    except Exception as e:
        util.log(f"Nguồn lỗi [{src['name']}]: {type(e).__name__}")
        ctx.stats["sources_error"] += 1
        return
    ctx.stats["sources_ok"] += 1
    cutoff = ctx.now - ctx.s["max_age_hours"] * HOUR
    fresh = [i for i in items if (i["published"] or ctx.now) >= cutoff]
    fresh.sort(key=lambda i: i["published"] or 0, reverse=True)
    start = ctx.stats["processed"]
    per_src = ctx.s.get("max_new_per_source", ctx.s["max_new_per_run"])
    for it in fresh:
        done = ctx.stats["processed"] - start
        if ctx.stats["processed"] >= ctx.s["max_new_per_run"] or done >= per_src:
            break
        if queue_full(ctx):
            ctx.stats["queue_full"] += 1
            break
        process_item(ctx, src, it)


def run(cfg, env, state, now=None, tg=None, fb=None, do_sources=True):
    now = now or util.now_ts()
    tg = tg or Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"],
                        (env.get("TELEGRAM_ALLOWED_USER_IDS") or "").split(","))
    fb = fb or Facebook(env.get("FB_PAGE_ID"), env.get("FB_PAGE_ACCESS_TOKEN"),
                        env.get("FB_GRAPH_VERSION") or "v25.0")
    ctx = Ctx(cfg, env, state, now, tg, fb)
    util.log(f"Bắt đầu. Chế độ: {ctx.s['publish_mode']}, DRY-RUN Facebook: {ctx.dry_run}")
    try:
        handle_decisions(ctx)
    except TelegramError as e:
        util.log(f"Đọc nút bấm Telegram lỗi: {e}")
    housekeeping(ctx)
    if do_sources:
        for src in cfg["sources"]:
            if src.get("enabled", True) and not src["url"].startswith("DÁN"):
                run_source(ctx, src)
    publish_queued(ctx)
    return ctx.stats


def _load_env_cfg():
    cfg = load_config(os.environ.get("AGENT_CONFIG", os.path.join(ROOT, "config.json")))
    env = dict(os.environ)
    ai_key = "ANTHROPIC_API_KEY" if cfg["settings"].get("provider", "gemini") == "anthropic" else "GEMINI_API_KEY"
    missing = [k for k in (ai_key, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not env.get(k)]
    if missing:
        print("Thiếu biến môi trường/secret: " + ", ".join(missing), file=sys.stderr)
        return None, None
    return cfg, env


def _state_path():
    return os.environ.get("AGENT_STATE", os.path.join(ROOT, "state.json"))


def main_once():
    cfg, env = _load_env_cfg()
    if not cfg:
        return 2
    path = _state_path()
    state = st_mod.load(path)
    stats = {}
    try:
        stats = run(cfg, env, state)
    finally:
        st_mod.prune(state, util.now_ts())
        st_mod.save(path, state)
    summary = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
    util.log("Tóm tắt: " + (summary or "không có gì mới"))
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write("### Kết quả lần chạy\n" + "\n".join(f"- {k}: {v}" for k, v in stats.items()) + "\n")
    return 0


def main_loop(poll=15, source_every=300, max_loops=None, sleep=None):
    """Chế độ chạy liên tục (máy luôn bật): nút Duyệt có hiệu lực sau ~poll giây."""
    import time
    sleep = sleep or time.sleep
    cfg, env = _load_env_cfg()
    if not cfg:
        return 2
    path = _state_path()
    state = st_mod.load(path)
    last_src, n = 0, 0
    while max_loops is None or n < max_loops:
        n += 1
        now = util.now_ts()
        do_src = now - last_src >= source_every
        try:
            run(cfg, env, state, now=now, do_sources=do_src)
            if do_src:
                last_src = now
        except Exception as e:   # không để một lỗi làm dừng cả vòng lặp
            util.log(f"Lỗi vòng lặp: {type(e).__name__}: {e}")
        st_mod.prune(state, util.now_ts())
        st_mod.save(path, state)
        sleep(poll)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--loop" in argv:
        i = argv.index("--loop")
        poll = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 15
        return main_loop(poll=max(5, poll))
    return main_once()


if __name__ == "__main__":
    sys.exit(main())
