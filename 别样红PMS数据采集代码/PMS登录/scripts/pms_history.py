from __future__ import annotations

import copy
import json
import os
import time
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pymysql


TABLES = {"jy01_hotel_statistics_daily", "rs01_room_revenue_daily"}


def previous_months(months: int = 6, today: date | None = None) -> list[tuple[str, str, str]]:
    today = today or date.today()
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    end_index = year * 12 + month - 1
    result = []
    for index in range(end_index - months + 1, end_index + 1):
        y, zero_month = divmod(index, 12)
        m = zero_month + 1
        result.append(
            (f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}")
        )
    return result


def incremental_window(report: str, today: date | None = None) -> tuple[str, str, str]:
    latest = (today or date.today()) - timedelta(days=1)
    if report == "JY01":
        return ("最近7天", (latest - timedelta(days=6)).isoformat(), latest.isoformat())
    start = latest.replace(day=1).isoformat()
    return ("本月至昨日", start, latest.isoformat())


def load_settings() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "OTA采集服务" / "config" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def mysql_config(settings: dict[str, Any]) -> dict[str, Any]:
    config = settings.get("mysql") or {}
    return {
        "host": os.environ.get("HOTEL_OTA_MYSQL_HOST") or config.get("host"),
        "port": int(os.environ.get("HOTEL_OTA_MYSQL_PORT") or config.get("port") or 3306),
        "user": os.environ.get("HOTEL_OTA_MYSQL_USER") or config.get("user"),
        "password": os.environ.get("HOTEL_OTA_MYSQL_PASSWORD") or config.get("password"),
        "database": os.environ.get("HOTEL_OTA_MYSQL_DATABASE") or config.get("database"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 12,
        "read_timeout": 20,
        "write_timeout": 20,
    }


def query_plan(table: str, report: str, months: int = 6) -> tuple[bool, list[tuple[str, str, str]]]:
    if table not in TABLES:
        raise ValueError(f"不支持的历史表: {table}")
    settings = load_settings()
    hotel_id = os.environ.get("HOTEL_ID") or str((settings.get("hotel") or {}).get("hotel_id") or "")
    if not hotel_id:
        raise RuntimeError("未配置hotel_id，无法判断历史采集状态")
    windows = previous_months(months)
    covered = None
    for attempt in range(2):
        try:
            with pymysql.connect(**mysql_config(settings)) as conn, conn.cursor() as cursor:
                room_filter = " AND dimension_type=%s" if report == "JY01" else ""
                params: tuple[Any, ...] = (hotel_id, windows[0][1], windows[-1][2])
                if room_filter:
                    params += ("房型",)
                cursor.execute(
                    f"""SELECT COUNT(DISTINCT DATE_FORMAT(business_date, '%%Y-%%m')) AS month_count
                        FROM {table}
                        WHERE hotel_id=%s AND business_date BETWEEN %s AND %s{room_filter}""",
                    params,
                )
                covered = int(cursor.fetchone()["month_count"] or 0)
            break
        except pymysql.MySQLError as exc:
            if attempt == 0:
                print(f"{report} 数据库连接失败，1秒后重试: {exc}")
                time.sleep(1)
            else:
                print(f"{report} 暂时无法检查历史覆盖，安全退回增量采集: {exc}")
    if covered is None:
        return False, [incremental_window(report)]
    if covered < months:
        print(f"{report} 历史覆盖 {covered}/{months} 个月，本轮补采前{months}个完整月")
        if report == "JY01":
            latest = date.today() - timedelta(days=1)
            month_start = latest.replace(day=1)
            if month_start <= latest:
                windows.append(("本月至昨日", month_start.isoformat(), latest.isoformat()))
        return True, windows
    window = incremental_window(report)
    print(f"{report} 历史已完整，本轮增量采集: {window[1]} ~ {window[2]}")
    return False, [window]


def merge_rs01(responses: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    merged = copy.deepcopy(responses[0])
    rows = []
    for response in responses:
        rows.extend(response.get("data", {}).get("dataList", []))
    merged.setdefault("data", {})["dataList"] = rows
    merged["data"].setdefault("variables", {}).update(startDate=start, endDate=end)
    return merged


def merge_jy01(responses: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    merged = copy.deepcopy(responses[0])
    target = merged.setdefault("data", {}).setdefault("data", {})
    keys = {
        key
        for response in responses
        for key, value in response.get("data", {}).get("data", {}).items()
        if isinstance(value, list)
    }
    for key in keys:
        target[key] = [
            row
            for response in responses
            for row in response.get("data", {}).get("data", {}).get(key, [])
        ]
    merged["data"].setdefault("variables", {}).update(startDate=start, endDate=end)
    return merged
