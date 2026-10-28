"""美团 PMS 独立采集：当前日期房态，不复用其他 PMS 会话。"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from room_status import parse_room_types, transform
from storage import sync_mysql

BASE_URL = "https://pms.meituan.com"
ROOM_TYPES_URL = BASE_URL + "/hotelpms/api/v1/inventory/rooms/getRealTimeRoomCount"
FORECAST_URL = BASE_URL + "/hotelpms/api/v2/report/roomState/batchSearchBaseRoomForcasting"


def now_shanghai():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def request_json(session, method, url, **kwargs):
    try:
        response = session.request(method, url, timeout=30, allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise ValueError(f"美团 PMS 接口 HTTP {response.status_code}，请检查登录状态")
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        # 不输出原始异常、响应或请求头，避免会话内容进入日志。
        if isinstance(exc, requests.RequestException):
            raise RuntimeError("美团 PMS 请求失败，请检查网络和登录状态") from None
        raise RuntimeError("美团 PMS 接口未返回有效成功响应，请检查登录状态") from None
    if not isinstance(data, dict) or data.get("code") != 10000:
        raise RuntimeError("美团 PMS 接口业务状态异常，请检查登录状态")
    return data


def collect(cookie, hotel_id, hotel_name, *, session=None, clock=now_shanghai):
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和美团 PMS 酒店名称")
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请配置有效的美团 PMS Cookie")
    snapshot = clock()
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json",
                            "Referer": BASE_URL + "/", "Origin": BASE_URL})
    try:
        room_types = parse_room_types(request_json(session, "GET", ROOM_TYPES_URL))
        today = snapshot.strftime("%Y-%m-%d 00:00:00")
        body = {"beginDate": today, "endDate": today, "beginHour": snapshot.strftime("%H:00"),
                "channelKey": "Hotel",
                "roomTypes": [{**item, "description": None} for item in room_types]}
        data = request_json(session, "POST", FORECAST_URL, json=body)
        if clock().date() != snapshot.date():
            raise ValueError("采集跨越日期边界，本轮不写入，请下一轮重新采集")
        return transform(room_types, data, snapshot, hotel_id, hotel_name)
    finally:
        if own_session:
            session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="美团 PMS 当日实时房态采集")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接 MySQL")
    args = parser.parse_args(argv)
    try:
        rows = collect(os.environ.get("MEITUAN_PMS_COOKIE", ""), os.environ.get("HOTEL_ID", "").strip(),
                       os.environ.get("MEITUAN_PMS_HOTEL_NAME", "").strip())
        if not args.dry_run:
            sync_mysql(rows)
        print(f"美团 PMS 当日房态{'校验' if args.dry_run else '入库'}完成：{len(rows)} 个房型")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团 PMS 当日房态执行失败，本轮未完成入库；请检查数据库配置")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
