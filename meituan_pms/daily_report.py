"""美团 PMS 日报：最近 30 个完整营业日的总营业指标。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from collector import BASE_URL, now_shanghai, request_json
from room_status import metric, room_count

REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/jy03"
TABLE = "jy01_hotel_statistics_daily"
SOURCE = "PMS（美团）"
RECENT_DAYS = 30


def collection_dates(today):
    return [today - timedelta(days=offset) for offset in range(RECENT_DAYS, 0, -1)]


def request_body(org_id, dates):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"orgIds": [org_id], "orgId": org_id, "env": "",
            "startDate": dates[0].isoformat(), "endDate": dates[-1].isoformat(),
            "searchCategory": [], "showCustomerPointRate": True,
            "showChannelPointRate": True, "channelCategoryGroup": False}


def historical_value(item, field, *, count=False):
    if field not in item:
        raise ValueError(f"日报 {field} 缺失")
    if item[field] is None:
        return 0  # 按用户确认的日报页面口径，明确的 null 显示并保存为 0。
    return room_count(item[field], field) if count else metric(item[field], field)


def ratio(numerator, denominator):
    return ((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if denominator else Decimal("0.00"))


def transform(response, org_id, dates, snapshot_time, hotel_id, hotel_name):
    if not isinstance(response, dict) or response.get("code") != 10000:
        raise ValueError("日报接口业务状态无效")
    envelope = response.get("data")
    if not isinstance(envelope, dict):
        raise ValueError("日报数据缺失")
    variables, data = envelope.get("variables"), envelope.get("data")
    if (not isinstance(variables, dict) or variables.get("startDate") != dates[0].isoformat()
            or variables.get("endDate") != dates[-1].isoformat()
            or not isinstance(data, dict)):
        raise ValueError("日报响应日期范围不符或数据无效")
    items = data.get("summaryDetailList")
    if not isinstance(items, list) or not items:
        raise ValueError("日报总营业指标为空，保留旧数据")
    summaries = {}
    expected = {day.isoformat(): day for day in dates}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("日报总营业指标无效")
        if item.get("businessDateWeek") == "总计" and not item.get("businessDate"):
            continue
        day = item.get("businessDate")
        if str(item.get("orgId") or "") != org_id or day not in expected:
            raise ValueError("日报酒店机构或营业日期不符")
        if day in summaries:
            raise ValueError("日报同日总营业指标重复")
        summaries[day] = item
    if summaries.keys() != expected.keys():
        raise ValueError("日报总营业指标日期不完整，保留旧数据")
    return [summary_row(summaries[day.isoformat()], day, snapshot_time, hotel_id, hotel_name) for day in dates]


def summary_row(item, business_date, snapshot_time, hotel_id, hotel_name):
    total = room_count(item.get("roomCount"), "roomCount")
    sold = historical_value(item, "nightRoomCount", count=True)
    maintain = room_count(item.get("maintainRoomCount"), "maintainRoomCount")
    if maintain > total:
        raise ValueError("日报维修房数超过客房总数")
    revenue = historical_value(item, "roomRent")
    available = total - maintain
    return {"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
            "business_date": business_date, "dimension_type": "总营业指标", "dimension_name": "总营业指标",
            "room_type_id": None, "room_count": total, "room_nights": sold, "room_revenue": revenue,
            "occupancy_rate": ratio(sold * 100, available), "adr": ratio(revenue, sold),
            "revpar": ratio(revenue, total), "sold_rooms": sold,
            "remaining_rooms": max(available - sold, 0),
            "orders_today": None, "snapshot_time": snapshot_time}


def collect(cookie, hotel_id, hotel_name, org_id, *, session=None, clock=now_shanghai):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = clock()
    dates = collection_dates(snapshot.date())
    body = request_body(org_id, dates)
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json",
                            "Referer": BASE_URL + "/pms-report/home/report/", "Origin": BASE_URL})
    try:
        data = request_json(session, "POST", REPORT_URL, json=body)
        return transform(data, org_id, dates, snapshot, hotel_id, hotel_name)
    finally:
        if owns:
            session.close()


def sync_mysql(rows, connection=None):
    if not rows:
        raise ValueError("日报总营业指标为空")
    first = rows[0]
    expected = set(collection_dates(first["snapshot_time"].date()))
    if (len(rows) != RECENT_DAYS or {row["business_date"] for row in rows} != expected
            or any(row.get("source_platform") != SOURCE or not row.get("hotel_id") or not row.get("hotel_name")
                   or row.get("dimension_type") != "总营业指标" or row.get("dimension_name") != "总营业指标"
                   or any(row[key] != first[key] for key in ("hotel_id", "hotel_name", "snapshot_time")) for row in rows)):
        raise ValueError("日报必须是同批酒店最近 30 个完整营业日的总营业指标")
    owns = connection is None
    if owns:
        from ota_mysql_writer import connect_mysql
        connection = connect_mysql()
    try:
        with connection.cursor() as cur:
            # 标准唯一键按酒店名称建索引，校验内部酒店 ID，避免覆盖同名其他酒店。
            scope = (first["hotel_name"], SOURCE, min(expected), max(expected), "总营业指标", "总营业指标")
            where = "hotel_name=%s AND source_platform=%s AND business_date BETWEEN %s AND %s AND dimension_type=%s AND dimension_name=%s"
            cur.execute(f"SELECT hotel_id FROM `{TABLE}` WHERE {where} FOR UPDATE", scope)
            for existing in cur.fetchall():
                existing_id = existing.get("hotel_id") if isinstance(existing, dict) else existing[0]
                if str(existing_id) != first["hotel_id"]:
                    raise ValueError("同名日报属于其他酒店，拒绝覆盖")
            columns = list(first)
            updates = [key for key in columns if key not in {"hotel_name", "source_platform", "business_date",
                                                            "dimension_type", "dimension_name", "hotel_id"}]
            cur.executemany(f"INSERT INTO `{TABLE}` ({', '.join(columns)}) VALUES "
                        f"({', '.join('%(' + key + ')s' for key in columns)}) ON DUPLICATE KEY UPDATE "
                        + ', '.join(f"{key}=COALESCE(VALUES({key}), {key})" for key in updates), rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if owns:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS 最近 30 天总营业指标采集")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接 MySQL")
    args = parser.parse_args(argv)
    try:
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), os.environ.get("HOTEL_ID", "").strip(),
                      os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(), os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS 最近 30 天总营业指标{'校验' if args.dry_run else '入库'}完成：{len(rows)} 天")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS 日报执行失败，请检查数据库配置；本轮未完成入库")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
