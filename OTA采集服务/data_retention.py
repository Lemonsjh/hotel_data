from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pymysql


DEFAULT_RETENTION = {
    "pms_hourly_days": 180,
    "pms_daily_days": 400,
    "jl02_daily_days": 450,
    "pms_monthly_months": 36,
    "price_task_days": 90,
}

PMS_RETENTION_RULES = (
    ("pms_room_type_hourly_status", "snapshot_hour", "pms_hourly_days", "DAY"),
    ("rs01_room_revenue_daily", "business_date", "pms_daily_days", "DAY"),
    ("kf11_room_status_snapshot", "business_date", "pms_daily_days", "DAY"),
    ("jl01_room_type_performance_daily", "business_date", "pms_daily_days", "DAY"),
    ("jl02_hotel_performance_daily", "business_date", "jl02_daily_days", "DAY"),
    ("jy01_hotel_statistics_daily", "business_date", "pms_daily_days", "DAY"),
    ("jd01_booking_detail", "COALESCE(booking_time, snapshot_time, created_at)", "pms_daily_days", "DAY"),
    ("jd04_inhouse_extension", "COALESCE(op_time, checkin_time, snapshot_time, created_at)", "pms_daily_days", "DAY"),
    ("jy03_hotel_statistics_month", "period_month", "pms_monthly_months", "MONTH"),
)


def retention_settings(settings: dict[str, Any]) -> dict[str, int]:
    configured = settings.get("data_retention") or {}
    values: dict[str, int] = {}
    for name, default in DEFAULT_RETENTION.items():
        try:
            values[name] = int(configured.get(name, default))
        except (TypeError, ValueError):
            values[name] = default
    return values


def cleanup_pms_history(connection, settings: dict[str, Any], logger: Callable[[str], None] = print) -> int:
    limits = retention_settings(settings)
    deleted = 0
    with connection.cursor() as cursor:
        for table, column, limit_name, unit in PMS_RETENTION_RULES:
            try:
                if unit == "MONTH":
                    sql = f"""
                    DELETE FROM `{table}`
                    WHERE `{column}` < DATE_FORMAT(
                        DATE_SUB(CURRENT_DATE, INTERVAL %s MONTH), '%%Y-%%m'
                    )
                    """
                else:
                    sql = f"""
                    DELETE FROM `{table}`
                    WHERE {column} < DATE_SUB(CURRENT_DATE, INTERVAL %s DAY)
                    """
                cursor.execute(sql, (limits[limit_name],))
                affected = cursor.rowcount
                deleted += affected
                if affected:
                    logger(f"PMS retention cleanup {table}: deleted {affected} expired rows")
            except pymysql.ProgrammingError as exc:
                if exc.args and exc.args[0] == 1146:
                    logger(f"Retention skipped missing table: {table}")
                    continue
                raise
    connection.commit()
    logger(f"PMS retention cleanup deleted {deleted} expired rows")
    return deleted


def cleanup_price_tasks(connection, settings: dict[str, Any], logger: Callable[[str], None] = print) -> int:
    days = retention_settings(settings)["price_task_days"]
    deleted = 0
    with connection.cursor() as cursor:
        for table in ("meituan_price_task", "ctrip_price_task"):
            try:
                cursor.execute(
                    f"""
                    DELETE FROM `{table}`
                    WHERE execute_status IN ('SUCCESS', 'FAILED')
                      AND COALESCE(verified_at, executed_at, created_at)
                          < DATE_SUB(CURRENT_DATE, INTERVAL %s DAY)
                    """,
                    (days,),
                )
                affected = cursor.rowcount
                deleted += affected
                if affected:
                    logger(f"Price-task retention cleanup {table}: deleted {affected} expired rows")
            except pymysql.ProgrammingError as exc:
                if exc.args and exc.args[0] == 1146:
                    logger(f"Retention skipped missing table: {table}")
                    continue
                raise
    connection.commit()
    logger(f"Price-task retention cleanup deleted {deleted} expired rows")
    return deleted
