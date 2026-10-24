from __future__ import annotations

import json
import time
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
DETAIL_PATH = "/restapi/soa2/24267/queryDomesticPromoEffectDetails"
OVERVIEW_PATH = "/restapi/soa2/24267/queryDomesticPromoEffectOverview"
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
    if (start_date, end_date) != rolling_30_day_range():
        raise ValueError("Ctrip promotion-effect collector requires the completed 30-day window")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(locale="zh-CN", user_agent=USER_AGENT)
            context.add_cookies(ctrip_cookies(cookie))
            page = context.new_page()
            page.goto(PROMOTION_PAGE_URL, wait_until="domcontentloaded", timeout=60_000)
            if any(marker in page.url.lower() for marker in ("/login", "passport", "security", "verify")):
                raise RuntimeError("Ctrip login session is invalid or requires verification")
            # Read the page's real responses, including its current hotel and request metadata.
            # Replaying a constructed POST is not equivalent to the page's own API client.
            results: dict[str, Any] = {}

            def capture(response: Any) -> None:
                path = response.url.split("?")[0]
                name = next((key for key, suffix in (("details", DETAIL_PATH), ("overview", OVERVIEW_PATH))
                             if path.endswith(suffix)), None)
                if name is None:
                    return
                request = response.request.post_data_json
                if not isinstance(request, dict) or request.get("startDate") != start_date.isoformat() or request.get("endDate") != end_date.isoformat():
                    return
                results[name] = {"status": response.status, "text": response.text()}

            page.on("response", capture)
            page.get_by_text("促销效果", exact=True).click(timeout=15_000)
            page.get_by_text("过去30天", exact=True).click(timeout=15_000)
            deadline = time.monotonic() + 30
            while len(results) < 2 and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if len(results) != 2:
                raise RuntimeError("Ctrip promotion-effect APIs did not return both 30-day results")
            result = dict(results)
        finally:
            browser.close()
    return {name: decode_response(value) for name, value in result.items()}


def decode_response(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") != 200:
        raise RuntimeError("Ctrip promotion-effect API failed")
    try:
        payload = json.loads(str(result.get("text") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Ctrip promotion-effect API did not return JSON") from exc
    validate_payload(payload)
    return payload


def validate_payload(payload: Any) -> None:
    status = payload.get("resStatus") if isinstance(payload, dict) else None
    if not isinstance(status, dict) or status.get("rcode") not in (200, "200"):
        raise RuntimeError("Ctrip promotion-effect response is not successful")
    transport = payload.get("ResponseStatus", {})
    if not isinstance(transport, dict) or transport.get("Ack", "Success") != "Success" or transport.get("Errors"):
        raise RuntimeError("Ctrip promotion-effect response contains service errors")


def rows_from_payload(
    payload: dict[str, Any], hotel_id: str, captured_at: datetime, start_date: date, end_date: date,
) -> list[list[Any]]:
    detail_data, data = payload.get("details"), payload.get("overview")
    validate_payload(detail_data)
    validate_payload(data)
    details = detail_data.get("details")
    if not isinstance(details, list):
        raise RuntimeError("Ctrip promotion-effect response is missing details")
    if not hotel_id.strip():
        raise RuntimeError("Ctrip promotion-effect hotel_id is empty")
    shared = [
        hotel_id, DEFAULT_HOTEL_NAME, PLATFORM_SCOPE, start_date, end_date, captured_at,
        None, data.get("promoRoomNights"), data.get("totalRoomNights"),
        data.get("competitorsAvgPromoRoomNights"), None, None,
    ]
    rows, names = [], set()
    for detail in details:
        name = detail.get("promoSet") if isinstance(detail, dict) else None
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise RuntimeError("Ctrip promotion-effect detail has no valid promotion name")
        name = name.strip()
        if name.casefold() in names:
            raise RuntimeError("Ctrip promotion-effect contains duplicate promotion names")
        names.add(name.casefold())
        rows.append(shared + [
            None, name, detail.get("promoRoomNights"), None,
            detail.get("promoGmv"), None, None,
        ])
    return rows


def collect_rows(hotel_id: str, captured_at: datetime, cookie: str = COOKIE) -> list[list[Any]]:
    start_date, end_date = rolling_30_day_range()
    return rows_from_payload(fetch_payload(start_date, end_date, cookie), hotel_id, captured_at, start_date, end_date)


def sync_rows(rows: list[list[Any]]) -> None:
    # No scope can be inferred from an empty batch; never turn it into a full-table DELETE.
    if not rows:
        return
    sync_table(TABLE_NAME, HEADERS, rows, allow_empty_replace=True)
