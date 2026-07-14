import os


COOKIE = os.environ.get("CTRIP_COOKIE", "").strip()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

DEFAULT_HOTEL_NAME = os.environ.get(
    "CTRIP_HOTEL_NAME",
    "",
).strip()

# 如后续抓包发现携程接口需要额外公共 header，可统一加到这里。
EXTRA_HEADERS = {}
