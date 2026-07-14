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
JSON_PATH = ROOT / "output" / "JL01.json"
TABLE_NAME = "jl01_room_type_performance_daily"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip() or str(HOTEL_CONFIG.get("id") or "").strip()


def number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def report_date(payload: dict[str, Any], item: dict[str, Any]) -> str:
    value = item.get("businessDate") or (payload.get("_query") or {}).get("businessDate")
    return str(value).split(" ")[0]


def transform_one(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("data", {}).get("data", {}).get("managingData", [])
    captured_at = datetime.now()
    rows = []
    for item in items:
        if item.get("groupType") != "rateRoomType":
            continue
        room_type_name = str(item.get("classifiedStatistic") or "").strip()
        business_date = report_date(payload, item)
        if not room_type_name or not business_date:
            continue
        rent_rate = number(item.get("rentRate"))
        rows.append(
            {
                "hotel_id": HOTEL_ID,
                "hotel_name": HOTEL_CONFIG["name"],
                "source_platform": HOTEL_CONFIG.get("source_platform") or "PMS（别样红）",
                "business_date": business_date,
                "room_type_name": room_type_name,
                "pms_rate_room_type_id": str(item.get("selectedSubjectId") or "").strip(),
                "room_nights": number(item.get("roomPoint")),
                "occupancy_rate": round(rent_rate * 100, 4) if rent_rate is not None else None,
                "room_revenue": number(item.get("roomFee")),
                "adr": number(item.get("avgRoomPrice")),
                "revpar": number(item.get("revPar")),
                "snapshot_time": captured_at,
            }
        )
    return rows


def transform(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reports = payload.get("_reports")
    if not isinstance(reports, list):
        reports = [payload]
    return [row for report in reports if isinstance(report, dict) for row in transform_one(report)]


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        print("JL01 无房型实际经营数据")
        return
    sql = f"""
    INSERT INTO {TABLE_NAME} (
        hotel_id, hotel_name, source_platform, business_date, room_type_name,
        pms_rate_room_type_id, room_nights, occupancy_rate, room_revenue,
        adr, revpar, snapshot_time
    ) VALUES (
        %(hotel_id)s, %(hotel_name)s, %(source_platform)s, %(business_date)s, %(room_type_name)s,
        %(pms_rate_room_type_id)s, %(room_nights)s, %(occupancy_rate)s, %(room_revenue)s,
        %(adr)s, %(revpar)s, %(snapshot_time)s
    ) ON DUPLICATE KEY UPDATE
        hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
        pms_rate_room_type_id=VALUES(pms_rate_room_type_id), room_nights=VALUES(room_nights),
        occupancy_rate=VALUES(occupancy_rate), room_revenue=VALUES(room_revenue),
        adr=VALUES(adr), revpar=VALUES(revpar), snapshot_time=VALUES(snapshot_time)
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
    print(f"JL01 入库完成：{len(rows)} 行")


def main(conn=None) -> None:
    if not HOTEL_ID:
        raise RuntimeError("未配置 HOTEL_ID，拒绝将 JL01 数据写入空酒店")
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    rows = transform(payload)
    print(f"JL01 转换完成：{len(rows)} 行")
    upsert_mysql(rows, conn)


if __name__ == "__main__":
    main()
