from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import pymysql
import requests


REPORT_URL = os.environ.get("BYPMS_CLASSIFICATION_URL", "https://www.bypms.cn/console/report/get")
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
TABLE_NAME = "jl11_room_type_classification"
SOURCE_PLATFORM = "PMS（宝寓）"
WINDOW_DAYS = 30


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def report_window(today: date | None = None) -> tuple[date, date]:
    end = (today or date.today()) - timedelta(days=1)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def fetch_report(start: date, end: date, session: requests.Session) -> list[dict[str, Any]]:
    response = session.post(
        REPORT_URL,
        data={
            "nightTags": "", "sortState": "", "roomIds": "", "channels": "", "accountIds": "",
            "date": f"{start.isoformat()},{end.isoformat()}", "groupBy": "roomType",
        },
        headers={
            "Accept": "application/json, text/plain, */*",
            "Cookie": COOKIE,
            "Referer": "https://www.bypms.cn/console/report/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    report = ((payload.get("data") or {}).get("data") or {}) if isinstance(payload, dict) else {}
    rows = report.get("rows")
    if payload.get("state") != 0 or not isinstance(rows, list):
        raise RuntimeError("ByPMS JL11 report response is invalid")
    return rows


def build_rows(items: list[dict[str, Any]], start: date, end: date, snapshot: datetime) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            raise RuntimeError("ByPMS JL11 report contains a room type without a name")
        required = ("roomCount", "roomCountSold", "roomSoldPercent", "price", "priceAvg", "revPar")
        if any(item.get(field) is None for field in required):
            raise RuntimeError("ByPMS JL11 report is missing required room type metrics")
        rows.append({
            "hotel_id": HOTEL_ID,
            "hotel_name": HOTEL_NAME,
            "source_platform": SOURCE_PLATFORM,
            "snapshot_date": snapshot.date(),
            "period_start": start,
            "period_end": end,
            "section": "summary",
            "room_type_id": None,
            "room_type_name": str(item["title"]).strip(),
            "dimension_code": "",
            "dimension_name": "",
            "room_count": item["roomCount"],
            "room_nights": item["roomCountSold"],
            "occupancy_rate": round(float(item["roomSoldPercent"]) * 100, 4),
            "room_revenue": item["price"],
            "average_room_price": item["priceAvg"],
            "revpar": item["revPar"],
            "overnight_room_count": item["roomCountSold"],
            "overnight_occupancy_rate": round(float(item["roomSoldPercent"]) * 100, 4),
            "snapshot_time": snapshot,
        })
    if not rows:
        raise RuntimeError("ByPMS JL11 report returned no room types")
    return rows


def sync_mysql(rows: list[dict[str, Any]], connection=None) -> None:
    if not rows or not HOTEL_ID:
        raise RuntimeError("ByPMS JL11 has no valid hotel or room types")
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    columns = (
        "hotel_id", "hotel_name", "source_platform", "snapshot_date", "period_start", "period_end",
        "section", "room_type_id", "room_type_name", "dimension_code", "dimension_name", "room_count",
        "room_nights", "occupancy_rate", "room_revenue", "average_room_price", "revpar",
        "overnight_room_count", "overnight_occupancy_rate", "snapshot_time",
    )
    values = ", ".join(f"%({name})s" for name in columns)
    sql = f"INSERT INTO `{TABLE_NAME}` ({', '.join(columns)}) VALUES ({values})"
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s", (HOTEL_ID,))
            cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def main() -> int:
    if not COOKIE or not HOTEL_ID:
        raise RuntimeError("BYPMS_COOKIE or HOTEL_ID is empty")
    snapshot = datetime.now()
    start, end = report_window(snapshot.date())
    with requests.Session() as session:
        items = fetch_report(start, end, session)
    rows = build_rows(items, start, end, snapshot)
    sync_mysql(rows)
    print(f"ByPMS JL11 synced: {start}~{end} room_types={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
