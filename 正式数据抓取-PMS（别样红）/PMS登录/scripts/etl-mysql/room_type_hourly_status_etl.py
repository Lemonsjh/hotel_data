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
JSON_PATH = ROOT / "output" / "ROOM_STATUS.json"
TABLE_NAME = "pms_room_type_hourly_status"
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip() or str(HOTEL_CONFIG.get("id") or "").strip()


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
    snapshot_hour = datetime.fromisoformat(str(meta["snapshot_hour"]))
    stay_date = str(meta["stay_date"])
    rows = []
    for item in payload.get("rows") or []:
        room_type_name = str(item.get("room_type_name") or "").strip()
        pms_room_type_id = str(item.get("pms_room_type_id") or "").strip()
        if not room_type_name or not pms_room_type_id:
            continue
        rows.append(
            {
                "hotel_id": HOTEL_ID,
                "hotel_name": HOTEL_CONFIG["name"],
                "source_platform": HOTEL_CONFIG.get("source_platform") or "PMS",
                "snapshot_time": snapshot_time,
                "snapshot_hour": snapshot_hour,
                "stay_date": stay_date,
                "room_type_name": room_type_name,
                "pms_room_type_id": pms_room_type_id,
                "total_rooms": number(item.get("total_rooms")),
                "available_rooms": number(item.get("available_rooms")),
                "occupied_rooms": number(item.get("occupied_rooms")),
                "overbooking_rooms": number(item.get("overbooking_rooms")),
            }
        )
    return rows


def upsert_mysql(rows: list[dict[str, Any]], conn=None) -> None:
    if not rows:
        raise RuntimeError("hourly room status has no valid rows")
    sql = f"""
    INSERT INTO `{TABLE_NAME}` (
        hotel_id, hotel_name, source_platform, snapshot_time, snapshot_hour, stay_date,
        room_type_name, pms_room_type_id, total_rooms, available_rooms, occupied_rooms,
        overbooking_rooms
    ) VALUES (
        %(hotel_id)s, %(hotel_name)s, %(source_platform)s, %(snapshot_time)s, %(snapshot_hour)s,
        %(stay_date)s, %(room_type_name)s, %(pms_room_type_id)s, %(total_rooms)s,
        %(available_rooms)s, %(occupied_rooms)s, %(overbooking_rooms)s
    ) ON DUPLICATE KEY UPDATE
        hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
        snapshot_time=VALUES(snapshot_time), stay_date=VALUES(stay_date),
        room_type_name=VALUES(room_type_name), total_rooms=VALUES(total_rooms),
        available_rooms=VALUES(available_rooms), occupied_rooms=VALUES(occupied_rooms),
        overbooking_rooms=VALUES(overbooking_rooms)
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
    print(f"Hourly room status sync completed: {len(rows)} rows")


def main(conn=None) -> None:
    if not HOTEL_ID:
        raise RuntimeError("HOTEL_ID is required for hourly room status")
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    upsert_mysql(transform(payload), conn)


if __name__ == "__main__":
    main()
