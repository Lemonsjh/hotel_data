from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from meituan_config import HOTEL_NAME, MEITUAN_EB_COOKIE, PARTNER_ID, POI_ID, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_latest_row


API_URL = "https://eb.meituan.com/api/shepherdGw/bizDatacenter/hotel/eb/dataCenter/analyse/flowConversion"
TABLE_NAME = "meituan_ota_flow_conversion_30d"
DATE_RANGE = 30
HEADERS = [
    "hotel_id", "hotel_name", "business_date", "period_start_date", "period_end_date",
    "snapshot_time", "data_updated_at", "exposure_uv", "browse_uv", "pay_order_count",
    "exposure_to_browse_rate_pct", "browse_to_pay_rate_pct", "peer_exposure_uv",
    "peer_browse_uv", "peer_pay_order_count", "peer_exposure_to_browse_rate_pct",
    "peer_browse_to_pay_rate_pct",
]


def number(value: Any) -> float | int | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return int(result) if result.is_integer() else result
    except (TypeError, ValueError):
        return None


def data_updated_at(value: Any, fallback: datetime) -> datetime:
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp)
    except (TypeError, ValueError, OSError):
        return fallback


def fetch_flow_conversion() -> dict[str, Any]:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("MEITUAN_EB_COOKIE is empty; run Meituan Edge login first")
    response = requests.get(
        API_URL,
        params={
            "poiId": POI_ID,
            "partnerId": PARTNER_ID,
            "dateRange": DATE_RANGE,
            "dataScope": "vpoi",
            "yodaReady": "h5",
            "csecplatform": "4",
            "csecversion": "4.2.4",
        },
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://eb.meituan.com/",
            "Origin": "https://eb.meituan.com",
            "Cookie": MEITUAN_EB_COOKIE,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"Meituan flow conversion response is invalid: {payload.get('status')}")
    return payload["data"]


def build_row(data: dict[str, Any], captured_at: datetime) -> list[object]:
    updated_at = data_updated_at(data.get("rtDataUpdateTime"), captured_at)
    period_end = updated_at.date()
    period_start = period_end - timedelta(days=DATE_RANGE - 1)
    my_hotel = data.get("myHotel") or {}
    peer_avg = data.get("peerAvg") or {}
    return [
        os.environ.get("HOTEL_ID", "").strip(), HOTEL_NAME, period_end, period_start, period_end,
        captured_at, updated_at, number(my_hotel.get("exposureUV")), number(my_hotel.get("intentionUV")),
        number(my_hotel.get("payOrderCnt")), number(my_hotel.get("intentionPerExposure")),
        number(my_hotel.get("payOrderPerIntention")), number(peer_avg.get("exposureUV")),
        number(peer_avg.get("intentionUV")), number(peer_avg.get("payOrderCnt")),
        number(peer_avg.get("intentionPerExposure")), number(peer_avg.get("payOrderPerIntention")),
    ]


def write_output(row: list[object], data: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {header: value.isoformat() if hasattr(value, "isoformat") else value for header, value in zip(HEADERS, row)}
    payload["index_name"] = data.get("indexName") or {}
    (OUTPUT_DIR / "meituan_ota_flow_conversion_30d.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    captured_at = datetime.now()
    data = fetch_flow_conversion()
    row = build_row(data, captured_at)
    write_output(row, data)
    sync_latest_row(TABLE_NAME, HEADERS, row)
    print("Meituan 30-day flow conversion sync completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
