"""Rezen One 房态图与综合预测房型。只读已登录客户端，不另开进程。"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rezen_pms.client import fetch_room_chart, fetch_room_type_forecast
from rezen_pms.session import load_auth
from rezen_pms.storage import sync_mysql
from rezen_pms.transform import SOURCE, room_status_rows


def now_shanghai():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _session(token: str, profile: str) -> tuple[str, str]:
    text = token.strip()
    if text:
        if any(char in text for char in "\r\n") or len(text) < 8:
            raise ValueError("Rezen 会话无效")
        return text, ""
    return load_auth(Path(profile) if profile else None)


def _view_days(value: str) -> int:
    try:
        days = int(value or "15")
    except ValueError as exc:
        raise ValueError("Rezen 预测天数无效") from exc
    if not 1 <= days <= 90:
        raise ValueError("Rezen 预测天数无效")
    return days


def _tax(value: str) -> str:
    text = (value or "1").strip()
    if text not in {"0", "1"}:
        raise ValueError("Rezen 含税参数无效")
    return text


def collect(session_token: str, hotel_id: str, hotel_name: str, *, base_url: str = "",
            profile_dir: str = "", view_days: int = 15, include_tax: str = "1",
            clock=now_shanghai) -> list[dict]:
    if not hotel_id or not hotel_name:
        raise ValueError("请配置酒店 ID 和 Rezen 酒店名称")
    if not isinstance(view_days, int):
        raise ValueError("Rezen 预测天数无效")
    snapshot = clock()
    start = snapshot.date()
    token, cookie = _session(session_token, profile_dir)
    available, chart = fetch_room_chart(base_url, token, cookie=cookie)
    forecast = fetch_room_type_forecast(base_url, token, start.isoformat(), 1, include_tax, cookie=cookie)
    if clock().date() != start:
        raise ValueError("采集跨越日期边界，本轮不写入，请下一轮重新采集")
    return room_status_rows(available, chart, forecast, snapshot, hotel_id, hotel_name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rezen One 房态与综合预测采集")
    parser.add_argument("--dry-run", action="store_true", help="只查询并校验，不连接 MySQL")
    args = parser.parse_args(argv)
    try:
        rows = collect(
            os.environ.get("REZEN_SESSION", ""), os.environ.get("HOTEL_ID", "").strip(),
            os.environ.get("REZEN_HOTEL_NAME", "").strip(),
            base_url=os.environ.get("REZEN_BASE_URL", ""),
            profile_dir=os.environ.get("REZEN_PROFILE_DIR", "").strip(),
            view_days=_view_days(os.environ.get("REZEN_VIEW_DAYS", "")),
            include_tax=_tax(os.environ.get("REZEN_INCLUDE_TAX", "")))
        if not args.dry_run:
            sync_mysql(rows, rows, source=SOURCE, mapping_platform="pms_rezen")
        print(f"Rezen 房态{'校验' if args.dry_run else '入库'}完成：{len(rows)} 个房型")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
