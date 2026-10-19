#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RS01：当月已退房订单的房费科目流水；保留现有字段和跨月账务日期。

order_id=foxhis:账号:流水号，避免多笔过账覆盖。--dry-run 不写 MySQL。
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from foxhis_common import Config, FoxhisError, SOURCE_PLATFORM, db_version, mysql_connection, query, report_main, require_columns, save_output, table_columns
from foxhis_sql import ROOM_REVENUE_ORDERS_QUERY, ROOM_REVENUE_QUERY, ROOM_REVENUE_REQUIRED, ROOM_REVENUE_TABLE

SUMMARY_FILE = "foxhis_room_revenue_summary.json"
INSERT_COLUMNS = [
    "hotel_name", "hotel_id", "source_platform", "business_date", "room_no",
    "room_type_name", "room_type_id", "guest_name", "customer_source", "checkin_time",
    "checkout_time", "rack_rate", "price_type", "room_daily_price", "stay_type",
    "charge_subject", "room_nights", "room_fee", "operator_name", "order_id",
    "channel_unit_id", "snapshot_time",
]


def month_window(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    return start, date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value.strip().replace(",", ""))
    except (AttributeError, InvalidOperation):
        raise FoxhisError("RS01 金额或数量缺失/无效，拒绝写入不完整流水。") from None
    if not result.is_finite():
        raise FoxhisError("RS01 金额或数量不是有限数值。")
    return result


