"""美团 PMS 按表清理历史；由调用方与本批写入共同提交。"""
from OTA采集服务.pms_history_retention import cleanup_history as cleanup_pms_history

SOURCE = "PMS（美团）"
RULES = {
    "pms_room_type_hourly_status": ("snapshot_hour", "pms_hourly_days"),
    "jy01_hotel_statistics_daily": ("business_date", "pms_daily_days"),
    "jy03_hotel_statistics_month": ("period_month", "pms_monthly_months"),
    "jl02_hotel_performance_daily": ("business_date", "jl02_daily_days"),
    "rs01_room_revenue_daily": ("business_date", "pms_daily_days"),
}


def cleanup_history(cursor, table, hotel_id, snapshot):
    if table not in RULES:
        raise ValueError("美团 PMS 历史清理范围无效")
    cleanup_pms_history(cursor, table, hotel_id, SOURCE, snapshot)
