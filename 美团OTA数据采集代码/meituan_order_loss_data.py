from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from meituan_config import HOTEL_NAME, MEITUAN_EB_COOKIE, PARTNER_ID, POI_ID, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_order_loss_snapshot


API_URL = "https://eb.meituan.com/api/v1/ebooking/peerRank/order/loss/query"
HEADERS = [
    "hotel_id", "hotel_name", "business_date", "period_start_date", "period_end_date", "snapshot_time",
    "total_loss_order_count", "total_loss_room_nights", "total_loss_amount", "competitor_poi_id",
    "competitor_hotel_name", "competitor_star", "competitor_score", "competitor_lowest_price",
    "competitor_distance_m", "competitor_circle_name", "vip_tag", "follow_status",
    "competitor_loss_order_count", "competitor_loss_order_ratio_pct", "competitor_loss_amount",
    "lost_room_types_text", "lost_room_types_json",
]


def report_window(now: datetime) -> tuple[date, date]:
    end_date = now.date() - timedelta(days=1 if now.hour >= 9 else 2)
    return end_date - timedelta(days=29), end_date


def query_loss_data(start_date: date, end_date: date) -> dict[str, Any]:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("美团 EB Cookie 缺失，请先在配置面板执行 Edge 登录")
    params = {
        "poiId": POI_ID,
        "partnerId": PARTNER_ID,
        "lossType": 0,
        "startDate": start_date.strftime("%Y%m%d"),
        "endDate": end_date.strftime("%Y%m%d"),
        "yodaReady": "h5",
        "csecplatform": "4",
        "csecversion": "4.2.4",
    }
    response = requests.get(
        API_URL,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*", "Referer": "https://eb.meituan.com/", "Cookie": MEITUAN_EB_COOKIE},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"美团流失订单接口返回异常：{payload.get('status')}")
    data = payload["data"]
    if not isinstance(data.get("orderLossPeerDetails"), list):
        raise RuntimeError("美团流失订单接口缺少竞争酒店明细列表")
    return data


def room_types(items: list[dict[str, Any]]) -> tuple[str, str]:
    rows = [item for item in items if isinstance(item, dict)]
    text = "; ".join(f"{item.get('lossRoomCnt', 0)}间{item.get('lossRoomName', '')}" for item in rows)
    return text, json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def build_rows(data: dict[str, Any], start_date: date, end_date: date, captured_at: datetime) -> list[list[object]]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    rows = []
    for item in data.get("orderLossPeerDetails") or []:
        if not isinstance(item, dict) or not item.get("poiId"):
            continue
        room_text, room_json = room_types(item.get("lossRoomList") or [])
        rows.append([
            hotel_id, HOTEL_NAME, end_date, start_date, end_date, captured_at,
            data.get("lossTotalCnt"), data.get("lossTotalPayRoomNight"), data.get("lossTotalPayAmount"),
            item.get("poiId"), item.get("poiName"), item.get("lossPoiStar"), item.get("score"),
            item.get("lowestPrice"), item.get("distance"), item.get("circleName"), int(bool(item.get("vipTag"))),
            item.get("followStatus"), item.get("lossOrderCount"), item.get("lossOrderRatio"),
            item.get("lossSinglePayAmount"), room_text, room_json,
        ])
    return rows


def write_output(start_date: date, end_date: date, data: dict[str, Any], rows: list[list[object]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "period_start_date": start_date.isoformat(), "period_end_date": end_date.isoformat(),
        "total_loss_order_count": data.get("lossTotalCnt"), "total_loss_room_nights": data.get("lossTotalPayRoomNight"),
        "total_loss_amount": data.get("lossTotalPayAmount"), "competitor_count": len(rows),
    }
    (OUTPUT_DIR / "meituan_ota_order_loss_monthly.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    captured_at = datetime.now()
    start_date, end_date = report_window(captured_at)
    data = query_loss_data(start_date, end_date)
    rows = build_rows(data, start_date, end_date, captured_at)
    write_output(start_date, end_date, data, rows)
    total_loss_count = data.get("lossTotalCnt")
    try:
        allow_empty = float(total_loss_count) == 0
    except (TypeError, ValueError):
        allow_empty = False
    sync_order_loss_snapshot(HEADERS, rows, allow_empty_replace=allow_empty)
    print(f"美团近30天流失订单采集完成：竞争酒店 {len(rows)} 家")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
