from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME, USER_AGENT
from ctrip_goods_price_mapping import CtripGoodsClient, normalize_products

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_metric_history_table


TABLE_NAME = "ctrip_ota_promotion_status"
HOME_URL = "https://ebooking.ctrip.com/home/mainland?microJump=true"
HOTEL_HIGHLIGHTS_URL = "https://ebooking.ctrip.com/hotelinfo/ebooking/hoteltag?microJump=true"
QUICK_CHECK_INN_URL = "https://ebooking.ctrip.com/ebkfinance/settlement/settlementQuickCheckInn/sendEmail"
SHORT_TAGS_URL = "https://ebooking.ctrip.com/restapi/soa2/23942/queryShortTags"
HOTEL_TAGS_URL = "https://ebooking.ctrip.com/restapi/soa2/23942/getHotelTags"
APPLY_SELECTOR = 'button[he-click="connectEquity_submit"]'
PROMOTION_MENU_POINT = (110, 235)
PROMOTION_MENU_TEXT = "促销推广"
POINTS_ALLIANCE_MENU_POINT = (110, 385)
POINTS_ALLIANCE_MENU_TEXT = "积分联盟"
PREFERRED_CLUB_MENU_POINT = (110, 458)
PREFERRED_CLUB_MENU_TEXT = "优享会"
BUSINESS_TRAVEL_MENU_POINT = (110, 503)
BUSINESS_TRAVEL_MENU_TEXT = "商旅专享价"
TRAVEL_PHOTO_TAB_TEXT = "\u65c5\u62cd"
EMPTY_DATA_TEXT = "\u6682\u65e0\u6570\u636e"
MY_UPLOADS_TEXT = "\u6211\u7684\u4e0a\u4f20"
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


def ensure_logged_in(page: Any) -> None:
    url = page.url.lower()
    if any(marker in url for marker in ("/login", "passport", "security", "verify")):
        raise RuntimeError(f"Ctrip login session is invalid or requires verification: {page.url}")
    if page.locator('input[type="password"]').count():
        raise RuntimeError("Ctrip login session is invalid or requires verification")
    body = page.locator("body").inner_text(timeout=1_000)
    if any(marker in body for marker in ("登录后查看", "请先登录", "安全验证", "滑动验证")):
        raise RuntimeError("Ctrip login session is invalid or requires verification")


def click_menu(page: Any, text: str, point: tuple[int, int]) -> None:
    menu = page.get_by_text(text, exact=True)
    if menu.count():
        menu.last.click(timeout=10_000)
        return
    page.mouse.click(*point)


def open_promotion_page(page: Any, menu_point: tuple[int, int], menu_text: str) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    ensure_logged_in(page)
    page.wait_for_timeout(1_500)
    dismiss_overlays(page)
    click_menu(page, PROMOTION_MENU_TEXT, PROMOTION_MENU_POINT)
    page.wait_for_timeout(700)
    click_menu(page, menu_text, menu_point)


def activity_enabled(page: Any, menu_point: tuple[int, int], markers: tuple[str, ...], selector: str, apply_text: str) -> int:
    menu_text = {
        POINTS_ALLIANCE_MENU_POINT: POINTS_ALLIANCE_MENU_TEXT,
        PREFERRED_CLUB_MENU_POINT: PREFERRED_CLUB_MENU_TEXT,
        BUSINESS_TRAVEL_MENU_POINT: BUSINESS_TRAVEL_MENU_TEXT,
    }[menu_point]
    open_promotion_page(page, menu_point, menu_text)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ensure_logged_in(page)
        apply_buttons = page.locator(selector)
        if any(button.is_visible() for button in apply_buttons.all()):
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


def short_tag_names_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or payload.get("resStatus", {}).get("rcode") != 200:
        raise RuntimeError("Ctrip short-tags response failed")
    tags = payload.get("shortTags")
    if not isinstance(tags, list):
        raise RuntimeError("Ctrip short-tags response is missing shortTags")
    names = [
        str(tag.get("tagName")).strip()
        for tag in tags
        if isinstance(tag, dict) and tag.get("canDelete") is False and str(tag.get("tagName") or "").strip()
    ]
    return "，".join(names)


def listing_short_tags() -> str:
    client = CtripGoodsClient(COOKIE)
    response = client.session.get(SHORT_TAGS_URL, timeout=30)
    response.raise_for_status()
    return short_tag_names_from_payload(response.json())


def recommendation_words_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or payload.get("resStatus", {}).get("rcode") != 200:
        raise RuntimeError("Ctrip hotel-tags response failed")
    comment_clause = payload.get("ugcCommentClause")
    info = comment_clause.get("info") if isinstance(comment_clause, dict) else None
    clause = info.get("clause") if isinstance(info, dict) else None
    return str(clause or "").strip()


def listing_recommendation_words() -> str:
    client = CtripGoodsClient(COOKIE)
    response = client.session.get(HOTEL_TAGS_URL, timeout=30)
    response.raise_for_status()
    return recommendation_words_from_payload(response.json())


def information_completeness_score(page: Any) -> float:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    ensure_logged_in(page)
    dismiss_overlays(page)
    home = information_submenu(page, "信息首页")
    with page.expect_response(lambda response: "/23942/getHotelInfoScoreItems" in response.url, timeout=30_000) as captured:
        home.click(timeout=10_000)
    return information_score_from_payload(captured.value.json())


def information_submenu(page: Any, text: str) -> Any:
    item = page.get_by_text(text, exact=True).first
    if not item.is_visible():
        page.get_by_text("信息维护", exact=True).first.click(timeout=10_000)
    return item


