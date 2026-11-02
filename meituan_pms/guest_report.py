"""美团 PMS JD02：昨日营业日离店客人明细，每条记录单独保存60天。"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from collector import BASE_URL, now_shanghai, request_json
from booking_report import text
from room_status import metric, room_count
from OTA采集服务.pms_guest_storage import TABLE, RETENTION_DAYS, sync_guest_details

SOURCE = "PMS（美团）"
REPORT_URL = BASE_URL + "/hotelpms/api/v1/report/jd02"
TEXT_FIELDS = {
    "order_id": "orderSn", "room_number": "roomNumber", "room_type_name": "roomType",
    "customer_id": "customerId", "customer_name": "customerName", "id_card_number": "idCardNumber",
    "customer_mobile": "customerMobile", "checkin_type": "checkinType", "order_source": "orderSource",
    "guest_source": "source", "member_level_id": "memberLevelId", "member_level": "memberLevel",
    "oneself_or_not": "oneselfOrNot", "price_stakeholder_name": "priceStackeholderName",
    "price_stakeholder_mobile": "priceStackeholderMobile", "order_memo": "orderMemo",
    "check_out_tip": "checkOutTip", "checkin_status": "checkinStatus", "operator_name": "operator",
    "room_attr": "roomAttr", "main_checkin_id": "mainCheckinId",
}


def request_body(org_id, day):
    if not re.fullmatch(r"[0-9]+", org_id):
        raise ValueError("请配置有效的美团 PMS 机构 ID（orgId）")
    return {"orgId": org_id, "dateCategory": "0", "dateType": "2",
            "startDate": day.isoformat(), "endDate": day.isoformat(), "roomType": "", "roomAttr": "",
            "checkinTypes": [], "orderSource": "", "source": "", "memberLevelKey": "",
            "checkinStatusList": [], "operator": "", "startPrice": "", "endPrice": "", "orderFlagId": "",
            "showTotalCheckinCount": False, "keyWord": "", "isNewCustomer": False}


def timestamp(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed
    except ValueError:
        pass
    raise ValueError("JD02 入住或离店时间格式无效")


def transform(response, org_id, day, snapshot, hotel_id, hotel_name):
    data = response.get("data") if isinstance(response, dict) and response.get("code") == 10000 else None
    variables = data.get("variables") if isinstance(data, dict) else None
    items = data.get("dataList") if isinstance(data, dict) else None
    if (not isinstance(variables, dict) or str(variables.get("orgId") or "") != org_id
            or variables.get("startDate") != day.isoformat()
            or variables.get("endDate") not in (day.isoformat(), (day + timedelta(days=1)).isoformat())
            or not isinstance(items, list)):
        raise ValueError("JD02 酒店、查询日期或住店明细列表不符")
    for key in ("total", "totalCount"):
        if key in data and room_count(data[key], key) != len(items):
            raise ValueError("JD02 住店明细列表不完整")
    # 营业日查询可能覆盖次日凌晨；保留真实离店时间，归档日期仍是请求的营业日。
    last_day = datetime.fromisoformat(variables["endDate"]).date()
    rows = []
    for item in items:
        if not isinstance(item, dict) or not text(item.get("orderSn")):
            raise ValueError("JD02 订单号缺失或记录无效")
        flag = str(item.get("mainCheckinPerson"))
        if flag not in ("0", "1"):
            raise ValueError("JD02 主客人标记无效")
        arrival, departure = timestamp(item.get("arrivalTime")), timestamp(item.get("departureTime"))
        if (arrival is None or departure is None or not day <= departure.date() <= last_day
                or departure < arrival):
            raise ValueError("JD02 客人离店日期不符或入住离店时间无效")
        row = {key: text(item.get(field)) for key, field in TEXT_FIELDS.items()}
        row.update(hotel_id=hotel_id, hotel_name=hotel_name, source_platform=SOURCE, business_date=day,
                   snapshot_time=snapshot, arrival_time=arrival, departure_time=departure,
                   room_price=metric(item.get("price"), "JD02 price"), main_checkin_person=int(flag),
                   total_checkin_count=(room_count(item["totalCheckinCount"], "JD02 totalCheckinCount")
                                        if item.get("totalCheckinCount") is not None else None))
        rows.append(row)
    return rows


def collect(cookie, hotel_id, hotel_name, org_id, *, session=None, clock=now_shanghai):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    snapshot = clock()
    day = snapshot.date() - timedelta(days=1)
    body = request_body(org_id, day)
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    try:
        response = request_json(session, "POST", REPORT_URL, json=body)
        if clock().date() != snapshot.date():
            raise ValueError("JD02 采集跨日，本轮不写入")
        return transform(response, org_id, day, snapshot, hotel_id, hotel_name), snapshot
    finally:
        if owns:
            session.close()


def sync_mysql(rows, hotel_id, snapshot, connection=None):
    return sync_guest_details(rows, hotel_id, snapshot, SOURCE, "pms_meituan", connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS JD02 昨日离店客人明细采集（逐客保存，保留60天）")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        hotel_id = os.environ.get("HOTEL_ID", "").strip()
        rows, snapshot = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id,
                                 os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip(),
                                 os.environ.get("MEITUAN_PMS_ORG_ID", "").strip())
        if not args.dry_run:
            sync_mysql(rows, hotel_id, snapshot)
        print(f"美团 PMS JD02 {'校验' if args.dry_run else '入库'}完成：{len(rows)} 条客人住店记录")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS JD02 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
