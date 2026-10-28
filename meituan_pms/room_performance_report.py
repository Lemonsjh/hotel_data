"""美团 PMS JL01：昨日及去年同日的房型实际经营数据。"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from classification_report import REPORT_URL, SOURCE, transform_period, validate_context
from performance_report import same_day_last_year

TABLE = "jl01_room_type_performance_daily"
RETENTION_DAYS = 400


def collection_dates(today):
    yesterday = today - timedelta(days=1)
    return [yesterday, same_day_last_year(yesterday)]


def request_body(org_id, day):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"hotelId": org_id, "startDate": day.isoformat(), "endDate": day.isoformat(), "searchCategory": []}


def transform(response, org_id, day, snapshot, hotel_id, hotel_name):
    try:
        envelope = response.get("data") if isinstance(response, dict) else None
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if (isinstance(response, dict) and response.get("code") == 10000 and isinstance(data, dict) and data.get("summaryList") == []
                and day == collection_dates(snapshot.date())[1]):
            validate_context(envelope.get("variables"), None, org_id, day, day)
            return []  # 去年同期尚无营业数据；不补零、不删除既有历史。
        summaries = transform_period(response, org_id, day, day, snapshot, hotel_id, hotel_name)
    except ValueError as exc:
        raise ValueError(str(exc).replace("JL11", "JL01")) from None
    return [{"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
             "business_date": day, "room_type_name": row["room_type_name"], "pms_rate_room_type_id": "",
             "room_nights": row["room_nights"], "occupancy_rate": row["occupancy_rate"],
             "room_revenue": row["room_revenue"], "adr": row["average_room_price"], "revpar": row["revpar"],
             "snapshot_time": snapshot} for row in summaries]


def collect(cookie, hotel_id, hotel_name, org_id, *, session=None, clock=now_shanghai, wait=time.sleep):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = clock()
    dates = collection_dates(snapshot.date())
    bodies = [request_body(org_id, day) for day in dates]
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    try:
        rows = []
        for index, (day, body) in enumerate(zip(dates, bodies)):
            response = request_json(session, "POST", REPORT_URL, json=body)
            if clock().date() != snapshot.date():
                raise ValueError("JL01 采集跨日，本轮不写入")
            rows.extend(transform(response, org_id, day, snapshot, hotel_id, hotel_name))
            if index + 1 < len(dates):
                wait(1)
        return rows
    finally:
        if owns:
            session.close()


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("JL01 为空，保留旧数据")
    first = rows[0]
    dates = collection_dates(first["snapshot_time"].date())
    keys = {(row["business_date"], row["room_type_name"]) for row in rows}
    days = {day for day, _ in keys}
    if (len(keys) != len(rows) or dates[0] not in days or not days <= set(dates)
            or any(not row.get("hotel_id") or not row.get("hotel_name") or not row.get("room_type_name")
                   or row["room_type_name"] == "总计" or row.get("source_platform") != SOURCE
                   or row.get("pms_rate_room_type_id") != ""
                   or any(row[key] != first[key] for key in ("hotel_id", "hotel_name", "snapshot_time")) for row in rows)):
        raise ValueError("JL01 酒店、批次、日期或房型不一致")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT source_platform, snapshot_time FROM `{TABLE}` WHERE hotel_id=%s "
                        "AND business_date IN (%s, %s) FOR UPDATE", (first["hotel_id"], *dates))
            for source, stamp in cur.fetchall():
                if source != SOURCE:
                    raise ValueError("目标日期 JL01 已有其他 PMS 数据，拒绝覆盖")
                if stamp > first["snapshot_time"]:
                    raise ValueError("JL01 已有更新批次，拒绝旧快照覆盖")
            cur.execute("SELECT source_room_type_name, room_type_id FROM hotel_room_type_mapping "
                        "WHERE hotel_id=%s AND source_platform='pms_meituan' AND is_active=1 AND source_product_id=''",
                        (first["hotel_id"],))
            aliases = {}
            for name, room_id in cur.fetchall():
                if name and room_id:
                    aliases.setdefault(name, set()).add(room_id)
            records = [{**row, "room_type_id": next(iter(aliases[row["room_type_name"]]))
                        if len(aliases.get(row["room_type_name"], set())) == 1 else None} for row in rows]
            columns = list(records[0])
            identity = {"hotel_id", "business_date", "room_type_name", "source_platform"}
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + key + ')s' for key in columns)}) ON DUPLICATE KEY UPDATE "
                            + ', '.join(f"{key}=VALUES({key})" for key in columns if key not in identity), records)
            cutoff = first["snapshot_time"].date() - timedelta(days=RETENTION_DAYS - 1)
            cur.execute(f"DELETE FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s AND business_date < %s",
                        (first["hotel_id"], SOURCE, cutoff))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS JL01 昨日及去年同日房型经营日报")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), os.environ.get("HOTEL_ID", "").strip(),
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(), os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS JL01 房型经营日报{'校验' if args.dry_run else '入库'}完成：{len(rows)} 条日期房型记录")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS JL01 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
