"""美团 PMS JL11：最近 30 个完整营业日的房型汇总快照。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from room_status import metric

REPORT_URL = BASE_URL + "/hotelpms/api/v2/report/jy10"
TABLE = "jl11_room_type_classification"
SOURCE = "PMS（美团）"
WINDOW_DAYS = 30
FIELDS = {"roomCount": "room_count", "roomPoint": "room_nights", "rentRate": "occupancy_rate",
          "avgRoomPrice": "average_room_price", "revpar": "revpar", "roomFee": "room_revenue",
          "nightRoomCount": "overnight_room_count", "nightRentRate": "overnight_occupancy_rate"}
PERCENTAGES = {"rentRate", "nightRentRate"}


def report_window(today):
    end = today - timedelta(days=1)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def request_body(org_id, today):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    start, end = report_window(today)
    return {"hotelId": org_id, "startDate": start.isoformat(), "endDate": end.isoformat(), "searchCategory": []}


def validate_context(variables, link, org_id, start, end):
    expected = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if not isinstance(variables, dict) or any(variables.get(key) != value for key, value in expected.items()):
        raise ValueError("JL11 响应日期与请求不符")
    for key in ("orgId", "hotelId"):
        if variables.get(key) is not None and str(variables[key]) != org_id:
            raise ValueError("JL11 响应酒店与请求不符")
    if link is None:
        return
    if not isinstance(link, str):
        raise ValueError("JL11 明细链接无效")
    url = urlsplit(link)
    params = parse_qs(url.query or url.fragment.partition("?")[2], keep_blank_values=True)
    for key, value in {**expected, "orgId": org_id, "hotelId": org_id}.items():
        if key in params and params[key] != [value]:
            raise ValueError("JL11 明细链接的酒店或日期不符")


def value(item, key):
    if key not in item:
        raise ValueError(f"JL11 缺少 {key}")
    raw = item[key]
    if isinstance(raw, str):
        raw = raw.strip().replace(",", "")
        if key in PERCENTAGES:
            raw = raw.removesuffix("%")
    number = metric(raw, key)
    if number is None or (key in {"roomCount", "roomPoint", "rentRate", "nightRoomCount", "nightRentRate"} and number < 0):
        raise ValueError(f"JL11 {key} 缺失或无效")
    return number


def transform(response, org_id, snapshot, hotel_id, hotel_name):
    start, end = report_window(snapshot.date())
    return transform_period(response, org_id, start, end, snapshot, hotel_id, hotel_name)


def transform_period(response, org_id, start, end, snapshot, hotel_id, hotel_name):
    """校验 JY10 指定区间的原生房型汇总，供 JL11 和单日 JL01 共用。"""
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("JL11 接口业务状态无效")
    envelope = response.get("data")
    data = envelope.get("data") if isinstance(envelope, dict) else None
    items = data.get("summaryList") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("JL11 房型汇总为空或缺失，保留旧数据")
    variables = envelope.get("variables")
    validate_context(variables, None, org_id, start, end)
    rows, names, ids = [], set(), set()
    total = None
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("JL11 房型统计行无效")
        name = str(item.get("roomTypeName") or "").strip()
        code = str(item.get("roomTypeKey") or "").strip()
        if not name or not code or name in names or code in ids or ((name == "总计") != (code == "总计")):
            raise ValueError("JL11 房型名称或编码为空、重复或总计标记不符")
        names.add(name)
        ids.add(code)
        validate_context(variables, item.get("url"), org_id, start, end)
        row = {"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
               "snapshot_date": snapshot.date(), "period_start": start, "period_end": end, "section": "summary",
               "room_type_name": name, "dimension_code": "", "dimension_name": "",
               **{field: value(item, key) for key, field in FIELDS.items()}, "snapshot_time": snapshot}
        rows.append(row)
        if code == "总计":
            total = row
    details = [row for row in rows if row is not total]
    if not details or total is None:
        raise ValueError("JL11 房型明细或总计缺失，保留旧数据")
    for field in ("room_count", "room_nights", "room_revenue", "overnight_room_count"):
        if abs(sum(row[field] for row in details) - total[field]) > Decimal("0.01"):
            raise ValueError("JL11 房型明细与总计不一致，保留旧数据")
    return details


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
        response = request_json(session, "POST", REPORT_URL, json=body)
        if clock().date() != snapshot.date():
            raise ValueError("JL11 采集跨日，本轮不写入")
        return transform(response, org_id, snapshot, hotel_id, hotel_name)
    finally:
        if owns:
            session.close()


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("JL11 为空，保留旧数据")
    first = rows[0]
    start, end = report_window(first["snapshot_time"].date())
    names = {row["room_type_name"] for row in rows}
    if (len(names) != len(rows) or "总计" in names
            or any(not row.get("hotel_id") or not row.get("hotel_name") or not row.get("room_type_name")
                   or row.get("source_platform") != SOURCE or row.get("section") != "summary"
                   or row.get("dimension_code") != "" or row.get("dimension_name") != ""
                   or row.get("period_start") != start or row.get("period_end") != end
                   or row.get("snapshot_date") != first["snapshot_time"].date()
                   or any(row[key] != first[key] for key in ("hotel_id", "hotel_name", "snapshot_time")) for row in rows)):
        raise ValueError("JL11 酒店、批次或统计窗口不一致")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT snapshot_date, section, room_type_name, dimension_code, source_platform, snapshot_time "
                        f"FROM `{TABLE}` WHERE hotel_id=%s FOR UPDATE", (first["hotel_id"],))
            for day, section, name, dimension, source, stamp in cur.fetchall():
                if source == SOURCE and stamp > first["snapshot_time"]:
                    raise ValueError("JL11 已有更新批次，拒绝旧快照覆盖")
                if (source != SOURCE and day == first["snapshot_date"] and section == "summary"
                        and name in names and dimension == ""):
                    raise ValueError("同房型 JL11 快照属于其他 PMS，拒绝覆盖")
            cur.execute("SELECT source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_meituan' AND is_active=1 AND source_product_id=''",
                        (first["hotel_id"],))
            aliases = {}
            for name, room_id in cur.fetchall():
                if name and room_id:
                    aliases.setdefault(name, set()).add(room_id)
            records = [{**row, "room_type_id": next(iter(aliases[row["room_type_name"]]))
                        if len(aliases.get(row["room_type_name"], set())) == 1
                        else None} for row in rows]
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s", (first["hotel_id"], SOURCE))
            columns = list(records[0])
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + key + ')s' for key in columns)})", records)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS JL11 最近 30 个完整营业日房型汇总")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), os.environ.get("HOTEL_ID", "").strip(),
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(), os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS JL11 房型汇总{'校验' if args.dry_run else '入库'}完成：{len(rows)} 个房型")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS JL11 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
