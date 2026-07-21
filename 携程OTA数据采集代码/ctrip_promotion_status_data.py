from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME
from ctrip_goods_price_mapping import CtripGoodsClient, normalize_products

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_metric_history_table


TABLE_NAME = "ctrip_ota_promotion_status"
HOME_URL = "https://ebooking.ctrip.com/home/mainland?microJump=true"
HOTEL_HIGHLIGHTS_URL = "https://ebooking.ctrip.com/hotelinfo/ebooking/hoteltag?microJump=true"
APPLY_SELECTOR = 'button[he-click="connectEquity_submit"]'
PROMOTION_MENU_POINT = (110, 235)
POINTS_ALLIANCE_MENU_POINT = (110, 385)
PREFERRED_CLUB_MENU_POINT = (110, 458)
BUSINESS_TRAVEL_MENU_POINT = (110, 503)
INFORMATION_MENU_POINT = (110, 435)
PICTURE_VIDEO_MENU_POINT = (110, 571)
VIDEO_TAB_POINT = (340, 167)
TRAVEL_PHOTO_TAB_TEXT = "\u65c5\u62cd"
EMPTY_DATA_TEXT = "\u6682\u65e0\u6570\u636e"
MY_UPLOADS_TEXT = "\u6211\u7684\u4e0a\u4f20"
LISTING_MANAGEMENT_TEXT = "\u6302\u724c\u7ba1\u7406"
LISTING_PASS_TEXT = "\u6302\u724c\u901a"
LISTING_SIGNUP_SELECTOR = 'button[he-click="Sign_Up_Now"]'
POINTS_PAGE_MARKERS = ("积分可抵", "积分膨胀", "十倍积分")
PREFERRED_CLUB_PAGE_MARKERS = ("体验优享会计划说明", "优享会酒店附加协议")
BUSINESS_TRAVEL_PAGE_MARKERS = ("商旅专享说明", "企业间对公结算")


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


def dismiss_overlays(page: Any) -> None:
    page.keyboard.press("Escape")
    for dialog in page.locator('[role="dialog"]').all():
        try:
            if not dialog.is_visible():
                continue
            close = dialog.locator('button[aria-label="Close"], [class*="close" i]').first
            if close.count() and close.is_visible():
                close.click(force=True, timeout=1_000)
        except Exception:
            continue


def open_promotion_page(page: Any, menu_point: tuple[int, int]) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    dismiss_overlays(page)
    page.mouse.click(*PROMOTION_MENU_POINT)
    page.wait_for_timeout(700)
    page.mouse.click(*menu_point)


def activity_enabled(page: Any, menu_point: tuple[int, int], markers: tuple[str, ...], selector: str, apply_text: str) -> int:
    open_promotion_page(page, menu_point)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        button_texts = page.locator(selector).all_inner_texts()
        if any(apply_text in text for text in button_texts):
            return 0
        body = page.locator("body").inner_text(timeout=1_000)
        if any(marker in body for marker in markers):
            return 1
        page.wait_for_timeout(500)
    raise RuntimeError(f"Ctrip promotion page did not return a recognized status: {markers[0]}")


def enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def hourly_room_status() -> tuple[int, int]:
    products = normalize_products(CtripGoodsClient(COOKIE).query_goods())
    if not products:
        raise RuntimeError("Ctrip room-product response contains no products")
    room_types = {
        str(product.get("ota_room_type_id"))
        for product in products
        if enabled_flag(product.get("is_hour_room")) and product.get("ota_room_type_id") not in (None, "")
    }
    return int(bool(room_types)), len(room_types)


def information_completeness_score(page: Any) -> float:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    dismiss_overlays(page)
    page.mouse.click(*INFORMATION_MENU_POINT)
    deadline = time.monotonic() + 20
    pattern = re.compile(r"\u4fe1\u606f\u5206\s*([0-9]+(?:\.[0-9]+)?)\s*%?")
    while time.monotonic() < deadline:
        match = pattern.search(page.locator("body").inner_text(timeout=1_000))
        if match:
            return float(match.group(1))
        page.wait_for_timeout(500)
    raise RuntimeError("Ctrip information page did not return an information score")


