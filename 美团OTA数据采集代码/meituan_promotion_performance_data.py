from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import DB_CONFIG


TABLE_NAME = "meituan_ota_promotion_performance_30d"
PAGE_SIZE = 50
PERIOD_DAYS = 30
TAB_IDS = (
    "T30002,T30003,T30032,T30034,T30033,T30001,T30004,T300030,"
    "T30005,T30047,T30006,T300071"
)
METRIC_COLUMNS = {
    "T30002": "exposure_count",
    "T30003": "click_count",
    "T30032": "booking_order_count",
    "T30034": "room_night_count",
    "T30033": "booking_order_amount",
    "T30001": "spend_amount",
    "T30004": "cost_per_click",
    "T300030": "click_rate_pct",
    "T30005": "merchant_view_count",
    "T30047": "cash_spend_amount",
}
COLUMNS = [
    "hotel_id", "period_start_date", "period_end_date", "snapshot_time",
    "plan_id", "plan_name", "promotion_status", "launch_id", "launch_name",
    "promotion_name", "promotion_type", "shop_id", "exposure_count", "click_count",
    "booking_order_count", "room_night_count", "booking_order_amount", "spend_amount",
    "cost_per_click", "click_rate_pct", "merchant_view_count", "cash_spend_amount",
]


def profile_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "HotelAgent" / "browser_profiles" / "meituan"


def number(value: Any) -> float | int | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return int(result) if result.is_integer() else result
    except (TypeError, ValueError):
        return None


def promotion_status(value: Any) -> str:
    statuses = {1: "RUNNING", 3: "PAUSED"}
    return statuses.get(number(value), "UNKNOWN")


def request_payload(period_start: date, period_end: date, page_num: int) -> dict[str, Any]:
    return {
        "searchContent": "", "shopIdList": "", "statusList": "",
        "beginDate": period_start.isoformat(), "endDate": period_end.isoformat(),
        "pageSize": PAGE_SIZE, "pageNum": page_num, "promoTypeList": "",
        "launchAimList": "", "planIdList": "", "launchIdList": "",
        "premiumFilter": "", "clientKey": "cpc.shop.promotion.list",
        "filterInnerAccountList": "", "tabIds": TAB_IDS, "customCols": "", "tabType": 2,
    }


def request_page(page: Any, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = page.evaluate(
        """async ({url, payload}) => {
          const response = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
            body: new URLSearchParams(payload).toString(),
          });
          return {status: response.status, text: await response.text()};
        }""",
        {"url": url, "payload": payload},
    )
    if result["status"] != 200:
        raise RuntimeError(f"Promotion performance API failed: HTTP {result['status']}")
    response = json.loads(result["text"])
    if response.get("code") != 200 or not isinstance(response.get("msg"), dict):
        raise RuntimeError(f"Promotion performance API response is invalid: {response.get('code')}")
    return response["msg"]


def fetch_plans(url: str, period_start: date, period_end: date) -> list[dict[str, Any]]:
    if not url:
        raise RuntimeError("MEITUAN_PROMOTION_PERFORMANCE_URL is empty")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path()), channel="msedge", headless=True,
            chromium_sandbox=True, locale="zh-CN",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://ebmidas.dianping.com/", wait_until="domcontentloaded", timeout=60_000)
            plans: list[dict[str, Any]] = []
            for page_num in range(1, 101):
                response = request_page(page, url, request_payload(period_start, period_end, page_num))
                page_rows = response.get("planList") or []
                plans.extend(row for row in page_rows if isinstance(row, dict))
                if len(plans) >= int(response.get("total") or 0) or len(page_rows) < PAGE_SIZE:
                    return plans
        finally:
            context.close()
    raise RuntimeError("Promotion performance pagination exceeded 100 pages")


def build_rows(plans: list[dict[str, Any]], period_start: date, period_end: date) -> list[tuple[Any, ...]]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    captured_at = datetime.now()
    rows: list[tuple[Any, ...]] = []
    for plan in plans:
        for launch in plan.get("launchList") or []:
            metrics = {column: None for column in METRIC_COLUMNS.values()}
            for item in launch.get("reportData") or []:
                column = METRIC_COLUMNS.get(str(item.get("id") or ""))
                if column:
                    metrics[column] = number(item.get("originVal"))
            booking_amount = metrics["booking_order_amount"]
            click_rate = metrics["click_rate_pct"]
            if click_rate is not None:
                click_rate *= 100
            rows.append(tuple([
                hotel_id, period_start, period_end, captured_at,
                plan.get("planId"), plan.get("planName") or "", promotion_status(launch.get("launchStatus")),
                launch.get("launchId"), launch.get("launchName") or "", launch.get("promoName") or "",
                launch.get("promoType"), launch.get("longShopId"), metrics["exposure_count"],
                metrics["click_count"], metrics["booking_order_count"], metrics["room_night_count"],
                booking_amount, metrics["spend_amount"], metrics["cost_per_click"], click_rate,
                metrics["merchant_view_count"], metrics["cash_spend_amount"],
            ]))
    return rows


def save_rows(hotel_id: str, rows: list[tuple[Any, ...]]) -> None:
    import pymysql

    hotel_id = hotel_id.strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is required for promotion performance sync")
    if any(str(row[0] or "").strip() != hotel_id for row in rows):
        raise RuntimeError("Promotion performance rows contain a different hotel_id")

    connection = pymysql.connect(**DB_CONFIG, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s", (hotel_id,))
            placeholders = ", ".join(["%s"] * len(COLUMNS))
            updates = ", ".join(
                f"`{column}`=VALUES(`{column}`)" for column in COLUMNS if column not in {
                    "hotel_id", "period_end_date", "plan_id", "launch_id"
                }
            )
            columns = ", ".join(f"`{column}`" for column in COLUMNS)
            if rows:
                cursor.executemany(
                    f"INSERT INTO `{TABLE_NAME}` ({columns}) VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {updates}", rows,
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    period_end = date.today() - timedelta(days=1)
    period_start = period_end - timedelta(days=PERIOD_DAYS - 1)
    plans = fetch_plans(
        os.environ.get("MEITUAN_PROMOTION_PERFORMANCE_URL", "").strip(), period_start, period_end
    )
    rows = build_rows(plans, period_start, period_end)
    save_rows(hotel_id, rows)
    print(f"promotion performance plans={len(plans)} launches={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
