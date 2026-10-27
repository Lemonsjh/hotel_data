"""西软 JY01：最近 30 个完整自然营业日的酒店经营日报。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foxhis_common import (Config, FoxhisError, SOURCE_PLATFORM, mysql_connection,
                           query, report_main, require_columns, save_output)
from foxhis_sql import JY01_TABLE, JY01_DAILY_QUERY, JY01_ROOM_CONTROL_QUERY

RECENT_DAYS = 30
SUMMARY_FILE = "foxhis_jy01_summary.json"
DIMENSION = "总营业指标"
COLUMNS = ["hotel_name", "hotel_id", "source_platform", "business_date", "dimension_type",
           "dimension_name", "room_type_id", "room_count", "room_nights", "room_revenue",
           "occupancy_rate", "adr", "revpar", "sold_rooms", "remaining_rooms", "orders_today", "snapshot_time"]
CONTROL_FIELDS = {"010100": "total_rooms", "010110": "maintain_rooms", "010120": "locked_rooms",
                  "010130": "own_use_rooms", "010190": "available_rooms", "011000": "room_nights",
                  "012000": "occupancy_rate", "013000": "room_revenue", "014000": "adr",
                  "017000": "room_other_income", "018000": "room_income_total",
                  "018530": "free_rooms", "018560": "revpar"}
REVENUE_CODES = ("013000", "017000", "018000")
ROOM_FIELDS = ("total_rooms", "maintain_rooms", "locked_rooms", "own_use_rooms", "available_rooms",
               "remaining_rooms", "sold_rooms", "free_rooms", "room_revenue")


def collection_dates(today: date) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(RECENT_DAYS, 0, -1)]


def number(value: str) -> Decimal:
    try:
        result = Decimal(value.strip().replace(",", ""))
    except (AttributeError, InvalidOperation):
        raise FoxhisError("JY01 指标缺失或格式无效，拒绝补零。") from None
    if not result.is_finite():
        raise FoxhisError("JY01 含非有限指标。")
    return result


def report_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except (AttributeError, ValueError):
        raise FoxhisError("JY01 营业日期无效。") from None


def rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def whole(value: Decimal) -> int:
    if value < 0 or value != value.to_integral_value():
        raise FoxhisError("JY01 房量必须是非负整数。")
    return int(value)


def build_rows(records: list[dict], room_records: list[dict], config: Config,
               snapshot: datetime) -> tuple[list[dict], dict]:
    days = collection_dates(snapshot.date())
    metrics = {day: {} for day in days}
    rebates = {day: {} for day in days}
    for record in records:
        day = report_date(record.get("business_date"))
        code = record.get("metric_code")
        if day not in metrics or code not in CONTROL_FIELDS:
            raise FoxhisError("JY01 日报返回非请求日期或未知指标。")
        field = CONTROL_FIELDS[code]
        if field in metrics[day]:
            raise FoxhisError("JY01 同日指标重复。")
        metrics[day][field] = number(record.get("metric_value"))
        if code in REVENUE_CODES:
            rebates[day][code] = number(record.get("daily_rebate"))
    if any(len(item) != len(CONTROL_FIELDS) for item in metrics.values()):
        raise FoxhisError("JY01 最近 30 天日结指标不完整，保留目标表。")

    totals, details, seen = {}, {day: [] for day in days}, set()
    manual_zero_rows, manual_adjustments = 0, {}
    for record in room_records:
        day = report_date(record.get("business_date"))
        code, hall = (record.get("room_type_code") or "").strip(), (record.get("hall") or "").strip()
        if day not in metrics or record.get("gkey") != "t" or not code or (day, code, hall) in seen:
            raise FoxhisError("JY01 房型控制行日期、维度或唯一键无效。")
        seen.add((day, code, hall))
        values = {field: number(record.get(field)) for field in ROOM_FIELDS}
        for field in ROOM_FIELDS:
            if field != "room_revenue":
                whole(values[field])
        if code == "{{{":
            if day in totals:
                raise FoxhisError("JY01 同日房型总计重复。")
            totals[day] = values
        elif code == "ZZZ":
            # 酒店总营业指标可包含未分配到房型的原生手工调整，不能丢弃。
            # 与房型明细一起核对总计，不把该笔收入强制分摊到某个房型。
            details[day].append(values)
            if any(values.values()):
                manual_adjustments[day.isoformat()] = values
            else:
                manual_zero_rows += 1
        else:
            details[day].append(values)

    rows, audits = [], {}
    for day in days:
        if day not in totals or not details[day]:
            raise FoxhisError("JY01 缺少房型日结总计或明细。")
        total, metric = totals[day], metrics[day]
        for field in ROOM_FIELDS:
            if sum((r[field] for r in details[day]), Decimal(0)) != total[field]:
                raise FoxhisError(f"JY01 {day} 房型明细与总计 {field} 不一致。")
        for field in ("total_rooms", "maintain_rooms", "locked_rooms", "own_use_rooms",
                      "available_rooms", "free_rooms"):
            if metric[field] != total[field]:
                raise FoxhisError(f"JY01 {day} 酒店日报与房型日结 {field} 不一致。")
        if metric["room_revenue"] + rebates[day]["013000"] != total["room_revenue"]:
            raise FoxhisError(f"JY01 {day} 房租净收入与房型日结不一致。")
        if (metric["room_income_total"] != metric["room_revenue"] + metric["room_other_income"]
                or rebates[day]["018000"] != rebates[day]["013000"] + rebates[day]["017000"]):
            raise FoxhisError(f"JY01 {day} 客房收入合计与房租、其他收入不一致。")
        revenue = metric["room_income_total"] + rebates[day]["018000"]
        for field in ("total_rooms", "maintain_rooms", "locked_rooms", "own_use_rooms",
                      "available_rooms", "free_rooms", "room_nights"):
            whole(metric[field])
        if (total["total_rooms"] - total["maintain_rooms"] - total["locked_rooms"] - total["own_use_rooms"] != total["available_rooms"]
                or total["available_rooms"] - total["sold_rooms"] != total["remaining_rooms"]
                or total["sold_rooms"] - total["free_rooms"] != metric["room_nights"]):
            raise FoxhisError(f"JY01 {day} 房量、免费房与计费出租间夜不一致。")
        for field, numerator, denominator, factor in (
                ("occupancy_rate", "room_nights", "available_rooms", 100),
                ("adr", "room_revenue", "room_nights", 1),
                ("revpar", "room_revenue", "total_rooms", 1)):
            expected = rounded(metric[numerator] * factor / metric[denominator]) if metric[denominator] else Decimal(0)
            if rounded(metric[field]) != expected:
                raise FoxhisError(f"JY01 {day} 原生 {field} 与房费/房量不一致。")
        rows.append({"hotel_name": config.hotel_name, "hotel_id": config.hotel_id, "source_platform": SOURCE_PLATFORM,
                     "business_date": day, "dimension_type": DIMENSION, "dimension_name": DIMENSION,
                     "room_type_id": None, "room_count": whole(metric["total_rooms"]),
                     "room_nights": metric["room_nights"], "room_revenue": rounded(revenue),
                     "occupancy_rate": rounded(metric["occupancy_rate"]),
                     "adr": rounded(revenue / metric["room_nights"]) if metric["room_nights"] else Decimal(0),
                     "revpar": rounded(revenue / metric["total_rooms"]) if metric["total_rooms"] else Decimal(0),
                     "sold_rooms": whole(total["sold_rooms"]),
                     "remaining_rooms": whole(total["remaining_rooms"]), "orders_today": None,
                     "snapshot_time": snapshot.replace(microsecond=0)})
        audits[day.isoformat()] = {**metric, "net_room_income_total": revenue, "room_rebate": rebates[day]["018000"],
                                  "sold_rooms": total["sold_rooms"], "remaining_rooms": total["remaining_rooms"]}
    return rows, {"requested_dates": [d.isoformat() for d in days], "by_date": audits,
                  "manual_zero_rows": manual_zero_rows,
                  "manual_adjustments_included": manual_adjustments,
                  "room_revenue_total": sum((r["room_revenue"] for r in rows), Decimal(0)),
                  "room_nights_total": sum((r["room_nights"] for r in rows), Decimal(0)),
                  "unmapped_fields": {"orders_today": "未验证当日新增订单数的等价指标，保留 NULL"},
                  "basis": "yjourrep 018000客房收入合计含返减及其他客房收入；ADR、RevPAR按客房净收入重算；计费间夜不含免费房"}


def fetch_rows(config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    days = collection_dates(snapshot.date())
    require_columns(config, "sysdata", ["bdate"])
    closed = query("select convert(char(8),bdate,112) as current_business_date from sysdata", config, timeout=30)
    if len(closed) != 1 or report_date(closed[0].get("current_business_date")) <= days[-1]:
        raise FoxhisError("JY01 西软尚未切换到下一营业日，拒绝采集未完成日结。")
    require_columns(config, "yjourrep", ["date", "class", "day", "day_rebate"])
    require_columns(config, "yrmsalerep_new", ["date", "gkey", "hall", "code", "ttl", "mnt", "os", "htl", "avl", "vac", "ent",
                                               "soldf", "soldg", "soldc", "soldl", "incomef", "incomeg", "incomec", "incomel"])
    params = {"start_date": days[0].strftime("%Y%m%d"), "end_date": snapshot.strftime("%Y%m%d")}
    records = query(JY01_DAILY_QUERY.format(**params), config, timeout=180)
    controls = query(JY01_ROOM_CONTROL_QUERY.format(**params), config, timeout=180)
    rows, summary = build_rows(records, controls, config, snapshot)
    summary["current_source_business_date"] = closed[0]["current_business_date"]
    return rows, summary


def sync_rows(rows: list[dict], config: Config) -> int:
    if not rows or not config.hotel_id.strip() or not config.hotel_name.strip():
        raise FoxhisError("JY01 无有效数据或酒店 ID、名称为空。")
    stamp = rows[0]["snapshot_time"]
    days = collection_dates(stamp.date())
    if (len(rows) != len(days) or {r["business_date"] for r in rows} != set(days)
            or any(r["hotel_id"] != config.hotel_id or r["hotel_name"] != config.hotel_name
                   or r["source_platform"] != SOURCE_PLATFORM or r["snapshot_time"] != stamp
                   or r["dimension_type"] != DIMENSION or r["dimension_name"] != DIMENSION for r in rows)):
        raise FoxhisError("JY01 输出必须为同批本酒店最近 30 个完整日的总营业指标。")
    condition = "hotel_id=%s AND source_platform=%s AND business_date >= %s AND business_date < %s AND dimension_type=%s AND dimension_name=%s"
    args = (config.hotel_id, SOURCE_PLATFORM, days[0], stamp.date(), DIMENSION, DIMENSION)
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT snapshot_time FROM `{JY01_TABLE}` WHERE {condition} FOR UPDATE", args)
            if any(item[0] > stamp for item in cursor.fetchall()):
                raise FoxhisError("JY01 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{JY01_TABLE}` WHERE {condition}", args)
            cursor.executemany(f"INSERT INTO `{JY01_TABLE}` ({', '.join('`'+c+'`' for c in COLUMNS)}) "
                               f"VALUES ({', '.join(['%s']*len(COLUMNS))})", [tuple(r[c] for c in COLUMNS) for r in rows])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, business_date: date, snapshot_time: datetime) -> dict:
    rows, summary = fetch_rows(config, snapshot_time)
    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else sync_rows(rows, config)
    summary.update(report="jy01", source_platform=SOURCE_PLATFORM, hotel_id=config.hotel_id,
                   snapshot_time=snapshot_time.isoformat(), row_count=len(rows), written=written, dry_run=dry_run, rows=rows)
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jy01", collect)


if __name__ == "__main__":
    raise SystemExit(main())
