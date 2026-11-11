"""Gửi bản xem trước tới Telegram và đọc nút bấm Duyệt/Bỏ."""
import json

from . import util

LABEL = {"khan": "🚨 KHẨN", "giao_thong": "🚦 Giao thông", "dich_vu_tien_ich": "💡 Tiện ích",
         "su_kien": "🎉 Sự kiện", "du_lich_van_hoa": "🏖 Du lịch/Văn hóa", "kinh_te": "💼 Kinh tế",
         "phap_luat": "⚖️ Pháp luật", "giao_duc": "🎓 Giáo dục", "y_te": "🩺 Y tế",
         "thoi_tiet": "⛈ Thời tiết", "doi_song": "🏙 Đời sống", "khac": "📰 Tin tức"}


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token, chat_id, allowed_ids=None):
        self.token = token
        self.chat_id = str(chat_id)
        ids = {str(i).strip() for i in (allowed_ids or []) if str(i).strip()}
        self.allowed = ids or {self.chat_id}

    def api(self, method, payload=None, files=None):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            if files:
                body, ctype = util.multipart(
                    {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in (payload or {}).items()},
                    files)
                r = util.http(url, method="POST", data=body, headers={"Content-Type": ctype}, retries=1)
            else:
                r = util.http(url, method="POST", data=json.dumps(payload or {}).encode(),
                              headers={"Content-Type": "application/json"}, retries=1)
        except util.HttpError as e:  # không để lộ token trong thông báo lỗi
            raise TelegramError(f"{method}: HTTP {e.status} {e.body[:200]!r}") from None
        data = r.json()
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description')}")
        return data["result"]

    # ----- gửi bản duyệt -----
    def send_draft(self, draft, images):
        """Gửi ảnh (đơn/album) rồi tin nhắn chứa caption + nút. Trả message_id của tin nhắn nút."""
        if len(images) == 1:
            im = images[0]
            self.api("sendPhoto", {"chat_id": self.chat_id},
                     {"photo": ("p." + im["mime"].split("/")[1], im["data"], im["mime"])})
        elif len(images) > 1:
            media, files = [], {}
            for i, im in enumerate(images[:10]):
                media.append({"type": "photo", "media": f"attach://f{i}"})
                files[f"f{i}"] = (f"p{i}." + im["mime"].split("/")[1], im["data"], im["mime"])
            self.api("sendMediaGroup", {"chat_id": self.chat_id, "media": media}, files)
        head = LABEL.get(draft["category"], "📰 Tin tức")
        if draft.get("urgent") and draft["category"] != "khan":
            head = "🚨 KHẨN · " + head
        lines = [f"{head}  ·  {draft['site']}  ·  {len(images)} ảnh"]
        if draft["flags"]:
            lines.append("⚠️ CẦN XEM KỸ:\n- " + "\n- ".join(draft["flags"]))
        lines += ["", draft["caption"], "", f"Mã: {draft['id']}"]
        text = "\n".join(lines)[:3900]
        kb = {"inline_keyboard": [
            [{"text": "✅ Đăng Page", "callback_data": f"ok:{draft['id']}"},
             {"text": "❌ Bỏ", "callback_data": f"no:{draft['id']}"}],
            [{"text": "🔗 Mở bài gốc", "url": draft["url"]}]]}
        res = self.api("sendMessage", {"chat_id": self.chat_id, "text": text,
                                       "reply_markup": kb, "disable_web_page_preview": True})
        return res["message_id"]

    def notify(self, text):
        return self.api("sendMessage", {"chat_id": self.chat_id, "text": text[:3900],
                                        "disable_web_page_preview": True})

    # ----- nhận nút bấm -----
    def poll(self, offset):
        """Trả (danh_sách_quyết_định, offset_mới). Chỉ chấp nhận người dùng được phép."""
        ups = self.api("getUpdates", {"offset": offset, "timeout": 0,
                                      "allowed_updates": ["callback_query"]})
        decisions, new_off = [], offset
        for u in ups:
            new_off = max(new_off, u["update_id"] + 1)
            cq = u.get("callback_query")
            if not cq:
                continue
            uid = str(cq.get("from", {}).get("id"))
            chat = str(cq.get("message", {}).get("chat", {}).get("id"))
            if uid not in self.allowed or chat != self.chat_id:
                self.answer(cq["id"], "Bạn không có quyền.")
                util.log(f"Bỏ qua nút bấm từ người dùng lạ (id={uid})")
                continue
            act, _, did = (cq.get("data") or "").partition(":")
            if act in ("ok", "no") and did:
                decisions.append((act, did, cq))
            else:
                self.answer(cq["id"], "Lệnh không hợp lệ.")
        return decisions, new_off

    def answer(self, cq_id, text):
        try:
            self.api("answerCallbackQuery", {"callback_query_id": cq_id, "text": text[:190]})
        except TelegramError as e:
            util.log(f"answerCallbackQuery lỗi: {e}")

    def clear_buttons(self, message_id):
        try:
            self.api("editMessageReplyMarkup", {
                "chat_id": self.chat_id, "message_id": message_id,
                "reply_markup": {"inline_keyboard": []}})
        except TelegramError as e:
            util.log(f"editMessageReplyMarkup lỗi: {e}")
