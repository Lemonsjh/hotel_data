from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from meituan_page_capture import page_access_issue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


VIDEO_URL = "https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=videomanage"
VIDEO_TYPES = (
    ("room_type_video", "\u623f\u578b\u89c6\u9891"),
    ("hotel_preview_video", "\u9152\u5e97\u9884\u89c8\u89c6\u9891"),
    ("room_type_preview_video", "\u623f\u578b\u9884\u89c8\u89c6\u9891"),
)


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def page_text(page: object) -> str:
    parts = []
    for frame in page.frames:
        try:
            parts.append(frame.locator("body").inner_text(timeout=1_000))
        except Exception:
            continue
    return "\n".join(parts)


def extract_video_counts(text: str) -> list[tuple[str, int, int]]:
    rows = []
    for code, label in VIDEO_TYPES:
        match = re.search(rf"{re.escape(label)}\s*(\d+)\s*/\s*(\d+)", text)
        if match:
            rows.append((code, int(match.group(1)), int(match.group(2))))
    return rows


def fetch_video_counts() -> list[tuple[str, int, int]]:
    last_url = VIDEO_URL
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()),
            channel="msedge",
            headless=True,
            chromium_sandbox=True,
            locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                last_url = page.url
                rows = extract_video_counts(page_text(page))
                if len(rows) == len(VIDEO_TYPES):
                    return rows
                if issue := page_access_issue(page):
                    raise RuntimeError(f"Video management page requires manual action: {issue}")
                page.wait_for_timeout(500)
        finally:
            context.close()
    raise RuntimeError(f"Video management page did not return all upload counts: url={last_url}")


def save_video_counts(hotel_id: str, rows: list[tuple[str, int, int]]) -> None:
    import pymysql

    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO meituan_ota_video_upload_status
                   (hotel_id, video_type, uploaded_count, required_count, status)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE uploaded_count=VALUES(uploaded_count),
                   required_count=VALUES(required_count), status=VALUES(status)""",
                [
                    (hotel_id, code, uploaded, required, "COMPLETE" if uploaded >= required else "INCOMPLETE")
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
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty")
    rows = fetch_video_counts()
    save_video_counts(hotel_id, rows)
    print(", ".join(f"{code}={uploaded}/{required}" for code, uploaded, required in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
