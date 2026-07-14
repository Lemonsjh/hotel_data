from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from meituan_config import HOTEL_NAME, MEITUAN_EB_COOKIE, PARTNER_ID, POI_ID, USER_AGENT

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ota_mysql_writer import OUTPUT_DIR, sync_joined_rights_snapshot


API_URL = "https://eb.meituan.com/api/v1/ebooking/rights/eb/centerV2"
HEADERS = [
    "hotel_id", "hotel_name", "right_id", "right_name", "rights_code", "rights_content", "confirm_mode",
    "effective_room_scope", "today_stock", "activity_names", "snapshot_time",
]


def request_rights() -> dict[str, Any]:
    if not MEITUAN_EB_COOKIE:
        raise RuntimeError("美团 EB Cookie 缺失，请先在配置面板执行 Edge 登录")
    response = requests.get(
        API_URL,
        params={"poiId": POI_ID, "partnerId": PARTNER_ID, "yodaReady": "h5", "csecplatform": "4", "csecversion": "4.2.4"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*", "Referer": "https://eb.meituan.com/", "Cookie": MEITUAN_EB_COOKIE},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"美团已报名权益接口返回异常：{payload.get('status')}")
    return payload["data"]


def confirm_mode(value: Any) -> str:
    return {0: "无需确认", 1: "接单后自动确认"}.get(value, str(value or ""))


def stock_text(stock_list: list[dict[str, Any]]) -> str:
    values = []
    for stock in stock_list:
        if stock.get("stockType") == "UNLIMITED":
            values.append("不限")
        elif stock.get("stockRemain") is not None:
            values.append(str(stock["stockRemain"]))
    return "; ".join(values)


def room_scope(item: dict[str, Any], stock_list: list[dict[str, Any]]) -> str:
    if item.get("goodsRule") == "ALL":
        return "全部房型生效"
    names = [str(stock.get("stockDimensionDesc") or "").strip() for stock in stock_list]
    return "; ".join(name for name in names if name) or "部分房型生效"


def rights_content(item: dict[str, Any], activities: list[dict[str, Any]]) -> str:
    card = item.get("cardInfo") or {}
    content = card.get("rightsContent") or item.get("desc")
    if content:
        return str(content).strip()
    values = []
    for activity in activities:
        activity_card = activity.get("cardInfo") or {}
        value = activity_card.get("contentProvided") or activity_card.get("content")
        if value:
            values.append(str(value).strip())
    return "; ".join(dict.fromkeys(values))


def build_rows(data: dict[str, Any], captured_at: datetime) -> list[list[object]]:
    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    rows = []
    for item in data.get("joinedRightsList") or []:
        if not isinstance(item, dict) or not item.get("rightId"):
            continue
        stock = item.get("stockRemainToday") or {}
        stock_list = item.get("stockRemainTodayList") or ([stock] if stock else [])
        activities = [activity for activity in item.get("activityList") or [] if isinstance(activity, dict)]
        rows.append([
            hotel_id, HOTEL_NAME, item.get("rightId"), item.get("rightName"), item.get("rightsCode"),
            rights_content(item, activities), confirm_mode(item.get("rightsConfirmType")),
            room_scope(item, stock_list), stock_text(stock_list),
            "; ".join(str(activity.get("activityName") or "").strip() for activity in activities),
            captured_at,
        ])
    return rows


def write_output(rows: list[list[object]], captured_at: datetime) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"snapshot_time": captured_at.strftime("%Y-%m-%d %H:%M:%S"), "joined_rights_count": len(rows)}
    (OUTPUT_DIR / "meituan_ota_joined_rights.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    captured_at = datetime.now()
    rows = build_rows(request_rights(), captured_at)
    write_output(rows, captured_at)
    sync_joined_rights_snapshot(HEADERS, rows)
    print(f"美团已报名权益采集完成：权益 {len(rows)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
