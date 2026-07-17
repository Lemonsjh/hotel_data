from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from meituan_config import BIZ_ACCOUNT_ID, MEITUAN_EB_COOKIE, PARTNER_ID, POI_ID, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_meituan_scan_orders


API_URL = "https://eb.meituan.com/api/v1/ebooking/merMember/queryCompleteOrderDetailByPage"
PAGE_SIZE = 100
HEADERS = [
    "hotel_id", "order_id", "scan_time", "scan_source", "user_type", "order_status",
    "check_in_time", "real_pay_amount", "collected_at",
]


def report_window(now: datetime) -> tuple[date, date]:
    return now.date() - timedelta(days=30), now.date()


def request_page(start_date: date, end_date: date, page_number: int) -> dict[str, Any]:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("MEITUAN_EB_COOKIE is empty; run Meituan Edge login first")
    response = requests.get(
        API_URL,
        params={
            "poiId": POI_ID,
            "partnerId": PARTNER_ID,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "currPage": page_number,
            "numPerPage": PAGE_SIZE,
            "yodaReady": "h5",
            "csecplatform": 4,
            "csecversion": "4.2.4",
            "_mtsi_eb_u": BIZ_ACCOUNT_ID,
            "_mtsi_eb_p": PARTNER_ID,
            "optimus_uuid": PARTNER_ID,
            "optimus_risk_level": 71,
            "optimus_code": 10,
            "login_type": "unknown",
        },
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://eb.meituan.com/",
            "Cookie": MEITUAN_EB_COOKIE,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if payload.get("status") != 0 or not isinstance(data, dict):
        raise RuntimeError(f"Meituan scan-order API failed: status={payload.get('status')}")
    return data


def fetch_orders(start_date: date, end_date: date) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    total = None
    for page_number in range(1, 101):
        data = request_page(start_date, end_date, page_number)
        details = data.get("completeOrderDetailInfo") or {}
        rows = details.get("completeOrderDetailList") if isinstance(details, dict) else []
        valid_rows = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            order_id = str(row.get("orderIdStr") or "").strip()
            if not order_id:
                continue
            normalized = dict(row)
            normalized["orderIdStr"] = order_id
            valid_rows.append(normalized)
        rows = valid_rows
        orders.extend(rows)
        total = int(data.get("totalNum") or 0)
        if len(orders) >= total or len(rows) < PAGE_SIZE:
            return orders
    raise RuntimeError("Meituan scan-order pagination exceeded 100 pages")


def scan_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp / 1000 if timestamp > 10_000_000_000 else timestamp)


def money_value(value: Any) -> str | None:
    matched = re.search(r"-?[\d,]+(?:\.\d+)?", str(value or ""))
    return matched.group(0).replace(",", "") if matched else None


def build_rows(
    orders: list[dict[str, Any]], start_date: date, end_date: date, collected_at: datetime
) -> list[list[object]]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    return [
        [
            hotel_id,
            str(item.get("orderIdStr") or "").strip(),
            scan_datetime(item.get("scanTime")),
            item.get("scanSource"),
            item.get("userType"),
            item.get("orderStatus"),
            item.get("checkInTime"),
            money_value(item.get("realPayMoney")),
            collected_at,
        ]
        for item in orders
    ]


def write_output(rows: list[list[object]], start_date: date, end_date: date) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "period_start_date": start_date.isoformat(),
        "period_end_date": end_date.isoformat(),
        "order_count": len(rows),
    }
    (OUTPUT_DIR / "meituan_ota_scan_orders.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    collected_at = datetime.now()
    start_date, end_date = report_window(collected_at)
    rows = build_rows(fetch_orders(start_date, end_date), start_date, end_date, collected_at)
    write_output(rows, start_date, end_date)
    sync_meituan_scan_orders(HEADERS, rows, os.environ.get("HOTEL_ID", "").strip(), start_date)
    print(f"Meituan scan orders collected: {len(rows)} ({start_date} to {end_date})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
