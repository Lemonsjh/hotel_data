"""两侧房态在内存里合并。同名房型相加，不同名且映到同一统一房型时也相加。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


COUNT_FIELDS = ("total_rooms", "available_rooms", "occupied_rooms", "overbooking_rooms")
MONEY_FIELDS = ("room_revenue",)
SOURCE = "PMS（美团+Rezen）"


def _day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError("入住日期无效，拒绝写入")


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("房态数值无效，拒绝写入")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("房态数值无效，拒绝写入")
    return number


def _combine(rows: list[dict], field: str):
    values = [row.get(field) for row in rows]
    if any(value is None for value in values):
        return None
    total = sum((_decimal(value) for value in values), Decimal(0))
    if field in COUNT_FIELDS:
        if total != total.to_integral_value() or total < 0:
            raise ValueError("房数无效，拒绝写入")
        return int(total)
    return total.quantize(Decimal("0.0001"))


def _ratio(revenue, rooms):
    if revenue is None or not rooms:
        return None
    return (_decimal(revenue) / Decimal(rooms)).quantize(Decimal("0.0001"))


def _in_house(rows: list[dict]):
    total = 0
    for row in rows:
        value = row.get("in_house_rooms")
        if value is None:
            value = row.get("occupied_rooms")
        if value is None:
            return None
        number = _decimal(value)
        if number != number.to_integral_value() or number < 0:
            raise ValueError("房数无效，拒绝写入")
        total += int(number)
    return total


def aliases_from_rows(rows) -> dict[tuple[str, str], str]:
    grouped: dict[tuple[str, str], set] = {}
    for platform, name, room_id in rows:
        grouped.setdefault((str(platform), str(name)), set()).add(str(room_id))
    result = {}
    for key, ids in grouped.items():
        if len(ids) != 1 or not key[1] or not next(iter(ids)):
            raise ValueError("同一 PMS 房型名称对应多个统一房型，拒绝写入")
        result[key] = next(iter(ids))
    return result


def merge_rows(left: list[dict], right: list[dict], aliases: dict, *, hotel_id: str, hotel_name: str,
               snapshot: datetime, left_platform: str, right_platform: str,
               left_prefix: str = "mt", right_prefix: str = "rz") -> list[dict]:
    if not left or not right:
        raise ValueError("有一侧没有房态，本轮不写入")
    sides = ((left_prefix, left_platform, left), (right_prefix, right_platform, right))
    by_day: dict[date, list[tuple[str, str, dict]]] = {}
    for prefix, platform, rows in sides:
        seen_names, seen_ids = set(), set()
        for row in rows:
            if row.get("hotel_id") != hotel_id:
                raise ValueError("两侧酒店不一致，拒绝写入")
            day = _day(row.get("stay_date"))
            name = str(row.get("room_type_name") or "").strip()
            raw_id = str(row.get("pms_room_type_id") or "").strip()
            if not name or not raw_id or (prefix, day, name) in seen_names or (prefix, day, raw_id) in seen_ids:
                raise ValueError("房型名称或原始 ID 缺失、重复，拒绝写入")
            seen_names.add((prefix, day, name))
            seen_ids.add((prefix, day, raw_id))
            by_day.setdefault(day, []).append((prefix, platform, {**row, "stay_date": day, "room_type_name": name}))
    shared = {day for day, items in by_day.items()
              if {prefix for prefix, _platform, _row in items} == {left_prefix, right_prefix}}
    if not shared:
        raise ValueError("两侧没有同一天的房态，本轮不写入")
    hour = snapshot.replace(minute=0, second=0, microsecond=0)
    output = []
    for day in sorted(shared):
        items = by_day[day]
        by_name: dict[str, list] = {}
        for item in items:
            by_name.setdefault(item[2]["room_type_name"], []).append(item)
        name_key = {}
        for name, group in by_name.items():
            mapped = {aliases.get((platform, name)) for _prefix, platform, _row in group}
            mapped.discard(None)
            if len(mapped) > 1:
                raise ValueError(f"房型「{name}」映到了不同的统一房型，拒绝写入")
            name_key[name] = ("id", next(iter(mapped))) if mapped else ("name", name)
        buckets: dict[tuple, list] = {}
        for name, group in by_name.items():
            buckets.setdefault(name_key[name], []).extend(group)
        stored_ids = set()
        for key in sorted(buckets, key=lambda item: (item[0], item[1])):
            group = buckets[key]
            if key[0] == "id":
                stored, room_type_id = key[1], key[1]
            elif len(group) == 1:
                prefix, _platform, row = group[0]
                stored, room_type_id = f"{prefix}:{row['pms_room_type_id']}", None
            else:
                stored, room_type_id = key[1], None
                if len(stored) > 64:
                    raise ValueError("合并后的房型主键过长，拒绝写入")
            if stored in stored_ids:
                raise ValueError("合并后的房型主键冲突，拒绝写入")
            stored_ids.add(stored)
            rows = [row for _prefix, _platform, row in group]
            labels = sorted({row["room_type_name"] for row in rows})
            label = " / ".join(labels)
            if len(label) > 255:
                raise ValueError("合并后的房型名称过长，拒绝写入")
            revenue = _combine(rows, "room_revenue")
            total = _combine(rows, "total_rooms")
            output.append({
                "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
                "snapshot_time": snapshot, "snapshot_hour": hour, "stay_date": day,
                "room_type_name": label, "room_type_id": room_type_id,
                "pms_room_type_id": stored,
                "total_rooms": total, "available_rooms": _combine(rows, "available_rooms"),
                "occupied_rooms": _combine(rows, "occupied_rooms"),
                "overbooking_rooms": _combine(rows, "overbooking_rooms"),
                "room_revenue": revenue, "adr": _ratio(revenue, _in_house(rows)),
                "revpar": _ratio(revenue, total),
            })
    return output
