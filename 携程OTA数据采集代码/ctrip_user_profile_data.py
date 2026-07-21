from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ctrip_config import DEFAULT_HOTEL_NAME
from ctrip_flow_conversion_data import FlowBrowserClient, number

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_table


API_BASE = "https://ebooking.ctrip.com/datacenter/api/dataCenter/userbehavior"
ENDPOINTS = {
    "gender": "queryUserSex",
    "travel_type": "queryUserType",
    "booking_lead": "queryUserBookingDays",
    "age": "queryUserAge",
    "travel_time": "queryUserTravelTime",
    "origin": "queryUserSource",
    "consumption": "queryUserPrice",
    "stay_days": "queryUserStayDays",
    "star_preference": "queryUserStar",
    "order_peak_time": "getOrderDistribution",
}
TABLE_NAME = "ctrip_ota_userprofile_distribution"
DISTRIBUTION_HEADERS = [
    "hotel_id", "hotel_name", "platform_scope", "snapshot_time",
    "dimension_code", "bucket_label", "rate_pct", "metric_value", "metric_unit", "rank_position",
]


def require_hotel_id() -> str:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise RuntimeError("HOTEL_ID is empty; configure the Ctrip internal hotel ID")
    return hotel_id


def request_json(client: FlowBrowserClient, endpoint: str) -> dict[str, Any]:
    result = client.page.evaluate(
        """async (url) => {
            for (const method of ['GET', 'POST']) {
                const response = await fetch(url, {
                    method, credentials: 'include',
                    headers: method === 'POST' ? {'Content-Type': 'application/json;charset=UTF-8'} : {},
                    body: method === 'POST' ? '{}' : undefined,
                });
                const contentType = response.headers.get('content-type') || '';
                const body = contentType.includes('application/json') ? await response.json() : null;
                if (body && body.rcode === 0) return {body};
            }
            return {body: null};
        }""",
        f"{API_BASE}/{endpoint}",
    )
    payload = result.get("body") if isinstance(result, dict) else None
    if not isinstance(payload, dict) or payload.get("rcode") != 0:
        raise RuntimeError(f"Ctrip user-profile request failed: {endpoint}")
    return payload


