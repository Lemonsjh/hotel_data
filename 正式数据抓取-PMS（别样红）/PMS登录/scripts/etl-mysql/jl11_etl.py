# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql

from config import DB_CONFIG, HOTEL_CONFIG


ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "output" / "JL11.json"
TABLE_NAME = "jl11_room_type_classification"
RETENTION_DAYS = 30
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip() or str(HOTEL_CONFIG.get("id") or "").strip()
SUMMARY_FIELDS = {
    "roomCount": ("room_count", False),
    "roomPoint": ("room_nights", False),
    "roomRate": ("occupancy_rate", True),
    "avgRoomPrice": ("average_room_price", False),
    "revPar": ("revpar", False),
    "roomFee": ("room_revenue", False),
    "emptyRoomCount": ("overnight_room_count", False),
    "debitSummary": ("overnight_occupancy_rate", True),
}
DETAIL_FIELDS = {
    "RoomPoint": "room_nights",
    "RoomRate": "occupancy_rate",
    "RoomRent": "room_revenue",
    "AvgPrice": "average_room_price",
    "RevPar": "revpar",
}
SECTION_NAMES = {"channels": "channel", "customers": "customer", "checkins": "checkin"}
NUMERIC_FIELDS = (*DETAIL_FIELDS.values(), *[field for field, _ in SUMMARY_FIELDS.values()])


def number(value: Any, percentage: bool = False) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
        return round(result * 100, 4) if percentage else result
    except (TypeError, ValueError):
        return None


def report_period(payload: dict[str, Any]) -> tuple[str, str]:
    query = payload.get("_query") or {}
    variables = payload.get("data", {}).get("variables") or {}
    start = str(query.get("startDate") or variables.get("startDate") or "").split(" ")[0]
    end = str(query.get("endDate") or variables.get("endDate") or "").split(" ")[0]
    if not start or not end:
        raise RuntimeError("JL11 report period is missing")
    return start, end


def base_row(start: str, end: str, snapshot: datetime) -> dict[str, Any]:
    return {
        "hotel_id": HOTEL_ID,
        "hotel_name": HOTEL_CONFIG["name"],
        "source_platform": HOTEL_CONFIG.get("source_platform") or "PMS",
        "snapshot_date": snapshot.date(),
        "period_start": start,
        "period_end": end,
        "snapshot_time": snapshot,
        **{field: None for field in NUMERIC_FIELDS},
    }


def transform(payload: dict[str, Any]) -> list[dict[str, Any]]:
    start, end = report_period(payload)
    snapshot = datetime.now()
    base = base_row(start, end, snapshot)
    data = payload.get("data", {}).get("data", {})
    rows: list[dict[str, Any]] = []
    for item in data.get("roomTypeSummaryDtoList", []):
        room_type_name = str(item.get("roomType") or "").strip()
        if not room_type_name:
            continue
        row = {
            **base,
            "section": "summary",
            "room_type_id": None,
            "room_type_name": room_type_name,
            "dimension_code": "",
            "dimension_name": "",
        }
        for source, (field, percentage) in SUMMARY_FIELDS.items():
            row[field] = number(item.get(source), percentage)
        rows.append(row)
    for source, section in SECTION_NAMES.items():
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in data.get(source, []):
            field = DETAIL_FIELDS.get(str(item.get("ic") or ""))
            room_type_name = str(item.get("rt") or "").strip()
            dimension_code = str(item.get("dc") or "").strip()
            if not field or not room_type_name or not dimension_code:
                continue
            key = (room_type_name, dimension_code, str(item.get("dn") or "").strip())
            row = grouped.setdefault(
                key,
                {
                    **base,
                    "section": section,
                    "room_type_id": None,
                    "room_type_name": room_type_name,
                    "dimension_code": dimension_code,
                    "dimension_name": key[2],
                    "room_count": number(item.get("rc")),
                },
            )
            row[field] = number(item.get("v"))
        rows.extend(grouped.values())
    return rows


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        raise RuntimeError("JL11 has no valid rows")
    columns = (
        "hotel_id, hotel_name, source_platform, snapshot_date, period_start, period_end, section, "
        "room_type_id, room_type_name, dimension_code, dimension_name, room_count, room_nights, "
        "occupancy_rate, room_revenue, average_room_price, revpar, overnight_room_count, "
        "overnight_occupancy_rate, snapshot_time"
    )
    values = ", ".join(f"%({column})s" for column in columns.split(", "))
    sql = f"""
    INSERT INTO `{TABLE_NAME}` ({columns}) VALUES ({values})
    ON DUPLICATE KEY UPDATE
        hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
        dimension_name=VALUES(dimension_name), room_count=VALUES(room_count),
        room_nights=VALUES(room_nights), occupancy_rate=VALUES(occupancy_rate),
        room_revenue=VALUES(room_revenue), average_room_price=VALUES(average_room_price),
        revpar=VALUES(revpar), overnight_room_count=VALUES(overnight_room_count),
        overnight_occupancy_rate=VALUES(overnight_occupancy_rate), snapshot_time=VALUES(snapshot_time)
    """
    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
            cursor.execute(
                f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s AND period_end<%s",
                (HOTEL_ID, date.today() - timedelta(days=RETENTION_DAYS)),
            )
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
    print(f"JL11 database sync completed: {len(rows)} rows")


def main(conn=None) -> None:
    if not HOTEL_ID:
        raise RuntimeError("HOTEL_ID is required for JL11")
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    upsert_mysql(transform(payload), conn)


if __name__ == "__main__":
    main()
