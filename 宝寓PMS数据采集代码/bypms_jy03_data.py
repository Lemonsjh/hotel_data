from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

import pymysql
import requests


REPORT_URL = os.environ.get("BYPMS_MONTHLY_REPORT_URL", "https://pms-api.bypms.cn/report/api/v1/normal-report/list")
COOKIE = os.environ.get("BYPMS_COOKIE", "").strip()
HOTEL_ID = os.environ.get("HOTEL_ID", "").strip()
HOTEL_NAME = os.environ.get("BYPMS_HOTEL_NAME", "").strip()
TIMEOUT_SECONDS = int(os.environ.get("BYPMS_TIMEOUT_SECONDS", "30"))
TABLE_NAME = "jy03_hotel_statistics_month"
SOURCE_PLATFORM = "PMS（宝寓）"
DIMENSIONS = (
    ("roomType", "房型", "roomTypeName", "roomTypeRoomCount", "roomTypeNightCount",
     "roomTypeContractIncomePrice", "roomTypeOccupancyRate", "roomTypeAvgContractIncomePrice", "roomTypeRevPar"),
    ("sourceType", "客源", "sourceTypeName", None, "sourceTypeNightCount",
     "sourceTypeContractIncomePrice", None, "sourceTypeAvgContractIncomePrice", None),
    ("channel", "渠道", "channelName", None, "channelNightCount",
     "channelContractIncomePrice", None, "channelAvgContractIncomePrice", None),
    ("checkInType", "入住类型", "checkInTypeName", None, "checkInTypeNightCount",
     "checkInTypeContractIncomePrice", None, "checkInTypeAvgContractIncomePrice", None),
)


def mysql_config() -> dict[str, Any]:
    return {
        "host": os.environ.get("MYSQL_HOST") or os.environ.get("HOTEL_OTA_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MYSQL_PORT") or os.environ.get("HOTEL_OTA_MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER") or os.environ.get("HOTEL_OTA_MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD") or os.environ.get("HOTEL_OTA_MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE") or os.environ.get("HOTEL_OTA_MYSQL_DATABASE", ""),
        "charset": "utf8mb4",
    }


def month_start(value: date, offset: int = 0) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def collection_months(today: date, initial: bool) -> list[date]:
    current = month_start(today)
    months = [month_start(current, offset) for offset in (0, -1, -2)] if initial else [current]
    return months + [month_start(value, -12) for value in months] if initial else months


def month_end(month: date, today: date) -> date:
    last_day = month_start(month, 1) - timedelta(days=1)
    if month.year == today.year - 1 and month.month == today.month:
        return min(last_day, date(month.year, month.month, max(today.day - 1, 1)))
    return min(last_day, today - timedelta(days=1))


def number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def percentage(value: Any) -> float | None:
    parsed = number(value)
    return round(parsed * 100, 2) if parsed is not None else None