def information_score_from_payload(payload: Any) -> float:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Ctrip information-score response failed")
    score_info = payload.get("scoreInfo")
    score = score_info.get("currentScore") if isinstance(score_info, dict) else None
    if isinstance(score, bool) or score is None:
        raise RuntimeError("Ctrip information-score response is missing currentScore")
    try:
        value = float(score)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Ctrip information-score response has invalid currentScore") from exc
    if not 0 <= value <= 100:
        raise RuntimeError("Ctrip information-score response has invalid currentScore")
    return value


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


def quick_check_inn_status(page: Any) -> int:
    page.goto(QUICK_CHECK_INN_URL, wait_until="domcontentloaded", timeout=60_000)
    ensure_logged_in(page)
    page.wait_for_timeout(2_000)
    body = page.locator("body").inner_text(timeout=3_000)
    markers = ("请选择押金系数", "我已阅读并同意")
    submit_or_application = ("立即提交加盟", "立即在线加盟")
    if all(marker in body for marker in markers) and any(marker in body for marker in submit_or_application):
        return 0
    if (
        "settlementQuickCheckInn" in page.url
        and "闪住" in body
        and not any(marker in body for marker in ("页面不存在", "无权限", "系统异常"))
    ):
        return 1
    raise RuntimeError("Ctrip quick-check-inn page did not return a recognized status")


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
            context = browser.new_context(
                locale="zh-CN",
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
            )
            context.add_cookies(ctrip_cookies())
            page = context.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
            ensure_logged_in(page)
            return {
                "points_alliance": collect_one("points_alliance", lambda: activity_enabled(page, POINTS_ALLIANCE_MENU_POINT, POINTS_PAGE_MARKERS, APPLY_SELECTOR, "立即报名")),
                "preferred_club": collect_one("preferred_club", lambda: activity_enabled(page, PREFERRED_CLUB_MENU_POINT, PREFERRED_CLUB_PAGE_MARKERS, 'button[he-click="join_tplus"]', "立即报名")),
                "business_travel": collect_one("business_travel", lambda: activity_enabled(page, BUSINESS_TRAVEL_MENU_POINT, BUSINESS_TRAVEL_PAGE_MARKERS, 'button[he-click="businesstravel_join"]', "立即加入")),
                "hourly_room": collect_one("hourly_room", hourly_room_status),
                "listing_short_tags": collect_one("listing_short_tags", listing_short_tags),
                "listing_recommendation_words": collect_one(
                    "listing_recommendation_words", listing_recommendation_words
                ),
                "information": collect_one("information_completeness", lambda: information_completeness_score(page)),
                "travel_photo": collect_one("travel_photo", lambda: travel_photo_status(page)),
                "quick_check_inn": collect_one("quick_check_inn", lambda: quick_check_inn_status(page)),
            }
        finally:
            browser.close()


def status_row(
    hotel_id: str, captured_at: datetime, code: str, name: str, result: tuple[Any | None, str | None],
    active_status: str, inactive_status: str, status_detail: str | None = None,
    room_type_count: int | None = None, metric_value: float | None = None, metric_unit: str | None = None,
) -> list[Any] | None:
    value, error = result
    if error:
        print(f"{name} status unavailable; previous value retained: {error}")
        return None
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
    short_tags, short_tags_error = results["listing_short_tags"]
    recommendation_words, recommendation_words_error = results["listing_recommendation_words"]
    candidates = [
        status_row(hotel_id, captured_at, "points_alliance", "\u79ef\u5206\u8054\u76df", results["points_alliance"], "JOINED", "NOT_JOINED"),
        status_row(hotel_id, captured_at, "preferred_club", "\u4f18\u4eab\u4f1a", results["preferred_club"], "JOINED", "NOT_JOINED", "UNKNOWN" if results["preferred_club"][0] else None),
        status_row(hotel_id, captured_at, "business_travel_price", "\u5546\u65c5\u4e13\u4eab\u4ef7", results["business_travel"], "JOINED", "NOT_JOINED"),
        status_row(hotel_id, captured_at, "hourly_room", "\u949f\u70b9\u623f", (hourly_enabled, hourly_error), "ENABLED", "DISABLED", room_type_count=hourly_room_count),
        status_row(hotel_id, captured_at, "listing_short_tags", "\u5217\u8868\u9875\u77ed\u6807\u7b7e", (short_tags, short_tags_error), "CONFIGURED", "NOT_CONFIGURED", status_detail=short_tags),
        status_row(hotel_id, captured_at, "listing_recommendation_words", "\u5217\u8868\u9875\u63a8\u8350\u8bcd", (recommendation_words, recommendation_words_error), "CONFIGURED", "NOT_CONFIGURED", status_detail=recommendation_words),
        status_row(hotel_id, captured_at, "travel_photo", TRAVEL_PHOTO_TAB_TEXT, results["travel_photo"], "UPLOADED", "NOT_UPLOADED"),
        status_row(hotel_id, captured_at, "quick_check_inn", "\u95ea\u4f4f", results["quick_check_inn"], "joined", "not_joined"),
        status_row(hotel_id, captured_at, "information_completeness", "\u4fe1\u606f\u5b8c\u6574\u5ea6", (information_enabled, information_error), "COMPLETE", "INCOMPLETE", metric_value=information_score, metric_unit="%"),
    ]
    return [row for row in candidates if row is not None]


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
    if not rows:
        raise RuntimeError("Ctrip promotion status did not return any recognizable result")
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