def _datetime(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y%m%d %H:%M:%S")
    except ValueError:
        raise FoxhisError("RS01 主单的入住/退房时间格式无效。") from None


def rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def order_map(records: list[dict[str, str]], start: date, end: date, snapshot: datetime) -> dict[str, dict]:
    orders: dict[str, dict] = {}
    for record in records:
        folio = (record.get("folio_no") or "").strip()
        checkout = _datetime(record.get("checkout_time") or "")
        checkin = _datetime(record.get("checkin_time") or "")
        if not folio or not checkout or not start <= checkout.date() < end or checkout > snapshot:
            raise FoxhisError("RS01 退房主单账号、状态范围或退房时间不合法。")
        if checkin and checkin > checkout:
            raise FoxhisError("RS01 主单入住时间晚于退房时间。")
        order = {**{key: (value or "").strip() for key, value in record.items()},
                 "checkin_time": checkin, "checkout_time": checkout}
        previous = orders.get(folio)
        if previous:
            if any(previous[key] != order[key] for key in ("room_no", "room_type_code", "checkin_time", "checkout_time")):
                raise FoxhisError("RS01 当前与历史主单存在冲突，拒绝重复关联。")
            if order["order_origin"] != "history":
                continue
        orders[folio] = order
    return orders


def build_rows(records: list[dict[str, str]], orders: dict[str, dict], type_names: dict[str, str],
               room_types: dict[str, str], markets: dict[str, str], config: Config,
               snapshot_time: datetime) -> tuple[list[dict], dict]:
    rows = []
    seen: dict[tuple[str, str], tuple] = {}
    duplicate_count = zero_count = 0
    for record in records:
        folio = (record.get("folio_no") or "").strip()
        line = (record.get("line_no") or "").strip()
        if not folio or not line:
            raise FoxhisError("RS01 流水缺少账号或流水号。")
        order = orders.get(folio)
        if not order:
            raise FoxhisError("RS01 流水未关联当月已退房订单，请重新采集。")
        amount = _decimal(record.get("amount"))
        quantity = _decimal(record.get("quantity"))
        try:
            business_date = datetime.strptime((record.get("business_date") or "").strip(), "%Y%m%d").date()
        except ValueError:
            raise FoxhisError("RS01 流水营业日期无效。") from None
        code = (record.get("charge_code") or "").strip()
        subject = (record.get("charge_subject") or "").strip()
        if not code or not subject:
            raise FoxhisError("RS01 房费科目缺少代码或名称。")
        source_room = (record.get("room_no") or "").strip()
        signature = (business_date, source_room, code, amount, quantity, (record.get("operator_code") or "").strip())
        key = (folio, line)
        if key in seen:
            if seen[key] != signature:
                raise FoxhisError("RS01 当前账和历史账的同一流水不一致。")
            duplicate_count += 1
            continue
        seen[key] = signature
        if not amount and not quantity:
            zero_count += 1
            continue
        room = source_room or order["room_no"]
        # 同退房房号时保留历史主单房型，换房前的流水再按其实际房号查字典。
        type_code = (order["room_type_code"] if room == order["room_no"] else "") or room_types.get(room)
        if not room or not type_code or type_code not in type_names:
            raise FoxhisError("RS01 有效房费流水缺少可核验的房号或房型。")
        is_room_night = (record.get("charge_tail") or "").strip() == "01"
        nights = quantity if is_room_night else Decimal("0")
        order_id = f"foxhis:{folio}:{line}"
        if len(order_id) > 100:
            raise FoxhisError("RS01 流水稳定键超过现有 order_id 长度。")
        rows.append({
            "hotel_name": config.hotel_name, "hotel_id": config.hotel_id, "source_platform": SOURCE_PLATFORM,
            "business_date": business_date, "room_no": room, "room_type_name": type_names[type_code],
            "room_type_id": None, "guest_name": order.get("guest_name") or None,
            "customer_source": markets.get(order.get("market_code", "")) or order.get("market_code") or None,
            "checkin_time": order["checkin_time"], "checkout_time": order["checkout_time"],
            "rack_rate": None, "price_type": order.get("price_type") or None,
            "room_daily_price": rounded(amount / quantity) if is_room_night and quantity else None,
            "stay_type": "钟点房" if code == "1080" else None,
            "charge_subject": subject, "room_nights": rounded(nights), "room_fee": rounded(amount),
            "operator_name": (record.get("operator_name") or record.get("operator_code") or "").strip() or None,
            "order_id": order_id, "channel_unit_id": None, "snapshot_time": snapshot_time.replace(microsecond=0),
        })
    return rows, {"source_rows": len(records), "unique_transactions": len(seen), "deduplicated_rows": duplicate_count,
                  "zero_placeholders_skipped": zero_count, "checkout_orders": len(orders)}


def fetch_rows(config: Config, snapshot_time: datetime) -> tuple[list[dict], dict]:
    start, end = month_window(snapshot_time.date())
    for table in ("account", "haccount"):
        require_columns(config, table, ROOM_REVENUE_REQUIRED[1])
    for table in ("master", "hmaster"):
        require_columns(config, table, ["accnt", "roomno", "type", "market", "ratecode", "haccnt", "citime", "cotime", "sta"])
    for table, columns in {"pccode": ["pccode", "descript", "jierep", "tail"], "guest": ["no", "name"],
                           "sys_empno": ["empno", "name"], "typim": ["type", "descript"],
                           "rmsta": ["roomno", "type"], "mktcode": ["code", "descript"]}.items():
        require_columns(config, table, columns)
    require_columns(config, "hmaster", ["name"])
    params = {"checkout_start": start.strftime("%Y%m%d"), "checkout_end": end.strftime("%Y%m%d")}
    orders = order_map(query(ROOM_REVENUE_ORDERS_QUERY.format(**params), config, timeout=180), start, end, snapshot_time)
    records = query(ROOM_REVENUE_QUERY.format(**params), config, timeout=240)
    type_names = {row["type"].strip(): row["descript"].strip() for row in query("select type,descript from typim", config)}
    room_types = {row["roomno"].strip(): row["type"].strip() for row in query("select roomno,type from rmsta", config)}
    markets = {row["code"].strip(): row["descript"].strip() for row in query("select code,descript from mktcode", config)}
    rows, summary = build_rows(records, orders, type_names, room_types, markets, config, snapshot_time)
    summary.update(checkout_start=start.isoformat(), checkout_end_exclusive=end.isoformat(),
                   room_fee_total=str(sum((row["room_fee"] for row in rows), Decimal("0"))),
                   room_nights_total=str(sum((row["room_nights"] for row in rows), Decimal("0"))),
                   orders_with_room_charges=len({row["order_id"].rsplit(":", 1)[0] for row in rows}))
    return rows, summary


def sync_rows(rows: list[dict], config: Config, start: date, end: date) -> int:
    if not rows or not config.hotel_id.strip():
        raise FoxhisError("RS01 无有效流水或酒店 ID 为空，保留已有数据。")
    stamp = rows[0]["snapshot_time"]
    if len({(row["hotel_id"], row["order_id"], row["business_date"], row["room_no"], row["charge_subject"]) for row in rows}) != len(rows):
        raise FoxhisError("RS01 输出稳定键重复。")
    if any(row["hotel_id"] != config.hotel_id or row["source_platform"] != SOURCE_PLATFORM or
           row["snapshot_time"] != stamp or not start <= row["checkout_time"].date() < end for row in rows):
        raise FoxhisError("RS01 酒店、来源、采集时间或退房范围不一致。")
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            scope = (config.hotel_id, SOURCE_PLATFORM, start, end)
            condition = "hotel_id=%s AND source_platform=%s AND checkout_time >= %s AND checkout_time < %s"
            cursor.execute(f"SELECT snapshot_time FROM `{ROOM_REVENUE_TABLE}` WHERE {condition} FOR UPDATE", scope)
            if any(item[0] > stamp for item in cursor.fetchall()):
                raise FoxhisError("RS01 已有更新快照，拒绝较早批次覆盖。")
            cursor.execute(f"DELETE FROM `{ROOM_REVENUE_TABLE}` WHERE {condition}", scope)
            cursor.executemany(
                f"INSERT INTO `{ROOM_REVENUE_TABLE}` ({', '.join('`'+c+'`' for c in INSERT_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(INSERT_COLUMNS))})",
                [tuple(row[name] for name in INSERT_COLUMNS) for row in rows],
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, business_date: date, snapshot_time: datetime) -> dict:
    rows, summary = fetch_rows(config, snapshot_time)
    start, end = month_window(snapshot_time.date())
    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else sync_rows(rows, config, start, end)
    summary.update(report="room_revenue", source_platform=SOURCE_PLATFORM, hotel_id=config.hotel_id,
                   business_date=snapshot_time.date().isoformat(), snapshot_time=snapshot_time.isoformat(),
                   row_count=len(rows), written=written, dry_run=dry_run,
                   basis="当月已退房订单的房费科目净过账流水；按退房日期筛选，保留跨月营业日期，不混入押金与代收款")
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    if "--introspect" in sys.argv:
        config = Config.from_env()
        print("数据库版本:", db_version(config))
        for table in ("account", "haccount", "master", "hmaster", "pccode"):
            print(table, table_columns(config, table))
        return 0
    return report_main("room_revenue", collect)


if __name__ == "__main__":
    raise SystemExit(main())
