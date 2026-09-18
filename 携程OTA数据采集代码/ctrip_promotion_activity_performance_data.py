from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME, PLATFORM_SCOPE, USER_AGENT

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import sync_table


TABLE_NAME = "ctrip_ota_promotion_activity_performance"
PROMOTION_PAGE_URL = "https://ebooking.ctrip.com/promotion/promotionCenter?microJump=true"
DETAIL_PATH = "/revenueroom/api/prcpromotion/getNewPromotionDetail?hostType=HE"
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "period_start_date", "period_end_date",
    "snapshot_time", "promotion_switch", "total_campaign_quantity",
    "total_quantity", "comp_avg_total_quantity", "total_quantity_yoy_pct",
    "comp_avg_quantity_yoy_pct", "campaign_id", "campaign_name", "campaign_quantity",
    "campaign_quantity_yoy_pct", "campaign_gmv", "campaign_gmv_yoy_pct", "discount_amt",
]


def rolling_30_day_range(today: date | None = None) -> tuple[date, date]:
    end_date = (today or date.today()) - timedelta(days=1)
    return end_date - timedelta(days=29), end_date


def ctrip_cookies(cookie: str) -> list[dict[str, str]]:
    entries = []
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name and value:
            entries.append({"name": name, "value": value, "url": "https://ebooking.ctrip.com/"})
    if not entries:
        raise RuntimeError("CTRIP_COOKIE is empty; log in through the control panel first")
    return entries


def fetch_payload(start_date: date, end_date: date, cookie: str = COOKIE) -> dict[str, Any]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(locale="zh-CN", user_agent=USER_AGENT)
            context.add_cookies(ctrip_cookies(cookie))
            page = context.new_page()
            page.goto(PROMOTION_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
            if any(marker in page.url.lower() for marker in ("/login", "passport", "security", "verify")):
                raise RuntimeError("Ctrip login session is invalid or requires verification")
            result = page.evaluate(
                """async ({startDate, endDate, path}) => {
                    const response = await fetch(path, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'},
                        body: new URLSearchParams({startDate, endDate}),
                    });
                    return {status: response.status, text: await response.text()};
                }""",
                {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "path": DETAIL_PATH},
            )
        finally:
            browser.close()
    if not isinstance(result, dict) or result.get("status") != 200:
        raise RuntimeError("Ctrip promotion-detail API failed")
    try:
        payload = json.loads(str(result.get("text") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ctrip promotion-detail API did not return JSON") from exc
    if not isinstance(payload, dict) or payload.get("rcode") not in (0, "0") or not isinstance(payload.get("data"), dict):
        raise RuntimeError("Ctrip promotion-detail response is invalid")
    return payload


def rows_from_payload(
    payload: dict[str, Any], hotel_id: str, captured_at: datetime, start_date: date, end_date: date,
) -> list[list[Any]]:
    data = payload["data"]
    details = data.get("promotionDetailBo")
    if not isinstance(details, list):
        raise RuntimeError("Ctrip promotion-detail response is missing promotionDetailBo")
    shared = [
        hotel_id, DEFAULT_HOTEL_NAME, PLATFORM_SCOPE, start_date, end_date, captured_at,
        data.get("promotionSwitch"), data.get("totalCampaignQuantity"),
        data.get("totalQuantity"), data.get("compAvgTotalQuantity"), data.get("quantityYoy"),
        data.get("compAvgQuantityYoy"),
    ]
    rows = []
    for detail in details:
        if not isinstance(detail, dict) or detail.get("campaignId") in (None, ""):
            continue
        rows.append(shared + [
            detail.get("campaignId"), detail.get("campaignName"), detail.get("campaignQuantity"),
            detail.get("quantityYoy"), detail.get("campaignGmv"), detail.get("campaignGmvYoy"),
            detail.get("discountAmt"),
        ])
    return rows


def collect_rows(hotel_id: str, captured_at: datetime, cookie: str = COOKIE) -> list[list[Any]]:
    start_date, end_date = rolling_30_day_range()
    return rows_from_payload(fetch_payload(start_date, end_date, cookie), hotel_id, captured_at, start_date, end_date)


def sync_rows(rows: list[list[Any]]) -> None:
    sync_table(TABLE_NAME, HEADERS, rows, allow_empty_replace=True)