def list_values(payload: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
    values = payload.get("data")
    if not isinstance(values, list):
        raise RuntimeError(f"Ctrip user-profile response is invalid: {endpoint}")
    rows = [item for item in values if isinstance(item, dict) and str(item.get("name") or "").strip()]
    if not rows:
        raise RuntimeError(f"Ctrip user-profile response is empty: {endpoint}")
    return rows


def object_values(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
    values = payload.get("data")
    if not isinstance(values, dict):
        raise RuntimeError(f"Ctrip user-profile response is invalid: {endpoint}")
    return values


def chart_values(values: dict[str, Any], endpoint: str) -> list[tuple[str, int | float | None]]:
    titles = values.get("titleList")
    rates = values.get("valueList")
    if not isinstance(titles, list) or not isinstance(rates, list) or len(titles) != len(rates) or not titles:
        raise RuntimeError(f"Ctrip user-profile chart is invalid: {endpoint}")
    return [(str(title).strip(), number(rate)) for title, rate in zip(titles, rates) if str(title).strip()]


def hourly_order_values(values: dict[str, Any]) -> list[tuple[str, int | float | None, int]]:
    entries = values.get("orderDistributionEntities")
    if not isinstance(entries, list):
        raise RuntimeError("Ctrip user-profile order-hour distribution is invalid")
    rows = []
    for item in entries:
        hour = str(item.get("hour") or "").strip() if isinstance(item, dict) else ""
        if not hour or ":" not in hour:
            continue
        try:
            position = int(hour.split(":", 1)[0])
        except ValueError:
            continue
        rows.append((hour, number(item.get("proportion")), position))
    if len(rows) != 24 or {position for _, _, position in rows} != set(range(24)):
        raise RuntimeError("Ctrip user-profile order-hour distribution is incomplete")
    return sorted(rows, key=lambda row: row[2])


def distribution_rows(
    hotel_id: str, captured_at: datetime, dimension: str, values: list[tuple[str, Any]]
) -> list[list[Any]]:
    return [
        [hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at, dimension, label, number(value), None, None, position]
        for position, (label, value) in enumerate(values, 1)
    ]


def build_rows(payloads: dict[str, dict[str, Any]], captured_at: datetime) -> list[list[Any]]:
    hotel_id = require_hotel_id()
    booking = object_values(payloads["booking_lead"], ENDPOINTS["booking_lead"])
    age = object_values(payloads["age"], ENDPOINTS["age"])
    consumption = object_values(payloads["consumption"], ENDPOINTS["consumption"])
    stay_days = object_values(payloads["stay_days"], ENDPOINTS["stay_days"])
    star_preference = object_values(payloads["star_preference"], ENDPOINTS["star_preference"])
    order_peak_time = object_values(payloads["order_peak_time"], ENDPOINTS["order_peak_time"])
    origin = object_values(payloads["origin"], ENDPOINTS["origin"])
    dimensions = {
        "gender": [(item["name"], item.get("value")) for item in list_values(payloads["gender"], ENDPOINTS["gender"])],
        "travel_type": [(item["name"], item.get("value")) for item in list_values(payloads["travel_type"], ENDPOINTS["travel_type"])],
        "booking_advance_days": chart_values(booking, ENDPOINTS["booking_lead"]),
        "age_group": chart_values(age, ENDPOINTS["age"]),
        "travel_time": [(item["name"], item.get("value")) for item in list_values(payloads["travel_time"], ENDPOINTS["travel_time"])],
        "consumption_price": chart_values(consumption, ENDPOINTS["consumption"]),
        "stay_days": chart_values(stay_days, ENDPOINTS["stay_days"]),
        "hotel_star_preference": chart_values(star_preference, ENDPOINTS["star_preference"]),
        "city_origin": [
            ("\u672c\u5730", origin.get("localCityRate")),
            ("\u5f02\u5730", origin.get("otherCityRate")),
        ],
        "city_origin_top5": [
            (item.get("name"), item.get("value"))
            for item in origin.get("cities", [])[:5]
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ],
    }
    rows = [row for dimension, values in dimensions.items() for row in distribution_rows(hotel_id, captured_at, dimension, values)]
    average_stay_nights = number(stay_days.get("avg"))
    if average_stay_nights is not None:
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at, "stay_days",
            "\u5e73\u5747\u5165\u4f4f\u665a\u6570", None, average_stay_nights, "nights", 0,
        ])
    average_advance_booking_days = number(booking.get("avg"))
    if average_advance_booking_days is not None:
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at, "booking_advance_days",
            "avg_advance_booking_days", None, average_advance_booking_days, "days", 0,
        ])
    peak_hour = str(order_peak_time.get("maxProportionHour") or "").strip()
    peak_rate = number(order_peak_time.get("maxProportion"))
    if peak_hour and peak_rate is not None:
        rows.append([
            hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at, "order_peak_time",
            peak_hour, peak_rate, None, None, 0,
        ])
    rows.extend([
        [hotel_id, DEFAULT_HOTEL_NAME, "ctrip", captured_at, "order_hourly_distribution",
         hour, rate, None, None, position]
        for hour, rate, position in hourly_order_values(order_peak_time)
    ])
    if not rows:
        raise RuntimeError("Ctrip user-profile distribution is empty")
    return rows


def write_output(rows: list[list[Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "table_name": TABLE_NAME,
        "row_count": len(rows),
        "rows": [
            {header: value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) else value
             for header, value in zip(DISTRIBUTION_HEADERS, row)}
            for row in rows
        ],
    }
    (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    captured_at = datetime.now()
    with FlowBrowserClient() as client:
        payloads = {name: request_json(client, endpoint) for name, endpoint in ENDPOINTS.items()}
    rows = build_rows(payloads, captured_at)
    write_output(rows)
    sync_table(TABLE_NAME, DISTRIBUTION_HEADERS, rows)
    print(f"Ctrip user profile sync completed: rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
