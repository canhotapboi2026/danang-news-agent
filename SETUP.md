# Agent tin tức Đà Nẵng: hướng dẫn cài đặt

Agent làm việc này, tự động, theo chu kỳ:

```
Quét RSS báo + Google News → lấy bài + ảnh → chống trùng → Claude viết lại theo giọng Page
→ kiểm tra tự động (số liệu, sao chép, chính sách, đủ caption + ảnh)
→ gửi bản xem trước tới Telegram (nút ✅ Đăng / ❌ Bỏ) → đăng Page → gửi bản dán Group
```

**Chế độ mặc định là `approve`** (bạn bấm ✅ mới đăng). Chưa nối Facebook thì agent chạy **DRY-RUN**:
vẫn gửi bản xem trước và "đăng giả", không đăng gì thật. Nên chạy ở chế độ này vài ngày trước.

> **Quy tắc bảo mật:** token/API key chỉ nhập vào ô *Secrets* của GitHub (bước 3).
> Không dán vào chat, không ghi vào file trong repo.

---

## Bước 1. Tạo bot Telegram (≈ 5 phút)

1. Mở Telegram, tìm **@BotFather**, gửi `/newbot`, đặt tên và username (phải kết thúc bằng `bot`).
2. BotFather trả về **token** (dạng `123456789:AA...`). Giữ kín. Đây là `TELEGRAM_BOT_TOKEN`.
3. Mở bot vừa tạo, bấm **Start**, gửi chữ `hi`.
4. Trên trình duyệt, mở địa chỉ sau (tự thay `TOKEN` bằng token của bạn):
   `https://api.telegram.org/botTOKEN/getUpdates`
5. Tìm đoạn `"chat":{"id":123456789` → số đó là `TELEGRAM_CHAT_ID`.

**Xong khi:** bạn có 2 giá trị: token và chat id.

## Bước 2. Tạo khóa AI MIỄN PHÍ của Google Gemini (≈ 5 phút, không cần thẻ)

1. Vào **aistudio.google.com**, đăng nhập bằng tài khoản Google.
2. Bấm **Get API key** → **Create API key** → copy khóa (đây là `GEMINI_API_KEY`). Lưu vào ghi chú riêng.
3. Gói miễn phí có giới hạn số yêu cầu mỗi phút/ngày; con số của bạn xem trong AI Studio (mục rate limit).
   Agent đã tự giãn cách 7 giây giữa các lần gọi để không vượt giới hạn.
4. Lưu ý: ở gói miễn phí, Google cho biết nội dung gửi lên có thể được dùng để cải thiện sản phẩm của họ.
   Với tin báo công khai thì không sao; đừng gửi nội dung riêng tư.

> Muốn dùng Claude sau này (trả phí): đổi `"provider": "anthropic"` trong `config.json` và thêm secret
> `ANTHROPIC_API_KEY`. Không cần sửa code.

## Bước 3. Đưa code lên GitHub (≈ 15 phút)

1. Đăng nhập github.com → **New repository** → tên `danang-news-agent`.
   - **Đã chọn: Public** (không giới hạn phút, chạy **15 phút/lần**). Ai cũng đọc được file cấu hình và
     `state.json` (các bản nháp chờ duyệt, link bài). Khóa/secret thì KHÔNG bị lộ.
   - (Nếu sau này muốn Private: miễn phí 2.000 phút/tháng, phải đổi chu kỳ thành 30 phút/lần.)
   - Repo public: nếu 60 ngày không có hoạt động, GitHub có thể tự tắt lịch chạy; khi đó vào tab Actions bấm Enable.
2. Trong repo: **Add file → Upload files**. Giải nén file zip rồi kéo thả: thư mục `agent`, `tests`,
   và các file `config.json`, `state.json`, `SETUP.md`, `.gitignore` → **Commit changes**.
3. Tạo 3 file workflow (vì thư mục ẩn `.github` khó kéo thả): **Add file → Create new file**:
   - Tên file: `.github/workflows/agent.yml` → dán nội dung file `workflows_copy/agent.yml` → Commit.
   - Tên file: `.github/workflows/check_sources.yml` → dán nội dung `workflows_copy/check_sources.yml` → Commit.
   - Tên file: `.github/workflows/check_ai.yml` → dán nội dung `workflows_copy/check_ai.yml` → Commit.
4. Thêm **Secrets**: *Settings → Secrets and variables → Actions → New repository secret*:

   | Tên | Giá trị |
   |---|---|
   | `GEMINI_API_KEY` | khóa ở bước 2 |
   | `TELEGRAM_BOT_TOKEN` | token ở bước 1 |
   | `TELEGRAM_CHAT_ID` | chat id ở bước 1 |
   | `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN` | **để trống đến khi làm bước 6** |

**Xong khi:** thấy 3 workflow trong tab **Actions**.

## Bước 4a. Kiểm tra khóa AI

*Actions → "Kiểm tra khóa AI" → Run workflow →* mở log. Kỳ vọng: danh sách model "flash" và dòng
"✅ Gọi thành công". Nếu báo "Tên model không tồn tại", chọn một tên trong danh sách rồi sửa `model_gemini`
trong `config.json`.

## Bước 4b. Kiểm tra các nguồn tin

*Actions → "Kiểm tra nguồn RSS" → Run workflow →* bấm vào lần chạy → mở bước "Kiểm tra từng nguồn".

- ✅ = đọc được. ❌ = lỗi: mở trang chủ báo đó, tìm biểu tượng RSS, lấy địa chỉ RSS đúng, sửa trong `config.json`
  (bấm biểu tượng bút chì để sửa trực tiếp trên GitHub) hoặc đổi `"enabled": false`.
