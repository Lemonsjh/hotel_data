from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from meituan_config import MEITUAN_ME_COOKIE
from meituan_page_capture import browser_profile_lock, cookie_entries, page_access_issue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


VIDEO_URL = "https://me.meituan.com/ebooking/merchant/i/hasVpoiSelect?biz=universal&page=videomanage"
VIDEO_TASK_PATH = "/gw/tdc/hubble/eb/hotel/video/poi/video/task"
PAGE_WAIT_SECONDS = 20
COOLDOWN_MIN_HOURS = 22.0
COOLDOWN_MAX_HOURS = 26.0
COOLDOWN_REASONS = {"success", "failure"}
LEGACY_VIDEO_TYPES = ("hotel_preview_video", "room_type_preview_video")


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


def mark_attempt_started(attempted_at: datetime) -> None:
    state = load_schedule_state()
    state["last_attempt_at"] = attempted_at.isoformat(timespec="seconds")
    save_schedule_state(state)


def schedule_cooldown(completed_at: datetime, reason: str) -> datetime:
    if reason not in COOLDOWN_REASONS:
        raise ValueError(f"unsupported cooldown reason: {reason}")
    cooldown_hours = random.uniform(COOLDOWN_MIN_HOURS, COOLDOWN_MAX_HOURS)
    next_allowed_at = completed_at + timedelta(hours=cooldown_hours)
    state = load_schedule_state()
    state.update(
        {
            "last_result_at": completed_at.isoformat(timespec="seconds"),
            "cooldown_reason": reason,
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


def is_video_task_response(response: Any) -> bool:
    try:
        return response.request.method == "GET" and urlparse(response.url).path == VIDEO_TASK_PATH
    except Exception:
        return False


def _payload_int(data: dict[str, Any], field: str) -> int:
    if field not in data:
        raise RuntimeError(f"Meituan video task response missing field: {field}")
    try:
        value = int(data[field])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Meituan video task field is not an integer: {field}") from exc
    if value < 0:
        raise RuntimeError(f"Meituan video task field is negative: {field}")
    return value


def video_rows_from_payload(payload: Any) -> list[tuple[str, int, int]]:
    if not isinstance(payload, dict) or payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError("Meituan video task response is invalid")

    data = payload["data"]
    has_hotel_official = _payload_int(data, "hasHotelOfficial")
    has_hotel_official_preview = _payload_int(data, "hasHotelOfficialPreview")
    room_video_count = _payload_int(data, "hasRoomVideoOnlineRealRoomNum")
    online_room_count = _payload_int(data, "onlineRealRoomNum")

    if has_hotel_official not in (0, 1):
        raise RuntimeError("Meituan video task hasHotelOfficial must be 0 or 1")
    if has_hotel_official_preview not in (0, 1):
        raise RuntimeError("Meituan video task hasHotelOfficialPreview must be 0 or 1")

    return [
        ("hotel_official_video", has_hotel_official, 1),
        ("hotel_official_preview_video", has_hotel_official_preview, 1),
        ("room_type_video", room_video_count, online_room_count),
    ]


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
            responses: list[Any] = []

            def on_response(response: Any) -> None:
                if is_video_task_response(response):
                    responses.append(response)

            page.on("response", on_response)
            try:
                page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=60_000)
                deadline = time.monotonic() + PAGE_WAIT_SECONDS
                while time.monotonic() < deadline:
                    last_url = page.url
                    if responses:
                        response = responses[-1]
                        if response.status != 200:
                            raise RuntimeError(f"Meituan video task API failed: HTTP {response.status}")
                        return video_rows_from_payload(response.json())
                    if issue := page_access_issue(page):
                        raise RuntimeError(f"Video management page requires manual action: {issue}")
                    page.wait_for_timeout(500)
            finally:
                page.remove_listener("response", on_response)
        finally:
            context.close()
    raise RuntimeError(f"Video management page did not return the video task API response: url={last_url}")


def save_video_counts(hotel_id: str, rows: list[tuple[str, int, int]]) -> None:
    import pymysql

    snapshot_time = datetime.now()
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """DELETE FROM meituan_ota_video_upload_status
                   WHERE hotel_id=%s AND video_type IN (%s, %s)""",
                (hotel_id, *LEGACY_VIDEO_TYPES),
            )
            cursor.executemany(
                """INSERT INTO meituan_ota_video_upload_status
                   (hotel_id, video_type, uploaded_count, required_count, status, snapshot_time)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE uploaded_count=VALUES(uploaded_count),
                   required_count=VALUES(required_count), status=VALUES(status),
                   snapshot_time=VALUES(snapshot_time)""",
                [
                    (hotel_id, code, uploaded, required, "COMPLETE" if uploaded >= required else "INCOMPLETE", snapshot_time)
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
    cooldown_reason = str(state.get("cooldown_reason") or "")
    if (
        not args.force
        and cooldown_reason in COOLDOWN_REASONS
        and next_allowed_at is not None
        and now < next_allowed_at
    ):
        print(
            "video upload status skipped: cooldown active; "
            f"reason={cooldown_reason}; next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        return 0

    mode = "forced" if args.force else "scheduled"
    mark_attempt_started(now)
    print(f"video upload status attempt mode={mode}")

    try:
        rows = fetch_video_counts()
        save_video_counts(hotel_id, rows)
    except Exception:
        failed_at = datetime.now()
        next_allowed_at = schedule_cooldown(failed_at, "failure")
        print(
            "video upload status failed; cooldown scheduled; "
            f"next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        raise

    success_at = datetime.now()
    next_allowed_at = schedule_cooldown(success_at, "success")
    mark_schedule_success(success_at)
    print(", ".join(f"{code}={uploaded}/{required}" for code, uploaded, required in rows))
    print(f"video upload status next automatic attempt after {next_allowed_at:%Y-%m-%d %H:%M:%S}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
