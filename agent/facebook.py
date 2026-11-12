"""Đăng bài lên Facebook Page qua Graph API (ảnh đơn hoặc album)."""
import json

from . import util


class FacebookError(Exception):
    pass


class Facebook:
    def __init__(self, page_id, token, version="v25.0"):
        self.page_id = page_id
        self.token = token
        self.version = version

    @property
    def enabled(self):
        return bool(self.page_id and self.token)

    def _post(self, path, fields, files=None):
        url = f"https://graph.facebook.com/{self.version}/{path}"
        fields = dict(fields, access_token=self.token)   # token nằm trong body, không nằm trong URL
        try:
            if files:
                body, ctype = util.multipart(fields, files)
                r = util.http(url, method="POST", data=body, headers={"Content-Type": ctype},
                              timeout=90, retries=0)
            else:
                from urllib.parse import urlencode
                r = util.http(url, method="POST", data=urlencode(fields).encode(),
                              headers={"Content-Type": "application/x-www-form-urlencoded"},
                              timeout=60, retries=0)
        except util.HttpError as e:
            msg = ""
            try:
                msg = json.loads(e.body.decode("utf-8", "replace")).get("error", {}).get("message", "")
            except Exception:
                pass
            raise FacebookError(f"Graph API lỗi {e.status}: {msg}") from None
        return r.json()

    def publish(self, caption, images):
        """images: list {mime, data}. Trả (post_id, permalink)."""
        if not images:
            raise FacebookError("Không có ảnh")
        if len(images) == 1:
            im = images[0]
            res = self._post(f"{self.page_id}/photos",
                             {"caption": caption, "published": "true"},
                             {"source": ("p." + im["mime"].split("/")[1], im["data"], im["mime"])})
            post_id = res.get("post_id") or res.get("id")
        else:
            ids = []
            for i, im in enumerate(images):
                res = self._post(f"{self.page_id}/photos", {"published": "false"},
                                 {"source": (f"p{i}." + im["mime"].split("/")[1], im["data"], im["mime"])})
                ids.append(res["id"])
            fields = {"message": caption}
            for i, pid in enumerate(ids):
                fields[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
            res = self._post(f"{self.page_id}/feed", fields)
            post_id = res.get("id")
        if not post_id:
            raise FacebookError("Graph API không trả về post id")
        return post_id, f"https://www.facebook.com/{post_id}"

    def check(self):
        """Kiểm tra token + Page ID mà KHÔNG đăng gì. Trả (id, tên Page)."""
        url = f"https://graph.facebook.com/{self.version}/{self.page_id}?fields=id,name"
        try:
            r = util.http(url, headers={"Authorization": f"Bearer {self.token}"}, retries=0)
        except util.HttpError as e:
            msg = ""
            try:
                msg = json.loads(e.body.decode("utf-8", "replace")).get("error", {}).get("message", "")
            except Exception:
                pass
            raise FacebookError(f"Graph API lỗi {e.status}: {msg}") from None
        d = r.json()
        return d.get("id"), d.get("name")


def _main():
    import os
    fb = Facebook(os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_ACCESS_TOKEN"),
                  os.environ.get("FB_GRAPH_VERSION") or "v25.0")
    if not fb.enabled:
        print("Thiếu FB_PAGE_ID hoặc FB_PAGE_ACCESS_TOKEN trong Secrets")
        return 2
    try:
        pid, name = fb.check()
    except FacebookError as e:
        print(f"KHÔNG ĐẠT: {e}")
        return 1
    print(f"ĐẠT: token đọc được Page '{name}' (id={pid}). Chưa đăng bài nào.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