def homepage_video_status(page: Any) -> int:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    dismiss_overlays(page)
    page.mouse.click(*INFORMATION_MENU_POINT)
    page.wait_for_timeout(700)
    page.mouse.click(*PICTURE_VIDEO_MENU_POINT)
    page.wait_for_timeout(2_500)
    page.mouse.click(*VIDEO_TAB_POINT)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        title = page.locator("div.currentUsingTitle-mfgIVI")
        if title.count() and title.first.inner_text(timeout=1_000).strip() == "主视频":
            container = title.first.locator("xpath=..")
            return int(bool(container.locator("div.videoLeftWrapper-PnlzeB img").count()))
        page.wait_for_timeout(500)
    raise RuntimeError("Ctrip video page did not return a main-video section")


def travel_photo_status(page: Any) -> int:
    page.goto(HOTEL_HIGHLIGHTS_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    dismiss_overlays(page)
    travel_tab = page.get_by_text(TRAVEL_PHOTO_TAB_TEXT, exact=True)
    if not travel_tab.count():
        raise RuntimeError("Ctrip hotel-highlights page did not return a travel-photo tab")
    travel_tab.last.click(timeout=10_000)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        empty = page.get_by_text(EMPTY_DATA_TEXT, exact=True)
        if any(item.is_visible() for item in empty.all()):
            return 0
        if MY_UPLOADS_TEXT in page.locator("body").inner_text(timeout=1_000):
            return 1
        page.wait_for_timeout(500)
    raise RuntimeError("Ctrip travel-photo tab did not return a recognized status")


def listing_pass_status(page: Any) -> int:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(4_000)
    dismiss_overlays(page)
    management = page.get_by_text(LISTING_MANAGEMENT_TEXT, exact=True)
    if not management.count():
        raise RuntimeError("Ctrip home page did not return a listing-management entry")
    management.last.click(timeout=10_000)
    page.wait_for_timeout(1_500)
    listing_pass = page.get_by_text(LISTING_PASS_TEXT, exact=True)
    if not listing_pass.count():
        raise RuntimeError("Ctrip listing-management page did not return a listing-pass entry")
    listing_pass.last.click(timeout=10_000)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        signup = page.locator(LISTING_SIGNUP_SELECTOR)
        if signup.count() and any(item.is_visible() for item in signup.all()):
            return 0
        if LISTING_PASS_TEXT in page.locator("body").inner_text(timeout=1_000):
            return 1
        page.wait_for_timeout(500)
    raise RuntimeError("Ctrip listing-pass page did not return a recognized status")


def collect_one(name: str, func: Any) -> tuple[Any | None, str | None]:
    try:
        return func(), None
    except Exception as exc:
        message = str(exc).replace("\n", " ").strip()[:64]
        print(f"Ctrip promotion status item failed: {name}: {message}")
        return None, message or exc.__class__.__name__


def collect_statuses() -> dict[str, tuple[Any | None, str | None]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(locale="zh-CN", viewport={"width": 1920, "height": 1080})
            context.add_cookies(ctrip_cookies())
            page = context.new_page()
            return {
                "points_alliance": collect_one("points_alliance", lambda: activity_enabled(page, POINTS_ALLIANCE_MENU_POINT, POINTS_PAGE_MARKERS, APPLY_SELECTOR, "立即报名")),
                "preferred_club": collect_one("preferred_club", lambda: activity_enabled(page, PREFERRED_CLUB_MENU_POINT, PREFERRED_CLUB_PAGE_MARKERS, 'button[he-click="join_tplus"]', "立即报名")),
                "business_travel": collect_one("business_travel", lambda: activity_enabled(page, BUSINESS_TRAVEL_MENU_POINT, BUSINESS_TRAVEL_PAGE_MARKERS, 'button[he-click="businesstravel_join"]', "立即加入")),
                "hourly_room": collect_one("hourly_room", hourly_room_status),
                "information": collect_one("information_completeness", lambda: information_completeness_score(page)),
                "homepage_video": collect_one("homepage_video", lambda: homepage_video_status(page)),
                "travel_photo": collect_one("travel_photo", lambda: travel_photo_status(page)),
                "listing_pass": collect_one("listing_pass", lambda: listing_pass_status(page)),
            }
        finally:
            browser.close()


def status_row(
    hotel_id: str, captured_at: datetime, code: str, name: str, result: tuple[Any | None, str | None],
    active_status: str, inactive_status: str, status_detail: str | None = None,
    room_type_count: int | None = None, metric_value: float | None = None, metric_unit: str | None = None,
) -> list[Any]:
    value, error = result
    if error:
        return [hotel_id, DEFAULT_HOTEL_NAME, "ctrip", code, name, None, "ERROR", error, None, None, captured_at, None, None]
    return [
        hotel_id, DEFAULT_HOTEL_NAME, "ctrip", code, name, int(bool(value)),
        active_status if value else inactive_status, status_detail, room_type_count, None,
        captured_at, metric_value, metric_unit,
    ]


def status_rows(hotel_id: str, captured_at: datetime, results: dict[str, tuple[Any | None, str | None]]) -> list[list[Any]]:
    hourly_value, hourly_error = results["hourly_room"]
    hourly_enabled, hourly_room_count = (hourly_value or (None, None)) if not hourly_error else (None, None)
    information_score, information_error = results["information"]
    information_enabled = None if information_error else int(float(information_score) >= 100)
    return [
        status_row(hotel_id, captured_at, "points_alliance", "\u79ef\u5206\u8054\u76df", results["points_alliance"], "JOINED", "NOT_JOINED"),
        status_row(hotel_id, captured_at, "preferred_club", "\u4f18\u4eab\u4f1a", results["preferred_club"], "JOINED", "NOT_JOINED", "UNKNOWN" if results["preferred_club"][0] else None),
        status_row(hotel_id, captured_at, "business_travel_price", "\u5546\u65c5\u4e13\u4eab\u4ef7", results["business_travel"], "JOINED", "NOT_JOINED"),
        status_row(hotel_id, captured_at, "hourly_room", "\u949f\u70b9\u623f", (hourly_enabled, hourly_error), "ENABLED", "DISABLED", room_type_count=hourly_room_count),
        status_row(hotel_id, captured_at, "travel_photo", TRAVEL_PHOTO_TAB_TEXT, results["travel_photo"], "UPLOADED", "NOT_UPLOADED"),
        status_row(hotel_id, captured_at, "homepage_video", "\u9996\u9875\u89c6\u9891", results["homepage_video"], "UPLOADED", "NOT_UPLOADED"),
        status_row(hotel_id, captured_at, "listing_pass", LISTING_PASS_TEXT, results["listing_pass"], "JOINED", "NOT_JOINED"),
        status_row(hotel_id, captured_at, "information_completeness", "\u4fe1\u606f\u5b8c\u6574\u5ea6", (information_enabled, information_error), "COMPLETE", "INCOMPLETE", metric_value=information_score, metric_unit="%"),
    ]


def write_output(headers: list[str], rows: list[list[Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(
        json.dumps([dict(zip(headers, row)) for row in rows], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    captured_at = datetime.now()
    results = collect_statuses()
    headers = [
        "hotel_id", "hotel_name", "platform_scope", "activity_code", "activity_name", "enabled",
        "status", "status_detail", "room_type_count", "orders_30d", "snapshot_time", "metric_value", "metric_unit",
    ]
    rows = status_rows(require_hotel_id(), captured_at, results)
    sync_metric_history_table(
        TABLE_NAME, headers, rows,
        {"hotel_id", "platform_scope", "activity_code"}, retention_days=None,
    )
    write_output(headers, rows)
    print(
        "Ctrip promotion status sync completed: "
        f"success={sum(error is None for _, error in results.values())}/{len(results)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
