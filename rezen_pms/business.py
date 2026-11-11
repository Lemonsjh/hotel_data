"""Rezen 经营、预订和离店。只读已登录客户端，产出与美团报表相同的列。"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal

from rezen_pms.collector import _session
from rezen_pms.client import post_json


DAILY_PATH = "/hotel_api/hotel/stat/getComprehensiveAnalysisStatList"
FEE_PATH = "/hotel_api/hotel/cashier/getTrnTypDayRevn"
ACCOUNTS_PATH = "/hotel_api/hotel/rsvaccount/queryRsvAccountList"
CHUNK_DAYS = 30


def _post(path: str, payload: dict) -> dict:
    token, cookie = _session(os.environ.get("REZEN_SESSION", ""), os.environ.get("REZEN_PROFILE_DIR", "").strip())
    data = post_json(os.environ.get("REZEN_BASE_URL", ""), token, path, payload, timeout=40, cookie=cookie)
    body = data.get("body")
    if not isinstance(body, dict):
        raise RuntimeError("Rezen 接口没有返回数据，本轮不写入")
    return body


def _chunks(start: date, end: date):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=CHUNK_DAYS - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _day(value) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError("Rezen 营业日期无效，拒绝写入") from None


def _number(value, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValueError(f"Rezen {label}无效，拒绝写入") from None
    if not number.is_finite() or number < 0:
        raise ValueError(f"Rezen {label}无效，拒绝写入")
    return number


def _whole(value, label: str) -> int:
    number = _number(value, label)
    if number != number.to_integral_value():
        raise ValueError(f"Rezen {label}不是整数，拒绝写入")
    return int(number)


def fetch_daily(start: date, end: date) -> dict[date, dict]:
    """闭区间内每一天的酒店总营业指标。缺一天就拒绝，不补零。"""
    if end < start:
        raise ValueError("Rezen 经营日期无效，拒绝写入")
    found = {}
    for chunk_start, chunk_end in _chunks(start, end):
        body = _post(DAILY_PATH, {"beginDate": chunk_start.isoformat(), "endDate": chunk_end.isoformat(), "dataType": "D"})
        rows = body.get("businessAnalysisList")
        if not isinstance(rows, list):
            raise RuntimeError("Rezen 经营日报没有返回数据，本轮不写入")
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError("Rezen 经营日报无效，拒绝写入")
            day = _day(item.get("statDt"))
            if day in found or not chunk_start <= day <= chunk_end:
                raise ValueError("Rezen 经营日报日期重复或超出范围，拒绝写入")
            found[day] = {
                "room_count": _whole(item.get("totalRoomCount"), "总房"),
                "rentable": _whole(item.get("curRatePAR"), "可售房"),
                "nights": _number(item.get("nightNums"), "间夜"),
                "revenue": _number(item.get("roomIncome"), "房费"),
            }
            if found[day]["rentable"] > found[day]["room_count"]:
                raise ValueError("Rezen 可售房超过总房，拒绝写入")
    expected = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
    if set(found) != expected:
        raise ValueError("Rezen 经营日报日期不完整，本轮不写入")
    return found


def fetch_months(start: date, end: date) -> dict[str, dict]:
    """按月总营业指标。一次查到截止日期；截止日期落在已结束的月份时接口会回空。"""
    if end < start:
        return {}
    if (end.year - start.year) * 12 + end.month - start.month > 36:
        raise ValueError("Rezen 经营月报区间过长，拒绝写入")
    body = _post(DAILY_PATH, {"beginDate": start.isoformat(), "endDate": end.isoformat(), "dataType": "M"})
    rows = body.get("businessAnalysisList")
    if not isinstance(rows, list):
        raise RuntimeError("Rezen 经营月报没有返回数据，本轮不写入")
    found = {}
    first, last = start.strftime("%Y-%m"), end.strftime("%Y-%m")
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Rezen 经营月报无效，拒绝写入")
        month = str(item.get("statDt") or "")
        if len(month) != 7 or month[4] != "-":
            raise ValueError("Rezen 经营月报月份无效，拒绝写入")
        if month in found or not first <= month <= last:
            raise ValueError("Rezen 经营月报月份重复或超出范围，拒绝写入")
        room_count = _whole(item.get("totalRoomCount"), "总房日")
        rentable = _whole(item.get("curRatePAR"), "可售房日")
        if rentable > room_count:
            raise ValueError("Rezen 可售房日超过总房日，拒绝写入")
        found[month] = {
            "room_count": room_count,
            "maintain_rooms": room_count - rentable,
            "nights": _number(item.get("nightNums"), "间夜"),
            "revenue": _number(item.get("roomIncome"), "房费"),
        }
    return found


def _records(payload: dict) -> list[dict]:
    body = _post(ACCOUNTS_PATH, payload)
    listing = body.get("rsvAccountInfoList")
    records = listing.get("records") if isinstance(listing, dict) else None
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise RuntimeError("Rezen 账户列表无效，本轮不写入")
    total = listing.get("total")
    if total is not None and int(total) != len(records):
        raise RuntimeError("Rezen 账户列表不完整，本轮不写入")
    return records


def _fee_payload(start: date, end: date) -> list:
    body = _post(FEE_PATH, {
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "turnCds": [], "operCds": [], "trnTyp": ["100", "101", "105"],
    })
    rows = body.get("auditItemInfoList")
    if not isinstance(rows, list):
        raise RuntimeError("Rezen 房费明细没有返回数据，本轮不写入")
    return rows


def fetch_room_fees(day: date) -> list[dict]:
    """一天的夜审房费明细。金额能按房型加总，客人字段不进入结果。"""
    from rezen_pms.room_fee import normalize_lines

    return normalize_lines(_fee_payload(day, day), day)


def fetch_room_fee_lines(start: date, end: date) -> list[dict]:
    """一段营业日的夜审房费。客人字段不进入结果。"""
    from rezen_pms.room_fee import revenue_lines

    if end < start:
        raise ValueError("Rezen 房费日期无效，拒绝写入")
    return revenue_lines(_fee_payload(start, end), start, end)


def fetch_reservations(start: date, end: date) -> list[dict]:
    return _records({
        "page": False,
        "rsvAcctStusList": ["R"],
        "pseudoFlgList": ["NML"],
        "arrivalBeginDate": start.isoformat(),
        "arrivalEndDate": end.isoformat(),
    })


def fetch_departures(day: date) -> list[dict]:
    return _records({
        "page": False,
        "rsvAcctStusList": ["O"],
        "pseudoFlgList": ["NML"],
        "leaveBeginDate": day.isoformat(),
        "leaveEndDate": day.isoformat(),
    })


def account_stamp(value, day_value, time_value) -> datetime | None:
    """账户上的日期时间。解析失败时不把原文字放进异常。"""
    if value not in (None, ""):
        text = str(value).strip().replace("T", " ")
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text[:19], pattern)
            except ValueError:
                continue
    if day_value in (None, ""):
        return None
    clock = str(time_value or "00:00").strip()
    if len(clock) == 5:
        clock += ":00"
    try:
        return datetime.strptime(f"{str(day_value)[:10]} {clock}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
