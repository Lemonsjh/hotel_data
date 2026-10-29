"""西软 JY03 总营业指标：首次六个月，后续刷新本月及去年同期。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM, mysql_connection, query, require_columns, report_main, save_output
from foxhis_jy01_data import number, rounded, whole, report_date
from foxhis_common import cleanup_history
from foxhis_sql import JY03_TABLE, JY03_DAILY_QUERY

SUMMARY_FILE = "foxhis_jy03_summary.json"
TOTAL = "总营业指标"
DIMENSIONS = (TOTAL,)
# 月报保留既有指标口径，不随 JY01 新增客房其他收入而变化。
CONTROL_FIELDS = ("010100", "010110", "010120", "010130", "010190",
                  "011000", "012000", "013000", "014000", "018530", "018560")
COLUMNS = ["hotel_name", "hotel_id", "source_platform", "period_month", "dimension_type", "dimension_name",
           "room_type_id", "room_count", "room_nights", "room_revenue", "maintain_rooms", "occupancy_rate", "adr", "revpar", "snapshot_time"]


def month_start(value: date, offset: int = 0) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def collection_months(today: date, initial: bool) -> list[date]:
    months = [month_start(today, n) for n in (0, -1, -2)] if initial else [month_start(today)]
    return months + [month_start(m, -12) for m in months]


def month_end(month: date, today: date) -> date:
    last = month_start(month, 1) - timedelta(days=1)
    if month == month_start(today, -12):
        # 当月1日没有已完成日，去年同期也不应伪造1日数据。
        return month - timedelta(days=1) if today.day == 1 else min(last, month + timedelta(days=today.day - 2))
    return min(last, today - timedelta(days=1))


def has_monthly_data(config: Config) -> bool:
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT 1 FROM `{JY03_TABLE}` WHERE hotel_id=%s AND source_platform=%s AND dimension_type=%s LIMIT 1",
                           (config.hotel_id, SOURCE_PLATFORM, TOTAL))
            return bool(cursor.fetchall())
    finally:
        connection.close()


def rates(revenue: Decimal, nights: Decimal, available: Decimal | None, capacity: Decimal | None) -> dict:
    return {"adr": rounded(revenue / nights) if nights else None,
            "occupancy_rate": rounded(nights * 100 / available) if available else None,
            "revpar": rounded(revenue / capacity) if capacity else None}


def output_row(config: Config, month: date, snapshot: datetime,
               nights: Decimal, revenue: Decimal, capacity=None, maintain=None, available=None) -> dict:
    return {"hotel_name": config.hotel_name, "hotel_id": config.hotel_id, "source_platform": SOURCE_PLATFORM,
            "period_month": month.strftime("%Y-%m"), "dimension_type": TOTAL, "dimension_name": TOTAL, "room_type_id": None,
            "room_count": whole(capacity) if capacity is not None else None,
            "room_nights": nights, "room_revenue": rounded(revenue),
            "maintain_rooms": whole(maintain) if maintain is not None else None,
            **rates(revenue, nights, available, capacity), "snapshot_time": snapshot.replace(microsecond=0)}


def build_month(records: list[dict], config: Config, month: date, end: date, snapshot: datetime) -> tuple[list[dict], dict]:
    if end < month or month.day != 1 or end >= snapshot.date() or end >= month_start(month, 1):
        raise FoxhisError("JY03 月份窗口无效或包含未完成日。")
    days = [month + timedelta(days=n) for n in range((end - month).days + 1)]
    daily = {d: {} for d in days}
    references, rebates = {}, {}
    for r in records:
        day, code = report_date(r.get("business_date")), r.get("metric_code")
        if day not in daily or code not in CONTROL_FIELDS or code in daily[day]:
            raise FoxhisError("JY03 日结指标日期、代码或唯一键无效。")
        daily[day][code] = number(r.get("metric_value"))
        if day == end:
            references[code] = number(r.get("month_value"))
        if code == "013000":
            rebates[day] = number(r.get("daily_rebate"))
            if day == end:
                reference_rebate = number(r.get("month_rebate"))
    if any(len(m) != len(CONTROL_FIELDS) for m in daily.values()):
        raise FoxhisError("JY03 月份内日结缺日或缺指标，拒绝以零补齐。")
    for metric in daily.values():
        for code in ("010100", "010110", "010120", "010130", "010190"):
            whole(metric[code])
        if (metric["011000"] < 0 or metric["018530"] < 0
                or metric["010100"] - metric["010110"] - metric["010120"] - metric["010130"] != metric["010190"]):
            raise FoxhisError("JY03 日结房量或计费间夜无效。")
    summed = {code: sum((m[code] for m in daily.values()), Decimal(0)) for code in CONTROL_FIELDS}
    for code in CONTROL_FIELDS:
        if code not in ("012000", "014000", "018560") and summed[code] != references[code]:
            raise FoxhisError(f"JY03 原生月累计与逐日合计 {code} 不一致。")
    rebate = sum(rebates.values(), Decimal(0))
    if rebate != reference_rebate:
        raise FoxhisError("JY03 返减月累计与逐日合计不一致。")
    for code, numerator, denominator, factor in (("012000", "011000", "010190", 100), ("014000", "013000", "011000", 1), ("018560", "013000", "010100", 1)):
        expected = rounded(summed[numerator] * factor / summed[denominator]) if summed[denominator] else Decimal(0)
        if rounded(references[code]) != expected:
            raise FoxhisError(f"JY03 原生月累计比率 {code} 不一致。")

    revenue = summed["013000"] + rebate
    rows = [output_row(config, month, snapshot, summed["011000"], revenue,
                       summed["010100"], summed["010110"], summed["010190"])]
    return rows, {"start_date": month.isoformat(), "end_date": end.isoformat(), "completed_days": len(days),
                  "gross_room_revenue": summed["013000"], "room_rebate": rebate, "net_room_revenue": revenue,
                  "room_nights": summed["011000"], "room_days": summed["010100"], "available_room_days": summed["010190"],
                  "maintain_room_days": summed["010110"], "row_count": len(rows), "hotel_metrics": rows[0]}


def fetch_rows(config: Config, snapshot: datetime, initial: bool) -> tuple[list[dict], dict]:
    require_columns(config, "yjourrep", ["date", "class", "day", "month", "day_rebate", "month_rebate"])
    require_columns(config, "sysdata", ["bdate"])
    closed = query("select convert(char(8),bdate,112) as current_business_date from sysdata", config, timeout=30)
    if len(closed) != 1:
        raise FoxhisError("JY03 无法确认西软当前营业日。")
    source_day = report_date(closed[0].get("current_business_date"))
    rows, by_month, missing, skipped = [], {}, [], []
    months = collection_months(snapshot.date(), initial)
    for month in months:
        end = month_end(month, snapshot.date())
        if end < month:
            skipped.append(month.strftime("%Y-%m")); continue
        if source_day <= end:
            raise FoxhisError("JY03 西软尚未完成请求日的夜审。")
        params = {"start_date": month.strftime("%Y%m%d"), "end_date": (end + timedelta(days=1)).strftime("%Y%m%d")}
        records = query(JY03_DAILY_QUERY.format(**params), config, timeout=120)
        if not records:
            if month == month_start(snapshot.date()):
                raise FoxhisError("JY03 本月日结数据缺失。")
            missing.append(month.strftime("%Y-%m")); continue
        month_rows, audit = build_month(records, config, month, end, snapshot)
        rows.extend(month_rows); by_month[month.strftime("%Y-%m")] = audit
    return rows, {"initial": initial, "requested_months": [m.strftime("%Y-%m") for m in months], "by_month": by_month,
                  "missing_months": missing, "skipped_empty_windows": skipped,
                  "basis": "完整日结累计房日/维修房日；计费间夜不含免费、自用；净房费包含返减；ADR、RevPAR按月净额重算，非日均值平均"}


def sync_rows(rows: list[dict], config: Config) -> int:
    if not rows or not config.hotel_id.strip() or not config.hotel_name.strip():
        raise FoxhisError("JY03 无有效数据或酒店 ID、名称为空。")
    stamp = rows[0]["snapshot_time"]
    allowed = {m.strftime("%Y-%m") for m in collection_months(stamp.date(), True) if month_end(m, stamp.date()) >= m}
    keys = {(r["period_month"], r["dimension_type"], r["dimension_name"]) for r in rows}
    months = sorted({r["period_month"] for r in rows})
    if (len(keys) != len(rows) or any(r["hotel_id"] != config.hotel_id or r["hotel_name"] != config.hotel_name
            or r["source_platform"] != SOURCE_PLATFORM or r["snapshot_time"] != stamp or r["period_month"] not in allowed
            or r["dimension_type"] != TOTAL or r["dimension_name"] != TOTAL for r in rows)
            or any((m, TOTAL, TOTAL) not in keys for m in months)):
        raise FoxhisError("JY03 月份、维度、酒店或批次不一致，拒绝替换。")
    condition = f"hotel_id=%s AND source_platform=%s AND period_month IN ({', '.join(['%s'] * len(months))}) AND dimension_type=%s"
    args = (config.hotel_id, SOURCE_PLATFORM, *months, TOTAL)
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT snapshot_time FROM `{JY03_TABLE}` WHERE {condition} FOR UPDATE", args)
            if any(r[0] > stamp for r in cursor.fetchall()):
                raise FoxhisError("JY03 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{JY03_TABLE}` WHERE {condition}", args)
            cursor.executemany(f"INSERT INTO `{JY03_TABLE}` ({', '.join('`'+c+'`' for c in COLUMNS)}) VALUES ({', '.join(['%s']*len(COLUMNS))})",
                               [tuple(r[c] for c in COLUMNS) for r in rows])
            cleanup_history(cursor, JY03_TABLE, rows, config)
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, business_date: date, snapshot_time: datetime) -> dict:
    initial = "--initial" in sys.argv or not has_monthly_data(config)
    rows, summary = fetch_rows(config, snapshot_time, initial)
    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run or not rows else sync_rows(rows, config)
    summary.update(report="jy03", source_platform=SOURCE_PLATFORM, hotel_id=config.hotel_id,
                   snapshot_time=snapshot_time.isoformat(), row_count=len(rows), written=written, dry_run=dry_run, rows=rows)
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jy03", collect)


if __name__ == "__main__":
    raise SystemExit(main())
