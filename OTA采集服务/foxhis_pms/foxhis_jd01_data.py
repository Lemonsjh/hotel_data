"""西软 JD01：保留 PMS 原始账号，离店日期从一个自然月前起，未知字段留NULL。"""
from __future__ import annotations

import sys
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM, query, mysql_connection, report_main, save_output
from foxhis_jy01_data import number, rounded
from foxhis_sql import JD01_TABLE, JD01_ORDERS_QUERY, JD01_LEDGER_QUERY, JD01_RATE_QUERY

SUMMARY_FILE = "foxhis_jd01_summary.json"
STATUS = {"R": "预订", "I": "在住", "O": "已离店"}
COLUMNS = ["hotel_name", "hotel_id", "source_platform", "order_id", "booking_time", "contact", "guest_source", "member_level",
           "arrival_time", "departure_time", "room_type_name", "room_type_id", "room_count", "price_type", "room_price",
           "prepayment", "guarantee_method", "booking_status", "hold_time", "remarks", "operator_name", "snapshot_time"]


def checkout_cutoff(today: date) -> date:
    year, month = divmod(today.year * 12 + today.month - 2, 12)
    return date(year, month + 1, min(today.day, monthrange(year, month + 1)[1]))


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d %H:%M:%S")
    except (AttributeError, ValueError):
        return None


