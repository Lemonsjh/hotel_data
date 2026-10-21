"""西软 JL01：昨日及去年同月同日的原生房型日结数据。"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM, mysql_connection, query, report_main, require_columns, save_output
from foxhis_sql import JL01_TABLE, JL01_REPORT_QUERY, JL01_CONTROL_QUERY

SUMMARY_FILE = "foxhis_jl01_summary.json"
COLUMNS = ["hotel_id", "hotel_name", "source_platform", "business_date", "room_type_name", "room_type_id",
           "pms_rate_room_type_id", "room_nights", "occupancy_rate", "room_revenue", "adr", "revpar", "snapshot_time"]
BASE_METRICS = ("total_rooms", "available_rooms", "room_nights", "room_revenue")
CONTROL_FIELDS = {"010100": "total_rooms", "010190": "available_rooms", "011000": "room_nights",
                  "012000": "occupancy_rate", "013000": "room_revenue", "014000": "adr", "018560": "revpar"}


def collection_dates(today: date) -> list[date]:
    yesterday = today - timedelta(days=1)
    try:
        last_year = yesterday.replace(year=yesterday.year - 1)
    except ValueError:
        last_year = yesterday.replace(year=yesterday.year - 1, day=28)
    return [yesterday, last_year]


def number(value: str) -> Decimal:
    try:
        result = Decimal(value.strip().replace(",", ""))
    except (AttributeError, InvalidOperation):
        raise FoxhisError("JL01 报表指标缺失或格式无效，拒绝补零。") from None
    if not result.is_finite():
        raise FoxhisError("JL01 报表含非有限指标。")
    return result


def report_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except (AttributeError, ValueError):
        raise FoxhisError("JL01 日结报表日期无效。") from None


def rounded(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def derived(metrics: dict) -> dict:
    revenue, nights = metrics["room_revenue"], metrics["room_nights"]
    return {**metrics,
            "occupancy_rate": rounded(nights * 100 / metrics["available_rooms"], "0.0001") if metrics["available_rooms"] else None,
            "adr": rounded(revenue / nights) if nights else None,
            "revpar": rounded(revenue / metrics["total_rooms"]) if metrics["total_rooms"] else None}


def build_rows(records: list[dict], controls: list[dict], config: Config, days: list[date], snapshot: datetime) -> tuple[list[dict], dict]:
    grouped: dict[tuple[date, str], dict] = {}
    totals: dict[date, dict] = {}
    seen: set[tuple] = set()
    manual_rows = 0
    manual_totals: dict[date, dict] = {}
    for record in records:
        day = report_date(record.get("business_date"))
        if day not in days or day >= snapshot.date() or record.get("gkey") != "t":
            raise FoxhisError("JL01 返回非请求营业日或非房型维度数据。")
        code = (record.get("room_type_code") or "").strip()
        hall = (record.get("hall") or "").strip()
        key = (day, code, hall)
        if not code or key in seen:
            raise FoxhisError("JL01 日结房型行重复或缺少代码。")
        seen.add(key)
        values = {field: number(record.get(field)) for field in BASE_METRICS}
        if any(values[field] < 0 for field in ("total_rooms", "available_rooms", "room_nights")) or values["available_rooms"] > values["total_rooms"]:
            raise FoxhisError("JL01 房量、可用房或间夜不合法。")
        if code == "{{{":
            if day in totals:
                raise FoxhisError("JL01 同一日期出现多个总计，拒绝重复汇总。")
            totals[day] = values
            continue
        if code == "ZZZ":
            manual = manual_totals.setdefault(day, {field: Decimal(0) for field in BASE_METRICS})
            for field in BASE_METRICS:
                manual[field] += values[field]
            manual_rows += int(not any(values.values()))
            continue
        name = (record.get("room_type_name") or "").strip()
        if not name:
            raise FoxhisError("JL01 原生日结缺少历史房型名称。")
        previous = grouped.get((day, code))
        if previous:
            if previous["name"] != name:
                raise FoxhisError("JL01 同房型代码的历史名称不一致。")
            for field in BASE_METRICS:
                previous[field] += values[field]
        else:
            grouped[(day, code)] = {"name": name, **values}

    available = sorted({day for day, _code in grouped})
    if days[0] not in available:
        raise FoxhisError("JL01 昨日日结数据缺失，保留目标表。")
    if set(totals) != set(available):
        raise FoxhisError("JL01 房型明细与总计日期不完整。")
    control_map = {}
    for record in controls:
        day, code = report_date(record.get("business_date")), record.get("metric_code")
        if day not in days or code not in CONTROL_FIELDS or (day, code) in control_map:
            raise FoxhisError("JL01 日报控制指标重复或日期不合法。")
        control_map[(day, code)] = number(record.get("metric_value"))
    audits = {}
    for day in available:
        details = [value for (item_day, _), value in grouped.items() if item_day == day]
        summed = {field: sum((value[field] for value in details), Decimal(0)) for field in BASE_METRICS}
        # 手工调整参与酒店总额核对，不生成房型，也不分摊到真实房型。
        summed = {field: value + manual_totals.get(day, {}).get(field, Decimal(0)) for field, value in summed.items()}
        if any(summed[field] != totals[day][field] for field in BASE_METRICS):
            raise FoxhisError("JL01 房型明细与原生总计不一致。")
        metrics = derived(summed)
        for code, field in CONTROL_FIELDS.items():
            reference = control_map.get((day, code))
            if reference is None:
                raise FoxhisError("JL01 对应酒店日报控制指标缺失。")
            value = metrics[field]
            if (value is None and reference != 0) or (value is not None and rounded(value) != rounded(reference)):
                raise FoxhisError(f"JL01 与酒店日结指标 {code} 不一致。")
        audits[day.isoformat()] = metrics
    rows = []
    for (day, code), values in sorted(grouped.items()):
        metrics = derived(values)
        rows.append({"hotel_id": config.hotel_id, "hotel_name": config.hotel_name, "source_platform": SOURCE_PLATFORM,
                     "business_date": day, "room_type_name": values["name"], "room_type_id": None,
                     "pms_rate_room_type_id": code, "room_nights": metrics["room_nights"],
                     "occupancy_rate": metrics["occupancy_rate"], "room_revenue": rounded(metrics["room_revenue"]),
                     "adr": metrics["adr"], "revpar": metrics["revpar"], "snapshot_time": snapshot.replace(microsecond=0)})
    if len({(row["business_date"], row["room_type_name"]) for row in rows}) != len(rows):
        raise FoxhisError("JL01 存在同日同名不同代码的房型，目标唯一键无法区分。")
    return rows, {"requested_dates": [day.isoformat() for day in days], "available_dates": [day.isoformat() for day in available],
                  "missing_dates": [day.isoformat() for day in days if day not in available],
                  "manual_zero_rows_skipped": manual_rows,
                  "excluded_manual_by_date": {day.isoformat(): values for day, values in manual_totals.items()},
                  "by_date": audits}


def fetch_rows(config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    days = collection_dates(snapshot.date())
    require_columns(config, "yrmsalerep_new", ["date", "gkey", "hall", "code", "descript", "ttl", "avl",
                                               "soldf", "soldg", "soldc", "soldl", "ent", "incomef", "incomeg", "incomec", "incomel"])
    require_columns(config, "yjourrep", ["date", "class", "day"])
    params = {"yesterday": days[0].strftime("%Y%m%d"), "last_year": days[1].strftime("%Y%m%d")}
    records = query(JL01_REPORT_QUERY.format(**params), config, timeout=180)
    controls = query(JL01_CONTROL_QUERY.format(**params), config, timeout=180)
    return build_rows(records, controls, config, days, snapshot)


def sync_rows(rows: list[dict], config: Config) -> int:
    if not rows or not config.hotel_id.strip():
        raise FoxhisError("JL01 无有效日结数据或酒店 ID 为空。")
    stamp = rows[0]["snapshot_time"]
    requested = collection_dates(stamp.date())
    if any(row["hotel_id"] != config.hotel_id or row["source_platform"] != SOURCE_PLATFORM or
           row["snapshot_time"] != stamp or row["business_date"] not in requested for row in rows):
        raise FoxhisError("JL01 输出酒店、来源、营业日或采集时间不一致。")
    if len({(r["business_date"], r["room_type_name"]) for r in rows}) != len(rows):
        raise FoxhisError("JL01 输出唯一键重复。")
    days = sorted({row["business_date"] for row in rows})
    args = (config.hotel_id, SOURCE_PLATFORM, *days)
    condition = f"hotel_id=%s AND source_platform=%s AND business_date IN ({', '.join(['%s']*len(days))})"
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT snapshot_time FROM `{JL01_TABLE}` WHERE {condition} FOR UPDATE", args)
            if any(item[0] > stamp for item in cursor.fetchall()):
                raise FoxhisError("JL01 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{JL01_TABLE}` WHERE {condition}", args)
            cursor.executemany(f"INSERT INTO `{JL01_TABLE}` ({', '.join('`'+c+'`' for c in COLUMNS)}) "
                               f"VALUES ({', '.join(['%s']*len(COLUMNS))})",
                               [tuple(row[c] for c in COLUMNS) for row in rows])
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
    if summary["missing_dates"]:
        print("WARN JL01 无历史数据，未补零、未替换该日期：" + ",".join(summary["missing_dates"]))
    written = 0 if dry_run else sync_rows(rows, config)
    summary.update(report="jl01", business_date=collection_dates(snapshot_time.date())[0].isoformat(),
                   source_platform=SOURCE_PLATFORM, hotel_id=config.hotel_id, snapshot_time=snapshot_time.isoformat(),
                   row_count=len(rows), written=written, dry_run=dry_run, rows=rows,
                   basis="原生房型日结：计费间夜=已售间夜-免费房间夜，出租率=计费间夜/可用房数*100，ADR=房费/计费间夜，RevPAR=房费/总房数")
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jl01", collect)


if __name__ == "__main__":
    raise SystemExit(main())
