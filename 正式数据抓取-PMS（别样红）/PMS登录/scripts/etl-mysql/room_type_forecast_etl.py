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
JSON_PATH = ROOT / "output" / "FORECAST.json"
TABLE_NAME = "pms_room_type_forecast_snapshot"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip() or str(HOTEL_CONFIG.get("id") or "").strip()
RETENTION_DAYS = 30


def number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def transform(payload: dict[str, Any]) -> list[dict[str, Any]]:
    meta = payload.get("meta") or {}
    snapshot_time = datetime.fromisoformat(str(meta["snapshot_time"]))
    rows = []
    for room_type in payload.get("rows") or []:
        for detail in room_type.get("details") or []:
            stay_date = str(detail.get("Date") or "").split(" ")[0]
            if not stay_date:
                continue
            rows.append(
                {
                    "hotel_id": HOTEL_ID,
                    "hotel_name": HOTEL_CONFIG["name"],
                    "source_platform": HOTEL_CONFIG.get("source_platform") or "PMS（别样红）",
                    "snapshot_time": snapshot_time,
                    "stay_date": stay_date,
                    "room_type_name": str(room_type.get("room_type_name") or "").strip(),
                    "pms_room_type_id": str(room_type.get("pms_room_type_id") or "").strip(),
                    "total_rooms": number(room_type.get("total_rooms")),
                    "available_rooms": number(detail.get("AvailiableCount")),
                    "occupied_rooms": number(detail.get("OccupationCount")),
                    "overbooking_rooms": number(detail.get("OverbookingCount")),
                    "room_revenue": number(detail.get("RoomRent")),
                    "adr": number(detail.get("ADR")),
                    "revpar": number(detail.get("RevPar")),
                }
            )
    return [row for row in rows if row["room_type_name"] and row["pms_room_type_id"]]


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        print("房类预测无可入库数据")
        return
    sql = f"""
    INSERT INTO {TABLE_NAME} (
        hotel_id, hotel_name, source_platform, snapshot_time, stay_date,
        room_type_name, pms_room_type_id, total_rooms, available_rooms,
        occupied_rooms, overbooking_rooms, room_revenue, adr, revpar
    ) VALUES (
        %(hotel_id)s, %(hotel_name)s, %(source_platform)s, %(snapshot_time)s, %(stay_date)s,
        %(room_type_name)s, %(pms_room_type_id)s, %(total_rooms)s, %(available_rooms)s,
        %(occupied_rooms)s, %(overbooking_rooms)s, %(room_revenue)s, %(adr)s, %(revpar)s
    ) ON DUPLICATE KEY UPDATE
        hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
        total_rooms=VALUES(total_rooms), available_rooms=VALUES(available_rooms),
        occupied_rooms=VALUES(occupied_rooms), overbooking_rooms=VALUES(overbooking_rooms),
        room_revenue=VALUES(room_revenue), adr=VALUES(adr), revpar=VALUES(revpar)
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
    print(f"房类预测入库完成：{len(rows)} 行")


def cleanup_expired_snapshots(conn=None) -> int:
    """按入住日期保留最近 30 天的预测历史。"""
    cutoff = date.today() - timedelta(days=RETENTION_DAYS)
    owns_connection = conn is None
    conn = conn or pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {TABLE_NAME} WHERE hotel_id=%s AND stay_date<%s",
                (HOTEL_ID, cutoff),
            )
            deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        if owns_connection:
            conn.close()


def main(conn=None) -> None:
    if not HOTEL_ID:
        raise RuntimeError("未配置 HOTEL_ID，拒绝将房类预测写入空酒店")
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    rows = transform(payload)
    print(f"房类预测转换完成：{len(rows)} 行")
    upsert_mysql(rows, conn)
    deleted = cleanup_expired_snapshots(conn)
    print(f"房类预测历史清理：保留 {RETENTION_DAYS} 天，删除 {deleted} 行")


if __name__ == "__main__":
    main()
