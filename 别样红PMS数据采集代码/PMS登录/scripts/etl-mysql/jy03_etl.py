# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql

from config import DB_CONFIG, HOTEL_CONFIG


ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "output" / "JY03.json"
TABLE_NAME = "jy03_hotel_statistics_month"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
DIMENSION_SOURCES = [
    ("客源", "monthCustomerCategoryList"),
    ("入住类型", "monthCheckinTypeList"),
    ("订单来源", "monthOrderSourceList"),
    ("房型", "monthRoomTypeList"),
    ("渠道", "monthChannelList"),
]
METRIC_MAP = {
    "客房数": "room_count",
    "间夜数": "room_nights",
    "房费": "room_revenue",
    "维修房": "maintain_rooms",
    "平均房价": "adr",
    "出租率": "occupancy_rate",
    "RevPar": "revpar",
}


def to_number(value: Any, default: float = 0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def source_platform() -> str:
    return HOTEL_CONFIG.get("source_platform") or "PMS（别样红）"


def transform_jy03(payload: dict[str, Any], allowed_months: set[str] | None = None) -> list[dict[str, Any]]:
    data = payload.get("data", {}).get("data", {})
    snapshot_time = datetime.now()
    rows: list[dict[str, Any]] = []

    for item in data.get("monthSummaryDetailList", []):
        month = item.get("businessDateMonth")
        if not month or month == "总计" or (allowed_months and month not in allowed_months):
            continue

        room_count = to_number(item.get("roomCount"))
        room_nights = to_number(item.get("nightRoomCount"))
        room_revenue = to_number(item.get("roomRent"))
        maintain_rooms = to_number(item.get("maintainRoomCount"))

        available_rooms = max(room_count - maintain_rooms, 0)
        occupancy_rate = round(room_nights / available_rooms * 100, 2) if available_rooms else 0
        adr = round(room_revenue / room_nights, 2) if room_nights else 0
        revpar = round(room_revenue / room_count, 2) if room_count else 0

        row = base_row(month, "总营业指标", "总营业指标", snapshot_time)
        row.update(
            room_count=int(room_count),
            room_nights=room_nights,
            room_revenue=room_revenue,
            maintain_rooms=int(maintain_rooms),
            occupancy_rate=occupancy_rate,
            adr=adr,
            revpar=revpar,
        )
        rows.append(row)

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for dimension_type, list_key in DIMENSION_SOURCES:
        for item in data.get(list_key, []):
            month = item.get("businessDateMonth")
            dimension_name = item.get("analysisDimensionName") or ""
            metric_col = METRIC_MAP.get(item.get("analysisDimensionSubjectName"))
            if (
                not month
                or month == "总计"
                or (allowed_months and month not in allowed_months)
                or not dimension_name
                or not metric_col
            ):
                continue
            key = (month, dimension_type, dimension_name)
            row = grouped.setdefault(key, base_row(month, dimension_type, dimension_name, snapshot_time))
            value = to_number(item.get("analysisDimensionSubjectValue"), 0)
            row[metric_col] = int(value) if metric_col in {"room_count", "maintain_rooms"} else value

    rows.extend(grouped.values())
    return rows


def transform(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reports = payload.get("_reports")
    if not isinstance(reports, list):
        reports = [payload]
    allowed = set((payload.get("_meta") or {}).get("allowed_months") or [])
    return [
        row
        for report in reports
        if isinstance(report, dict)
        for row in transform_jy03(report, allowed or None)
    ]


def base_row(period_month: str, dimension_type: str, dimension_name: str, snapshot_time: datetime) -> dict[str, Any]:
    return {
        "hotel_name": HOTEL_CONFIG["name"],
        "hotel_id": HOTEL_ID,
        "source_platform": source_platform(),
        "period_month": period_month,
        "dimension_type": dimension_type,
        "dimension_name": dimension_name,
        "room_count": None,
        "room_nights": None,
        "room_revenue": None,
        "maintain_rooms": None,
        "occupancy_rate": None,
        "adr": None,
        "revpar": None,
        "snapshot_time": snapshot_time,
    }


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        print("JY03 无可入库数据")
        return

    sql = f"""
    INSERT INTO {TABLE_NAME} (
        hotel_name, hotel_id, source_platform, period_month, dimension_type, dimension_name, room_count, room_nights,
        room_revenue, maintain_rooms, occupancy_rate, adr, revpar, snapshot_time
    ) VALUES (
        %(hotel_name)s, %(hotel_id)s, %(source_platform)s, %(period_month)s, %(dimension_type)s, %(dimension_name)s, %(room_count)s, %(room_nights)s,
        %(room_revenue)s, %(maintain_rooms)s, %(occupancy_rate)s, %(adr)s, %(revpar)s, %(snapshot_time)s
    )
    ON DUPLICATE KEY UPDATE
        room_count = VALUES(room_count),
        room_nights = VALUES(room_nights),
        room_revenue = VALUES(room_revenue),
        maintain_rooms = VALUES(maintain_rooms),
        occupancy_rate = VALUES(occupancy_rate),
        adr = VALUES(adr),
        revpar = VALUES(revpar),
        snapshot_time = VALUES(snapshot_time)
    """

    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
    print(f"JY03 入库完成：{len(rows)} 行")


def main(conn=None) -> None:
    with JSON_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    rows = transform(payload)
    print("JY03 转换完成：", len(rows))
    if rows:
        print("JY03 示例：", rows[0])
    upsert_mysql(rows, conn)


if __name__ == "__main__":
    main()
