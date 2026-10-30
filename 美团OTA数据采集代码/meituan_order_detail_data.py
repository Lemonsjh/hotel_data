"""美团近30天普通订单及会员等级采集。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meituan_config import HOTEL_NAME, MEITUAN_EB_COOKIE, PARTNER_ID, POI_ID, USER_AGENT
from ota_mysql_writer import OUTPUT_DIR, sync_order_detail_history

API_URL = "https://eb.meituan.com/api/v1/ebooking/orders"
TABLE_NAME = "meituan_ota_order_detail"
SHANGHAI = timezone(timedelta(hours=8))
PAGE_SIZE = 10
MEMBER_LEVELS = {1: "普通会员", 2: "白银会员", 3: "黄金会员", 4: "白金会员", 5: "黑金会员", 6: "黑钻会员"}


def report_window(captured_at: datetime) -> tuple[date, date]:
    return captured_at.date() - timedelta(days=29), captured_at.date()


def request_params(start: date, end: date, offset: int) -> dict:
    return {
        "poiId": POI_ID, "partnerId": PARTNER_ID, "filter": "ALL",
        "startTime": int(datetime.combine(start, day_time.min, SHANGHAI).timestamp() * 1000),
        "endTime": int(datetime.combine(end + timedelta(days=1), day_time.min, SHANGHAI).timestamp() * 1000) - 1,
        "searchTimeType": 2,  # 购买日期
        "offset": offset, "limit": PAGE_SIZE, "orderType": "4,7",
        "sortField": 2, "sortType": 2, "invoiceMark": 0, "memberMark": 0,
        "hkbMark": 0, "roomIds": "", "zlPois": "", "bizTag": 0,
    }


def request_page(client: requests.Session, start: date, end: date, offset: int) -> dict:
    try:
        response = client.get(
            API_URL, params=request_params(start, end, offset),
            headers={"Cookie": MEITUAN_EB_COOKIE, "User-Agent": USER_AGENT,
                     "Accept": "application/json, text/plain, */*",
                     "Referer": "https://eb.meituan.com/sub-app/order-eb/index.html"},
            timeout=30, allow_redirects=False,
        )
    except requests.RequestException:
        raise RuntimeError("美团订单接口连接失败") from None
    if response.status_code != 200:
        raise RuntimeError(f"美团订单接口 HTTP {response.status_code}")
    payload = response.json()
    if payload.get("status") != 0:
        raise RuntimeError("美团订单接口未返回成功状态")
    result = payload.get("data") or {}
    if not isinstance(result.get("results"), list) or not isinstance(result.get("total"), int):
        raise RuntimeError("美团订单列表数据不完整")
    return result


def fetch_orders(start: date, end: date) -> list[dict]:
    if not MEITUAN_EB_COOKIE or not POI_ID or not PARTNER_ID:
        raise RuntimeError("请先配置美团 EB Cookie、poiId 和 partnerId")
    orders = {}
    with requests.Session() as client:
        for page in range(1000):
            result = request_page(client, start, end, page * PAGE_SIZE)
            items, total = result["results"], result["total"]
            for item in items:
                if (str(item.get("poiIdStr") or item["poiId"]) != POI_ID
                        or str(item["partnerId"]) != PARTNER_ID):
                    raise RuntimeError("美团订单所属酒店与配置不符")
                orders[str(item["orderId"])] = item
            if page * PAGE_SIZE + len(items) >= total:
                if len(orders) != total:
                    raise RuntimeError("美团订单分页不完整")
                return list(orders.values())
            if len(items) < PAGE_SIZE:
                raise RuntimeError("美团订单分页不完整")
            time.sleep(1)
    raise RuntimeError("美团订单分页超出上限")


def string_id(value) -> str | None:
    return None if value in (None, "") else str(value)


def cents(value) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value)) / 100


def time_value(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    text = str(value)
    if text.isdigit():
        stamp = int(text)
        return datetime.fromtimestamp(stamp / 1000 if stamp > 10_000_000_000 else stamp, SHANGHAI).replace(tzinfo=None)
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(SHANGHAI).replace(tzinfo=None) if parsed.tzinfo else parsed


def date_value(value) -> date | None:
    parsed = time_value(value)
    return parsed.date() if parsed else None


def build_rows(items: list[dict], captured_at: datetime) -> list[dict]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    if not hotel_id:
        raise ValueError("请先配置内部酒店 ID")
    rows = []
    for item in items:
        level = item.get("memberLevel")
        level = int(level) if level not in (None, "") else None
        rows.append({
            "hotel_id": hotel_id, "hotel_name": HOTEL_NAME or None,
            "poi_id": POI_ID, "partner_id": PARTNER_ID, "order_id": str(item["orderId"]),
            "source_order_id": string_id(item.get("sourceOrderId")),
            "order_status": item["status"], "order_type": item.get("type"),
            "payment_type": item.get("paymentType"),
            "booking_time": time_value(item.get("bookingTimeString") or item.get("bookingTime")),
            "arrival_date": date_value(item.get("checkInDateString") or item.get("checkInDate")),
            "departure_date": date_value(item.get("checkOutDateString") or item.get("checkOutDate")),
            "room_id": string_id(item.get("roomId")), "room_type_name": item.get("roomName"),
            "goods_id": string_id(item.get("goodsId")), "room_quantity": item.get("roomCount"),
            "order_price": cents(item.get("price")), "total_fee": cents(item.get("totalFee")),
            "commission": cents(item.get("commission")), "real_commission": cents(item.get("realCommission")),
            "floor_price": cents(item.get("floorPrice")), "real_floor_price": cents(item.get("realFloorPrice")),
            "channel_label": item.get("channelLabel"), "user_id": string_id(item.get("userId")),
            "member_level": level, "member_level_name": MEMBER_LEVELS.get(level),
            "member_benefits": item.get("memberBenefits"), "member_mark": item.get("memberMark"),
            "member_display_mode": item.get("memberDisplayMode"),
            "check_in_status": item.get("checkInStatus"), "snapshot_time": captured_at,
        })
    return rows


def sync_orders(rows: list[dict], start: date) -> None:
    if rows:
        headers = list(rows[0])
        sync_order_detail_history(
            TABLE_NAME, headers, [[row[key] for key in headers] for row in rows], rows[0]["hotel_id"], start,
            key_column="order_id", date_column="booking_time", scope={"poi_id": POI_ID, "partner_id": PARTNER_ID},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只读取，不写数据库或文件")
    args = parser.parse_args(argv)
    captured_at = datetime.now(SHANGHAI).replace(tzinfo=None)
    start, end = report_window(captured_at)
    rows = build_rows(fetch_orders(start, end), captured_at)
    if not args.dry_run:
        sync_orders(rows, start)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary = {"table_name": TABLE_NAME, "row_count": len(rows), "start_date": str(start),
                   "end_date": str(end), "snapshot_time": str(captured_at)}
        (OUTPUT_DIR / f"{TABLE_NAME}.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    print(f"Meituan order detail {'validated' if args.dry_run else 'synced'}: rows={len(rows)} period={start}..{end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
