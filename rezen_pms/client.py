"""Rezen One 只读 JSON 接口。异常里不带响应正文或会话。"""
from __future__ import annotations

from urllib.parse import urlsplit

import requests


DEFAULT_BASE = "https://one.rezen.tech"
FORECAST_PATH = "/hotel_api/hotel/rsvaccount/querySalesForecastData"
MERGE_PATH = "/hotel_api/merge/merge"
CHART_DATA = "/hotel_api/room/getRoomChartData"
CHART_AVAILABLE = "/hotel_api/room/queryRoomChartAvailable"


def origin_of(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "rezen.tech" or host.endswith(".rezen.tech")):
        raise ValueError("Rezen 接口地址无效")
    return f"https://{parsed.netloc}"


def post_json(base_url: str, session_token: str, path: str, payload: dict, timeout: int = 30,
             cookie: str = "") -> dict:
    origin = origin_of(base_url)
    headers = {"Accept": "application/json", "Origin": origin, "Referer": origin + "/",
               "rezensid": session_token, "x-rezenone-sessionid": session_token}
    if cookie:
        headers["Cookie"] = cookie
    try:
        response = requests.post(
            origin + path, json=payload, timeout=timeout, allow_redirects=False, headers=headers)
    except requests.RequestException:
        raise RuntimeError("Rezen 请求失败，请检查网络和登录状态") from None
    if response.status_code != 200:
        raise RuntimeError(f"Rezen 接口 HTTP {response.status_code}，请检查登录状态")
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Rezen 接口未返回有效成功响应，请检查登录状态") from None
    code = data.get("retcode") if isinstance(data, dict) else None
    if not isinstance(data, dict) or code != 0:
        detail = str(code) if isinstance(code, int) else "异常"
        raise RuntimeError(f"Rezen 接口业务状态 {detail}，请检查登录状态")
    return data


def _part(merged: dict, path: str) -> dict:
    body = merged.get("body")
    part = body.get(path) if isinstance(body, dict) else None
    if not isinstance(part, dict) or part.get("retcode") != 0 or not isinstance(part.get("body"), dict):
        raise RuntimeError("Rezen 房态接口业务状态异常，请检查登录状态")
    return part["body"]


def fetch_room_chart(base_url: str, session_token: str, timeout: int = 30, cookie: str = "") -> tuple[dict, dict]:
    merged = post_json(base_url, session_token, MERGE_PATH,
                        {CHART_DATA: {}, CHART_AVAILABLE: {}}, timeout, cookie)
    return _part(merged, CHART_AVAILABLE), _part(merged, CHART_DATA)


def fetch_room_type_forecast(base_url: str, session_token: str, start_date: str,
                             view_days: int, include_tax: str, timeout: int = 30,
                             cookie: str = "") -> dict:
    return post_json(base_url, session_token, FORECAST_PATH, {
        "startDate": start_date, "viewDays": view_days,
        "isIncludeTax": include_tax, "salesForecastType": "ROOMTYP",
    }, timeout, cookie)
