"""美团 PMS 经营业绩日报：原生日/月/年三列，映射标准 JL02。"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from room_status import metric
from meituan_pms.retention import cleanup_history

REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/jy01"
TABLE = "jl02_hotel_performance_daily"
SOURCE = "PMS（美团）"
CATEGORY = "总营业指标"
CORE_METRICS = {"间夜数", "出租率", "总房费", "平均房价", "RevPar"}


def same_day_last_year(day):
    return day.replace(year=day.year - 1, day=min(day.day, monthrange(day.year - 1, day.month)[1]))


def collection_dates(today):
    yesterday = today - timedelta(days=1)
    previous_end = today.replace(day=1) - timedelta(days=1)
    earlier_end = previous_end.replace(day=1) - timedelta(days=1)
    return list(dict.fromkeys(day for latest in (yesterday, previous_end, earlier_end)
                              for day in (latest, same_day_last_year(latest))))


def request_body(org_id, business_date):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"orgId": org_id, "businessDate": business_date.isoformat(), "statisticsCodes": [],
            "expandAgentCompany": True, "env": "/pms-report"}


def validate_link(link, org_id, day):
    """响应无顶层酒店/日期回显；存在原生本日明细链接时核对其参数。"""
    if not link:
        return
    if not isinstance(link, str):
        raise ValueError("经营业绩日报明细链接格式无效")
    url = urlsplit(link)
    params = parse_qs(url.query or url.fragment.partition("?")[2], keep_blank_values=True)
    for key, expected in (("orgId", org_id), ("startDate", day), ("endDate", day)):
        if key in params and params[key] != [expected]:
            raise ValueError("经营业绩日报响应明细的酒店或日期不符")


def value(item, key):
    if key not in item:
        raise ValueError(f"经营业绩日报缺少 {key}")
    raw = item[key]
    if isinstance(raw, str):
        raw = raw.strip().removesuffix("%").replace(",", "")
    return metric(raw, key)


def transform(response, org_id, business_date, snapshot, hotel_id, hotel_name):
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("经营业绩日报业务状态无效")
    data = response.get("data")
    items = data.get("summaryList") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("经营业绩日报总营业指标缺失")
    rows, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("经营业绩日报统计行无效")
        if item.get("category") != CATEGORY:
            continue
        name = str(item.get("groupName") or item.get("statistics") or "").strip()
        if not name or name in seen:
            raise ValueError("经营业绩日报统计项目为空或重复")
        seen.add(name)
        validate_link(item.get("currentDayUrl"), org_id, business_date.isoformat())
        rows.append({"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
                     "business_date": business_date, "category": CATEGORY, "metric_name": name,
                     "value_day": value(item, "currentDay"), "value_month": value(item, "currentMonth"),
                     "value_year": value(item, "currentYear"), "snapshot_time": snapshot})
    if not CORE_METRICS <= seen:
        raise ValueError("经营业绩日报核心总营业指标不完整")
    return rows


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
            rows.extend(transform(response, org_id, day, snapshot, hotel_id, hotel_name))
            if index + 1 < len(dates):
                wait(1)
        return rows
    finally:
        if owns:
            session.close()


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("经营业绩日报为空，保留旧数据")
    first = rows[0]
    keys = {(row["business_date"], row["metric_name"]) for row in rows}
    expected = set(collection_dates(first["snapshot_time"].date()))
    if (len(keys) != len(rows) or {day for day, _ in keys} != expected
            or any(not CORE_METRICS <= {name for row_day, name in keys if row_day == day} for day in expected)
            or any(not row.get("hotel_id") or not row.get("hotel_name") or row.get("category") != CATEGORY
                   or row.get("source_platform") != SOURCE
                   or any(row[key] != first[key] for key in ("hotel_id", "hotel_name", "snapshot_time")) for row in rows)):
        raise ValueError("经营业绩日报批次、日期或总营业指标不完整")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            dates = sorted(expected)
            marks = ', '.join(['%s'] * len(dates))
            cur.execute(f"SELECT business_date, metric_name, source_platform FROM `{TABLE}` "
                        f"WHERE hotel_id=%s AND category=%s AND business_date IN ({marks}) FOR UPDATE",
                        (first["hotel_id"], CATEGORY, *dates))
            for day, name, source in cur.fetchall():
                if (day, name) in keys and source != SOURCE:
                    raise ValueError("同指标的经营业绩日报属于其他 PMS，拒绝覆盖")
            columns = list(first)
            identity = {"hotel_id", "business_date", "category", "metric_name"}
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                            f"({', '.join('%(' + key + ')s' for key in columns)}) ON DUPLICATE KEY UPDATE "
                            + ', '.join(f"{key}=VALUES({key})" for key in columns if key not in identity), rows)
            cleanup_history(cur, TABLE, first["hotel_id"], first["snapshot_time"])
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS JL02 经营业绩日报（总营业指标）")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), os.environ.get("HOTEL_ID", "").strip(),
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(), os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS JL02 总营业指标{'校验' if args.dry_run else '入库'}完成：{len(rows)} 行")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS JL02 执行失败，请检查数据库配置；本轮未完成入库")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
