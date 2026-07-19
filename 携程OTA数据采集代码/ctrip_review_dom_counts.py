from __future__ import annotations

import re
from http.cookies import SimpleCookie
from typing import Any


PAGE_URL = "https://ebooking.ctrip.com/comment/commentList?microJump=true"
COUNT_FIELDS = {
    "全部点评": "total_review_count",
    "待回复": "unreplied_review_count",
    "差评": "negative_review_count",
}


def _cookies(raw_cookie: str) -> list[dict[str, str]]:
    parsed = SimpleCookie()
    parsed.load(raw_cookie)
    return [
        {"name": name, "value": item.value, "domain": ".ctrip.com", "path": "/"}
        for name, item in parsed.items()
    ]


def _compact_name(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _read_count(page, label: str) -> int:
    node = page.get_by_text(label, exact=True)
    count = node.count()
    if count != 1:
        raise RuntimeError(f"{label} matched {count} elements")
    text = node.locator("xpath=following-sibling::p[1]").inner_text().strip()
    number = re.search(r"\d+", text.replace(",", ""))
    if not number:
        raise RuntimeError(f"{label} has no numeric value")
    return int(number.group())


def collect_review_counts(
    cookie: str,
    *,
    expected_hotel_name: str = "",
    expected_hotel_id: str = "",
) -> dict[str, Any]:
    if not cookie.strip():
        raise RuntimeError("CTRIP_COOKIE is empty")
    from ctrip_config import USER_AGENT
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        try:
            context.add_cookies(_cookies(cookie))
            page = context.new_page()
            page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
            page.get_by_text("全部点评", exact=True).wait_for(
                state="visible", timeout=45000
            )
            hotel_link = page.locator('a[href*="hotels.ctrip.com/hotels/"]').first
            hotel_name = hotel_link.inner_text().strip() if hotel_link.count() else ""
            hotel_url = hotel_link.get_attribute("href") if hotel_link.count() else ""
            hotel_id_match = re.search(r"/hotels/(\d+)", hotel_url or "")
            hotel_id = hotel_id_match.group(1) if hotel_id_match else ""
            expected_name = _compact_name(expected_hotel_name)
            actual_name = _compact_name(hotel_name)
            if expected_name and expected_name not in actual_name and actual_name not in expected_name:
                raise RuntimeError(f"携程酒店名称不匹配：{hotel_name}")
            if expected_hotel_id and hotel_id and expected_hotel_id != hotel_id:
                raise RuntimeError(f"携程酒店 ID 不匹配：{hotel_id}")
            result: dict[str, Any] = {
                "hotel_name": hotel_name,
                "ota_hotel_id": hotel_id,
            }
            for label, field in COUNT_FIELDS.items():
                result[field] = _read_count(page, label)
            return result
        finally:
            context.close()
            browser.close()


def load_existing_counts(
    db_config: dict[str, Any], hotel_id: str, platform_scope: str = "ctrip"
) -> dict[str, int]:
    import pymysql

    try:
        connection = pymysql.connect(**db_config)
    except pymysql.MySQLError:
        return {}
    try:
        with connection.cursor() as cursor:
            where = "WHERE hotel_id=%s AND platform_scope=%s" if hotel_id else ""
            params = (hotel_id, platform_scope) if hotel_id else ()
            cursor.execute(
                f"""
                SELECT total_review_count, unreplied_review_count,
                       negative_review_count
                FROM ctrip_ota_review_overview
                {where} ORDER BY id DESC LIMIT 1
                """,
                params,
            )
            row = cursor.fetchone()
    except (pymysql.MySQLError, TypeError, ValueError):
        return {}
    finally:
        connection.close()
    if not row:
        return {}
    fields = (
        "total_review_count",
        "unreplied_review_count",
        "negative_review_count",
    )
    return {
        field: int(value)
        for field, value in zip(fields, row)
        if value is not None
    }


def merge_counts(rows: list[list[Any]], counts: dict[str, Any]) -> None:
    if not rows:
        return
    for index, field in enumerate(COUNT_FIELDS.values(), start=8):
        if counts.get(field) is not None:
            rows[0][index] = int(counts[field])