- Dòng Google News cho biết **giải link Google News có thành công không**. Nếu THẤT BẠI thì tin từ Google News
  bị bỏ qua (các nguồn RSS trực tiếp vẫn chạy bình thường).
- Báo Đà Nẵng: điền URL RSS vào `config.json` rồi đổi `"enabled": true`.

**Xong khi:** ít nhất 3 nguồn ✅ và có bài mẫu "có ảnh".

## Bước 5. Chạy thử agent (DRY-RUN)

*Actions → "Agent tin tức Đà Nẵng" → Run workflow* (ô `dry_run` nhập `1`).

Kỳ vọng: vài phút sau Telegram nhận **ảnh + caption + nút ✅/❌**. Bấm ✅ ở lần chạy kế tiếp sẽ có tin
"DRY-RUN … KHÔNG đăng thật". Nếu không có gì: mở log của lần chạy, dòng cuối "Tóm tắt" cho biết
bao nhiêu tin bị loại và vì sao (`off_topic`, `duplicates`, `no_image`, `too_short`…).

> **Lưu ý:** nút bấm chỉ có hiệu lực khi agent chạy lại (15 phút/lần). Muốn bấm là đăng
> gần như ngay, chạy agent liên tục trên một máy luôn bật: `python -m agent.main --loop 15`
> (cần Python 3.10+, đặt đủ biến môi trường như bảng secrets).

**Tiêu chí hoàn thành giai đoạn này (đo trong 3 ngày):**
- ≥ 5 bản xem trước/ngày, 100% có caption + ít nhất 1 ảnh.
- 0 cặp tin trùng nội dung được gửi cho bạn.
- Mọi con số trong caption đều có trong bài gốc (agent tự kiểm; bài lệch sẽ có cảnh báo ⚠️).

## Bước 6. Nối Facebook Page (làm sau, nhắn tôi để được hướng dẫn từng cú nhấp)

Cần: Meta Developer App, quyền `pages_manage_posts`, Page Access Token, `FB_PAGE_ID`. Vì Page do bạn
quản lý, thường không cần App Review (cần thử thực tế). Sau khi điền 2 secret FB, agent tự thoát DRY-RUN.

## Bước 7. Chuyển sang tự động (sau ≥ 2 tuần chạy duyệt tay)

Sửa `config.json`: `"publish_mode": "auto"`. Ở chế độ này agent chỉ tự đăng khi **tất cả** đúng:
nguồn có `"auto_ok": true`, không có cảnh báo ⚠️, đủ caption + ảnh, không vượt giới hạn
(10 bài/ngày, cách nhau ≥ 20 phút, tin KHẨN được ưu tiên). Còn lại vẫn gửi bạn duyệt.

---

## Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách kiểm tra / sửa |
|---|---|---|
| Workflow báo "Thiếu biến môi trường" | Chưa thêm Secret hoặc gõ sai tên | Settings → Secrets, kiểm tra đúng 3 tên ở bước 3 |
| Telegram không nhận gì | Chưa bấm Start với bot; sai chat id | Làm lại bước 1.3–1.5 |
| `sources_error` > 0 | URL RSS sai hoặc báo chặn | Chạy "Kiểm tra nguồn RSS" |
| `no_image` cao | Báo chặn tải ảnh hoặc bài không có ảnh | Bình thường với một số báo; bài thiếu ảnh bị bỏ theo quy tắc "đủ combo" |
| `unresolved` cao | Giải link Google News thất bại | Dựa vào nguồn RSS trực tiếp; báo lại cho tôi log |
| Đăng Facebook lỗi | Token hết hạn / thiếu quyền | Tin nhắn lỗi của bot nêu lý do; tạo lại token |
| Tin bị gửi trùng | `state.json` không được lưu | Xem bước "Lưu trạng thái" trong log; workflow cần quyền `contents: write` |

## Giới hạn đã biết (nói thẳng)

- **Chưa chạy thử với Internet thật** (môi trường tôi xây dựng bị chặn mạng). Đã kiểm thử 26 tình huống
  bằng dữ liệu giả; lần chạy thật đầu tiên có thể lộ lỗi, gửi log cho tôi để sửa.
- **Tên model Gemini mặc định (`gemini-3.8-flash`) lấy từ tài liệu bên thứ ba, chưa kiểm chứng.** Bước 4a sẽ cho bạn danh sách tên thật.
- Chất lượng tiếng Việt của Gemini với giọng Page của bạn chưa kiểm chứng; chạy DRY-RUN vài ngày để đánh giá.
- URL RSS các báo lấy theo địa chỉ thường gặp, **chưa kiểm chứng**. Dùng bước 4 để xác nhận.
- Chưa có nút ✏️ Sửa trên Telegram (sẽ làm ở giai đoạn sau). Hiện chỉ Đăng/Bỏ.
- Group: Meta không cho đăng tự động. Agent gửi sẵn bản để bạn dán.
- GitHub Actions có thể trễ vài phút so với giờ hẹn.
- **Bản quyền:** một số báo ghi rõ chỉ cho phát hành lại khi có đồng ý bằng văn bản. Agent viết lại bằng lời mới
  (có kiểm tra không sao chép quá 12 từ liên tiếp) và luôn ghi nguồn, nhưng **ảnh vẫn là ảnh của báo**.
  Với nguồn quan trọng, nên xin phép hoặc dùng ảnh riêng.
- Từ khóa lọc trong `config.json` hiện tập trung quận/huyện Đà Nẵng cũ. Nếu Page của bạn cũng đưa tin khu vực
  Quảng Nam cũ nay thuộc Đà Nẵng (Hội An, Tam Kỳ, Điện Bàn…), hãy thêm vào mục `keywords`.
