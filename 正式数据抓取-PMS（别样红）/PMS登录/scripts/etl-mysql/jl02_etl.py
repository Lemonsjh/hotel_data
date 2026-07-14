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
JSON_PATH = ROOT / "output" / "JL02.json"
TABLE_NAME = "jl02_hotel_performance_daily"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip() or str(HOTEL_CONFIG.get("id") or "").strip()


def to_number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def business_date(payload: dict[str, Any]) -> str:
    value = (payload.get("_query") or {}).get("businessDate")
    if value:
        return str(value)
    return (date.today() - timedelta(days=1)).isoformat()


def make_row(
    item: dict[str, Any], report_date: str, snapshot_time: datetime, detail: bool = False
) -> dict[str, Any]:
    room_type_name = str(item.get("statistics") or "").strip() if detail else ""
    metric_name = str(item.get("groupName") or item.get("statistics") or "").strip()
    return {
        "hotel_name": HOTEL_CONFIG["name"],
        "hotel_id": HOTEL_ID,
        "source_platform": HOTEL_CONFIG.get("source_platform") or "PMS（别样红）",
        "business_date": report_date,
        "category": str(item.get("category") or "").strip(),
        "room_type_name": room_type_name,
        "metric_name": metric_name,
        "value_day": to_number(item.get("currentDay")),
        "value_month": to_number(item.get("currentMonth")),
        "value_year": to_number(item.get("currentYear")),
        "snapshot_time": snapshot_time,
    }


def transform_one(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data", {}).get("data", {})
    report_date = business_date(payload)
    snapshot_time = datetime.now()
    rows: list[dict[str, Any]] = []
    for item in data.get("summaryList", []):
        rows.append(make_row(item, report_date, snapshot_time))
    for item in data.get("incomeList", []):
        rows.append(make_row(item, report_date, snapshot_time))
    for item in data.get("detailList", []):
        if str(item.get("statistics") or "").strip() == "小计":
            continue
        rows.append(make_row(item, report_date, snapshot_time, detail=True))
    return [row for row in rows if row["category"] and row["metric_name"]]


def transform(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reports = payload.get("_reports")
    if not isinstance(reports, list):
        reports = [payload]
    return [row for report in reports if isinstance(report, dict) for row in transform_one(report)]


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        print("JL02 无可入库数据")
        return
    sql = f"""
    INSERT INTO {TABLE_NAME} (
        hotel_name, hotel_id, source_platform, business_date, category,
        room_type_name, metric_name, value_day, value_month, value_year, snapshot_time
    ) VALUES (
        %(hotel_name)s, %(hotel_id)s, %(source_platform)s, %(business_date)s, %(category)s,
        %(room_type_name)s, %(metric_name)s, %(value_day)s, %(value_month)s, %(value_year)s,
        %(snapshot_time)s
    ) ON DUPLICATE KEY UPDATE
        hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
        value_day=VALUES(value_day), value_month=VALUES(value_month),
        value_year=VALUES(value_year), snapshot_time=VALUES(snapshot_time)
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
    print(f"JL02 入库完成：{len(rows)} 行")


def main(conn=None) -> None:
    if not HOTEL_ID:
        raise RuntimeError("未配置 HOTEL_ID，拒绝将 JL02 数据写入空酒店")
    with JSON_PATH.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    rows = transform(payload)
    print(f"JL02 转换完成：{len(rows)} 行，营业日期：{business_date(payload)}")
    if rows:
        print("JL02 示例：", rows[0])
    upsert_mysql(rows, conn)


if __name__ == "__main__":
    main()
