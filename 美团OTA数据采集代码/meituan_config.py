from __future__ import annotations

import os


HOTEL_NAME = os.environ.get("MEITUAN_HOTEL_NAME", "").strip()
POI_ID = os.environ.get("MEITUAN_POI_ID", "").strip()
PARTNER_ID = os.environ.get("MEITUAN_PARTNER_ID", "").strip()
BIZ_ACCOUNT_ID = os.environ.get("MEITUAN_BIZ_ACCOUNT_ID", "").strip()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

# Optional generic fallback when one browser Cookie works for every Meituan endpoint.
MEITUAN_COOKIE = os.environ.get("MEITUAN_COOKIE", "").strip()

MEITUAN_EB_COOKIE = os.environ.get("MEITUAN_EB_COOKIE", "").strip() or MEITUAN_COOKIE
MEITUAN_ME_COOKIE = os.environ.get("MEITUAN_ME_COOKIE", "").strip() or MEITUAN_COOKIE
