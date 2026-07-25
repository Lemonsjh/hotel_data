from __future__ import annotations

import os
import time
from typing import Any

import requests

from ctrip_config import COOKIE, EXTRA_HEADERS, USER_AGENT


RATING_URL = os.environ.get(
    "CTRIP_GET_HOTEL_RATING_URL",
    "https://ebooking.ctrip.com/restapi/soa2/26353/getHotelRating",
).strip()
COUNT_URL = os.environ.get(
    "CTRIP_GET_COMMENT_NUM_URL",
    "https://ebooking.ctrip.com/restapi/soa2/26353/getCommentNumV2",
).strip()
CHANNELS = (
    ("携程", "trip", "ctripCount"),
    ("去哪儿", "qunar", "qunarCount"),
    ("同程旅行", "elong", "elongCount"),
    ("智行", "zx", "zxCount"),
)


class CtripReviewChannelError(RuntimeError):
    pass


def request_head() -> dict[str, Any]:
    return {
        "host": "ebooking.ctrip.com",
        "pathName": "/comment/commentList",
        "locale": "zh-CN",
        "release": "",
        "client": {
            "deviceType": "PC", "os": "Windows", "osVersion": "Windows 10",
            "deviceName": "Windows PC", "clientId": "hotel-agent",
            "screenWidth": 1536, "screenHeight": 864,
            "isIn": {"ie": False, "chrome": True, "chrome49": False, "wechat": False,
                     "firefox": False, "ios": False, "android": False},
            "isModernBrowser": True, "browser": "Chrome", "browserVersion": "150",
            "platform": "pc", "technology": "web",
        },
        "ubt": {"pageid": "10650085973", "pvid": 1, "sid": 1, "vid": "", "fp": ""},
        "gps": {"coord": "", "lat": "", "lng": "", "cid": 0, "cnm": ""},
        "protocal": "https:",
    }


def request_base() -> dict[str, Any]:
    return {
        "reqHead": request_head(),
        "header": {"platform": "WEB"},
        "head": {"cid": "hotel-agent", "ctok": "", "cver": "1.0", "lang": "01",
                 "sid": "8888", "syscode": "09", "auth": "", "xsid": "", "extension": []},
    }


def rating_payload(channel: str) -> dict[str, Any]:
    payload = request_base()
    payload["channelSource"] = channel
    return payload


def count_payload() -> dict[str, Any]:
    payload = request_base()
    payload["channelSources"] = [channel for _, channel, _ in CHANNELS]
    return payload


def review_session() -> requests.Session:
    if not COOKIE.strip():
        raise CtripReviewChannelError("CTRIP_COOKIE is empty; log in through the control panel first")
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://ebooking.ctrip.com",
        "Referer": "https://ebooking.ctrip.com/comment/commentList?microJump=true",
        "Cookie": COOKIE.strip(),
    })
    session.headers.update(EXTRA_HEADERS or {})
    return session


def post_json(session: requests.Session, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = session.post(url, json=payload, timeout=45)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise CtripReviewChannelError("Ctrip review response is not an object")
            status = data.get("resStatus") or {}
            if status.get("rcode") not in (None, 0, "0", 200, "200"):
                raise CtripReviewChannelError(f"Ctrip review request failed: {status.get('rmsg')}")
            return data
        except (requests.RequestException, ValueError, CtripReviewChannelError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise CtripReviewChannelError(f"Ctrip review request failed after retry: {last_error}")


def parse_counts(payload: dict[str, Any], key: str) -> dict[str, int]:
    values = payload.get(key) or {}
    if not isinstance(values, dict):
        raise CtripReviewChannelError(f"Ctrip review count is missing: {key}")
    return {
        "total_review_count": int(values.get("commentCount") or 0),
        "unreplied_review_count": int(values.get("unReplyCount") or 0),
        "negative_review_count": int(values.get("noRecommendCount") or 0),
    }


def collect_review_channels() -> list[tuple[str, dict[str, Any], dict[str, int]]]:
    session = review_session()
    try:
        counts_payload = post_json(session, COUNT_URL, count_payload())
        results = []
        for source, channel, count_key in CHANNELS:
            rating = post_json(session, RATING_URL, rating_payload(channel))
            if not isinstance(rating.get("ratingInfo"), dict):
                raise CtripReviewChannelError(f"Ctrip review rating is missing: {source}")
            results.append((source, rating, parse_counts(counts_payload, count_key)))
        return results
    finally:
        session.close()
