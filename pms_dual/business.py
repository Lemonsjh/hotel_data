"""美团 PMS 与 Rezen 的经营、业绩、房型业绩、预订和离店。列与对应的单系统报表一致。"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from meituan_pms.booking_report import collection_window
from meituan_pms.daily_report import RECENT_DAYS, collection_dates as daily_dates, ratio
from meituan_pms.performance_report import CATEGORY, CORE_METRICS, collection_dates as performance_dates
from pms_dual.merge import SOURCE


JY01_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "business_date", "dimension_type", "dimension_name",
    "room_type_id", "room_count", "room_nights", "room_revenue", "occupancy_rate", "adr", "revpar",
    "sold_rooms", "remaining_rooms", "orders_today", "snapshot_time",
)
JY03_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "period_month", "dimension_type", "dimension_name",
    "room_type_id", "room_count", "room_nights", "room_revenue", "maintain_rooms", "occupancy_rate",
    "adr", "revpar", "snapshot_time",
)
JL02_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "business_date", "category", "metric_name",
    "value_day", "value_month", "value_year", "snapshot_time",
)
JL01_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "business_date", "room_type_name",
    "pms_rate_room_type_id", "room_nights", "occupancy_rate", "room_revenue", "adr", "revpar",
    "snapshot_time", "room_type_id",
)
JL11_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "snapshot_date", "period_start", "period_end",
    "section", "room_type_id", "room_type_name", "dimension_code", "dimension_name",
    "room_count", "room_nights", "occupancy_rate", "room_revenue", "average_room_price", "revpar",
    "overnight_room_count", "overnight_occupancy_rate", "snapshot_time",
)
RS01_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "business_date", "order_id", "room_no",
    "room_type_name", "room_type_id", "guest_name", "customer_source", "checkin_time", "checkout_time",
    "rack_rate", "price_type", "room_daily_price", "stay_type", "charge_subject", "room_nights",
    "room_fee", "operator_name", "channel_unit_id", "snapshot_time",
)
JD01_COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "order_id", "booking_time", "contact", "guest_source",
    "member_level", "arrival_time", "departure_time", "room_type_name", "room_type_id", "room_count",
    "price_type", "room_price", "prepayment", "guarantee_method", "booking_status", "hold_time",
    "remarks", "operator_name", "snapshot_time",
)
JD02_COLUMNS = (
    "order_id", "room_number", "room_type_name", "customer_id", "customer_name", "id_card_number",
    "customer_mobile", "checkin_type", "order_source", "guest_source", "member_level_id", "member_level",
    "oneself_or_not", "price_stakeholder_name", "price_stakeholder_mobile", "order_memo", "check_out_tip",
    "checkin_status", "operator_name", "room_attr", "main_checkin_id", "hotel_id", "hotel_name",
    "source_platform", "business_date", "snapshot_time", "arrival_time", "departure_time", "room_price",
    "main_checkin_person", "total_checkin_count", "room_type_id",
)
DIMENSION = "总营业指标"
FOUR = Decimal("0.0001")


def hotel_context() -> tuple[str, str]:
    from pms_dual.room_status import hotel_name

    hotel_id = os.environ.get("HOTEL_ID", "").strip()
    name = hotel_name(os.environ.get("MEITUAN_PMS_HOTEL_NAME", ""), os.environ.get("REZEN_HOTEL_NAME", ""))
    if not hotel_id:
        raise ValueError("请配置酒店 ID")
    return hotel_id, name


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("汇总数值无效，拒绝写入")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("汇总数值无效，拒绝写入")
    return number


def _whole(value, label: str) -> int:
    number = _decimal(value)
    if number != number.to_integral_value():
        raise ValueError(f"{label}不是整数，拒绝写入")
    return int(number)


def _q4(value) -> Decimal:
    return _decimal(value).quantize(FOUR, rounding=ROUND_HALF_UP)


def _money(value):
    """与美团报表的金额列一致：空值保持空，允许负数，写入前收到分。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("汇总数值无效，拒绝写入")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("汇总数值无效，拒绝写入") from None
    if not number.is_finite():
        raise ValueError("汇总数值无效，拒绝写入")
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(number) >= Decimal("100000000"):
        raise ValueError("汇总数值无效，拒绝写入")
    return number