def build_rows(records: list[dict], ledger: list[dict], rates: list[dict], config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    cutoff = checkout_cutoff(snapshot.date())
    orders, order_keys = {}, set()
    for r in records:
        key = r.get("folio_no")
        if not key or r.get("origin") not in ("current", "history") or r.get("status_code") not in ("R", "I", "O", "S", "D"):
            raise FoxhisError("JD01 主单账号、来源或状态不合法。")
        identity = (key, r["origin"])
        if identity in order_keys:
            raise FoxhisError("JD01 同来源主单重复。")
        order_keys.add(identity)
        old = orders.get(key)
        if old:
            fields = ("arrival_time", "departure_time", "type_code", "room_count")
            if any(old[f] != r[f] for f in fields):
                raise FoxhisError("JD01 同账号主单重复或当前/历史记录冲突。")
            if r["origin"] != "history":
                continue
        orders[key] = r

    seen = {}
    fees = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for r in ledger:
        key = (r.get("folio_no"), r.get("line_no"))
        if not all(key):
            raise FoxhisError("JD01 房费流水缺少账号或流水号。")
        value = (number(r.get("amount")), number(r.get("nights")))
        if key in seen:
            if seen[key] != value:
                raise FoxhisError("JD01 同笔房费流水重复且金额不一致。")
            continue
        seen[key] = value
        fees[key[0]][0] += value[0]
        fees[key[0]][1] += value[1]
    segments = defaultdict(list)
    for r in rates:
        segments[r["folio_no"]].append(r)

    rows, price_basis = [], Counter()
    for key, r in sorted(orders.items()):
        arrival, departure = parse_time(r.get("arrival_time")), parse_time(r.get("departure_time"))
        count = number(r.get("room_count"))
        if not arrival or not departure or arrival >= departure or departure.date() < cutoff or count <= 0 or count != int(count):
            raise FoxhisError("JD01 主单抵离时间、离店范围或房数无效。")
        status, price, basis = r["status_code"], None, "unknown"
        if status == "O" and key in fees and fees[key][1] > 0:
            price, basis = rounded(fees[key][0] / fees[key][1]), "posted_average"
        elif status in ("R", "I") and r.get("share_code") == "F" and len(segments[key]) == 1:
            segment = segments[key][0]
            if (segment["start_date"] == arrival.strftime("%Y%m%d") and segment["end_date"] == departure.strftime("%Y%m%d")
                    and segment["type_code"] == r["type_code"] and segment.get("quantity") and number(segment["quantity"]) == count
                    and segment.get("rate")):
                rate = number(segment["rate"])
                if rate >= 0:
                    price, basis = rounded(rate), "whole_stay_rate"
        price_basis[basis] += 1
        rows.append({"hotel_name": config.hotel_name, "hotel_id": config.hotel_id, "source_platform": SOURCE_PLATFORM,
                     "order_id": key, "booking_time": parse_time(r.get("booking_time")), "contact": r.get("contact") or None,
                     "guest_source": r.get("guest_source") or None, "member_level": None, "arrival_time": arrival, "departure_time": departure,
                     "room_type_name": r.get("room_type_name") or None, "room_type_id": None, "room_count": int(count),
                     "price_type": r.get("price_type") or None, "room_price": price, "prepayment": None, "guarantee_method": None,
                     "booking_status": STATUS.get(status), "hold_time": None, "remarks": None,
                     "operator_name": r.get("operator_name") or None, "snapshot_time": snapshot.replace(microsecond=0)})
    if not rows and (ledger or rates):
        raise FoxhisError("JD01 主单为空但仍返回账务或价格数据，拒绝替换快照。")
    return rows, {"cutoff": str(cutoff), "source_orders": len(records), "archived_duplicates_removed": len(records)-len(orders),
                  "status_counts": dict(Counter(r["status_code"] for r in orders.values())), "price_basis": dict(price_basis),
                  "unmapped_statuses": dict(Counter(r["status_code"] for r in orders.values() if r["status_code"] not in STATUS)),
                  "nullable_fields": ["member_level", "room_type_id", "prepayment", "guarantee_method", "hold_time", "remarks"]}


def fetch_rows(config: Config, snapshot: datetime) -> tuple[list[dict], dict]:
    params = {"cutoff": checkout_cutoff(snapshot.date()).strftime("%Y%m%d")}
    records = query(JD01_ORDERS_QUERY.format(**params), config, timeout=120)
    ledger = query(JD01_LEDGER_QUERY.format(**params), config, timeout=120)
    rates = query(JD01_RATE_QUERY.format(**params), config, timeout=120)
    return build_rows(records, ledger, rates, config, snapshot)


def sync_rows(rows: list[dict], config: Config, snapshot: datetime | None = None) -> int:
    if not config.hotel_id.strip():
        raise FoxhisError("JD01 酒店ID为空。")
    stamp = rows[0]["snapshot_time"] if rows else snapshot
    if not isinstance(stamp, datetime):
        raise FoxhisError("JD01 空快照缺少已完成采集的时间，拒绝替换。")
    cutoff = checkout_cutoff(stamp.date())
    if (len({r["order_id"] for r in rows}) != len(rows)
            or any(r["hotel_id"] != config.hotel_id or r["source_platform"] != SOURCE_PLATFORM or r["snapshot_time"] != stamp
                   or r["departure_time"].date() < cutoff for r in rows)):
        raise FoxhisError("JD01 输出账号、酒店、来源或日期范围不一致。")
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            args = (config.hotel_id, SOURCE_PLATFORM, cutoff)
            condition = "hotel_id=%s AND source_platform=%s AND departure_time>=%s"
            cursor.execute(f"SELECT snapshot_time FROM `{JD01_TABLE}` WHERE {condition} FOR UPDATE", args)
            if any(r[0] and r[0] > stamp for r in cursor.fetchall()):
                raise FoxhisError("JD01 已有更新批次，拒绝旧快照覆盖。")
            cursor.execute(f"DELETE FROM `{JD01_TABLE}` WHERE {condition}", args)
            if rows:
                cursor.executemany(f"INSERT INTO `{JD01_TABLE}` ({', '.join('`'+c+'`' for c in COLUMNS)}) VALUES ({', '.join(['%s']*len(COLUMNS))})",
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
    summary.update(report="jd01", row_count=len(rows), written=0 if dry_run else sync_rows(rows, config, snapshot_time), dry_run=dry_run)
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jd01", collect)


if __name__ == "__main__":
    raise SystemExit(main())
