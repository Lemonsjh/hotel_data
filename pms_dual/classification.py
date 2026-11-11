"""美团 PMS 与 Rezen 的 JL11。没有房型房日的行不填出租率和 RevPAR。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from meituan_pms.classification_report import collect as collect_classification
from pms_dual.business import JL11_COLUMNS, SOURCE
from rezen_pms.room_fee import nights_by_room_type, revenue_by_room_type


FOUR = Decimal("0.0001")
CENT = Decimal("0.01")
BLANK_RATES = ("room_count", "occupancy_rate", "revpar", "overnight_room_count", "overnight_occupancy_rate")


def _signed(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("汇总数值无效，拒绝写入")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("汇总数值无效，拒绝写入")
    return number


def _money(value) -> Decimal:
    return _signed(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _q4(value) -> Decimal:
    return _signed(value).quantize(FOUR, rounding=ROUND_HALF_UP)


def _adr(revenue: Decimal, nights: Decimal):
    if nights == 0:
        return None if revenue != 0 else Decimal("0.0000")
    return _q4(revenue / nights)


def rezen_room_totals(lines: list[dict], daily: dict[date, dict], label: str = "jl11") -> dict[str, dict]:
    """按房型加总窗口内的间夜和房费。房费必须逐日等于经营日报，间夜最多允许一天少 1。"""
    if any(line.get("business") not in daily for line in lines):
        raise ValueError("Rezen 房费日期超出经营日报，拒绝写入")
    codes: dict[str, str] = {}
    for line in lines:
        code = str(line.get("room_type_code") or "").strip()
        name = str(line.get("room_type_name") or "").strip()
        if not code or not name:
            raise ValueError("Rezen 房费房型无效，拒绝写入")
        if code in codes and codes[code] != name:
            raise ValueError("Rezen 房费房型编码对应多个名称，拒绝写入")
        codes[code] = name
    names = {}
    for code, name in codes.items():
        if name in names and names[name] != code:
            raise ValueError("Rezen 房费房型名称对应多个编码，拒绝写入")
        names[name] = code
    short = None
    nights: dict[str, Decimal] = {}
    fees: dict[str, Decimal] = {}
    for day, stats in sorted(daily.items()):
        day_lines = [line for line in lines if line.get("business") == day]
        fee = sum((_signed(line["amount"]) for line in day_lines), Decimal(0))
        if _money(fee) != _money(stats["revenue"]):
            raise ValueError("Rezen 房型房费与经营日报不一致，本轮不写入")
        counted = nights_by_room_type(day_lines, day)
        gap = sum(counted.values(), Decimal(0)) - _signed(stats["nights"])
        if gap != 0:
            if gap != -1 or short is not None:
                raise ValueError("Rezen 房型间夜与经营日报不一致，本轮不写入")
            short = day
        for name, count in counted.items():
            nights[name] = nights.get(name, Decimal(0)) + count
        for name, amount in revenue_by_room_type(day_lines).items():
            fees[name] = fees.get(name, Decimal(0)) + amount
    if short is not None:
        print(f"INFO [{label}] {short.isoformat()} 的 Rezen 间夜比经营日报少 1，仍按夜审规则写入")
    totals = {}
    for name in set(nights) | set(fees):
        sold = nights.get(name, Decimal(0))
        amount = _money(fees.get(name, Decimal(0)))
        if sold == 0 and amount == 0:
            continue
        totals[name] = {"room_nights": sold, "room_revenue": amount}
    return totals


def _row(base: dict, hotel_id: str, hotel_name: str, snapshot: datetime, label: str, room_type_id) -> dict:
    item = {
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "snapshot_date": base["snapshot_date"], "period_start": base["period_start"], "period_end": base["period_end"],
        "section": "summary", "room_type_id": room_type_id, "room_type_name": label,
        "dimension_code": "", "dimension_name": "", "room_count": base.get("room_count"),
        "room_nights": _q4(base["room_nights"]), "occupancy_rate": base.get("occupancy_rate"),
        "room_revenue": _money(base["room_revenue"]), "average_room_price": base.get("average_room_price"),
        "revpar": base.get("revpar"), "overnight_room_count": base.get("overnight_room_count"),
        "overnight_occupancy_rate": base.get("overnight_occupancy_rate"), "snapshot_time": snapshot,
    }
    if set(item) != set(JL11_COLUMNS):
        raise ValueError("写入列与现有报表不一致，拒绝写入")
    return {key: item[key] for key in JL11_COLUMNS}


def _blank(base: dict, nights: Decimal, revenue: Decimal) -> dict:
    item = {
        "snapshot_date": base["snapshot_date"], "period_start": base["period_start"], "period_end": base["period_end"],
        "room_nights": nights, "room_revenue": revenue, "average_room_price": _adr(revenue, nights),
    }
    for key in BLANK_RATES:
        item[key] = None
    return item


def _recomputed(base: dict, rows: list[dict]) -> dict:
    nights = sum((_signed(row["room_nights"]) for row in rows), Decimal(0))
    revenue = sum((_signed(row["room_revenue"]) for row in rows), Decimal(0))
    rooms = sum((_signed(row["room_count"]) for row in rows), Decimal(0))
    overnight = sum((_signed(row["overnight_room_count"]) for row in rows), Decimal(0))
    item = {
        "snapshot_date": base["snapshot_date"], "period_start": base["period_start"], "period_end": base["period_end"],
        "room_count": rooms, "room_nights": nights, "room_revenue": revenue, "overnight_room_count": overnight,
        "average_room_price": _adr(_money(revenue), nights),
        "occupancy_rate": _q4(nights * 100 / rooms) if rooms else None,
        "revpar": _q4(revenue / rooms) if rooms else None,
        "overnight_occupancy_rate": _q4(overnight * 100 / rooms) if rooms else None,
    }
    return item


def merge_jl11(meituan: list[dict], rezen: dict[str, dict], hotel_id: str, hotel_name: str,
               snapshot: datetime, aliases: dict) -> list[dict]:
    if not meituan:
        raise ValueError("房型分类有一侧没有数据，本轮不写入")
    first = meituan[0]
    if any(row.get("snapshot_date") != snapshot.date() or row.get("snapshot_time") != snapshot
           or row.get("period_start") != first.get("period_start") or row.get("period_end") != first.get("period_end")
           or row.get("section") != "summary" for row in meituan):
        raise ValueError("房型分类统计窗口不一致，拒绝写入")
    parts = []
    seen = set()
    for row in meituan:
        name = str(row.get("room_type_name") or "").strip()
        if not name or name == "总计" or ("pms_meituan", name) in seen:
            raise ValueError("房型分类名称缺失或重复，拒绝写入")
        seen.add(("pms_meituan", name))
        parts.append({**row, "room_type_name": name, "platform": "pms_meituan", "complete": True})
    for name, item in rezen.items():
        label = str(name or "").strip()
        if not label or label == "总计" or ("pms_rezen", label) in seen:
            raise ValueError("房型分类名称缺失或重复，拒绝写入")
        seen.add(("pms_rezen", label))
        parts.append({
            "room_type_name": label, "platform": "pms_rezen", "complete": False,
            "room_nights": item["room_nights"], "room_revenue": item["room_revenue"],
        })
    by_name: dict[str, list[dict]] = {}
    for row in parts:
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
    merged = []
    used = set()
    for key in sorted(grouped, key=lambda item: (item[0], item[1])):
        rows = grouped[key]
        label = " / ".join(sorted({row["room_type_name"] for row in rows}))
        if len(label) > 255 or label in used:
            raise ValueError("合并后的房型名称过长或冲突，拒绝写入")
        used.add(label)
        room_type_id = key[1] if key[0] == "id" else None
        if any(not row["complete"] for row in rows):
            nights = sum((_signed(row["room_nights"]) for row in rows), Decimal(0))
            revenue = sum((_signed(row["room_revenue"]) for row in rows), Decimal(0))
            payload = _blank(first, nights, _money(revenue))
        elif len(rows) == 1:
            payload = rows[0]
        else:
            payload = _recomputed(first, rows)
        merged.append(_row(payload, hotel_id, hotel_name, snapshot, label, room_type_id))
    return merged


def collect_meituan(cookie: str, hotel_id: str, hotel_name: str, org_id: str, snapshot: datetime,
                    clock=None) -> list[dict]:
    return collect_classification(cookie, hotel_id, hotel_name, org_id, clock=clock or (lambda: snapshot))


def collect_rezen(start: date, end: date) -> dict[str, dict]:
    from rezen_pms.business import fetch_daily, fetch_room_fee_lines

    return rezen_room_totals(fetch_room_fee_lines(start, end), fetch_daily(start, end))
