"""西软 JL11：最近30个完整日按房型汇总，业务字段参考宝寓。"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM, mysql_connection, query, report_main, save_output
from foxhis_jy01_data import number, rounded, report_date
from foxhis_sql import JL11_TABLE, JL11_ROOM_QUERY, JL11_CONTROL_QUERY

WINDOW_DAYS = 30
SUMMARY_FILE = "foxhis_jl11_summary.json"
ROOM_FIELDS = ("room_count", "available_rooms", "room_nights", "room_revenue")
CONTROL_FIELDS = dict(zip(("010100", "010190", "011000", "013000"), ROOM_FIELDS))
COLUMNS = ["hotel_id", "hotel_name", "source_platform", "snapshot_date", "period_start", "period_end", "section",
           "room_type_id", "room_type_name", "dimension_code", "dimension_name", "room_count", "room_nights",
           "occupancy_rate", "room_revenue", "average_room_price", "revpar", "overnight_room_count",
           "overnight_occupancy_rate", "snapshot_time"]


def report_window(today: date) -> tuple[date, date]:
    return today - timedelta(days=WINDOW_DAYS), today - timedelta(days=1)


def build_rows(records: list[dict], rooms: list[dict], config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    start, end = report_window(snapshot.date())
    controls = {start + timedelta(days=n): {} for n in range(WINDOW_DAYS)}
    for r in records:
        day, field = report_date(r.get("business_date")), CONTROL_FIELDS.get(r.get("metric_code"))
        if day not in controls or not field or field in controls[day]:
            raise FoxhisError("JL11 日结控制指标日期、代码或唯一键无效。")
        controls[day][field] = number(r.get("metric_value"))
    if any(len(values) != len(ROOM_FIELDS) for values in controls.values()):
        raise FoxhisError("JL11 最近30天日结不完整，保留旧数据。")

    totals, seen = {}, set()
    details = defaultdict(lambda: {field: Decimal(0) for field in ROOM_FIELDS})
    grouped = defaultdict(lambda: {field: Decimal(0) for field in ROOM_FIELDS})
    manual_revenue = Decimal(0)
    for r in rooms:
        day, code = report_date(r.get("business_date")), r.get("room_type_code")
        key = (day, code, r.get("hall"))
        if day not in controls or r.get("gkey") != "t" or not code or key in seen:
            raise FoxhisError("JL11 房型日结日期、维度或唯一键无效。")
        seen.add(key)
        values = {field: number(r.get(field)) for field in ROOM_FIELDS}
        if any(values[field] < 0 for field in ROOM_FIELDS if field != "room_revenue"):
            raise FoxhisError("JL11 房量、可用房日或计费间夜不能为负数。")
        if code == "{{{":
            if day in totals:
                raise FoxhisError("JL11 同日房型总计重复。")
            totals[day] = values
            continue
        name = (r.get("room_type_name") or "").strip()
        if not name:
            raise FoxhisError("JL11 缺少历史房型名称。")
        for field in ROOM_FIELDS:
            details[day][field] += values[field]
            if code != "ZZZ":
                grouped[name][field] += values[field]
        if code == "ZZZ":
            manual_revenue += values["room_revenue"]
    for day, expected in controls.items():
        if day not in totals or details[day] != totals[day] or totals[day] != expected:
            raise FoxhisError(f"JL11 {day} 房型明细、总计与酒店日结不一致。")

    base = dict(hotel_id=config.hotel_id, hotel_name=config.hotel_name, source_platform=SOURCE_PLATFORM,
                snapshot_date=snapshot.date(), period_start=start, period_end=end, section="summary",
                room_type_id=None, dimension_code="", dimension_name="", snapshot_time=snapshot.replace(microsecond=0))
    rows = []
    for name, v in sorted(grouped.items()):
        count, available, nights, revenue = (v[field] for field in ROOM_FIELDS)
        occupancy = (nights * 100 / available).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if available else None
        rows.append({**base, "room_type_name": name, "room_count": count, "room_nights": nights,
                     "occupancy_rate": occupancy, "room_revenue": rounded(revenue),
                     "average_room_price": rounded(revenue / nights) if nights else None,
                     "revpar": rounded(revenue / count) if count else None,
                     "overnight_room_count": nights, "overnight_occupancy_rate": occupancy})
    if not rows:
        raise FoxhisError("JL11 无有效房型汇总。")
    return rows, {"period_start": str(start), "period_end": str(end), "completed_days": WINDOW_DAYS,
                  "room_revenue_total": sum((r["room_revenue"] for r in rows), Decimal(0)),
                  "room_nights_total": sum((r["room_nights"] for r in rows), Decimal(0)),
                  "excluded_manual_room_revenue": manual_revenue,
                  "basis": "宝寓summary字段；西软历史房型累计房日、计费间夜和净房费；ZZZ手工调整仅参与对账，不写入房型表"}


def fetch_rows(config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    start, end = report_window(snapshot.date())
    closed = query("select convert(char(8),bdate,112) as current_business_date from sysdata", config, timeout=30)
    if len(closed) != 1 or report_date(closed[0].get("current_business_date")) <= end:
        raise FoxhisError("JL11 西软尚未完成请求日夜审。")
    params = {"start_date": start.strftime("%Y%m%d"), "end_date": snapshot.strftime("%Y%m%d")}
    records = query(JL11_CONTROL_QUERY.format(**params), config, timeout=120)
    rooms = query(JL11_ROOM_QUERY.format(**params), config, timeout=120)
    return build_rows(records, rooms, config, snapshot)


def sync_rows(rows: list[dict], config: Config) -> int:
    if not rows or not config.hotel_id.strip() or not config.hotel_name.strip():
        raise FoxhisError("JL11 无有效数据或酒店ID、名称为空。")
    stamp = rows[0]["snapshot_time"]
    start, end = report_window(stamp.date())
    if (len({r["room_type_name"] for r in rows}) != len(rows)
            or any(r["hotel_id"] != config.hotel_id or r["hotel_name"] != config.hotel_name or r["source_platform"] != SOURCE_PLATFORM
                   or r["snapshot_time"] != stamp or r["snapshot_date"] != stamp.date() or r["period_start"] != start or r["period_end"] != end
                   or r["section"] != "summary" or not r["room_type_name"].strip() or r["dimension_code"] or r["dimension_name"]
                   or r["overnight_room_count"] != r["room_nights"] or r["overnight_occupancy_rate"] != r["occupancy_rate"] for r in rows)):
        raise FoxhisError("JL11 输出批次、周期或宝寓参考字段不一致。")
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            args = (config.hotel_id, SOURCE_PLATFORM)
            cursor.execute(f"SELECT snapshot_time FROM `{JL11_TABLE}` WHERE hotel_id=%s AND source_platform=%s FOR UPDATE", args)
            if any(r[0] > stamp for r in cursor.fetchall()):
                raise FoxhisError("JL11 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{JL11_TABLE}` WHERE hotel_id=%s AND source_platform=%s", args)
            cursor.executemany(f"INSERT INTO `{JL11_TABLE}` ({', '.join('`'+c+'`' for c in COLUMNS)}) VALUES ({', '.join(['%s']*len(COLUMNS))})",
                               [tuple(r[c] for c in COLUMNS) for r in rows])
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
    summary.update(report="jl11", hotel_id=config.hotel_id, business_date=summary["period_end"], rows=rows,
                   row_count=len(rows), written=0 if dry_run else sync_rows(rows, config), dry_run=dry_run)
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jl11", collect)


if __name__ == "__main__":
    raise SystemExit(main())
