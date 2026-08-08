from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from meituan_config import MEITUAN_ME_COOKIE
from meituan_page_capture import browser_profile_lock, cookie_entries, page_access_issue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


VIDEO_URL = "https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=videomanage"
VIDEO_TYPES = (
    ("room_type_video", "\u623f\u578b\u89c6\u9891"),
    ("hotel_preview_video", "\u9152\u5e97\u9884\u89c8\u89c6\u9891"),
    ("room_type_preview_video", "\u623f\u578b\u9884\u89c8\u89c6\u9891"),
)
PAGE_WAIT_SECONDS = 12
COOLDOWN_MIN_HOURS = 22.0
COOLDOWN_MAX_HOURS = 26.0


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def schedule_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "state" / "meituan_video_upload_status_schedule.json"


def load_schedule_state() -> dict[str, Any]:
    path = schedule_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_schedule_state(data: dict[str, Any]) -> None:
    path = schedule_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        temporary = Path(file.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_state_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def schedule_next_attempt(attempted_at: datetime) -> datetime:
    cooldown_hours = random.uniform(COOLDOWN_MIN_HOURS, COOLDOWN_MAX_HOURS)
    next_allowed_at = attempted_at + timedelta(hours=cooldown_hours)
    state = load_schedule_state()
    state.update(
        {
            "last_attempt_at": attempted_at.isoformat(timespec="seconds"),
            "next_allowed_at": next_allowed_at.isoformat(timespec="seconds"),
            "cooldown_hours": round(cooldown_hours, 3),
        }
    )
    save_schedule_state(state)
    return next_allowed_at


def mark_schedule_success(success_at: datetime) -> None:
    state = load_schedule_state()
    state["last_success_at"] = success_at.isoformat(timespec="seconds")
    save_schedule_state(state)


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
    with browser_profile_lock(), sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()),
            channel="msedge",
            headless=True,
            chromium_sandbox=True,
            locale="zh-CN",
        )
        try:
            cookies = cookie_entries(MEITUAN_ME_COOKIE, "https://me.meituan.com/")
            if cookies:
                context.add_cookies(cookies)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + PAGE_WAIT_SECONDS
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
    parser = argparse.ArgumentParser(description="Collect Meituan video upload status data")
    parser.add_argument("--force", action="store_true", help="ignore the 22-26 hour automatic cooldown")
    args = parser.parse_args()

    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty")

    now = datetime.now()
    state = load_schedule_state()
    next_allowed_at = parse_state_time(state.get("next_allowed_at"))
    if not args.force and next_allowed_at is not None and now < next_allowed_at:
        print(
            "video upload status skipped: cooldown active; "
            f"next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        return 0

    next_allowed_at = schedule_next_attempt(now)
    mode = "forced" if args.force else "scheduled"
    print(
        f"video upload status attempt mode={mode}; "
        f"next automatic attempt after {next_allowed_at:%Y-%m-%d %H:%M:%S}"
    )

    rows = fetch_video_counts()
    save_video_counts(hotel_id, rows)
    mark_schedule_success(datetime.now())
    print(", ".join(f"{code}={uploaded}/{required}" for code, uploaded, required in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
