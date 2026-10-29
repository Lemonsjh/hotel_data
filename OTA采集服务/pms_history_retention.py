"""PMS 历史按表清理：在调用方写入事务内执行，不单独提交。"""
import json
import os
from datetime import datetime, timedelta

from OTA采集服务.data_retention import retention_settings

RULES = {
    "pms_room_type_hourly_status": ("snapshot_hour", "pms_hourly_days"),
    "jy01_hotel_statistics_daily": ("business_date", "pms_daily_days"),
    "jl01_room_type_performance_daily": ("business_date", "pms_daily_days"),
    "jy03_hotel_statistics_month": ("period_month", "pms_monthly_months"),
    "jl02_hotel_performance_daily": ("business_date", "jl02_daily_days"),
    "rs01_room_revenue_daily": ("business_date", "pms_daily_days"),
    "jd01_booking_detail": ("COALESCE(booking_time, snapshot_time, created_at)", "pms_daily_days"),
}
SOURCES = {"PMS（宝寓）", "PMS（Foxhis）", "PMS（美团）"}


def cleanup_history(cursor, table, hotel_id, source, snapshot):
    if table not in RULES or not hotel_id or source not in SOURCES or not isinstance(snapshot, datetime):
        raise ValueError("PMS 历史清理范围无效")
    configured = json.loads(os.environ.get("PMS_RETENTION") or "{}")
    if not isinstance(configured, dict):
        raise ValueError("PMS 保留周期配置无效")
    column, key = RULES[table]
    count = retention_settings({"data_retention": configured})[key]
    if count < 1:
        raise ValueError("PMS 保留周期必须大于零")
    today = snapshot.date()
    if key == "pms_monthly_months":
        year, month = divmod(today.year * 12 + today.month - count, 12)
        if year < 1:
            raise ValueError("PMS 月数据保留周期超出有效范围")
        cutoff = f"{year:04d}-{month + 1:02d}"
    else:
        cutoff = today - timedelta(days=count - 1)
    expression = column if column.startswith("COALESCE(") else f"`{column}`"
    cursor.execute(f"DELETE FROM `{table}` WHERE hotel_id=%s AND source_platform=%s AND {expression} < %s",
                   (hotel_id, source, cutoff))


def cleanup_rows(cursor, table, rows, hotel_id, source):
    if not rows:
        return
    stamp = rows[0]["snapshot_time"]
    if any(row.get("hotel_id") != hotel_id or row.get("source_platform") != source
           or row.get("snapshot_time") != stamp for row in rows):
        raise ValueError("PMS 历史清理的酒店、来源或批次不一致")
    cleanup_history(cursor, table, hotel_id, source, stamp)
