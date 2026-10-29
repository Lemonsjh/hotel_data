"""美团 PMS 月报：首次本年及去年已有月份，日常只刷新本月总营业指标。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from daily_report import ratio
from room_status import metric, room_count
from meituan_pms.retention import cleanup_history

REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/jy05"
TABLE = "jy03_hotel_statistics_month"
SOURCE = "PMS（美团）"


def connect_mysql():
    from ota_mysql_writer import connect_mysql as connect
    return connect()


def needs_initial_backfill(hotel_id, snapshot, connection=None):
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    owns = connection is None
    connection = connection or connect_mysql()
    try:
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                        "AND dimension_type=%s AND dimension_name=%s AND period_month BETWEEN %s AND %s",
                        (hotel_id, SOURCE, "总营业指标", "总营业指标", f"{snapshot.year - 1}-01", f"{snapshot.year}-12"))
            return cur.fetchone()[0] == 0
    finally:
        if owns:
            connection.close()


def request_body(org_id, year):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"orgIds": [org_id], "orgId": org_id, "year": year, "searchCategory": ["Summary"],
            "showCustomerPointRate": True, "showChannelPointRate": True}


def transform(response, org_id, year, snapshot, hotel_id, hotel_name, *, initial):
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("月报接口业务状态无效")
    envelope = response.get("data")
    data = envelope.get("data") if isinstance(envelope, dict) else None
    items = data.get("monthSummaryDetailList") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("月报总营业指标缺失")
    rows, seen = [], set()
    current_month = snapshot.strftime("%Y-%m")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("月报总营业指标无效")
        month = item.get("businessDateMonth")
        if month == "总计":
            continue
        if (not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month)
                or int(month[:4]) != year or str(item.get("orgId") or "") != org_id):
            raise ValueError("月报机构或月份与请求不符")
        if month in seen:
            raise ValueError("月报总营业指标月份重复")
        seen.add(month)
        if month > current_month or (not initial and month != current_month):
            continue
        total = room_count(item.get("roomCount"), "roomCount")
        nights = room_count(item.get("nightRoomCount"), "nightRoomCount")
        maintain = room_count(item.get("maintainRoomCount"), "maintainRoomCount")
        revenue = metric(item.get("roomRent"), "roomRent")
        if revenue is None or maintain > total:
            raise ValueError("月报房费缺失或房量无效")
        rows.append({"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
                     "period_month": month, "dimension_type": "总营业指标", "dimension_name": "总营业指标",
                     "room_type_id": None, "room_count": total, "room_nights": nights, "room_revenue": revenue,
                     "maintain_rooms": maintain, "occupancy_rate": ratio(nights * 100, total - maintain),
                     "adr": ratio(revenue, nights), "revpar": ratio(revenue, total), "snapshot_time": snapshot})
    return sorted(rows, key=lambda row: row["period_month"])


def collect(cookie, hotel_id, hotel_name, org_id, *, initial, snapshot=None, session=None):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = snapshot or now_shanghai()
    years = [snapshot.year, snapshot.year - 1] if initial else [snapshot.year]
    bodies = [request_body(org_id, year) for year in years]
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    try:
        rows = []
        for year, body in zip(years, bodies):
            data = request_json(session, "POST", REPORT_URL, json=body)
            rows.extend(transform(data, org_id, year, snapshot, hotel_id, hotel_name, initial=initial))
        if not rows or (not initial and not any(row["period_month"] == snapshot.strftime("%Y-%m") for row in rows)):
            raise ValueError("月报未返回目标月份，保留旧数据")
        return sorted(rows, key=lambda row: row["period_month"])
    finally:
        if owns:
            session.close()


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("月报为空，保留旧数据")
    first = rows[0]
    if (len({row["period_month"] for row in rows}) != len(rows)
            or any(not row.get("hotel_id") or not row.get("hotel_name") or row.get("source_platform") != SOURCE
                   or row.get("dimension_type") != "总营业指标" or row.get("dimension_name") != "总营业指标"
                   or any(row[key] != first[key] for key in ("hotel_id", "hotel_name", "snapshot_time")) for row in rows)):
        raise ValueError("月报酒店、批次或维度不一致")
    owns = connection is None
    connection = connection or connect_mysql()
    try:
        with connection.cursor() as cur:
            months = [row["period_month"] for row in rows]
            marks = ', '.join(['%s'] * len(months))
            cur.execute(f"SELECT hotel_id FROM `{TABLE}` WHERE hotel_name=%s AND source_platform=%s "
                        f"AND dimension_type=%s AND dimension_name=%s AND period_month IN ({marks}) FOR UPDATE",
                        (first["hotel_name"], SOURCE, "总营业指标", "总营业指标", *months))
            if any(str(existing[0]) != first["hotel_id"] for existing in cur.fetchall()):
                raise ValueError("同名月报属于其他酒店，拒绝覆盖")
            columns = list(first)
            keys = {"hotel_id", "hotel_name", "source_platform", "period_month", "dimension_type", "dimension_name"}
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + key + ')s' for key in columns)}) ON DUPLICATE KEY UPDATE "
                            + ', '.join(f"{key}=VALUES({key})" for key in columns if key not in keys), rows)
            cleanup_history(cur, TABLE, first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS 月报总营业指标采集")
    parser.add_argument("--dry-run", action="store_true", help="只读查询本年和去年，不连接数据库")
    args = parser.parse_args(argv)
    try:
        hotel_id = os.environ.get("HOTEL_ID", "").strip()
        snapshot = now_shanghai()
        initial = True if args.dry_run else needs_initial_backfill(hotel_id, snapshot)
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id,
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(),
                       os.environ.get("MEITUAN_PMS_ORG_ID", "").strip(), initial=initial, snapshot=snapshot)
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS 月报总营业指标{'校验' if args.dry_run else '入库'}完成：{len(rows)} 个月")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS 月报执行失败，请检查数据库配置；本轮未完成入库")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
