"""把 Rezen 房态图和综合预测房型页转换成现有房态行。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation


SOURCE = "PMS（Rezen）"
OCCUPIED_STATUS = frozenset({"O", "OC", "OI", "OD", "OT"})
REPAIR_STATUS = frozenset({"OOO"})
LOCK_STATUS = frozenset({"OOS"})
DISABLED_STATUS = frozenset({"OOI"})
VACANT_STATUS = frozenset({"V", "VC", "VI", "VD", "VT"})
KNOWN_STATUS = OCCUPIED_STATUS | REPAIR_STATUS | LOCK_STATUS | DISABLED_STATUS | VACANT_STATUS
REVENUE_NAMES = frozenset({"房费"})


def _text(value) -> str:
    return str(value or "").strip()


def _decimal(value, field: str) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    if text in {"", "--", "-"}:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field} 无效") from exc
    if not number.is_finite():
        raise ValueError(f"{field} 无效")
    return number


def _count(value, field: str) -> int:
    number = _decimal(value, field)
    if number is None or number != number.to_integral_value() or number < 0:
        raise ValueError(f"{field} 无效")
    return int(number)


def _money(value, field: str) -> Decimal:
    number = _decimal(value, field)
    if number is None:
        raise ValueError(f"{field} 缺失")
    return number.quantize(Decimal("0.0001"))


def _cell(row: dict, index: int, field: str):
    if str(index) in row:
        return row[str(index)]
    if index in row:
        return row[index]
    raise ValueError(f"{field} 缺失")


def _day_value(row: dict, stay, field: str):
    data = row.get("data")
    if isinstance(data, dict):
        key = stay.isoformat()
        if key not in data:
            raise ValueError(f"{field} 缺失")
        return data[key]
    return _cell(row, 1, field)


def _metric_row(details: list, names: frozenset, field: str) -> dict:
    matches = [row for row in details if isinstance(row, dict) and _text(row.get("name")) in names]
    if len(matches) != 1:
        raise ValueError(f"{field} 缺失或重复")
    return matches[0]


def _room_name(item: dict, rooms: list[dict]) -> str:
    for key in ("roomTypeName", "desc", "roomTypeDrpt"):
        name = _text(item.get(key))
        if name:
            return name
    names = {_text(room.get("rmTypMessage")) for room in rooms}
    names.discard("")
    if len(names) == 1:
        return next(iter(names))
    if len(names) > 1:
        raise ValueError("房型名称不一致")
    return ""


def room_chart_rows(available: dict, chart: dict, snapshot: datetime, hotel_id: str, hotel_name: str) -> list[dict]:
    if not hotel_id or not hotel_name:
        raise ValueError("酒店 ID 或酒店名称缺失")
    items = available.get("items") if isinstance(available, dict) else None
    rooms = chart.get("roomInfoList") if isinstance(chart, dict) else None
    if not isinstance(items, list) or not items or not isinstance(rooms, list):
        raise ValueError("Rezen 房态响应无效")
    grouped: dict[str, list[dict]] = {}
    for room in rooms:
        if not isinstance(room, dict):
            raise ValueError("房态房间数据无效")
        code = _text(room.get("rmTyp") or room.get("roomTypeCd"))
        status = _text(room.get("roomStatus"))
        if not code or not status:
            raise ValueError("房态房间缺少房型或状态")
        grouped.setdefault(code, []).append(room)
    rows = []
    seen = set()
    stay = snapshot.date()
    hour = snapshot.replace(minute=0, second=0, microsecond=0)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("房型可用数无效")
        code = _text(item.get("roomTypeCd"))
        counts = item.get("items")
        if not code or code in seen or not isinstance(counts, dict):
            raise ValueError("房型编码缺失或重复")
        seen.add(code)
        own_rooms = grouped.get(code, [])
        name = _room_name(item, own_rooms)
        total = _count(counts.get("ROOM_DETAIL_PHYSICS"), "总房数")
        available_count = _count(counts.get("ROOM_DETAIL_AVAILABLE"), "可用房")
        in_house = repair = lock = disabled = vacant = 0
        for room in own_rooms:
            status = _text(room.get("roomStatus"))
            if status not in KNOWN_STATUS:
                raise ValueError("房态状态无法识别")
            in_house += status in OCCUPIED_STATUS
            repair += status in REPAIR_STATUS
            lock += status in LOCK_STATUS
            disabled += status in DISABLED_STATUS
            vacant += status in VACANT_STATUS
        if total == 0 and not own_rooms:
            continue
        if not name:
            raise ValueError("房型名称缺失")
        if in_house > total:
            raise ValueError("房态数量超出总房数")
        # 可售是接口里还能卖的数量。空房多于可售的部分是已售未到，不是还能卖的房。
        not_arrived = max(0, vacant - available_count)
        sold = in_house + not_arrived
        overbooking = available_count + sold + repair + lock + disabled - total
        if overbooking < 0:
            raise ValueError("超售无效")
        rows.append({
            "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
            "snapshot_time": snapshot, "snapshot_hour": hour, "stay_date": stay,
            "room_type_name": name, "room_type_id": None, "pms_room_type_id": code,
            "total_rooms": total, "available_rooms": available_count,
            "occupied_rooms": sold, "in_house_rooms": in_house,
            "overbooking_rooms": overbooking,
            "room_revenue": None, "adr": None, "revpar": None,
        })
    unknown = set(grouped) - seen
    if unknown or not rows:
        raise ValueError("房态图房型与可用房列表不一致")
    return rows


def _ratio(revenue: Decimal, rooms: int) -> Decimal | None:
    if rooms == 0:
        return None
    return (revenue / Decimal(rooms)).quantize(Decimal("0.0001"))


def today_revenue(payload: dict, stay) -> dict[str, tuple[str, Decimal]]:
    body = payload.get("body") if isinstance(payload, dict) else None
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("Rezen 房费响应无效")
    found = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("房费房型无效")
        code = _text(item.get("salesForecastCd"))
        name = _text(item.get("salesForecastName"))
        if code == "SUMMARY":
            continue
        details = item.get("detailRows")
        if not code or not name or code in found or not isinstance(details, list):
            raise ValueError("房费房型名称或编码缺失、重复")
        revenue = _money(_day_value(_metric_row(details, REVENUE_NAMES, "房费"), stay, "房费"), "房费")
        found[code] = (name, revenue)
    if not found:
        raise ValueError("Rezen 房费没有房型")
    return found


def room_status_rows(available: dict, chart: dict, forecast: dict, snapshot: datetime,
                     hotel_id: str, hotel_name: str) -> list[dict]:
    rows = room_chart_rows(available, chart, snapshot, hotel_id, hotel_name)
    revenues = today_revenue(forecast, snapshot.date())
    if {row["pms_room_type_id"] for row in rows} != set(revenues):
        raise ValueError("房费房型与房态图不一致")
    for row in rows:
        name, revenue = revenues[row["pms_room_type_id"]]
        if name != row["room_type_name"]:
            raise ValueError("房型名称不一致")
        row["room_revenue"] = revenue
        row["adr"] = _ratio(revenue, row["in_house_rooms"])
        row["revpar"] = _ratio(revenue, row["total_rooms"])
    return rows
