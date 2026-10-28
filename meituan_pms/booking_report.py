"""美团 PMS JD01：最近 30 天预计到店的预订中订单（含今天）。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from room_status import metric, room_count

REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/jd01"
TABLE = "jd01_booking_detail"
SOURCE = "PMS（美团）"
STATUSES = ("预订中", "InProgress")
RECENT_DAYS = 30
RETENTION_DAYS = 90


def collection_window(today):
    return today - timedelta(days=RECENT_DAYS - 1), today


def request_body(org_id, day):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    start, end = collection_window(day)
    return {"orgId": org_id, "dateType": "1", "startDate": start.isoformat(), "endDate": end.isoformat(),
            "orderSource": "", "customerCategory": "", "orderStatus": "InProgress", "roomPriceType": "",
            "roomType": "", "prePaymentType": "", "prePayment": False, "analysisChannel": ""}


def timestamp(value, field):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise ValueError(f"JD01 {field} 时间格式无效") from None
    if parsed.tzinfo is not None:
        raise ValueError(f"JD01 {field} 时间口径无效")
    return parsed


def text(value):
    return str(value).strip() if value is not None and str(value).strip() else None


def transform(response, org_id, day, snapshot, hotel_id, hotel_name):
    start, end = collection_window(day)
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("JD01 接口业务状态无效")
    data = response.get("data")
    variables = data.get("variables") if isinstance(data, dict) else None
    items = data.get("dataList") if isinstance(data, dict) else None
    if (not isinstance(variables, dict) or str(variables.get("orgId") or "") != org_id
            or variables.get("startDate") != start.isoformat() or variables.get("endDate") != end.isoformat()
            or not isinstance(items, list)):
        raise ValueError("JD01 酒店、查询日期或订单列表不符")
    for key in ("total", "totalCount"):
        if key in data and room_count(data[key], key) != len(items):
            raise ValueError("JD01 订单列表不完整")
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("JD01 订单记录无效")
        order_id = text(item.get("orderNo"))
        arrival = timestamp(item.get("estArriveTime"), "estArriveTime")
        departure = timestamp(item.get("estDepatureTime"), "estDepatureTime")
        status = text(item.get("orderStatus"))
        count = room_count(item.get("roomCount"), "roomCount")
        if (not order_id or arrival is None or not start <= arrival.date() <= end or status not in STATUSES or count < 1
                or (departure is not None and departure < arrival)):
            raise ValueError("JD01 订单号、入住日期、状态或房数无效")
        rows.append({"hotel_name": hotel_name, "hotel_id": hotel_id, "source_platform": SOURCE,
                     "order_id": order_id, "booking_time": timestamp(item.get("orderDate"), "orderDate"),
                     "contact": text(item.get("contractName")), "guest_source": text(item.get("source")),
                     "member_level": text(item.get("customerLevel")), "arrival_time": arrival,
                     "departure_time": departure, "room_type_name": text(item.get("roomType")), "room_count": count,
                     "price_type": text(item.get("roomPriceType")), "room_price": metric(item.get("roomPrice"), "roomPrice"),
                     "prepayment": metric(item.get("prePayAmount"), "prePayAmount"),
                     "guarantee_method": text(item.get("prePaymentType")), "booking_status": status,
                     "hold_time": timestamp(item.get("expireKeepTime"), "expireKeepTime"),
                     "remarks": text(item.get("remark")), "operator_name": text(item.get("operator")),
                     "snapshot_time": snapshot})
    return rows


def collect(cookie, hotel_id, hotel_name, org_id, *, session=None, clock=now_shanghai):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = clock()
    body = request_body(org_id, snapshot.date())
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    try:
        data = request_json(session, "POST", REPORT_URL, json=body)
        if clock().date() != snapshot.date():
            raise ValueError("JD01 采集跨日，本轮不写入")
        rows = transform(data, org_id, snapshot.date(), snapshot, hotel_id, hotel_name)
        return rows, snapshot
    finally:
        if owns:
            session.close()


def sync_mysql(rows, hotel_id, snapshot, connection=None):
    start_day, end_day = collection_window(snapshot.date())
    if not hotel_id or any(row.get("hotel_id") != hotel_id or row.get("source_platform") != SOURCE
                           or row.get("snapshot_time") != snapshot or row.get("booking_status") not in STATUSES
                           or not start_day <= row["arrival_time"].date() <= end_day for row in rows):
        raise ValueError("JD01 订单快照范围不一致")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            aliases = {}
            if rows:
                cur.execute("SELECT source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                            "WHERE hotel_id=%s AND source_platform='pms_meituan' AND is_active=1 AND source_product_id=''",
                            (hotel_id,))
                candidates = {}
                for name, room_id in cur.fetchall():
                    if name and room_id:
                        candidates.setdefault(name, set()).add(room_id)
                aliases = {name: next(iter(ids)) for name, ids in candidates.items() if len(ids) == 1}
            start = datetime.combine(start_day, time.min)
            end = datetime.combine(end_day + timedelta(days=1), time.min)
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                        "AND arrival_time >= %s AND arrival_time < %s AND booking_status IN (%s, %s)",
                        (hotel_id, SOURCE, start, end, *STATUSES))
            if rows:
                records = [{**row, "room_type_id": aliases.get(row["room_type_name"])} for row in rows]
                columns = list(records[0])
                cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                                f"({', '.join('%(' + key + ')s' for key in columns)})", records)
            cutoff = datetime.combine(snapshot.date() - timedelta(days=RETENTION_DAYS - 1), time.min)
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s AND arrival_time < %s",
                        (hotel_id, SOURCE, cutoff))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS JD01 最近 30 天预订中订单采集（含今天）")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        hotel_id = os.environ.get("HOTEL_ID", "").strip()
        rows, snapshot = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id,
                                 os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(),
                                 os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows, hotel_id, snapshot)
        print(f"美团 PMS JD01 {'校验' if args.dry_run else '入库'}完成：{len(rows)} 条房型预订记录")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS JD01 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
