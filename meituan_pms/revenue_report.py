"""美团 PMS RS01：CW04 原生房费日报，首次三个完整月及本月至昨日。"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from booking_report import text, timestamp
from room_status import metric, room_count
from meituan_pms.retention import cleanup_history

REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/cw04"
TABLE = "rs01_room_revenue_daily"
SOURCE = "PMS（美团）"
BACKFILL_MONTHS = 3


def connect_mysql():
    from ota_mysql_writer import connect_mysql as connect
    return connect()


def needs_initial_backfill(hotel_id, connection=None):
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    owns = connection is None
    connection = connection or connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s", (hotel_id, SOURCE))
            return cur.fetchone()[0] == 0
    finally:
        if owns:
            connection.close()


def collection_windows(today, initial):
    latest = today - timedelta(days=1)
    current = (latest.replace(day=1), latest)
    if not initial:
        return [current]
    windows, end = [], today.replace(day=1) - timedelta(days=1)
    for _ in range(BACKFILL_MONTHS):
        start = end.replace(day=1)
        windows.append((start, end))
        end = start - timedelta(days=1)
    windows.reverse()
    if windows[-1] != current:
        windows.append(current)
    return windows


def request_body(org_id, start, end):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"orgId": org_id, "startDate": start.isoformat(), "endDate": end.isoformat(),
            "roomNumber": "", "customerCategory": "", "memberLevel": "", "contract": "", "roomType": "",
            "orderSource": "", "channel": "", "roomPriceType": "", "checkinTypes": [], "analysisChannel": "",
            "isCash": False, "roomNumbers": []}


def row_key(row):
    return tuple(row[key] for key in ("order_id", "business_date", "room_no", "charge_subject"))


def transform(response, org_id, start, end, snapshot, hotel_id, hotel_name):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("RS01 接口业务状态无效")
    data = response.get("data")
    variables = data.get("variables") if isinstance(data, dict) else None
    items = data.get("dataList") if isinstance(data, dict) else None
    if (not isinstance(variables, dict) or str(variables.get("orgId") or "") != org_id
            or variables.get("startDate") != start.isoformat() or variables.get("endDate") != end.isoformat()
            or not isinstance(items, list)):
        raise ValueError("RS01 酒店、查询日期或房费列表不符")
    for key in ("total", "totalCount"):
        if key in data and room_count(data[key], key) != len(items):
            raise ValueError("RS01 房费列表不完整")
    rows, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("RS01 房费记录无效")
        try:
            day = date.fromisoformat(item.get("businessDate") or "")
        except (TypeError, ValueError):
            raise ValueError("RS01 房费营业日无效") from None
        order, room, subject = (text(item.get(key)) for key in ("orderNumber", "roomNumber", "roomPriceCategory"))
        fee, nights = (metric(item.get(key), key) for key in ("roomFee", "roomNights"))
        if not start <= day <= end or not order or not room or not subject or fee is None or nights is None:
            raise ValueError("RS01 房费营业日、订单、房号、科目或实际指标缺失")
        row = {"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE, "business_date": day,
               "order_id": order, "room_no": room, "room_type_name": text(item.get("roomType")),
               "guest_name": text(item.get("customerName")), "customer_source": text(item.get("customerCategory")),
               "checkin_time": timestamp(item.get("checkinTime"), "checkinTime"),
               "checkout_time": timestamp(item.get("departureTime"), "departureTime"),
               "rack_rate": metric(item.get("marketPrice"), "marketPrice"), "price_type": text(item.get("roomRateType")),
               "room_daily_price": metric(item.get("actualPrice"), "actualPrice"), "stay_type": text(item.get("checkinType")),
               "charge_subject": subject, "room_nights": nights, "room_fee": fee,
               "operator_name": text(item.get("operator")), "channel_unit_id": None, "snapshot_time": snapshot}
        key = row_key(row)
        if key in seen:
            raise ValueError("RS01 房费唯一键重复，拒绝丢失明细")
        seen.add(key)
        rows.append(row)
    return rows


def collect(cookie, hotel_id, hotel_name, org_id, *, initial, snapshot=None, session=None, clock=now_shanghai, wait=time.sleep):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = snapshot or clock()
    windows = collection_windows(snapshot.date(), initial)
    bodies = [request_body(org_id, start, end) for start, end in windows]
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    try:
        rows = []
        for index, ((start, end), body) in enumerate(zip(windows, bodies)):
            data = request_json(session, "POST", REPORT_URL, json=body)
            if clock().date() != snapshot.date():
                raise ValueError("RS01 采集跨日，本轮不写入")
            rows.extend(transform(data, org_id, start, end, snapshot, hotel_id, hotel_name))
            if index + 1 < len(windows):
                wait(1)
        return rows
    finally:
        if owns:
            session.close()


def sync_mysql(rows, hotel_id, snapshot, *, initial, connection=None):
    windows = collection_windows(snapshot.date(), initial)
    if (not hotel_id or len({row_key(row) for row in rows}) != len(rows)
            or any(row.get("hotel_id") != hotel_id or row.get("source_platform") != SOURCE
                   or row.get("snapshot_time") != snapshot
                   or not any(start <= row["business_date"] <= end for start, end in windows) for row in rows)):
        raise ValueError("RS01 酒店、批次、唯一键或日期范围不一致")
    if not rows:
        return  # 合法空列表不清空旧房费，也不制造零记录。
    owns = connection is None
    connection = connection or connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT source_platform, snapshot_time FROM `{TABLE}` WHERE hotel_id=%s "
                        "AND business_date BETWEEN %s AND %s FOR UPDATE", (hotel_id, windows[0][0], windows[-1][1]))
            for source, stamp in cur.fetchall():
                if source != SOURCE or stamp > snapshot:
                    raise ValueError("RS01 查询范围已有其他 PMS 或更新批次，拒绝覆盖")
            cur.execute("SELECT source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_meituan' AND is_active=1 AND source_product_id=''",
                        (hotel_id,))
            aliases = {}
            for name, room_id in cur.fetchall():
                if name and room_id:
                    aliases.setdefault(name, set()).add(room_id)
            records = [{**row, "room_type_id": next(iter(aliases[row["room_type_name"]]))
                        if len(aliases.get(row["room_type_name"], set())) == 1 else None} for row in rows]
            columns = list(records[0])
            identity = {"hotel_id", "source_platform", "order_id", "business_date", "room_no", "charge_subject"}
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + key + ')s' for key in columns)}) ON DUPLICATE KEY UPDATE "
                            + ', '.join(f"{key}=VALUES({key})" for key in columns if key not in identity), records)
            cleanup_history(cur, TABLE, hotel_id, snapshot)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS RS01 CW04 房费日报采集")
    parser.add_argument("--dry-run", action="store_true", help="只查询本月至昨日，不连接数据库或补采历史")
    args = parser.parse_args(argv)
    try:
        hotel_id = os.environ.get("HOTEL_ID", "").strip()
        snapshot = now_shanghai()
        initial = False if args.dry_run else needs_initial_backfill(hotel_id)
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id,
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(), os.environ.get("MEITUAN_PMS_ORG_ID", "").strip(),
                       initial=initial, snapshot=snapshot)
        if not args.dry_run:
            sync_mysql(rows, hotel_id, snapshot, initial=initial)
        print(f"美团 PMS RS01 房费日报{'校验' if args.dry_run else '入库'}完成：{len(rows)} 条")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS RS01 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
