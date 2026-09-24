from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pymysql
import requests


REPORT_URL = os.environ.get(
    "BYPMS_DAILY_REPORT_URL", "https://pms-api.bypms.cn/report/api/v1/daily-report/list"
)
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
TABLE_NAME = "jl01_room_type_performance_daily"
JY01_TABLE_NAME = "jy01_hotel_statistics_daily"
SOURCE_PLATFORM = "PMS（宝寓）"
OUTPUT_DIR = Path(os.environ.get("HOTEL_OTA_OUTPUT_DIR", "OTA数据"))
RECENT_DAYS = 30


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def report_date(value: Any, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def collection_dates(today: date | None = None, days: int = RECENT_DAYS) -> list[date]:
    """Return the latest closed business days, oldest first."""
    if days < 1:
        raise ValueError("days must be positive")
    latest = (today or date.today()) - timedelta(days=1)
    return [latest - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def jl01_collection_dates(today: date | None = None) -> list[date]:
    """Match ByH JL01: yesterday and the corresponding day last year."""
    latest = (today or date.today()) - timedelta(days=1)
    try:
        previous_year = latest.replace(year=latest.year - 1)
    except ValueError:  # Leap day
        previous_year = latest.replace(year=latest.year - 1, day=28)
    return [latest, previous_year]


def fetch_report(business_date: date, session: requests.Session | None = None) -> dict[str, Any]:
    if not COOKIE:
        raise RuntimeError("BYPMS_COOKIE is empty")
    owns_session = session is None
    session = session or requests.Session()
    try:
        response = session.get(
            REPORT_URL,
            params={"showDate": business_date.isoformat()},
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
    finally:
        if owns_session:
            session.close()
    if payload.get("state") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"ByPMS daily report API failed: {payload.get('display') or payload.get('state')}")
    return payload


def build_rows(payload: dict[str, Any], fallback_date: date, snapshot_time: datetime) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    business_date = report_date(data.get("today"), fallback_date)
    hotel_name = str(data.get("companyName") or data.get("shopName") or HOTEL_NAME).strip()
    rows = []
    for item in data.get("roomType") or []:
        if not isinstance(item, dict):
            continue
        room_type_name = str(item.get("roomTypeName") or "").strip()
        if not room_type_name or room_type_name == "未指定房型":
            continue
        occupancy = number(item.get("roomTypeOccupancyRate"))
        rows.append(
            {
                "hotel_id": HOTEL_ID,
                "hotel_name": hotel_name,
                "source_platform": SOURCE_PLATFORM,
                "business_date": business_date,
                "room_type_name": room_type_name,
                "pms_rate_room_type_id": "",
                "room_nights": number(item.get("roomTypeNightCount")),
                "occupancy_rate": round(occupancy * 100, 4) if occupancy is not None else None,
                "room_revenue": number(item.get("roomTypeContractIncomePrice")),
                "adr": number(item.get("roomTypeAvgContractIncomePrice")),
                "revpar": number(item.get("roomTypeRevPar")),
                "snapshot_time": snapshot_time,
            }
        )
    return rows


def build_jy01_rows(payload: dict[str, Any], fallback_date: date, snapshot_time: datetime) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    business_date = report_date(data.get("today"), fallback_date)
    occupancy = number(data.get("occupancyRate"))
    return [{
        "hotel_name": str(data.get("companyName") or data.get("shopName") or HOTEL_NAME).strip(),
        "hotel_id": HOTEL_ID,
        "source_platform": SOURCE_PLATFORM,
        "business_date": business_date,
        "dimension_type": "总营业指标",
        "dimension_name": "总营业指标",
        "room_count": int(number(data.get("roomCount")) or 0),
        "room_nights": number(data.get("nightCount")),
        "room_revenue": number(data.get("contractIncomeTotalPrice")),
        "occupancy_rate": round(occupancy * 100, 2) if occupancy is not None else None,
        "adr": number(data.get("contractIncomeAvgPrice")),
        "revpar": number(data.get("revPar")),
        "sold_rooms": int(number(data.get("overNightRoomCount")) or 0),
        "remaining_rooms": int(number(data.get("emptyRoomCount")) or 0),
        "orders_today": int(number(data.get("newContractCount")) or 0),
        "snapshot_time": snapshot_time,
    }]


def sync_mysql(rows: list[dict[str, Any]], connection=None) -> None:
    if not rows:
        return
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    sql = f"""
        INSERT INTO `{TABLE_NAME}` (
            hotel_id, hotel_name, source_platform, business_date, room_type_name,
            pms_rate_room_type_id, room_nights, occupancy_rate, room_revenue, adr, revpar, snapshot_time
        ) VALUES (
            %(hotel_id)s, %(hotel_name)s, %(source_platform)s, %(business_date)s, %(room_type_name)s,
            %(pms_rate_room_type_id)s, %(room_nights)s, %(occupancy_rate)s, %(room_revenue)s,
            %(adr)s, %(revpar)s, %(snapshot_time)s
        ) ON DUPLICATE KEY UPDATE
            hotel_name=VALUES(hotel_name), source_platform=VALUES(source_platform),
            pms_rate_room_type_id=VALUES(pms_rate_room_type_id), room_nights=VALUES(room_nights),
            occupancy_rate=VALUES(occupancy_rate), room_revenue=VALUES(room_revenue), adr=VALUES(adr),
            revpar=VALUES(revpar), snapshot_time=VALUES(snapshot_time)
    """
    try:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def sync_jy01(rows: list[dict[str, Any]], connection=None) -> None:
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    sql = f"""
        INSERT INTO `{JY01_TABLE_NAME}` (
            hotel_name, hotel_id, source_platform, business_date, dimension_type, dimension_name,
            room_count, room_nights, room_revenue, occupancy_rate, adr, revpar,
            sold_rooms, remaining_rooms, orders_today, snapshot_time
        ) VALUES (
            %(hotel_name)s, %(hotel_id)s, %(source_platform)s, %(business_date)s, %(dimension_type)s, %(dimension_name)s,
            %(room_count)s, %(room_nights)s, %(room_revenue)s, %(occupancy_rate)s, %(adr)s, %(revpar)s,
            %(sold_rooms)s, %(remaining_rooms)s, %(orders_today)s, %(snapshot_time)s
        ) ON DUPLICATE KEY UPDATE
            room_count=VALUES(room_count), room_nights=VALUES(room_nights), room_revenue=VALUES(room_revenue),
            occupancy_rate=VALUES(occupancy_rate), adr=VALUES(adr), revpar=VALUES(revpar),
            sold_rooms=VALUES(sold_rooms), remaining_rooms=VALUES(remaining_rooms),
            orders_today=VALUES(orders_today), snapshot_time=VALUES(snapshot_time)
    """
    try:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def main() -> int:
    if not HOTEL_ID:
        raise RuntimeError("HOTEL_ID is empty")
    snapshot_time = datetime.now()
    jy01_dates = collection_dates(snapshot_time.date())
    jl01_dates = jl01_collection_dates(snapshot_time.date())
    requested_dates = list(dict.fromkeys([*jy01_dates, *jl01_dates]))
    session = requests.Session()
    try:
        reports = [(business_date, fetch_report(business_date, session)) for business_date in requested_dates]
    finally:
        session.close()
    for requested_date, report in reports:
        if report_date((report.get("data") or {}).get("today"), requested_date) != requested_date:
            raise RuntimeError(f"ByPMS daily report returned an unexpected business date: {requested_date}")
    rows = [
        row
        for requested_date, report in reports
        if requested_date in jl01_dates
        for row in build_rows(report, requested_date, snapshot_time)
    ]
    jy01_rows = [
        row
        for requested_date, report in reports
        if requested_date in jy01_dates
        for row in build_jy01_rows(report, requested_date, snapshot_time)
    ]
    sync_mysql(rows)
    sync_jy01(jy01_rows)
    hotel_name = next((row["hotel_name"] for row in jy01_rows if row["hotel_name"]), "")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "bypms_daily_report_summary.json").write_text(
        json.dumps(
            {
                "jl01_business_dates": [item.isoformat() for item in jl01_dates],
                "jy01_business_dates": [item.isoformat() for item in jy01_dates],
                "report_count": len(reports),
                "room_type_count": len(rows),
                "hotel_name": hotel_name,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"ByPMS daily report synced: JY01={jy01_dates[0]}~{jy01_dates[-1]} "
        f"JL01={','.join(item.isoformat() for item in jl01_dates)} room_types={len(rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
