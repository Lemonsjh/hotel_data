from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from ctrip_config import COOKIE, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


TABLE_NAME = "ctrip_ota_video_upload_status"
VIDEO_URL = "https://ebooking.ctrip.com/hotelinfo/ebooking/hotelvideo?microJump=true"
DETAIL_VIDEO_PATH = "/ebkovsproduct/api/video/queryDetailAreaVideo"
LIST_VIDEO_PATH = "/ebkovsproduct/api/video/queryHotelShortVideoList"
ROOM_TYPES_PATH = "/ebkovsproduct/api/video/getBasicRoomByHotelId"
ROOM_VIDEOS_PATH = "/ebkovsproduct/api/video/queryBasicRoomVideoList"
PAGE_WAIT_SECONDS = 20


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def ctrip_cookies() -> list[dict[str, Any]]:
    if not COOKIE:
        raise RuntimeError("CTRIP_COOKIE is empty; log in through the control panel first")
    cookies = []
    for part in COOKIE.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name and value:
            cookies.append({"name": name, "value": value, "url": "https://ebooking.ctrip.com/"})
    if not cookies:
        raise RuntimeError("CTRIP_COOKIE does not contain valid cookies")
    return cookies


def ensure_logged_in(page: Any) -> None:
    url = page.url.lower()
    if any(marker in url for marker in ("/login", "passport", "security", "verify")):
        raise RuntimeError(f"Ctrip login session is invalid or requires verification: {page.url}")
    if page.locator('input[type="password"]').count():
        raise RuntimeError("Ctrip login session is invalid or requires verification")


def valid_data(payload: Any, label: str) -> Any:
    if not isinstance(payload, dict) or payload.get("code") != 200 or "data" not in payload:
        raise RuntimeError(f"Ctrip {label} response is invalid")
    return payload["data"]


def video_rows_from_payloads(payloads: dict[str, Any]) -> list[tuple[str, int, int]]:
    detail = valid_data(payloads.get(DETAIL_VIDEO_PATH), "detail-video")
    listing = valid_data(payloads.get(LIST_VIDEO_PATH), "listing-video")
    room_types = valid_data(payloads.get(ROOM_TYPES_PATH), "room-types")
    room_videos = valid_data(payloads.get(ROOM_VIDEOS_PATH), "room-video")
    if not isinstance(detail, dict) or not isinstance(listing, dict):
        raise RuntimeError("Ctrip hotel-video response has invalid data")
    if not isinstance(room_types, list) or not isinstance(room_videos, list):
        raise RuntimeError("Ctrip room-video response has invalid data")

    room_uploaded = sum(
        1 for row in room_videos if isinstance(row, dict) and row.get("current")
    )
    return [
        ("hotel_detail_video", int(bool(detail.get("mainVideo"))), 1),
        ("room_type_video", room_uploaded, len(room_types)),
        ("hotel_listing_video", int(bool(listing.get("current"))), 1),
    ]


def fetch_video_counts() -> list[tuple[str, int, int]]:
    payloads: dict[str, Any] = {}
    expected_paths = {DETAIL_VIDEO_PATH, LIST_VIDEO_PATH, ROOM_TYPES_PATH, ROOM_VIDEOS_PATH}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(
                locale="zh-CN", user_agent=USER_AGENT, viewport={"width": 1920, "height": 1080}
            )
            context.add_cookies(ctrip_cookies())
            page = context.new_page()

            def on_response(response: Any) -> None:
                path = urlparse(response.url).path
                if path not in expected_paths or response.status != 200:
                    return
                try:
                    payloads[path] = response.json()
                except Exception:
                    return

            page.on("response", on_response)
            try:
                page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=60_000)
                ensure_logged_in(page)
                deadline = time.monotonic() + PAGE_WAIT_SECONDS
                while time.monotonic() < deadline:
                    if expected_paths.issubset(payloads):
                        return video_rows_from_payloads(payloads)
                    page.wait_for_timeout(500)
            finally:
                page.remove_listener("response", on_response)
        finally:
            browser.close()
    missing = ", ".join(sorted(path.rsplit("/", 1)[-1] for path in expected_paths - payloads.keys()))
    raise RuntimeError(f"Ctrip video page did not return required APIs: {missing}")


def save_video_counts(hotel_id: str, rows: list[tuple[str, int, int]]) -> None:
    import pymysql

    snapshot_time = datetime.now()
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                f"""INSERT INTO {TABLE_NAME}
                   (hotel_id, video_type, uploaded_count, required_count, status, snapshot_time)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE uploaded_count=VALUES(uploaded_count),
                   required_count=VALUES(required_count), status=VALUES(status),
                   snapshot_time=VALUES(snapshot_time)""",
                [
                    (hotel_id, code, uploaded, required,
                     "COMPLETE" if uploaded >= required else "INCOMPLETE", snapshot_time)
                    for code, uploaded, required in rows
                ],
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    hotel_id = require_hotel_id()
    rows = fetch_video_counts()
    save_video_counts(hotel_id, rows)
    print(", ".join(f"{code}={uploaded}/{required}" for code, uploaded, required in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
