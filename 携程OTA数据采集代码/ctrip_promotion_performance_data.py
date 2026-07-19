from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ctrip_config import DEFAULT_HOTEL_NAME
from ctrip_flow_conversion_data import FlowBrowserClient, number

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import sync_table


API_URL = "https://ebooking.ctrip.com/toolcenter/api/cpc/queryCampaignSummaryReport?hostType=HE"
TABLE_NAME = "ctrip_ota_promotion_performance_30d"
PERIOD_DAYS = 30
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "period_start_date", "period_end_date", "snapshot_time",
    "exposure_count", "click_count", "click_rate_pct", "spend_amount", "bonus_spend_amount",
    "cash_spend_amount", "cost_per_click", "booking_order_count", "booking_order_amount",
    "room_night_count", "conversion_rate_pct", "return_on_ad_spend", "avg_exposure_position",
    "avg_click_position", "ebk_order_count", "other_order_count", "data_delayed",
]


def percent(value: Any) -> float | None:
    parsed = number(value)
    return round(float(parsed) * 100, 2) if parsed is not None else None


def request_payload(start: date, end: date) -> dict[str, Any]:
    return {
        "campaignId": "", "startDate": start.isoformat(), "endDate": end.isoformat(),
        "keyword": "", "keywordType": "", "pageIdx": 1, "pageSize": 366,
        "isSummary": True, "convertPeriod": 3, "premiumCodes": [], "isChart": False,
    }


def build_row(payload: dict[str, Any], start: date, end: date, captured_at: datetime) -> list[Any]:
    report = ((payload.get("data") or {}).get("cpcCampaignReportBo") or {})
    if payload.get("code") != 0 or not isinstance(report, dict):
        raise RuntimeError(f"Ctrip promotion report failed: {payload.get('message') or 'invalid response'}")
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return [
        hotel_id, DEFAULT_HOTEL_NAME, "ctrip", start, end, captured_at,
        number(report.get("impressions")), number(report.get("clicks")), percent(report.get("ctr")),
        number(report.get("todayCost")), number(report.get("bonusCost")), number(report.get("cashCost")),
        number(report.get("ecpc")), number(report.get("bookings")), number(report.get("orderAmount")),
        number(report.get("nights")), percent(report.get("cvr")), number(report.get("roas")),
        number(report.get("avgImpressionPosition")), number(report.get("avgClickPosition")),
        number((payload.get("data") or {}).get("ebkOrderNum")),
        number((payload.get("data") or {}).get("antherOrderNum")),
        int(bool((payload.get("data") or {}).get("delay"))),
    ]


def main() -> int:
    captured_at = datetime.now()
    end = captured_at.date() - timedelta(days=1)
    start = end - timedelta(days=PERIOD_DAYS - 1)
    with FlowBrowserClient() as client:
        row = build_row(client.post_json(API_URL, request_payload(start, end)), start, end, captured_at)
    sync_table(TABLE_NAME, HEADERS, [row])
    print("Ctrip 30-day promotion performance sync completed: rows=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
