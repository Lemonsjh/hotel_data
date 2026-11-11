"""美团 PMS 与 Rezen 的 RS01。同一侧同一科目的多行先加总，单号保持原值。"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from meituan_pms.revenue_report import collect as collect_revenue, collection_windows
from pms_dual.business import SOURCE
from pms_dual.classification import _money, _signed, rezen_room_totals
from rezen_pms.room_fee import collapse_revenue


def _month_is_blank(item: dict) -> bool:
    return all(_signed(item[key]) == 0 for key in ("room_count", "maintain_rooms", "nights", "revenue"))


def collect_rezen(snapshot: datetime, *, initial: bool) -> list[dict]:
    from rezen_pms.business import fetch_daily, fetch_months, fetch_room_fee_lines

    windows = collection_windows(snapshot.date(), initial)
    months = fetch_months(windows[0][0], snapshot.date())
    lines = []
    daily: dict = {}
    for start, end in windows:
        month = start.strftime("%Y-%m")
        item = months.get(month)
        if item is None:
            raise ValueError("Rezen 经营月报月份不完整，本轮不写入")
        if _month_is_blank(item):
            continue
        month_days = fetch_daily(start, end)
        if (end + timedelta(days=1)).month != end.month:
            fee = sum((_signed(day["revenue"]) for day in month_days.values()), Decimal(0))
            nights = sum((_signed(day["nights"]) for day in month_days.values()), Decimal(0))
            if _money(fee) != _money(item["revenue"]) or nights != _signed(item["nights"]):
                raise ValueError("Rezen 房费日合计与经营月报不一致，本轮不写入")
        daily.update(month_days)
        lines.extend(fetch_room_fee_lines(start, end))
    if daily:
        rezen_room_totals(lines, daily, label="rs01")
    return collapse_revenue(lines)


def collect_meituan(cookie: str, hotel_id: str, hotel_name: str, org_id: str, snapshot: datetime, *,
                    initial: bool) -> list[dict]:
    rows = collect_revenue(cookie, hotel_id, hotel_name, org_id, initial=initial, snapshot=snapshot,
                           clock=lambda: snapshot)
    for row in rows:
        row["source_platform"] = SOURCE
        row["snapshot_time"] = snapshot
    return rows
