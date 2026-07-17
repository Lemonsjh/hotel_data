# -*- coding: utf-8 -*-
"""PMS 地址和超时配置，环境变量优先。"""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def _url(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip().rstrip("/")


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


LOGIN_BASE_URL = _url("PMS_LOGIN_BASE_URL", "https://xingfeng.beyondh.com:8101")
REPORT_BASE_URL = _url("PMS_REPORT_BASE_URL", "https://xingfeng.beyondh.com:8081")
SERVICE_API_BASE_URL = _url("PMS_SERVICE_API_BASE_URL", "https://xingfeng.beyondh.com:8077")
FORECAST_API_BASE_URL = _url("PMS_FORECAST_API_BASE_URL", "https://xingfeng.beyondh.com:8111")

LOGIN_URL = f"{LOGIN_BASE_URL}/login"
NAVIGATION_TIMEOUT_MS = _positive_int("PMS_NAVIGATION_TIMEOUT_MS", 60_000)
ACTION_TIMEOUT_MS = _positive_int("PMS_ACTION_TIMEOUT_MS", 15_000)
API_TIMEOUT_SECONDS = _positive_int("PMS_API_TIMEOUT_SECONDS", 30)


def report_url(path: str = "") -> str:
    return f"{REPORT_BASE_URL}/{path.lstrip('/')}" if path else f"{REPORT_BASE_URL}/"


def origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"
