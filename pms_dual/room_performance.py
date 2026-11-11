"""美团 PMS 与 Rezen 的 JL01。列与 jl01_room_type_performance_daily 的单系统报表一致。"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

import requests

from meituan_pms.classification_report import REPORT_URL, transform_period, validate_context, value
from meituan_pms.collector import BASE_URL, request_json
from meituan_pms.room_performance_report import collection_dates, request_body
from pms_dual.business import JL01_COLUMNS, SOURCE
from rezen_pms.room_fee import nights_by_room_type, revenue_by_room_type


FOUR = Decimal("0.0001")
CENT = Decimal("0.01")


def _signed(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("汇总数值无效，拒绝写入")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("汇总数值无效，拒绝写入")
    return number


def _whole(value, label: str) -> int:
    number = _signed(value)
    if number != number.to_integral_value() or number < 0:
        raise ValueError(f"{label}不是整数，拒绝写入")
    return int(number)


def _q4(value) -> Decimal:
    return _signed(value).quantize(FOUR, rounding=ROUND_HALF_UP)


def _money(value) -> Decimal:
    return _signed(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _rates(nights, revenue, available, total) -> dict[str, Decimal]:
    nights, revenue = _signed(nights), _signed(revenue)
    available, total = _signed(available), _signed(total)
    if nights > 0 and available <= 0:
        raise ValueError("有间夜但没有可售房，拒绝写入")
    return {
        "occupancy_rate": _q4(nights * 100 / available) if available else Decimal("0.0000"),
        "adr": _q4(revenue / nights) if nights else Decimal("0.0000"),
        "revpar": _q4(revenue / total) if total else Decimal("0.0000"),
    }


def rentable_rooms(nights, occupancy, total) -> int:
    """由美团出租率还原可售房。还原结果离整数超过 0.05 间时拒绝。"""
    sold = _signed(nights)
    rate = _signed(occupancy)
    rooms = _whole(total, "总房")
    if sold == 0:
        if rate != 0:
            raise ValueError("出租率与间夜不一致，拒绝写入")
        return rooms
    if rate <= 0:
        raise ValueError("有间夜但出租率为 0，拒绝写入")
    exact = sold * 100 / rate
    nearest = exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if abs(exact - nearest) > Decimal("0.05") or nearest < 0:
        raise ValueError("出租率无法还原可售房，拒绝写入")
    return int(nearest)


def _component(day, name, code, nights, revenue, total, available, platform, rates) -> dict:
    return {
        "business_date": day, "room_type_name": name, "pms_room_type_id": code, "platform": platform,
        "room_nights": _signed(nights), "room_revenue": _money(revenue),
        "total_rooms": _whole(total, "总房"), "available_rooms": _whole(available, "可售房"),
        "occupancy_rate": _q4(rates["occupancy_rate"]), "adr": _q4(rates["adr"]), "revpar": _q4(rates["revpar"]),
    }


def meituan_rows(response, org_id: str, day: date, hotel_id: str, hotel_name: str, *, allow_empty: bool) -> list[dict]:
    envelope = response.get("data") if isinstance(response, dict) else None
    data = envelope.get("data") if isinstance(envelope, dict) else None
    items = data.get("summaryList") if isinstance(data, dict) else None
    if (allow_empty and isinstance(response, dict) and response.get("code") == 10000
            and isinstance(items, list) and not items):
        validate_context(envelope.get("variables"), None, org_id, day, day)
        return []
    try:
        details = transform_period(response, org_id, day, day, datetime.now(), hotel_id, hotel_name)
    except ValueError as exc:
        raise ValueError(str(exc).replace("JL11", "JL01")) from None
    raw = {}
    for item in items or []:
        if not isinstance(item, dict):
            raise ValueError("JL01 房型统计行无效")
        name = str(item.get("roomTypeName") or "").strip()
        if name and name != "总计":
            raw[name] = item
    rows = []
    seen = set()
    for detail in details:
        name = detail["room_type_name"]
        item = raw.get(name)
        if item is None or name in seen:
            raise ValueError("JL01 房型名称缺失或重复，拒绝写入")
        seen.add(name)
        total = _whole(value(item, "roomCount"), "总房")
        available = rentable_rooms(detail["room_nights"], detail["occupancy_rate"], total)
        code = str(item.get("roomTypeKey") or "").strip()
        rates = {
            "occupancy_rate": detail["occupancy_rate"],
            "adr": detail["average_room_price"],
            "revpar": detail["revpar"],
        }
        rows.append(_component(day, name, code, detail["room_nights"], detail["room_revenue"], total, available,
                               "pms_meituan", rates))
    return rows


def build_rezen_rows(lines: list[dict], inventory: list[dict], stats: dict, day: date,
                     hotel_id: str, hotel_name: str) -> list[dict]:
    """房型总房来自当天房态图。合计必须等于这一天经营日报的总房，间夜和房费也必须对上。"""
    if not inventory:
        raise ValueError("Rezen 房态没有房型，本轮不写入")
    codes = {}
    by_name: dict[str, dict] = {}
    for item in inventory:
        name = str(item.get("room_type_name") or "").strip()
        code = str(item.get("room_type_code") or "").strip()
        total = _whole(item.get("total_rooms"), "总房")
        if not name or not code or total < 1 or code in codes:
            raise ValueError("Rezen 房态房型无效或重复，拒绝写入")
        codes[code] = name
        current = by_name.setdefault(name, {"total_rooms": 0})
        current["total_rooms"] += total
    for line in lines:
        chart_name = codes.get(line["room_type_code"])
        if chart_name is None or chart_name != line["room_type_name"]:
            raise ValueError("Rezen 房费房型不在当天房态图中，拒绝写入")
    nights = nights_by_room_type(lines, day)
    revenue = revenue_by_room_type(lines)
    if set(nights) - set(by_name) or set(revenue) - set(by_name):
        raise ValueError("Rezen 房费房型不在当天房态图中，拒绝写入")
    if sum(item["total_rooms"] for item in by_name.values()) != _whole(stats["room_count"], "总房"):
        raise ValueError("Rezen 房型总房与经营日报不一致，本轮不写入")
    night_total = sum(nights.values(), Decimal(0))
    if night_total != _signed(stats["nights"]):
        raise ValueError("Rezen 房型间夜与经营日报不一致，本轮不写入")
    fee_total = sum(revenue.values(), Decimal(0))
    if _money(fee_total) != _money(stats["revenue"]):
        raise ValueError("Rezen 房型房费与经营日报不一致，本轮不写入")
    rows = []
    for name, item in sorted(by_name.items()):
        sold = nights.get(name, Decimal(0))
        amount = revenue.get(name, Decimal(0))
        total = item["total_rooms"]
        rates = _rates(sold, amount, total, total)
        rows.append(_component(day, name, "", sold, amount, total, total, "pms_rezen", rates))
    return rows


def _output(row: dict, hotel_id: str, hotel_name: str, snapshot: datetime, room_type_id) -> dict:
    rates = _rates(row["room_nights"], row["room_revenue"], row["available_rooms"], row["total_rooms"])
    item = {
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "business_date": row["business_date"], "room_type_name": row["room_type_name"],
        "pms_rate_room_type_id": "", "room_nights": _q4(row["room_nights"]),
        "occupancy_rate": rates["occupancy_rate"], "room_revenue": _money(row["room_revenue"]),
        "adr": rates["adr"], "revpar": rates["revpar"], "snapshot_time": snapshot,
        "room_type_id": room_type_id,
    }
    if set(item) != set(JL01_COLUMNS):
        raise ValueError("写入列与现有报表不一致，拒绝写入")
    return {key: item[key] for key in JL01_COLUMNS}


def _kept(row: dict, hotel_id: str, hotel_name: str, snapshot: datetime, room_type_id, label: str) -> dict:
    item = {
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "business_date": row["business_date"], "room_type_name": label, "pms_rate_room_type_id": "",
        "room_nights": _q4(row["room_nights"]), "occupancy_rate": _q4(row["occupancy_rate"]),
        "room_revenue": _money(row["room_revenue"]), "adr": _q4(row["adr"]), "revpar": _q4(row["revpar"]),
        "snapshot_time": snapshot, "room_type_id": room_type_id,
    }
    return {key: item[key] for key in JL01_COLUMNS}


def merge_jl01(left: list[dict], right: list[dict], hotel_id: str, hotel_name: str, snapshot: datetime,
               aliases: dict) -> list[dict]:
    if any(row.get("business_date") is None or not str(row.get("room_type_name") or "").strip() for row in (*left, *right)):
        raise ValueError("房型业绩缺少日期或房型，拒绝写入")
    yesterday = collection_dates(snapshot.date())[0]
    left_days = {row["business_date"] for row in left}
    right_days = {row["business_date"] for row in right}
    if yesterday not in left_days or yesterday not in right_days:
        raise ValueError("昨天的房型业绩有一侧没有数据，本轮不写入")
    shared = left_days & right_days
    omitted = sorted((left_days | right_days) - shared)
    if omitted:
        print("INFO [jl01] 以下营业日不是两侧都有，本轮不写入：" + "、".join(day.isoformat() for day in omitted))
    merged = []
    for day in sorted(shared):
        day_rows = []
        names_seen = set()
        for row in [item for item in (*left, *right) if item["business_date"] == day]:
            name = str(row["room_type_name"]).strip()
            marker = (row["platform"], name)
            if marker in names_seen:
                raise ValueError("房型名称重复，拒绝写入")
            names_seen.add(marker)
            day_rows.append({**row, "room_type_name": name})
        by_name: dict[str, list[dict]] = {}
        for row in day_rows:
            by_name.setdefault(row["room_type_name"], []).append(row)
        name_key = {}
        for name, group in by_name.items():
            mapped = {aliases.get((row["platform"], name)) for row in group}
            mapped.discard(None)
            if len(mapped) > 1:
                raise ValueError(f"房型「{name}」映到了不同的统一房型，拒绝写入")
            name_key[name] = ("id", next(iter(mapped))) if mapped else ("name", name)
        grouped: dict[tuple, list[dict]] = {}
        for name, group in by_name.items():
            grouped.setdefault(name_key[name], []).extend(group)
        used = set()
        for key in sorted(grouped, key=lambda item: (item[0], item[1])):
            rows = grouped[key]
            label = " / ".join(sorted({row["room_type_name"] for row in rows}))
            if len(label) > 255:
                raise ValueError("合并后的房型名称过长，拒绝写入")
            room_type_id = key[1] if key[0] == "id" else None
            if label in used:
                raise ValueError("合并后的房型主键冲突，拒绝写入")
            used.add(label)
            if len(rows) == 1:
                merged.append(_kept(rows[0], hotel_id, hotel_name, snapshot, room_type_id, label))
                continue
            combined = {
                "business_date": day, "room_type_name": label,
                "room_nights": sum((_signed(row["room_nights"]) for row in rows), Decimal(0)),
                "room_revenue": sum((_signed(row["room_revenue"]) for row in rows), Decimal(0)),
                "available_rooms": sum(_whole(row["available_rooms"], "可售房") for row in rows),
                "total_rooms": sum(_whole(row["total_rooms"], "总房") for row in rows),
            }
            merged.append(_output(combined, hotel_id, hotel_name, snapshot, room_type_id))
    return merged




def collect_meituan(cookie: str, hotel_id: str, hotel_name: str, org_id: str, snapshot: datetime,
                    clock=None, wait=time.sleep) -> list[dict]:
    if not cookie.strip() or "\r" in cookie or "\n" in cookie:
        raise ValueError("请先手动登录美团 PMS")
    dates = collection_dates(snapshot.date())
    owns = True
    session = requests.Session()
    session.headers.update({"Cookie": cookie, "Accept": "application/json", "Origin": BASE_URL,
                            "Referer": BASE_URL + "/pms-report/home/report/"})
    clock = clock or (lambda: snapshot)
    try:
        rows = []
        for index, day in enumerate(dates):
            response = request_json(session, "POST", REPORT_URL, json=request_body(org_id, day))
            if clock().date() != snapshot.date():
                raise ValueError("JL01 采集跨日，本轮不写入")
            rows.extend(meituan_rows(response, org_id, day, hotel_id, hotel_name, allow_empty=day != dates[0]))
            if index + 1 < len(dates):
                wait(1)
        return rows
    finally:
        if owns:
            session.close()


def current_inventory(snapshot: datetime, hotel_id: str, hotel_name: str) -> list[dict]:
    from rezen_pms.client import fetch_room_chart
    from rezen_pms.collector import _session
    from rezen_pms.transform import room_chart_rows

    token, cookie = _session(os.environ.get("REZEN_SESSION", ""), os.environ.get("REZEN_PROFILE_DIR", "").strip())
    available, chart = fetch_room_chart(os.environ.get("REZEN_BASE_URL", ""), token, cookie=cookie)
    rows = room_chart_rows(available, chart, snapshot, hotel_id, hotel_name)
    return [{"room_type_name": row["room_type_name"], "room_type_code": row["pms_room_type_id"],
             "total_rooms": row["total_rooms"]} for row in rows]


def load_rezen_day(day: date, inventory: list[dict], hotel_id: str, hotel_name: str, *, required: bool) -> list[dict]:
    from rezen_pms.business import fetch_daily, fetch_room_fees

    try:
        stats = fetch_daily(day, day)[day]
        return build_rezen_rows(fetch_room_fees(day), inventory, stats, day, hotel_id, hotel_name)
    except (ValueError, RuntimeError) as exc:
        if required:
            raise
        print(f"INFO [jl01] {day.isoformat()} 的 Rezen 房型业绩不能并入：{exc}")
        return []


def collect_rezen(hotel_id: str, hotel_name: str, snapshot: datetime) -> list[dict]:
    from rezen_pms.business import fetch_months

    dates = collection_dates(snapshot.date())
    inventory = current_inventory(snapshot, hotel_id, hotel_name)
    rows = load_rezen_day(dates[0], inventory, hotel_id, hotel_name, required=True)
    last = dates[1]
    if last != dates[0]:
        months = fetch_months(last.replace(day=1), snapshot.date())
        item = months.get(last.strftime("%Y-%m"))
        if item is None:
            raise ValueError("Rezen 去年同月没有返回，本轮不写入")
        if any(_signed(item[key]) != 0 for key in ("room_count", "maintain_rooms", "nights", "revenue")):
            rows.extend(load_rezen_day(last, inventory, hotel_id, hotel_name, required=False))
    return rows
