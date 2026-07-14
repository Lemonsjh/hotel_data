#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 PMS 房类预测：未来房态、ADR 与 RevPar 快照。"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

import pms_utils


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT_DIR / "output" / "FORECAST.json"
BASE_URL = "https://xingfeng.beyondh.com:8111"
ROOM_TYPES_URL = f"{BASE_URL}/API/Room/GetRoomTypesForcasting"
FORECAST_URL = f"{BASE_URL}/API/Room/SearchBaseRoomForcasting"
PAGE_URL = "https://xingfeng.beyondh.com:8101/newFuture"


def api_session() -> requests.Session | None:
    session_data = pms_utils.load_session()
    if not session_data:
        return None
    session = requests.Session()
    session.cookies.update(session_data.get("cookies", {}))
    session.headers.update(
        {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Referer": PAGE_URL}
    )
    return session


def request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=30, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(f"接口状态码 {response.status_code}: {response.text[:160]}")
    data = response.json()
    if not isinstance(data, dict) or data.get("Code") != 0:
        raise RuntimeError(f"接口返回异常: {str(data)[:200]}")
    return data


def forecast_window(days: int) -> dict[str, str]:
    now = datetime.now()
    start = now.date()
    return {
        "beginDate": f"{start.isoformat()} 00:00:00",
        "beginHour": now.strftime("%H:00"),
        "endDate": f"{(start + timedelta(days=days - 1)).isoformat()} 00:00:00",
    }


def fetch_room_type_forecast(days: int = 10) -> bool:
    print("\n=== 抓取 PMS 房类预测快照 ===")
    if not 1 <= days <= 31:
        print("❌ days 必须在 1 到 31 之间")
        return False
    session = api_session()
    if session is None:
        return False
    try:
        room_types = request_json(session, "GET", ROOM_TYPES_URL).get("Content") or []
        window = forecast_window(days)
        snapshot_time = datetime.now().isoformat(timespec="microseconds")
        rows: list[dict[str, Any]] = []
        for index, room_type in enumerate(room_types, start=1):
            if not room_type.get("IsActive", True) or room_type.get("Virtual", False):
                continue
            name = str(room_type.get("RoomTypeName") or "").strip()
            source_id = str(room_type.get("Id") or "").strip()
            if not name or not source_id:
                continue
            payload = {**window, "roomType": room_type}
            data = request_json(session, "POST", FORECAST_URL, json=payload)
            content = data.get("Content") or []
            if content:
                rows.append(
                    {
                        "pms_room_type_id": source_id,
                        "room_type_name": name,
                        "total_rooms": content[0].get("TotalCount"),
                        "details": content[0].get("Details") or [],
                    }
                )
            print(f"📥 房类预测 [{index}/{len(room_types)}] {name}")
            time.sleep(0.2)
        if not rows:
            raise RuntimeError("房类预测未返回有效房型数据")
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps({"meta": {**window, "snapshot_time": snapshot_time}, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✅ 房类预测已保存：{len(rows)} 个房型，未来 {days} 天")
        return True
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"❌ 房类预测采集失败: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PMS 房类预测快照抓取")
    parser.add_argument("--days", type=int, default=10, help="未来天数，默认 10")
    args = parser.parse_args()
    raise SystemExit(0 if fetch_room_type_forecast(args.days) else 1)