def fetch_month(month: date, end: date, session: requests.Session) -> dict[str, Any] | None:
    response = session.get(
        REPORT_URL,
        params={"startDate": month.isoformat(), "endDate": end.isoformat()},
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
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("state") != 0 or not isinstance(data, dict):
        raise RuntimeError(f"ByPMS monthly report failed: {month:%Y-%m}")
    if not data.get("statisticsStartDate") and not data.get("statisticsEndDate"):
        if data.get("nightCount") is None and data.get("roomCount") is None:
            return None
        raise RuntimeError(f"ByPMS monthly report lacks dates: {month:%Y-%m}")
    if data.get("statisticsStartDate") != month.isoformat() or data.get("statisticsEndDate") != end.isoformat():
        raise RuntimeError(f"ByPMS monthly report returned wrong dates: {month:%Y-%m}")
    return data


def build_rows(data: dict[str, Any], month: date, snapshot: datetime) -> list[dict[str, Any]]:
    required = ("roomCount", "nightCount", "contractIncomeTotalPrice", "occupancyRate", "contractIncomeAvgPrice", "revPar")
    if any(data.get(field) is None for field in required):
        raise RuntimeError(f"ByPMS monthly report lacks hotel metrics: {month:%Y-%m}")
    base = {
        "hotel_id": HOTEL_ID,
        "hotel_name": str(data.get("companyName") or HOTEL_NAME).strip(),
        "source_platform": SOURCE_PLATFORM,
        "period_month": month.strftime("%Y-%m"),
        "snapshot_time": snapshot,
    }
    rows = [{
        **base, "dimension_type": "总营业指标", "dimension_name": "总营业指标",
        "room_count": int(data["roomCount"]), "room_nights": number(data["nightCount"]),
        "room_revenue": number(data["contractIncomeTotalPrice"]),
        "maintain_rooms": int(data["fixRoomCount"]) if data.get("fixRoomCount") is not None else None,
        "occupancy_rate": percentage(data["occupancyRate"]),
        "adr": number(data["contractIncomeAvgPrice"]), "revpar": number(data["revPar"]),
    }]
    for key, kind, name_key, count_key, nights_key, revenue_key, rate_key, adr_key, revpar_key in DIMENSIONS:
        values = data.get(key) or []
        if not isinstance(values, list):
            raise RuntimeError(f"ByPMS monthly report has invalid {key}: {month:%Y-%m}")
        for item in values:
            if not isinstance(item, dict):
                continue
            name = str(item.get(name_key) or "").strip()
            if not name:
                continue
            # The S14 reader expects this standardized Meituan channel name.
            if kind == "渠道" and name == "美团酒店":
                name = "美团EBK"
            rows.append({
                **base, "dimension_type": kind, "dimension_name": name,
                "room_count": int(item[count_key]) if count_key and item.get(count_key) is not None else None,
                "room_nights": number(item.get(nights_key)), "room_revenue": number(item.get(revenue_key)),
                "maintain_rooms": None, "occupancy_rate": percentage(item.get(rate_key)) if rate_key else None,
                "adr": number(item.get(adr_key)), "revpar": number(item.get(revpar_key)) if revpar_key else None,
            })
    return rows


def sync_mysql(rows: list[dict[str, Any]], connection=None) -> None:
    if not rows or not HOTEL_ID:
        raise RuntimeError("ByPMS monthly report has no valid hotel or rows")
    owns_connection = connection is None
    connection = connection or pymysql.connect(**mysql_config())
    columns = (
        "hotel_name", "hotel_id", "source_platform", "period_month", "dimension_type", "dimension_name",
        "room_count", "room_nights", "room_revenue", "maintain_rooms", "occupancy_rate", "adr", "revpar", "snapshot_time",
    )
    sql = f"INSERT INTO `{TABLE_NAME}` ({', '.join(columns)}) VALUES ({', '.join(f'%({key})s' for key in columns)})"
    try:
        with connection.cursor() as cursor:
            months = sorted({row["period_month"] for row in rows})
            cursor.execute(
                f"DELETE FROM `{TABLE_NAME}` WHERE hotel_id=%s AND source_platform=%s AND period_month IN ({', '.join(['%s'] * len(months))})",
                (HOTEL_ID, SOURCE_PLATFORM, *months),
            )
            cursor.executemany(sql, rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def has_monthly_data(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM `{TABLE_NAME}` WHERE hotel_id=%s AND source_platform=%s AND dimension_type=%s LIMIT 1",
            (HOTEL_ID, SOURCE_PLATFORM, "总营业指标"),
        )
        return cursor.fetchone() is not None


def main() -> int:
    if not COOKIE or not HOTEL_ID:
        raise RuntimeError("BYPMS_COOKIE or HOTEL_ID is empty")
    snapshot = datetime.now()
    connection = pymysql.connect(**mysql_config())
    try:
        initial = not has_monthly_data(connection)
        rows: list[dict[str, Any]] = []
        missing: list[str] = []
        with requests.Session() as session:
            for month in collection_months(snapshot.date(), initial):
                end = month_end(month, snapshot.date())
                if end < month:
                    continue
                report = fetch_month(month, end, session)
                if report is None:
                    missing.append(month.strftime("%Y-%m"))
                    continue
                rows.extend(build_rows(report, month, snapshot))
        if not rows:
            raise RuntimeError("ByPMS monthly report returned no months with data")
        sync_mysql(rows, connection)
    finally:
        connection.close()
    months = sorted({row["period_month"] for row in rows})
    print(f"ByPMS JY03 synced: months={','.join(months)} rows={len(rows)} empty_months={','.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
