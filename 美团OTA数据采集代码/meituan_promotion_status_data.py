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
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

from meituan_config import MEITUAN_EB_COOKIE, MEITUAN_ME_COOKIE, PARTNER_ID, POI_ID, USER_AGENT
from meituan_goods_price_mapping import MeituanGoodsClient
from meituan_page_capture import browser_profile_lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG, OUTPUT_DIR


STATUS_URL = "https://eb.meituan.com/api/v1/vip/member/platformMember/statusAndRight"
PROMOTION_CODE = "youmeihui"
PROMOTION_NAME = "\u4f18\u7f8e\u4f1a"
BUSINESS_TRAVEL_CODE = "business_travel_price"
BUSINESS_TRAVEL_NAME = "\u5546\u65c5\u4e13\u4eab\u4ef7"
BUSINESS_TRAVEL_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Fpromotion-new%2Findex.html%23%2Fbusiness-hotel%2Fhome"
)
HOURLY_ROOM_CODE = "hourly_room"
HOURLY_ROOM_NAME = "\u949f\u70b9\u623f"
HIGHLIGHTS_CODE = "hotel_highlights"
HIGHLIGHTS_NAME = "\u9152\u5e97\u4eae\u70b9"
WORKBENCH_URL = "https://eb.meituan.com/ebooking/new-workbench/index.html"
HIGHLIGHTS_EMPTY_TEXT = "\u5f53\u524d\u9152\u5e97\u6682\u65e0\u7279\u8272\u4eae\u70b9\u6570\u636e"
AUTO_ORDER_CODE = "auto_order_acceptance"
AUTO_ORDER_NAME = "\u81ea\u52a8\u63a5\u5355"
AUTO_ORDER_URL = (
    "https://me.meituan.com/ebooking/merchant/ebIframe?"
    "iUrl=%2Febooking%2Forder%2Findex.html%23%2Fauto"
)
PUBLIC_WELFARE_CODE = "public_welfare_traffic"
PUBLIC_WELFARE_NAME = "\u516c\u76ca\u6d41\u91cf"
PUBLIC_WELFARE_ACTIVE = "\u751f\u6548\u4e2d"
SCHEDULED_INVOICE_CODE = "reservation_invoice"
SCHEDULED_INVOICE_NAME = "\u9884\u7ea6\u53d1\u7968"
SCHEDULED_INVOICE_URL = "https://me.meituan.com/ebooking/merchant/ebIframe?iUrl=%2Febk%2Fhotel%2Fhotelinfo.html%23%2F"
PAGE_CHECK_SECONDS = 10
PAGE_ACTION_TIMEOUT_MS = 5_000
WORKBENCH_MENU_TIMEOUT_MS = 20_000
COOLDOWN_MIN_HOURS = 10.0
COOLDOWN_MAX_HOURS = 14.0
COOLDOWN_REASONS = {"success", "failure"}


def schedule_state_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "state" / "meituan_promotion_status_schedule.json"


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


def fetch_youmeihui_status() -> str:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("MEITUAN_EB_COOKIE is empty")
    response = requests.get(
        STATUS_URL,
        params={
            "poiId": POI_ID,
            "partnerId": PARTNER_ID,
            "yodaReady": "h5",
            "csecplatform": "4",
            "csecversion": "4.2.4",
        },
        headers={"Cookie": MEITUAN_EB_COOKIE, "User-Agent": USER_AGENT, "Referer": "https://eb.meituan.com/"},
        timeout=30,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    if payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"Youmeihui API failed: {payload.get('message') or payload.get('status')}")
    return "OPEN" if payload["data"].get("activeStatus") == 1 else "CLOSED"


def browser_cookies() -> list[dict[str, str]]:
    entries = []
    for header, url in (
        (MEITUAN_EB_COOKIE, "https://eb.meituan.com/"),
        (MEITUAN_ME_COOKIE, "https://me.meituan.com/"),
        (MEITUAN_EB_COOKIE, "https://epassport.meituan.com/"),
        (MEITUAN_ME_COOKIE, "https://epassport.meituan.com/"),
    ):
        for part in header.split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                entries.append({"name": name, "value": value, "url": url})
    return entries


