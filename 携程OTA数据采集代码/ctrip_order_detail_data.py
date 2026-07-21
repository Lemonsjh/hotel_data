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
from ota_mysql_writer import OUTPUT_DIR, sync_order_detail_history


API_URL = "https://ebooking.ctrip.com/restapi/soa2/27204/queryOrderList"
TABLE_NAME = "ctrip_ota_order_detail"
PAGE_SIZE = 20
MAX_PAGES = 100
HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "ctrip_hotel_id", "form_id", "order_id",
    "order_source_type", "ctrip_source_type", "alliance_name", "order_type", "order_type_name",
    "order_status_code", "order_status", "order_status_type", "booking_time", "arrival_date",
    "departure_date", "room_type_name", "room_quantity", "room_night_count", "guest_count",
    "currency", "payment_type", "payment_term", "is_auto_confirmed", "is_guaranteed",
    "is_hour_room", "is_credit_order", "is_free_room_order", "snapshot_time",
]
PLATFORM_SCOPES = {
    "ctrip": "ctrip",
    "elong": "elong",
    "qunar": "qunar",
    "tongcheng": "tongcheng",
    "zhixing": "zhixing",
    "zx": "zhixing",
    "zx12306": "zhixing",
}


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def int_value(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def flag(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return int(value.strip().lower() in {"1", "true", "yes", "y"})
    return int(bool(value))


def datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def date_value(value: Any) -> date | None:
    parsed = datetime_value(value)
    return parsed.date() if parsed else None


def request_payload(start: date, end: date, page_index: int, captured_at: datetime) -> dict[str, Any]:
    return {
        "timeZone": 8,
        "isHotelCompany": False,
        "orderCountTypes": ["UnBookingInvoice"],
        "isUnProcess": False,
        "orderQueryCondition": {
            "queryDateType": "ArrivalDate",
            "dateStart": start.isoformat(),
            "dateEnd": end.isoformat(),
            "formType": "All",
            "formTypes": [],
            "sourceType": "Ebooking",
            "receiveTypes": [],
            "allicanceNames": [],
            "unBookingInvoice": False,
            "queryOrderStatus": "All",
            "queryOrderStatuses": [],
            "queryCustemFilter": "None",
            "keyword": "",
            "roomName": "",
            "bookingNo": "",
            "confirmName": "",
            "isShowExtraInfo": True,
            "pageInfo": {"pageIndex": page_index, "orderBy": "FormDate", "sort": "Desc", "pageSize": PAGE_SIZE},
            "extraMap": {"DOMESTIC_NEW_WEB": "T", "UNPROCESS_COUNT_BY_SOURCE": "T"},
            "clientDateTime": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "header": {"platform": "WEB"},
    }


def fetch_orders(client: FlowBrowserClient, start: date, end: date, captured_at: datetime) -> list[dict[str, Any]]:
    orders_by_form_id: dict[str, dict[str, Any]] = {}
    previous_ids: set[str] = set()
    for page_index in range(1, MAX_PAGES + 1):
        payload = client.post_json(API_URL, request_payload(start, end, page_index, captured_at))
        status = payload.get("resStatus") or {}
        if status and status.get("rcode") not in (0, "0", 200, "200"):
            raise RuntimeError(f"Ctrip order-list request failed: {status.get('rmsg') or 'unknown error'}")
        items = payload.get("orderList")
        if not isinstance(items, list):
            raise RuntimeError("Ctrip order-list response is invalid")
        page_items = [item for item in items if isinstance(item, dict) and item.get("formId") not in (None, "")]
        page_ids = {str(item["formId"]) for item in page_items}
        if not page_items or page_ids == previous_ids:
            break
        orders_by_form_id.update({str(item["formId"]): item for item in page_items})
        if len(page_items) < PAGE_SIZE:
            break
        previous_ids = page_ids
    if not orders_by_form_id:
        raise RuntimeError("Ctrip order-list response contains no orders")
    return list(orders_by_form_id.values())


def platform_scope(item: dict[str, Any]) -> str:
    source = str(item.get("allinanceName") or "ctrip").strip().lower()
    return PLATFORM_SCOPES.get(source, "other")


def build_rows(items: list[dict[str, Any]], captured_at: datetime) -> list[list[Any]]:
    hotel_id = require_hotel_id()
    rows = []
    for item in items:
        form_id = int_value(item.get("formId"))
        arrival_date = date_value(item.get("arrival"))
        if form_id is None or arrival_date is None:
            continue
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, platform_scope(item), int_value(item.get("hotel")), form_id,
            str(item.get("orderId") or "").strip() or None, item.get("sourceType"), item.get("ctripSourceType"),
            item.get("allinanceName"), item.get("orderType"), item.get("orderTypeDesc"),
            int_value(item.get("orderStatus")), item.get("orderStatusDesc"), item.get("orderStatusType"),
            datetime_value(item.get("formDateOriginal") or item.get("formDate")), arrival_date,
            date_value(item.get("departure")), item.get("roomName"), int_value(item.get("quantity")),
            int_value(item.get("liveDays")), int_value(item.get("persons")), item.get("currency"),
            item.get("paymentType"), item.get("paymentTerm"), flag(item.get("isAutoConfirmed")),
            flag(item.get("isGuaranteed")), flag(item.get("isHourRoom")), flag(item.get("isCreditOrder")),
            flag(item.get("isFreeRoomOrder")), captured_at,
        ])
    if not rows:
        raise RuntimeError("Ctrip order-list contains no valid order rows")
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
            {header: value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat() if isinstance(value, date) else value
             for header, value in zip(HEADERS, row)}
            for row in rows
        ],
    }
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    captured_at = datetime.now()
    end = captured_at.date()
    start = end - timedelta(days=30)
    with FlowBrowserClient() as client:
        rows = build_rows(fetch_orders(client, start, end, captured_at), captured_at)
    write_output(rows, start, end, captured_at)
    sync_order_detail_history(TABLE_NAME, HEADERS, rows, require_hotel_id(), start)
    print(f"Ctrip order detail sync completed: rows={len(rows)} period={start}..{end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