def _same_hotel(rows: list[dict], hotel_id: str) -> None:
    if any(row.get("hotel_id") != hotel_id for row in rows):
        raise ValueError("两侧酒店不一致，拒绝写入")


def _index(rows: list[dict], key: str) -> dict:
    found = {}
    for row in rows:
        marker = row.get(key)
        if marker in found:
            raise ValueError("汇总键重复，拒绝写入")
        found[marker] = row
    return found


def daily_row(day: date, stats: dict, hotel_id: str, hotel_name: str, snapshot: datetime) -> dict:
    room_count = _whole(stats["room_count"], "总房")
    rentable = _whole(stats["rentable"], "可售房")
    nights = _decimal(stats["nights"])
    if nights != nights.to_integral_value():
        raise ValueError("间夜不是整数，拒绝写入")
    sold = int(nights)
    revenue = _decimal(stats["revenue"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "business_date": day, "dimension_type": DIMENSION, "dimension_name": DIMENSION,
        "room_type_id": None, "room_count": room_count, "room_nights": sold, "room_revenue": revenue,
        "occupancy_rate": ratio(sold * 100, rentable), "adr": ratio(revenue, sold),
        "revpar": ratio(revenue, room_count), "sold_rooms": sold,
        "remaining_rooms": max(rentable - sold, 0),
        "orders_today": None, "snapshot_time": snapshot,
    }


def merge_jy01(left: list[dict], right: list[dict], hotel_id: str, hotel_name: str, snapshot: datetime) -> list[dict]:
    if not left or not right:
        raise ValueError("有一侧没有经营日报，本轮不写入")
    _same_hotel([*left, *right], hotel_id)
    expected = set(daily_dates(snapshot.date()))
    if {row.get("business_date") for row in left} != expected or {row.get("business_date") for row in right} != expected:
        raise ValueError("两侧经营日报日期不一致，本轮不写入")
    if len(left) != RECENT_DAYS or len(right) != RECENT_DAYS:
        raise ValueError("经营日报日期重复，拒绝写入")
    right_by_day = _index(right, "business_date")
    merged = []
    for row in sorted(left, key=lambda item: item["business_date"]):
        other = right_by_day[row["business_date"]]
        room_count = _whole(row["room_count"], "总房") + _whole(other["room_count"], "总房")
        nights = _decimal(row["room_nights"]) + _decimal(other["room_nights"])
        revenue = (_decimal(row["room_revenue"]) + _decimal(other["room_revenue"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sold = _whole(row["sold_rooms"], "已售房") + _whole(other["sold_rooms"], "已售房")
        remaining = _whole(row["remaining_rooms"], "可售房") + _whole(other["remaining_rooms"], "可售房")
        merged.append({
            "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
            "business_date": row["business_date"], "dimension_type": DIMENSION, "dimension_name": DIMENSION,
            "room_type_id": None, "room_count": room_count, "room_nights": nights, "room_revenue": revenue,
            "occupancy_rate": ratio(nights * 100, sold + remaining), "adr": ratio(revenue, nights),
            "revpar": ratio(revenue, room_count), "sold_rooms": sold, "remaining_rooms": remaining,
            "orders_today": None, "snapshot_time": snapshot,
        })
    return merged


def month_row(month: str, stats: dict, hotel_id: str, hotel_name: str, snapshot: datetime) -> dict:
    room_count = _whole(stats["room_count"], "总房日")
    maintain = _whole(stats["maintain_rooms"], "维修房日")
    nights = _decimal(stats["nights"])
    revenue = _decimal(stats["revenue"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if maintain > room_count:
        raise ValueError("维修房日超过总房日，拒绝写入")
    return {
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "period_month": month, "dimension_type": DIMENSION, "dimension_name": DIMENSION,
        "room_type_id": None, "room_count": room_count, "room_nights": nights, "room_revenue": revenue,
        "maintain_rooms": maintain, "occupancy_rate": ratio(nights * 100, room_count - maintain),
        "adr": ratio(revenue, nights), "revpar": ratio(revenue, room_count), "snapshot_time": snapshot,
    }


def merge_jy03(left: list[dict], right: list[dict], hotel_id: str, hotel_name: str, snapshot: datetime) -> list[dict]:
    if not left or not right:
        raise ValueError("有一侧没有经营月报，本轮不写入")
    _same_hotel([*left, *right], hotel_id)
    left_months, right_months = _index(left, "period_month"), _index(right, "period_month")
    shared = set(left_months) & set(right_months)
    current = snapshot.strftime("%Y-%m")
    if current not in shared:
        raise ValueError("本月经营月报有一侧没有数据，本轮不写入")
    omitted = sorted((set(left_months) | set(right_months)) - shared)
    if omitted:
        print("INFO [jy03] 以下月份不是两侧都有，本轮不写入：" + "、".join(omitted))
    merged = []
    for month in sorted(shared):
        one, other = left_months[month], right_months[month]
        room_count = _whole(one["room_count"], "总房日") + _whole(other["room_count"], "总房日")
        maintain = _whole(one["maintain_rooms"], "维修房日") + _whole(other["maintain_rooms"], "维修房日")
        nights = _decimal(one["room_nights"]) + _decimal(other["room_nights"])
        revenue = (_decimal(one["room_revenue"]) + _decimal(other["room_revenue"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if maintain > room_count:
            raise ValueError("维修房日超过总房日，拒绝写入")
        merged.append({
            "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
            "period_month": month, "dimension_type": DIMENSION, "dimension_name": DIMENSION,
            "room_type_id": None, "room_count": room_count, "room_nights": nights, "room_revenue": revenue,
            "maintain_rooms": maintain, "occupancy_rate": ratio(nights * 100, room_count - maintain),
            "adr": ratio(revenue, nights), "revpar": ratio(revenue, room_count), "snapshot_time": snapshot,
        })
    return merged


def _parts(row: dict, period: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """由已按美团口径算好的指标反推间夜、房费、可售和总房，供两侧相加。"""
    nights = _decimal(row["间夜数"][period])
    revenue = _decimal(row["总房费"][period])
    occupancy = _decimal(row["出租率"][period])
    revpar = _decimal(row["RevPar"][period])
    rentable = nights / (occupancy / 100) if occupancy else Decimal(0)
    rooms = revenue / revpar if revpar else Decimal(0)
    return nights, revenue, rentable, rooms


def _metric_rows(parts: dict[str, tuple], hotel_id: str, hotel_name: str, day: date, snapshot: datetime) -> list[dict]:
    values = {}
    for period, (nights, revenue, rentable, rooms) in parts.items():
        values[period] = {
            "间夜数": _q4(nights),
            "总房费": _q4(revenue),
            "出租率": _q4(nights * 100 / rentable) if rentable else Decimal("0.0000"),
            "平均房价": _q4(revenue / nights) if nights else Decimal("0.0000"),
            "RevPar": _q4(revenue / rooms) if rooms else Decimal("0.0000"),
        }
    return [{
        "hotel_id": hotel_id, "hotel_name": hotel_name, "source_platform": SOURCE,
        "business_date": day, "category": CATEGORY, "metric_name": name,
        "value_day": values["day"][name], "value_month": values["month"][name],
        "value_year": values["year"][name], "snapshot_time": snapshot,
    } for name in ("间夜数", "出租率", "总房费", "平均房价", "RevPar")]


def performance_history_start(months: dict[str, dict], start: date, end: date) -> date:
    """整月总房、维修、间夜、房费都是 0 的月份不需要日明细。第一个有数的月从 1 号起必须有日明细。"""
    expected = []
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        expected.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    if set(months) != set(expected):
        raise ValueError("Rezen 经营月报月份不完整，本轮不写入")
    for month in expected:
        item = months[month]
        if any(_decimal(item.get(key) or 0) != 0 for key in ("room_count", "maintain_rooms", "nights", "revenue")):
            year, number = (int(part) for part in month.split("-"))
            return date(year, number, 1)
    return end + timedelta(days=1)


def performance_from_daily(days: dict[date, dict], hotel_id: str, hotel_name: str, snapshot: datetime,
                           zero_before: date | None = None) -> list[dict]:
    blank = {"nights": 0, "revenue": 0, "rentable": 0, "room_count": 0}
    rows = []
    for business_date in performance_dates(snapshot.date()):
        if business_date not in days and not (zero_before is not None and business_date < zero_before):
            raise ValueError("Rezen 经营业绩缺少营业日，本轮不写入")

        def pack(start: date, end: date) -> tuple[Decimal, Decimal, Decimal, Decimal]:
            chosen = []
            for offset in range((end - start).days + 1):
                day = start + timedelta(days=offset)
                if day in days:
                    chosen.append(days[day])
                elif zero_before is not None and day < zero_before:
                    chosen.append(blank)
                else:
                    raise ValueError("Rezen 经营业绩日期不完整，本轮不写入")
            nights = sum((_decimal(item["nights"]) for item in chosen), Decimal(0))
            revenue = sum((_decimal(item["revenue"]) for item in chosen), Decimal(0))
            rentable = sum((Decimal(item["rentable"]) for item in chosen), Decimal(0))
            room_count = sum((Decimal(item["room_count"]) for item in chosen), Decimal(0))
            return nights, revenue, rentable, room_count

        parts = {
            "day": pack(business_date, business_date),
            "month": pack(business_date.replace(day=1), business_date),
            "year": pack(date(business_date.year, 1, 1), business_date),
        }
        rows.extend(_metric_rows(parts, hotel_id, hotel_name, business_date, snapshot))
    return rows


def _grouped_metrics(rows: list[dict]) -> dict[date, dict[str, dict]]:
    grouped: dict[date, dict[str, dict]] = {}
    for row in rows:
        if row.get("category") != CATEGORY or row.get("metric_name") not in CORE_METRICS:
            continue
        bucket = grouped.setdefault(row["business_date"], {})
        if row["metric_name"] in bucket:
            raise ValueError("经营业绩指标重复，拒绝写入")
        bucket[row["metric_name"]] = row
    return grouped


def merge_jl02(left: list[dict], right: list[dict], hotel_id: str, hotel_name: str, snapshot: datetime) -> list[dict]:
    if not left or not right:
        raise ValueError("有一侧没有经营业绩，本轮不写入")
    _same_hotel([*left, *right], hotel_id)
    expected = set(performance_dates(snapshot.date()))
    one, other = _grouped_metrics(left), _grouped_metrics(right)
    if set(one) != expected or set(other) != expected:
        raise ValueError("两侧经营业绩日期不一致，本轮不写入")
    merged = []
    for day in sorted(expected):
        if set(one[day]) != CORE_METRICS or set(other[day]) != CORE_METRICS:
            raise ValueError("经营业绩核心指标不完整，拒绝写入")
        parts = {}
        for period, column in (("day", "value_day"), ("month", "value_month"), ("year", "value_year")):
            left_part = _parts({name: {column: row[column]} for name, row in one[day].items()}, column)
            right_part = _parts({name: {column: row[column]} for name, row in other[day].items()}, column)
            parts[period] = tuple(left_item + right_item for left_item, right_item in zip(left_part, right_part))
        merged.extend(_metric_rows(parts, hotel_id, hotel_name, day, snapshot))
    return merged


def _text(value, limit: int, label: str):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{label}超过字段长度，拒绝写入")
    return text


def _prefixed(order_id: str, prefix: str) -> str:
    raw = str(order_id or "").strip()
    if not raw:
        raise ValueError("单号为空，拒绝写入")
    marker = prefix + ":"
    value = raw if raw.startswith(marker) else marker + raw
    if len(value) > 100:
        raise ValueError("单号超过字段长度，拒绝写入")
    return value


def prefix_orders(rows: list[dict], prefix: str, hotel_id: str, hotel_name: str, snapshot: datetime,
                  columns: tuple[str, ...], mapping_platform: str, *, unique_order: bool = True) -> list[dict]:
    seen = set()
    output = []
    for row in rows:
        item = {key: row.get(key) for key in columns if key != "room_type_id"}
        item["order_id"] = _prefixed(row.get("order_id"), prefix)
        identity = item["order_id"] if unique_order else tuple(
            item.get(key) for key in columns if key not in {"snapshot_time", "room_type_id"})
        if identity in seen:
            raise ValueError("单号重复，拒绝写入" if unique_order else "客人记录重复，拒绝写入")
        seen.add(identity)
        item["hotel_id"] = hotel_id
        item["hotel_name"] = hotel_name
        item["source_platform"] = SOURCE
        item["snapshot_time"] = snapshot
        item["room_type_id"] = None
        item["mapping_platform"] = mapping_platform
        output.append(item)
    return output


def rezen_booking_row(account: dict, hotel_id: str, hotel_name: str, snapshot: datetime,
                      start: date, end: date) -> dict:
    from rezen_pms.business import account_stamp

    if str(account.get("acctStus") or "") != "R":
        raise ValueError("Rezen 预订状态不符，拒绝写入")
    arrival = account_stamp(account.get("arrDhm"), account.get("arrDt"), account.get("gstarrTm"))
    departure = account_stamp(account.get("dptDhm"), account.get("dptDt"), account.get("gstdptTm"))
    count = _whole(account.get("rmNums"), "房数")
    if arrival is None or not start <= arrival.date() <= end or count < 1 or (departure is not None and departure < arrival):
        raise ValueError("Rezen 预订的入住日期或房数无效，拒绝写入")
    return {
        "hotel_name": hotel_name, "hotel_id": hotel_id, "source_platform": SOURCE,
        "order_id": account.get("acctNum"), "booking_time": account_stamp(account.get("orgDhms"), account.get("orgBdt"), None),
        "contact": _text(account.get("bookGuestNm"), 100, "预订联系人"),
        "guest_source": _text(account.get("sourceDrpt"), 100, "客源"),
        "member_level": _text(account.get("memberTypNm") or account.get("vip"), 50, "会员等级"),
        "arrival_time": arrival, "departure_time": departure,
        "room_type_name": _text(account.get("rmTypDrpt") or account.get("rmTyp"), 255, "房型"),
        "room_count": count, "price_type": _text(account.get("rtCodeDesc"), 100, "价格类型"),
        "room_price": _money(account.get("rtAmt")),
        "prepayment": _money(account.get("paymentAmt")),
        "guarantee_method": _text(account.get("resvTypDrpt"), 100, "担保方式"),
        "booking_status": _text(account.get("acctStusDrpt"), 100, "预订状态") or "R",
        "hold_time": None, "remarks": _text(account.get("notice"), 2000, "备注"),
        "operator_name": _text(account.get("orgOperNm"), 100, "操作员"), "snapshot_time": snapshot,
    }


def rezen_guest_row(account: dict, hotel_id: str, hotel_name: str, day: date, snapshot: datetime) -> dict:
    from rezen_pms.business import account_stamp

    if str(account.get("acctStus") or "") != "O":
        raise ValueError("Rezen 离店状态不符，拒绝写入")
    arrival = account_stamp(account.get("arrDhm"), account.get("arrDt"), account.get("gstarrTm"))
    departure = account_stamp(account.get("dptDhm"), account.get("dptDt"), account.get("gstdptTm"))
    flag = str(account.get("mainFlg") if account.get("mainFlg") is not None else "")
    if flag not in {"0", "1"} or arrival is None or departure is None or departure.date() != day or departure < arrival:
        raise ValueError("Rezen 离店客人的日期或主客人标记无效，拒绝写入")
    return {
        "order_id": account.get("acctNum"),
        "room_number": _text(account.get("rmNum") or account.get("viewRmNum"), 100, "房号"),
        "room_type_name": _text(account.get("rmTypDrpt") or account.get("rmTyp"), 255, "房型"),
        "customer_id": _text(account.get("rezenGuestId"), 100, "客人编号"),
        "customer_name": _text(account.get("guestNm"), 100, "客人姓名"),
        "id_card_number": _text(account.get("crtfNum"), 100, "证件号"),
        "customer_mobile": _text(account.get("teleCtno"), 100, "手机"),
        "checkin_type": None, "order_source": _text(account.get("marketDrpt"), 100, "订单来源"),
        "guest_source": _text(account.get("sourceDrpt"), 100, "客源"),
        "member_level_id": _text(account.get("memberTyp"), 100, "会员等级编号"),
        "member_level": _text(account.get("memberTypNm"), 100, "会员等级"),
        "oneself_or_not": None, "price_stakeholder_name": None, "price_stakeholder_mobile": None,
        "order_memo": _text(account.get("notice"), 2000, "备注"), "check_out_tip": None,
        "checkin_status": _text(account.get("acctStusDrpt"), 100, "入住状态"),
        "operator_name": _text(account.get("chkoOperNm"), 100, "操作员"),
        "room_attr": None, "main_checkin_id": None, "hotel_id": hotel_id, "hotel_name": hotel_name,
        "source_platform": SOURCE, "business_date": day, "snapshot_time": snapshot,
        "arrival_time": arrival, "departure_time": departure,
        "room_price": _money(account.get("rtAmt")),
        "main_checkin_person": int(flag), "total_checkin_count": None,
    }


def departure_day(today: date) -> date:
    return today - timedelta(days=1)
