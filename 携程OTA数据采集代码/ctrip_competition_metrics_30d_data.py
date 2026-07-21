from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ctrip_business_data import CtripClient, percent_points, round_number
from ctrip_config import COOKIE, DEFAULT_HOTEL_NAME

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_metric_history_table


TABLE_NAME = "ctrip_ota_competition_metrics_30d"
ENDPOINT = "/restapi/soa2/24588/getManagementData"
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "metric_code", "metric_name", "metric_unit",
    "period_start_date", "period_end_date", "snapshot_time", "hotel_value", "previous_value",
    "competitor_avg", "competitor_rank", "previous_rank",
]
METRICS = {
    0: ("booking_order_count", "\u9884\u8ba2\u8ba2\u5355\u91cf", "order"),
    1: ("booking_sales_amount", "\u9884\u8ba2\u9500\u552e\u989d", "CNY"),
    2: ("inhouse_room_night", "\u5728\u5e97\u95f4\u591c", "room_night"),
    3: ("occupancy_rate", "\u51fa\u79df\u7387", "%"),
    4: ("ctrip_app_visitor_count", "\u643a\u7a0bAPP\u8bbf\u5ba2", "person"),
    5: ("ctrip_app_conversion_rate", "\u643a\u7a0bAPP\u8f6c\u5316\u7387", "%"),
}


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def latest_period(today: date | None = None) -> tuple[date, date]:
    end_date = (today or date.today()) - timedelta(days=1)
    return end_date - timedelta(days=29), end_date


def metric_value(value: Any, unit: str) -> int | float | None:
    if value in (None, ""):
        return None
    parsed = percent_points(value) if unit == "%" else round_number(value)
    return parsed if isinstance(parsed, (int, float)) else None


def query_metrics(client: CtripClient, period_start: date, period_end: date) -> list[dict[str, Any]]:
    response = client.post_json(
        ENDPOINT,
        {
            "dateType": 5,
            "beginDate": period_start.isoformat(),
            "endDate": period_end.isoformat(),
            "cipher": {},
            "header": {"platform": "WEB"},
        },
    )
    if not isinstance(response, dict):
        raise RuntimeError("Ctrip competition response is invalid")
    status = response.get("resStatus")
    if isinstance(status, dict) and status.get("rcode") not in (None, 0, 200, "0", "200"):
        raise RuntimeError(f"Ctrip competition request failed: {status.get('rmsg') or status.get('rcode')}")
    items = response.get("dataList")
    if not isinstance(items, list) or not items:
        raise RuntimeError("Ctrip competition response contains no metrics")
    return [item for item in items if isinstance(item, dict)]


def build_rows(
    items: list[dict[str, Any]], hotel_id: str, captured_at: datetime, period_start: date, period_end: date,
) -> list[list[Any]]:
    rows = []
    for item in items:
        metric = METRICS.get(item.get("indexType"))
        if not metric:
            continue
        metric_code, metric_name, metric_unit = metric
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", metric_code, metric_name, metric_unit,
            period_start, period_end, captured_at, metric_value(item.get("val"), metric_unit),
            metric_value(item.get("lastVal"), metric_unit), metric_value(item.get("avgComp"), metric_unit),
            metric_value(item.get("rankComp"), "rank"), metric_value(item.get("lastRank"), "rank"),
        ])
    if not rows:
        raise RuntimeError("Ctrip competition response has no recognized metrics")
    return rows


def write_output(rows: list[list[Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = [dict(zip(HEADERS, row, strict=True)) for row in rows]
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def main() -> int:
    period_start, period_end = latest_period()
    captured_at = datetime.now()
    items = query_metrics(CtripClient(COOKIE), period_start, period_end)
    rows = build_rows(items, require_hotel_id(), captured_at, period_start, period_end)
    sync_metric_history_table(
        TABLE_NAME, HEADERS, rows,
        {"hotel_id", "platform_scope", "metric_code"}, retention_days=None,
    )
    write_output(rows)
    print(f"Ctrip 30-day competition metrics sync completed: rows={len(rows)} period={period_start}~{period_end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
