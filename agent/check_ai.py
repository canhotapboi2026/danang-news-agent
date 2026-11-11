"""Kiểm tra khóa AI và tên model có dùng được không (chỉ tốn 1 yêu cầu nhỏ).

Chạy:  python -m agent.check_ai
"""
import json
import os
import sys

from . import editor, util
from .main import ROOT, load_config


def main():
    cfg = load_config(os.environ.get("AGENT_CONFIG", os.path.join(ROOT, "config.json")))
    env = dict(os.environ)
    provider = cfg["settings"].get("provider", "gemini")
    print(f"Nhà cung cấp AI trong config.json: {provider}")
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "GEMINI_API_KEY"
    if not env.get(key_name):
        print(f"❌ Thiếu secret {key_name}")
        return 1
    if provider == "gemini":
        try:
            r = util.http("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                          headers={"x-goog-api-key": env["GEMINI_API_KEY"]}, retries=1)
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            flash = sorted(n for n in names if "flash" in n)
            print("Các model 'flash' khóa của bạn dùng được:\n  " + "\n  ".join(flash or ["(không thấy)"]))
        except util.HttpError as e:
            print(f"❌ Không liệt kê được model (HTTP {e.status}). Khóa sai hoặc chưa kích hoạt?")
            return 1
    model = (env.get("GEMINI_MODEL") or cfg["settings"].get("model_gemini")) if provider == "gemini" \
        else (env.get("CLAUDE_MODEL") or cfg["settings"].get("model_anthropic"))
    print(f"Thử gọi model: {model}")
    try:
        blocks = [{"type": "text", "text": 'Trả về đúng JSON {"ok": true}'}]
        out = editor.call_llm(cfg, env, "Bạn chỉ trả về JSON.", blocks)
        print("✅ Gọi thành công. Phản hồi:", json.dumps(out[:80], ensure_ascii=False))
        return 0
    except util.HttpError as e:
        hint = {404: "Tên model không tồn tại: chọn một tên trong danh sách trên rồi sửa 'model_gemini' trong config.json",
                429: "Hết hạn mức miễn phí tạm thời, thử lại sau", 400: "Yêu cầu không hợp lệ (xem tên model)",
                403: "Khóa không có quyền hoặc bị chặn ở khu vực này"}.get(e.status, "")
        print(f"❌ Lỗi HTTP {e.status}. {hint}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
