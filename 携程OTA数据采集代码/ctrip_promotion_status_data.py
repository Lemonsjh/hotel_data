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


def collect_statuses() -> tuple[int, int, int, int, int, int, int, int, float]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(locale="zh-CN", viewport={"width": 1920, "height": 1080})
            context.add_cookies(ctrip_cookies())
            page = context.new_page()
            points = activity_enabled(page, POINTS_ALLIANCE_MENU_POINT, POINTS_PAGE_MARKERS, APPLY_SELECTOR, "立即报名")
            preferred = activity_enabled(page, PREFERRED_CLUB_MENU_POINT, PREFERRED_CLUB_PAGE_MARKERS, 'button[he-click="join_tplus"]', "立即报名")
            business = activity_enabled(page, BUSINESS_TRAVEL_MENU_POINT, BUSINESS_TRAVEL_PAGE_MARKERS, 'button[he-click="businesstravel_join"]', "立即加入")
            hourly_enabled, hourly_room_count = hourly_room_status()
            information_score = information_completeness_score(page)
            homepage_video = homepage_video_status(page)
            travel_photo = travel_photo_status(page)
            listing_pass = listing_pass_status(page)
            return points, preferred, business, hourly_enabled, hourly_room_count, homepage_video, travel_photo, listing_pass, information_score
        finally:
            browser.close()


def status_rows(
    hotel_id: str, captured_at: datetime, points: int, preferred: int, business: int,
    hourly_enabled: int, hourly_room_count: int, homepage_video: int, travel_photo: int,
    listing_pass: int, information_score: float,
) -> list[list[Any]]:
    hotel_name = DEFAULT_HOTEL_NAME
    rows = [
        [hotel_id, hotel_name, "ctrip", "points_alliance", "\u79ef\u5206\u8054\u76df", points,
         "JOINED" if points else "NOT_JOINED", None, None, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "preferred_club", "\u4f18\u4eab\u4f1a", preferred,
         "JOINED" if preferred else "NOT_JOINED", "UNKNOWN" if preferred else None, None, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "business_travel_price", "\u5546\u65c5\u4e13\u4eab\u4ef7", business,
         "JOINED" if business else "NOT_JOINED", None, None, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "hourly_room", "\u949f\u70b9\u623f", hourly_enabled,
         "ENABLED" if hourly_enabled else "DISABLED", None, hourly_room_count, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "travel_photo", TRAVEL_PHOTO_TAB_TEXT, travel_photo,
         "UPLOADED" if travel_photo else "NOT_UPLOADED", None, None, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "homepage_video", "\u9996\u9875\u89c6\u9891", homepage_video,
         "UPLOADED" if homepage_video else "NOT_UPLOADED", None, None, None, captured_at],
        [hotel_id, hotel_name, "ctrip", "listing_pass", LISTING_PASS_TEXT, listing_pass,
         "JOINED" if listing_pass else "NOT_JOINED", None, None, None, captured_at],
    ]
    return [row + [None, None] for row in rows] + [
        [
            hotel_id, hotel_name, "ctrip", "information_completeness", "\u4fe1\u606f\u5b8c\u6574\u5ea6",
            int(information_score >= 100), "COMPLETE" if information_score >= 100 else "INCOMPLETE",
            None, None, None, captured_at, information_score, "%",
        ]
    ]


def write_output(headers: list[str], rows: list[list[Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(
        json.dumps([dict(zip(headers, row)) for row in rows], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def main() -> int:
    captured_at = datetime.now()
    points, preferred, business, hourly_enabled, hourly_room_count, homepage_video, travel_photo, listing_pass, information_score = collect_statuses()
    headers = [
        "hotel_id", "hotel_name", "platform_scope", "activity_code", "activity_name", "enabled",
        "status", "status_detail", "room_type_count", "orders_30d", "snapshot_time", "metric_value", "metric_unit",
    ]
    rows = status_rows(
        require_hotel_id(), captured_at, points, preferred, business, hourly_enabled,
        hourly_room_count, homepage_video, travel_photo, listing_pass, information_score,
    )
    sync_metric_history_table(
        TABLE_NAME, headers, rows,
        {"hotel_id", "platform_scope", "activity_code"}, retention_days=None,
    )
    write_output(headers, rows)
    print(
        "Ctrip promotion status sync completed: "
        f"points={points} preferred={preferred} business={business} hourly={hourly_enabled}/{hourly_room_count} "
        f"homepage_video={homepage_video} travel_photo={travel_photo} listing_pass={listing_pass} information_score={information_score}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
