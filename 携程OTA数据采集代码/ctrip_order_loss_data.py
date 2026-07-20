from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ctrip_config import DEFAULT_HOTEL_NAME
from ctrip_flow_conversion_data import FlowBrowserClient, number

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_metric_history_table


API_URL = "https://ebooking.ctrip.com/restapi/soa2/24588/getTripartiteOrderLoss"
TABLE_NAME = "ctrip_ota_order_loss_monthly"
PLATFORM_SCOPES = ("ctrip", "qunar")
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "business_date", "period_start_date", "period_end_date",
    "snapshot_time", "ranking_position", "competitor_hotel_name", "common_browse_rate_pct",
    "order_conversion_rate_pct", "loss_order_count",
]
LIST_KEYS = ("data", "dataList", "list", "items", "hotelList", "orderLossList", "lossHotelList", "lossList")
HOTEL_NAME_KEYS = ("hotelName", "poiName", "lossHotelName", "competitorHotelName", "name")
COMMON_BROWSE_KEYS = ("commonBrowseRate", "sameBrowseRate", "commonViewRate", "browseRate", "browseRatio", "proportion")
CONVERSION_KEYS = ("orderConversionRate", "orderConvertRate", "conversionRate", "orderRate", "convertRate", "cr")
LOSS_ORDER_KEYS = ("lossOrderCount", "lostOrderCount", "orderLossCount", "lossOrderNum", "orderCount", "orderNumComp")


def previous_month_window(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def request_payload(client: FlowBrowserClient, scope: str, start: date, end: date) -> dict[str, Any]:
    payload = client.post_json(
        API_URL,
        {
            "ota": scope,
            "dateType": 4,
            "beginDate": start.isoformat(),
            "endDate": end.isoformat(),
            "pageNo": 1,
            "pageSize": 40,
            "sortKey": 0,
            "desc": 2,
            "cipher": {},
            "header": {"platform": "WEB"},
        },
    )
    status = payload.get("resStatus") or {}
    if status and status.get("rcode") != 200:
        raise RuntimeError(f"Ctrip order-loss request failed for {scope}: {status.get('rmsg') or 'unknown error'}")
    return payload


def lower_map(item: dict[str, Any]) -> dict[str, Any]:
    values = {str(key).lower(): value for key, value in item.items()}
    for key in ("hotel", "hotelInfo", "competitorHotel", "poi", "order"):
        nested = item.get(key)
        if isinstance(nested, dict):
            values.update({str(name).lower(): value for name, value in nested.items()})
    return values


def first_value(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    values = lower_map(item)
    for name in names:
        value = values.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def has_main_fields(item: dict[str, Any]) -> bool:
    return any(first_value(item, names) is not None for names in (HOTEL_NAME_KEYS, COMMON_BROWSE_KEYS, CONVERSION_KEYS, LOSS_ORDER_KEYS))


def find_items(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 3 or not isinstance(value, dict):
        return []
    for key in LIST_KEYS:
        items = value.get(key)
        if isinstance(items, list) and any(isinstance(item, dict) and has_main_fields(item) for item in items):
            return [item for item in items if isinstance(item, dict)]
    for nested in value.values():
        if isinstance(nested, dict):
            items = find_items(nested, depth + 1)
            if items:
                return items
    return []


def percent(value: Any) -> int | float | None:
    parsed = number(value)
    if parsed is None:
        return None
    parsed = float(parsed)
    if 0 < abs(parsed) <= 1:
        parsed *= 100
    return round(parsed, 2)


def build_rows(payload: dict[str, Any], scope: str, start: date, end: date, captured_at: datetime) -> list[list[Any]]:
    items = find_items(payload.get("data") if isinstance(payload.get("data"), dict) else payload)
    if not items:
        raise RuntimeError(f"Ctrip order-loss detail list is missing for {scope}")
    hotel_id = require_hotel_id()
    rows = []
    for position, item in enumerate(items[:40], 1):
        hotel_name = first_value(item, HOTEL_NAME_KEYS)
        if not hotel_name:
            continue
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, scope, end, start, end, captured_at, position, str(hotel_name).strip(),
            percent(first_value(item, COMMON_BROWSE_KEYS)), percent(first_value(item, CONVERSION_KEYS)),
            number(first_value(item, LOSS_ORDER_KEYS)),
        ])
    if not rows:
        raise RuntimeError(f"Ctrip order-loss detail rows are empty for {scope}")
    return rows


def write_output(rows: list[list[Any]], start: date, end: date, captured_at: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "table_name": TABLE_NAME,
        "period_start_date": start.isoformat(),
        "period_end_date": end.isoformat(),
        "snapshot_time": captured_at.isoformat(sep=" ", timespec="seconds"),
        "row_count": len(rows),
        "rows": [
            {header: value.isoformat() if hasattr(value, "isoformat") else value for header, value in zip(HEADERS, row)}
            for row in rows
        ],
    }
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    captured_at = datetime.now()
    start, end = previous_month_window(captured_at.date())
    rows: list[list[Any]] = []
    with FlowBrowserClient() as client:
        for scope in PLATFORM_SCOPES:
            rows.extend(build_rows(request_payload(client, scope, start, end), scope, start, end, captured_at))
    write_output(rows, start, end, captured_at)
    sync_metric_history_table(
        TABLE_NAME, HEADERS, rows,
        {"hotel_id", "platform_scope", "period_end_date", "ranking_position"}, None,
    )
    print(f"Ctrip order-loss sync completed: rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
