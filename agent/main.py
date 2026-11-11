"""Điều phối toàn bộ: nhận duyệt Telegram -> quét nguồn -> biên tập -> kiểm tra -> gửi duyệt/đăng.

Chạy:  python -m agent.main            (dùng config.json, state.json)
"""
import json
import os
import re
import sys

from . import dedupe, editor, extract, feeds, util
from . import state as st_mod
from .facebook import Facebook, FacebookError
from .telegram import Telegram, TelegramError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUR = 3600


class Ctx:
    def __init__(self, cfg, env, state, now, tg, fb):
        self.cfg, self.env, self.state, self.now, self.tg, self.fb = cfg, env, state, now, tg, fb
        self.s = cfg["settings"]
        self.dry_run = env.get("DRY_RUN") == "1" or not fb.enabled
        self.stats = {k: 0 for k in (
            "sources_ok", "sources_error", "processed", "blocked", "off_topic", "duplicates", "unresolved",
            "no_image", "too_short", "errors", "sent_for_review", "auto_posted", "queued",
            "posted", "dry_posted", "rejected", "expired")}


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
    if not urgent and times and ctx.now - max(times) < ctx.s["min_gap_minutes"] * 60:
        return False
    return True


# ---------- đăng bài ----------

def publish_draft(ctx, d, imgs=None, auto=False):
    """Đăng một bản nháp. Trả True nếu xong (kể cả dry-run)."""
    imgs = imgs or extract.download_images(d["images"], d["url"], ctx.s["max_images"], ctx.s["min_image_bytes"])
    if not imgs:
        d["status"], d["error"] = "failed", "không tải lại được ảnh"
        ctx.tg.notify(f"❌ Không đăng được (mất ảnh): {d['url']}")
        return False
    tag = "🤖 Tự động đăng" if auto else "✅ Đã đăng Page"
    if ctx.dry_run:
        d["status"] = "posted_dry"
        ctx.stats["dry_posted"] += 1
        ctx.tg.notify(f"🧪 DRY-RUN (chưa cấu hình Facebook nên KHÔNG đăng thật)\n"
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
    """Hết hạn bản nháp cũ; đăng bài đang xếp hàng khi đủ điều kiện."""
    drafts = sorted(ctx.state["drafts"].values(), key=lambda x: x["created"])
    for d in drafts:
        age_h = (ctx.now - d["created"]) / HOUR
        if d["status"] == "pending" and age_h > ttl_hours(ctx, d):
            d["status"] = "expired"
            ctx.stats["expired"] += 1
            if d.get("tg_msg_id"):
                ctx.tg.clear_buttons(d["tg_msg_id"])
        elif d["status"] == "queued":
            if age_h > 3:
                d["status"] = "expired"
                ctx.stats["expired"] += 1
            elif can_publish_now(ctx, d.get("urgent")):
                publish_draft(ctx, d, auto=True)


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
    if not imgs:
        mark_light(ctx, raw_key, link, art["title"])
        ctx.stats["no_image"] += 1          # không đủ combo caption + ảnh
        util.log(f"Bỏ (không có ảnh): {art['title'][:70]}")
        return

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

    cat = res.get("category") if res.get("category") in editor.CATEGORIES else "khac"
    d = {
        "id": util.sha(art["url"], 8), "url": art["url"], "title": art["title"], "site": art["site"],
        "caption": editor.final_caption(ctx.cfg, res, art), "images": [i["url"] for i in imgs],
        "category": cat, "urgent": bool(res.get("urgent")) or cat == "khan",
        "flags": res["flags"], "created": ctx.now, "status": "pending", "source": src["name"],
    }
    auto = (s["publish_mode"] == "auto" and src.get("auto_ok", False) and not d["flags"])
    try:
        if auto:
            if can_publish_now(ctx, d["urgent"]):
                ctx.state["drafts"][d["id"]] = d
                publish_draft(ctx, d, imgs=imgs, auto=True)
            else:
                d["status"] = "queued"
                ctx.state["drafts"][d["id"]] = d
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