def fetch_business_travel_status() -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(locale="zh-CN")
            context.add_cookies(browser_cookies())
            page = context.new_page()
            page.goto(BUSINESS_TRAVEL_URL, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(PAGE_CHECK_SECONDS * 2):
                for frame in page.frames:
                    try:
                        if "\u5df2\u52a0\u5165\u5546\u65c5\u5408\u4f5c" in frame.locator("body").inner_text(timeout=2_000):
                            return "OPEN"
                    except Exception:
                        continue
                page.wait_for_timeout(500)
        finally:
            browser.close()
    raise RuntimeError("Business travel page did not return a recognized status")


def fetch_hourly_room_status() -> str:
    client = MeituanGoodsClient(MEITUAN_ME_COOKIE)
    goods_data = client.query_goods()
    business_date = datetime.now().strftime("%Y-%m-%d")
    status_rows = client.query_price_status(goods_data, "", business_date)
    for item in status_rows:
        goods = item.get("goodsBaseInfo") or {}
        if not re.search(r"-[0-9]+(?:[.][0-9]+)?\u5c0f\u65f6-", str(goods.get("goodsName") or "")):
            continue
        status = (item.get("goodsStatusMap") or {}).get(business_date) or {}
        if status.get("fullRoomCode") in (0, "0", None) and not status.get("fullRoomDesc"):
            return "OPEN"
    return "CLOSED"


def find_visible_text(page: Any, text: str):
    locators = page.get_by_text(text, exact=True)
    for index in range(locators.count()):
        locator = locators.nth(index)
        try:
            if locator.is_visible(timeout=500):
                return locator
        except Exception:
            continue
    return None


def wait_for_visible_text(page: Any, text: str, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        locator = find_visible_text(page, text)
        if locator:
            return locator
        page.wait_for_timeout(250)
    raise RuntimeError(f"Visible workbench menu was not found: {text}")


def wait_for_workbench_wrapper(page: Any, timeout_ms: int = WORKBENCH_MENU_TIMEOUT_MS) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if urlparse(page.url).hostname == "me.meituan.com":
            return
        page.wait_for_timeout(250)
    raise RuntimeError("Meituan workbench wrapper did not finish loading")


def click_workbench_menu(page: Any, parent_name: str, item_name: str) -> None:
    parent = wait_for_visible_text(page, parent_name, WORKBENCH_MENU_TIMEOUT_MS)
    item = find_visible_text(page, item_name)
    if not item:
        parent.click(force=True, timeout=PAGE_ACTION_TIMEOUT_MS)
        item = wait_for_visible_text(page, item_name, WORKBENCH_MENU_TIMEOUT_MS)
    item.click(force=True, timeout=PAGE_ACTION_TIMEOUT_MS)


def fetch_hotel_highlights_status() -> str:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    profile = local_app_data / "HotelAgent" / "browser_profiles" / "meituan"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="msedge",
            headless=True,
            chromium_sandbox=True,
            locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(WORKBENCH_URL, wait_until="domcontentloaded", timeout=60_000)
            wait_for_workbench_wrapper(page)
            click_workbench_menu(page, "\u4fe1\u606f\u7ba1\u7406", HIGHLIGHTS_NAME)
            page.wait_for_timeout(1_000)
            for _ in range(PAGE_CHECK_SECONDS * 2):
                for frame in page.frames:
                    try:
                        if frame.locator(".list-empty").count():
                            return "CLOSED"
                        if HIGHLIGHTS_EMPTY_TEXT in frame.locator("body").inner_text(timeout=1_000):
                            return "CLOSED"
                    except Exception:
                        continue
                if "hasVpoiSelect" in page.url:
                    return "OPEN"
                page.wait_for_timeout(500)
        finally:
            context.close()
    raise RuntimeError("Hotel highlights page did not return a recognized status")


def fetch_auto_order_status() -> str:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    profile = local_app_data / "HotelAgent" / "browser_profiles" / "meituan"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="msedge",
            headless=True,
            chromium_sandbox=True,
            locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(AUTO_ORDER_URL, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(PAGE_CHECK_SECONDS * 2):
                for frame in page.frames:
                    try:
                        open_radio = frame.locator('input#status-open[value="1"]')
                        closed_radio = frame.locator('input#status-shut[value="0"]')
                        if open_radio.count() and open_radio.first.is_checked(timeout=1_000):
                            return "OPEN"
                        if closed_radio.count() and closed_radio.first.is_checked(timeout=1_000):
                            return "CLOSED"
                    except Exception:
                        continue
                page.wait_for_timeout(500)
        finally:
            context.close()
    raise RuntimeError("Auto order page did not return a recognized status")


def fetch_public_welfare_status() -> str:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    profile = local_app_data / "HotelAgent" / "browser_profiles" / "meituan"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="msedge", headless=True,
            chromium_sandbox=True, locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(WORKBENCH_URL, wait_until="domcontentloaded", timeout=60_000)
            wait_for_workbench_wrapper(page)
            click_workbench_menu(page, "\u4fc3\u9500\u63a8\u5e7f", PUBLIC_WELFARE_NAME)
            for _ in range(PAGE_CHECK_SECONDS * 2):
                for frame in page.frames:
                    if urlparse(frame.url).hostname != "gongyi.meituan.com":
                        continue
                    try:
                        states = frame.locator("span.benefits-color-desc").all_text_contents()
                        body = frame.locator("body").inner_text(timeout=1_000)
                        if PUBLIC_WELFARE_ACTIVE in states or "权益生效中" in body:
                            return "OPEN"
                        if "\u672a\u751f\u6548" in body or "\u5df2\u5931\u6548" in body:
                            return "CLOSED"
                    except Exception:
                        continue
                page.wait_for_timeout(500)
        finally:
            context.close()
    raise RuntimeError("Public welfare page did not return a recognized status")


def scheduled_invoice_is_closed(page: Any) -> bool:
    for frame in page.frames:
        try:
            text = frame.locator("body").inner_text(timeout=1_000)
        except Exception:
            continue
        if "\u5f53\u524d\u95e8\u5e97\u6682\u672a\u5f00\u901a" in text and SCHEDULED_INVOICE_NAME in text:
            return True
    return False


def fetch_scheduled_invoice_status() -> str:
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    profile = local_app_data / "HotelAgent" / "browser_profiles" / "meituan"
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel="msedge", headless=True,
            chromium_sandbox=True, locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SCHEDULED_INVOICE_URL, wait_until="domcontentloaded", timeout=60_000)
            maintenance_clicked = False
            for _ in range(PAGE_CHECK_SECONDS * 2):
                if scheduled_invoice_is_closed(page):
                    return "CLOSED"
                for frame in page.frames:
                    try:
                        links = frame.locator("a", has_text="\u9152\u5e97\u53d1\u7968\u4fe1\u606f\u7ef4\u62a4")
                        for index in range(links.count()):
                            link = links.nth(index)
                            if link.is_visible():
                                link.click(force=True)
                                maintenance_clicked = True
                                break
                    except Exception:
                        continue
                    if maintenance_clicked:
                        break
                if maintenance_clicked:
                    break
                page.wait_for_timeout(500)
            if not maintenance_clicked:
                raise RuntimeError("Hotel invoice maintenance link was not found")

            page.wait_for_timeout(1_000)
            page.goto(SCHEDULED_INVOICE_URL, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(PAGE_CHECK_SECONDS * 2):
                if scheduled_invoice_is_closed(page):
                    return "CLOSED"
                page.wait_for_timeout(500)
        finally:
            context.close()
    return "OPEN"


def save_status(hotel_id: str, code: str, name: str, status: str) -> None:
    import pymysql

    snapshot_time = datetime.now()
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO meituan_ota_promotion_status
                   (hotel_id, promotion_code, promotion_name, status, snapshot_time)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE promotion_name=VALUES(promotion_name),
                   status=VALUES(status), snapshot_time=VALUES(snapshot_time)""",
                (hotel_id, code, name, status, snapshot_time),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Meituan promotion status data")
    parser.add_argument("--force", action="store_true", help="ignore the 10-14 hour automatic cooldown")
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
            "promotion status skipped: cooldown active; "
            f"reason={cooldown_reason}; next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        return 0

    mode = "forced" if args.force else "scheduled"
    mark_attempt_started(now)
    print(f"promotion status attempt mode={mode}")

    checks = [
        (PROMOTION_CODE, PROMOTION_NAME, fetch_youmeihui_status),
        (BUSINESS_TRAVEL_CODE, BUSINESS_TRAVEL_NAME, fetch_business_travel_status),
        (PUBLIC_WELFARE_CODE, PUBLIC_WELFARE_NAME, fetch_public_welfare_status),
        (SCHEDULED_INVOICE_CODE, SCHEDULED_INVOICE_NAME, fetch_scheduled_invoice_status),
        (HOURLY_ROOM_CODE, HOURLY_ROOM_NAME, fetch_hourly_room_status),
        (HIGHLIGHTS_CODE, HIGHLIGHTS_NAME, fetch_hotel_highlights_status),
        (AUTO_ORDER_CODE, AUTO_ORDER_NAME, fetch_auto_order_status),
    ]
    results = []
    failures = []
    try:
        with browser_profile_lock():
            for code, name, check in checks:
                try:
                    status = check()
                    save_status(hotel_id, code, name, status)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:300]}"
                    failures.append((code, name, message))
                    print(f"{code} check failed; previous database status retained: {message}")
                    continue
                results.append((code, name, status))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / "meituan_ota_promotion_status.json").write_text(
            json.dumps(
                {
                    "hotel_id": hotel_id,
                    "checked_at": datetime.now(),
                    "items": [{"promotion_code": code, "status": status} for code, _name, status in results],
                    "failures": [
                        {"promotion_code": code, "promotion_name": name, "error": error}
                        for code, name, error in failures
                    ],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        failed_at = datetime.now()
        next_allowed_at = schedule_cooldown(failed_at, "failure")
        print(
            "promotion status failed; cooldown scheduled; "
            f"next_allowed_at={next_allowed_at:%Y-%m-%d %H:%M:%S}"
        )
        raise

    print(", ".join(f"{code} status={status}" for code, _name, status in results))
    completed_at = datetime.now()
    if failures:
        next_allowed_at = schedule_cooldown(completed_at, "failure")
        summary = "; ".join(f"{code}: {error}" for code, _name, error in failures)
        print(f"Promotion status partial warning; previous values retained: {summary}")
    else:
        next_allowed_at = schedule_cooldown(completed_at, "success")
        mark_schedule_success(completed_at)
    print(f"promotion status next automatic attempt after {next_allowed_at:%Y-%m-%d %H:%M:%S}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
