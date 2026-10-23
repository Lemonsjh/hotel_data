# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import pymysql
except ImportError:  # pragma: no cover
    pymysql = None

from config import DB_CONFIG, HOTEL_CONFIG


ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = ROOT / "output" / "JY01.json"
TABLE_NAME = "jy01_hotel_statistics_daily"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
DIMENSION_SOURCES = [
    ("客源", "dailyCustomerCategoryList"),
    ("入住类型", "dailyCheckinTypeList"),
    ("订单来源", "dailyOrderSourceList"),
    ("房型", "dailyRoomTypeList"),
    ("渠道", "dailyChannelList"),
]
METRIC_MAP = {
    "客房数": "room_count",
    "间夜数": "room_nights",
    "房费": "room_revenue",
    "平均房价": "adr",
    "出租率": "occupancy_rate",
    "RevPar": "revpar",
}


def get_conn():
    if pymysql is None:
        raise ImportError("未安装 pymysql，请先执行：pip install pymysql")
    return pymysql.connect(**DB_CONFIG)


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


def transform_jy01(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {}).get("data", {})
    snapshot_time = datetime.now()
    
    query_date = payload.get("data", {}).get("variables", {}).get("startDate")
    if not query_date:
        query_date = (snapshot_time - timedelta(days=1)).strftime("%Y-%m-%d")
    
    rows: list[dict[str, Any]] = []

    for item in data.get("dailySummaryDetailList", []):
        business_date = item.get("businessDate") or query_date
        room_count = to_number(item.get("roomCount"))
        sold_rooms = to_number(item.get("nightRoomCount"))
        maintain_rooms = to_number(item.get("maintainRoomCount"))
        room_revenue = to_number(item.get("roomRent"))

        available_rooms = max(room_count - maintain_rooms, 0)
        occupancy_rate = round(sold_rooms / available_rooms * 100, 2) if available_rooms else 0
        adr = round(room_revenue / sold_rooms, 2) if sold_rooms else 0
        revpar = round(room_revenue / room_count, 2) if room_count else 0
        remaining_rooms = max(available_rooms - sold_rooms, 0)

        row = base_row(business_date, "总营业指标", "总营业指标", snapshot_time)
        row.update(
            room_count=int(room_count),
            room_nights=sold_rooms,
            room_revenue=room_revenue,
            occupancy_rate=occupancy_rate,
            adr=adr,
            revpar=revpar,
            sold_rooms=int(sold_rooms),
            remaining_rooms=int(remaining_rooms),
            orders_today=int(to_number(item.get("ordersToday"), sold_rooms)),
        )
        rows.append(row)

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for dimension_type, list_key in DIMENSION_SOURCES:
        for item in data.get(list_key, []):
            business_date = item.get("businessDate") or query_date
            dimension_name = item.get("analysisDimensionName") or ""
            metric_col = METRIC_MAP.get(item.get("analysisDimensionSubjectName"))
            week_flag = item.get("businessDateWeek")
            if not dimension_name or not metric_col or week_flag == "总计":
                continue
            key = (business_date, dimension_type, dimension_name)
            row = grouped.setdefault(key, base_row(business_date, dimension_type, dimension_name, snapshot_time))
            value = to_number(item.get("analysisDimensionSubjectValue"), 0)
            row[metric_col] = int(value) if metric_col == "room_count" else value
            if metric_col == "room_nights":
                row["sold_rooms"] = int(value)

    rows.extend(grouped.values())
    return rows


def base_row(business_date: str, dimension_type: str, dimension_name: str, snapshot_time: datetime) -> dict[str, Any]:
    return {
        "hotel_name": HOTEL_CONFIG["name"],
        "hotel_id": HOTEL_ID,
        "source_platform": source_platform(),
        "business_date": business_date,
        "dimension_type": dimension_type,
        "dimension_name": dimension_name,
        "room_count": None,
        "room_nights": None,
        "room_revenue": None,
        "occupancy_rate": None,
        "adr": None,
        "revpar": None,
        "sold_rooms": None,
        "remaining_rooms": None,
        "orders_today": None,
        "snapshot_time": snapshot_time,
    }


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        print("JY01 无可入库数据")
        return

    sql = f"""
    INSERT INTO {TABLE_NAME} (
        hotel_name, hotel_id, source_platform, business_date, dimension_type, dimension_name,
        room_count, room_nights, room_revenue, occupancy_rate, adr, revpar,
        sold_rooms, remaining_rooms, orders_today, snapshot_time
    ) VALUES (
        %(hotel_name)s, %(hotel_id)s, %(source_platform)s, %(business_date)s, %(dimension_type)s, %(dimension_name)s,
        %(room_count)s, %(room_nights)s, %(room_revenue)s, %(occupancy_rate)s, %(adr)s, %(revpar)s,
        %(sold_rooms)s, %(remaining_rooms)s, %(orders_today)s, %(snapshot_time)s
    )
    ON DUPLICATE KEY UPDATE
        room_count = VALUES(room_count),
        room_nights = VALUES(room_nights),
        room_revenue = VALUES(room_revenue),
        occupancy_rate = VALUES(occupancy_rate),
        adr = VALUES(adr),
        revpar = VALUES(revpar),
        sold_rooms = VALUES(sold_rooms),
        remaining_rooms = VALUES(remaining_rooms),
        orders_today = VALUES(orders_today),
        snapshot_time = VALUES(snapshot_time)
    """

    owns_connection = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.executemany(sql, rows)
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
    print(f"JY01 入库完成：{len(rows)} 行")


def main(conn=None) -> None:
    with JSON_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    rows = transform_jy01(payload)
    print("JY01 转换完成：", len(rows))
    if rows:
        print("JY01 示例：", rows[0])
    upsert_mysql(rows, conn)


if __name__ == "__main__":
    main()
