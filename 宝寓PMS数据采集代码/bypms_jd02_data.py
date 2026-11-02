"""宝寓 JD02：昨日预离列表逐房读取已登记客人，逐条保存60天。"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bypms_jd01_data import parse_datetime
from OTA采集服务.pms_guest_storage import TABLE, RETENTION_DAYS, now_shanghai, sync_guest_details

BASE_URL = "https://pms-api.bypms.cn"
LIST_URL = BASE_URL + "/core/api/v1/night/list/pro"
GUEST_URL = BASE_URL + "/living/api/v1/night-bind/list"
SOURCE = "PMS（宝寓）"
PAGE_SIZE = 20


def text(value):
    return str(value).strip() if value is not None and str(value).strip() else None


def request_data(session, url, params):
    try:
        response = session.get(url, params=params, timeout=30, allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError("宝寓 JD02 请求失败，请检查网络和登录状态") from None
    if (not isinstance(payload, dict) or payload.get("state") != 0
            or not isinstance(payload.get("data"), dict)):
        raise RuntimeError("宝寓 JD02 接口未成功返回，请检查登录状态")
    return payload["data"]


def fetch_nights(session, day, wait=time.sleep):
    nights, seen, total = [], set(), None
    for page in range(1, 501):
        if page > 1:
            wait(1)
        data = request_data(session, LIST_URL, {"type": "checkout", "date": f"{day},{day}",
                                              "page": page, "pageSize": PAGE_SIZE, "state": "E"})
        batch, count = data.get("nightList"), data.get("total")
        if not isinstance(batch, list) or type(count) is not int or count < 0 or (total is not None and count != total):
            raise ValueError("宝寓 JD02 离店列表或分页总数不符")
        total = count
        for night in batch:
            night_id = text(night.get("id")) if isinstance(night, dict) else None
            if not night_id or night_id in seen:
                raise ValueError("宝寓 JD02 房晚记录缺失ID或分页重复")
            if night.get("state") != "E":
                raise ValueError("宝寓 JD02 返回非已退房记录")
            seen.add(night_id)
        nights.extend(batch)
        if len(nights) == total:
            return nights
        if not batch or len(nights) > total:
            raise ValueError("宝寓 JD02 离店列表分页不完整")
    raise ValueError("宝寓 JD02 离店列表超过500页")


def transform(night, data, day, snapshot, hotel_id, hotel_name):
    guests, header = data.get("binds"), data.get("night")
    if (not isinstance(guests, list) or not isinstance(header, dict)
            or text(header.get("id")) != text(night.get("id"))):
        raise ValueError("宝寓 JD02 登记客人列表与房晚不符")
    arrival, departure = parse_datetime(night.get("checkIn")), parse_datetime(night.get("checkOut"))
    # 预离列表可能包含计划离店日期不等于查询日期的记录，保留接口原值。
    if not text(night.get("contractId")) or arrival is None or departure is None or departure < arrival:
        raise ValueError("宝寓 JD02 订单或离店日期不符")
    try:
        price = Decimal(str(night["price"])) if night.get("price") not in (None, "", "****") else None
        if price is not None and not price.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ValueError("宝寓 JD02 房费格式无效") from None
    rows = []
    for guest in guests:
        if (not isinstance(guest, dict) or not text(guest.get("id"))
                or text(guest.get("roomNightId")) != text(night["id"])
                or text(guest.get("contractId")) != text(night["contractId"])):
            raise ValueError("宝寓 JD02 登记客人关联不符")
        rows.append({"hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
                     "business_date": day, "snapshot_time": snapshot, "order_id": text(night["contractId"]),
                     "room_number": text(night.get("roomNo")), "room_type_name": text(night.get("roomTypeName")),
                     "customer_id": text(guest["id"]), "customer_name": text(guest.get("renterName")),
                     "customer_mobile": text(guest.get("renterPhone")), "id_card_number": text(guest.get("renterIdcard")),
                     "arrival_time": arrival, "departure_time": departure, "room_price": price,
                     "checkin_type": text(night.get("contractType")), "order_source": text(night.get("channel")),
                     "guest_source": text(night.get("channelName") or night.get("channel")),
                     "checkin_status": {"P": "待入住", "S": "入住中", "E": "已退房"}.get(night.get("state"), text(night.get("state"))),
                     "operator_name": text(guest.get("emplName")), "order_memo": text(night.get("infoRemark")),
                     "main_checkin_person": None})
    return rows


def collect(cookie, hotel_id, hotel_name, *, session=None, clock=now_shanghai, wait=time.sleep):
    if not cookie.strip() or not hotel_id or not hotel_name:
        raise ValueError("请先登录宝寓 PMS 并配置酒店ID和酒店名称")
    snapshot = clock()
    day = snapshot.date() - timedelta(days=1)
    owns = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "User-Agent": "Mozilla/5.0",
                            "Referer": "https://p.bypms.cn/console/index.html"})
    try:
        rows = []
        nights = fetch_nights(session, day, wait)
        for index, night in enumerate(nights):
            if index:
                wait(1)
            data = request_data(session, GUEST_URL, {"nightId": night["id"]})
            rows.extend(transform(night, data, day, snapshot, hotel_id, hotel_name))
        if clock().date() != snapshot.date():
            raise ValueError("宝寓 JD02 采集跨日，本轮不写入")
        return rows, snapshot
    finally:
        if owns:
            session.close()


def sync_mysql(rows, hotel_id, snapshot, connection=None):
    return sync_guest_details(rows, hotel_id, snapshot, SOURCE, "pms_bypms", connection)


def main(argv=None):
    parser = argparse.ArgumentParser(description="宝寓 JD02 昨日离店客人明细（逐客保存，保留60天）")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接数据库")
    args = parser.parse_args(argv)
    try:
        hotel_id = os.environ.get("HOTEL_ID", "").strip()
        rows, snapshot = collect(os.environ.get("BYPMS_COOKIE", ""), hotel_id,
                                 os.environ.get("BYPMS_HOTEL_NAME", "").strip())
        if not args.dry_run:
            sync_mysql(rows, hotel_id, snapshot)
        print(f"宝寓 PMS JD02 {'校验' if args.dry_run else '入库'}完成：{len(rows)} 条客人住店记录")
        return 0
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("宝寓 JD02 执行失败，本轮未完成入库，请检查配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
