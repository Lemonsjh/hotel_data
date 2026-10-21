"""西软实时执行价与净入账房费计算；不得混用两种收入口径。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from foxhis_common import Config, FoxhisError, query, require_columns
from foxhis_sql import POSTED_ROOM_REVENUE_QUERY, REALTIME_ROOM_RATE_QUERY


def apply_posted_revenue(forecast_rows: list[dict], room_rows: list[dict], records: list[dict[str, str]]) -> dict:
    """房费取当天净入账，ADR 分母取计费间夜，RevPAR 分母取物理总房数。"""
    room_types = {room["room_no"]: room["_room_type_code"] for room in room_rows}
    totals = {row["pms_room_type_id"]: {"revenue": Decimal("0"), "room_nights": Decimal("0")}
              for row in forecast_rows}
    seen: dict[tuple[str, str], tuple] = {}
    duplicates = 0
    for record in records:
        key = ((record.get("folio_no") or "").strip(), (record.get("line_no") or "").strip())
        if not all(key):
            raise FoxhisError("房费流水缺少账号或流水号，无法去重。")
        try:
            amount = Decimal(record["amount"].replace(",", "").strip())
            nights = Decimal(record["room_nights"].replace(",", "").strip())
        except (KeyError, AttributeError, InvalidOperation):
            raise FoxhisError("房费流水金额或计费间夜无效，保留上次快照。") from None
        if not amount.is_finite() or not nights.is_finite():
            raise FoxhisError("房费流水含非有限数值，保留上次快照。")
        room_no = (record.get("room_no") or "").strip()
        signature = (room_no, (record.get("charge_code") or "").strip(), amount, nights)
        if key in seen:
            if seen[key] != signature:
                raise FoxhisError("当前账与历史账的同一流水数据冲突，保留上次快照。")
            duplicates += 1
            continue
        seen[key] = signature
        # 零金额、零间夜的占位流水不构成业务收入，不要求其房号仍在房态表中。
        if not amount and not nights:
            continue
        type_code = room_types.get(room_no)
        if type_code not in totals:
            raise FoxhisError("存在无法对应当前房型的有效房费流水，拒绝写入不完整收入。")
        totals[type_code]["revenue"] += amount
        totals[type_code]["room_nights"] += nights

    def rounded(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    for row in forecast_rows:
        metrics = totals[row["pms_room_type_id"]]
        revenue, nights = metrics["revenue"], metrics["room_nights"]
        row["room_revenue"] = rounded(revenue)
        row["adr"] = rounded(revenue / nights) if nights > 0 else None
        row["revpar"] = rounded(revenue / Decimal(row["total_rooms"])) if row["total_rooms"] else None
    return {
        "basis": "采集当天营业日期净入账房费；ADR=净房费/计费间夜，RevPAR=净房费/物理总房数",
        "source_rows": len(records), "unique_transactions": len(seen), "deduplicated_rows": duplicates,
        "by_room_type": totals,
    }


def posted_revenue(config: Config, room_rows: list[dict], forecast_rows: list[dict], day: date) -> dict:
    for table in ("account", "haccount"):
        require_columns(config, table, ["accnt", "number", "roomno", "pccode", "charge", "quantity", "bdate"])
    require_columns(config, "pccode", ["pccode", "jierep", "tail"])
    records = query(POSTED_ROOM_REVENUE_QUERY.format(business_date=day.strftime("%Y%m%d")), config, timeout=120)
    return apply_posted_revenue(forecast_rows, room_rows, records)


def apply_realtime_revenue(forecast_rows: list[dict], room_rows: list[dict], records: list[dict[str, str]]) -> dict:
    """以同一批物理在住房的当日执行价计算实时预计房费，避免混用已入账口径。"""
    occupied = {room["room_no"]: room["_room_type_code"] for room in room_rows if room["_occupy_status"] == "O"}
    totals = {row["pms_room_type_id"]: {"revenue": Decimal("0"), "occupied_rooms": 0}
              for row in forecast_rows}
    seen: set[str] = set()
    for record in records:
        room_no = (record.get("room_no") or "").strip()
        code = (record.get("room_type_code") or "").strip()
        if room_no not in occupied or occupied[room_no] != code:
            raise FoxhisError("实时房态与在住价格的房号或房型不一致，请重新采集。")
        if room_no in seen:
            raise FoxhisError("同一在住房存在多条主单或重叠价格段，拒绝重复计算房费。")
        if not (record.get("folio_no") or "").strip() or not (record.get("rate_segment_id") or "").strip():
            raise FoxhisError(f"在住房缺少有效主单或当日价格段（房号：{room_no}），保留上次快照。")
        try:
            rate = Decimal(record["room_rate"].replace(",", "").strip())
            quantity = Decimal(record["room_quantity"].replace(",", "").strip())
            rate_quantity = Decimal(record["rate_quantity"].replace(",", "").strip())
        except (KeyError, AttributeError, InvalidOperation):
            raise FoxhisError("实时执行价或房数无效，保留上次快照。") from None
        if not rate.is_finite() or rate < 0 or not quantity.is_finite() or not rate_quantity.is_finite():
            raise FoxhisError("实时执行价或房数不合法，保留上次快照。")
        if quantity != 1 or rate_quantity != 1 or (record.get("share_code") or "").strip().upper() != "F":
            raise FoxhisError("发现未验证的多房或共享主单，拒绝重复或遗漏房费。")
        seen.add(room_no)
        totals[code]["revenue"] += rate
        totals[code]["occupied_rooms"] += 1
    if seen != set(occupied):
        raise FoxhisError("部分在住房缺少实时执行价，拒绝写入不完整房费。")
    for row in forecast_rows:
        metrics = totals[row["pms_room_type_id"]]
        revenue, count = metrics["revenue"], metrics["occupied_rooms"]
        if count != row["occupied_rooms"]:
            raise FoxhisError("房型在住数与价格房数不一致，保留上次快照。")
        rounded = lambda value: value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        row["room_revenue"] = rounded(revenue)
        row["adr"] = rounded(revenue / count) if count else None
        row["revpar"] = rounded(revenue / Decimal(row["total_rooms"])) if row["total_rooms"] else None
    return {
        "basis": "当前物理在住房当日执行价合计；ADR=实时预计房费/在住房数，RevPAR=实时预计房费/物理总房数",
        "priced_rooms": len(seen), "by_room_type": totals,
    }


def realtime_revenue(config: Config, room_rows: list[dict], forecast_rows: list[dict], day: date) -> dict:
    require_columns(config, "master", ["accnt", "roomno", "sta", "rmnum", "share", "dep"])
    require_columns(config, "rsvsrc", ["accnt", "id", "quantity", "rate", "begin_", "end_", "arr", "dep"])
    records = query(REALTIME_ROOM_RATE_QUERY.format(business_date=day.strftime("%Y%m%d")), config, timeout=120)
    return apply_realtime_revenue(forecast_rows, room_rows, records)
