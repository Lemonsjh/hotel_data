"""美团 PMS + Rezen 的报表任务。两侧都查到才合并，失败的一侧不写库。"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "OTA采集服务")]

from meituan_pms.booking_report import collection_window, collect as collect_bookings
from meituan_pms.daily_report import collect as collect_daily, collection_dates as daily_dates
from meituan_pms.guest_report import collect as collect_guests
from meituan_pms.monthly_report import collect as collect_monthly
from meituan_pms.performance_report import collect as collect_performance, collection_dates as performance_dates
from pms_dual.business import (
    JD01_COLUMNS, JD02_COLUMNS, RS01_COLUMNS, daily_row, departure_day, hotel_context, merge_jl02, merge_jy01,
    merge_jy03, month_row, performance_from_daily, performance_history_start, prefix_orders,
    rezen_booking_row, rezen_guest_row,
)
from pms_dual.classification import collect_meituan as collect_classification
from pms_dual.classification import collect_rezen as collect_rezen_classification
from pms_dual.classification import merge_jl11
from pms_dual.revenue import collect_meituan as collect_revenue
from pms_dual.revenue import collect_rezen as collect_rezen_revenue
from pms_dual.room_performance import collect_meituan, collect_rezen, merge_jl01
from pms_dual.room_status import load_aliases, now_shanghai
from pms_dual.state import remember
from pms_dual.store import (
    needs_month_backfill, needs_revenue_backfill, write_jd01, write_jd02, write_jl01, write_jl02, write_jl11,
    write_jy01, write_jy03, write_rs01,
)
from rezen_pms.business import fetch_daily, fetch_departures, fetch_months, fetch_reservations


def _clock(snapshot):
    return lambda: snapshot


def _ids(hotel_id: str, hotel_name: str) -> tuple[str, str, str]:
    return os.environ.get("MEITUAN_PMS_COOKIE", ""), hotel_id, os.environ.get("MEITUAN_PMS_ORG_ID", "").strip()


def _combine(left: list[dict], right: list[dict], *, unique_order: bool = True) -> list[dict]:
    rows = [*left, *right]
    if unique_order and len({row["order_id"] for row in rows}) != len(rows):
        raise ValueError("两侧单号冲突，拒绝写入")
    return rows


def run_daily(clock=now_shanghai) -> list[dict]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_daily(cookie, hotel_id, name, org_id, clock=_clock(snapshot))
    remember("meituan", "jy01")
    dates = daily_dates(snapshot.date())
    stats = fetch_daily(dates[0], dates[-1])
    rezen = [daily_row(day, stats[day], hotel_id, name, snapshot) for day in dates]
    remember("rezen", "jy01")
    return merge_jy01(meituan, rezen, hotel_id, name, snapshot)


def run_monthly(clock=now_shanghai, initial: bool | None = None) -> list[dict]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    if initial is None:
        initial = needs_month_backfill(hotel_id, snapshot)
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_monthly(cookie, hotel_id, name, org_id, initial=initial, snapshot=snapshot)
    remember("meituan", "jy03")
    start = date(snapshot.year - 1, 1, 1) if initial else snapshot.date().replace(day=1)
    # 月接口的截止日期必须落在当前月，否则整段回空。当前月含今天，和美团月报一致。
    end = snapshot.date()
    stats = fetch_months(start, end) if end >= start else {}
    rezen = [month_row(month, item, hotel_id, name, snapshot) for month, item in sorted(stats.items())]
    remember("rezen", "jy03")
    return merge_jy03(meituan, rezen, hotel_id, name, snapshot)


def run_performance(clock=now_shanghai) -> list[dict]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_performance(cookie, hotel_id, name, org_id, clock=_clock(snapshot))
    remember("meituan", "jl02")
    dates = performance_dates(snapshot.date())
    end = snapshot.date() - timedelta(days=1)
    origin = date(min(dates).year, 1, 1)
    history = performance_history_start(fetch_months(origin, snapshot.date()), origin, end)
    stats = fetch_daily(history, end) if history <= end else {}
    rezen = performance_from_daily(stats, hotel_id, name, snapshot, zero_before=history)
    remember("rezen", "jl02")
    return merge_jl02(meituan, rezen, hotel_id, name, snapshot)


def run_room_performance(clock=now_shanghai, aliases=None) -> list[dict]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_meituan(cookie, hotel_id, name, org_id, snapshot, clock=clock)
    remember("meituan", "jl01")
    rezen = collect_rezen(hotel_id, name, snapshot)
    remember("rezen", "jl01")
    try:
        mapping = load_aliases(hotel_id) if aliases is None else aliases
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("读取房型映射失败，本轮不写入") from None
    return merge_jl01(meituan, rezen, hotel_id, name, snapshot, mapping)


def run_classification(clock=now_shanghai, aliases=None) -> list[dict]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_classification(cookie, hotel_id, name, org_id, snapshot)
    remember("meituan", "jl11")
    period = meituan[0]
    rezen = collect_rezen_classification(period["period_start"], period["period_end"])
    remember("rezen", "jl11")
    try:
        mapping = load_aliases(hotel_id) if aliases is None else aliases
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("读取房型映射失败，本轮不写入") from None
    return merge_jl11(meituan, rezen, hotel_id, name, snapshot, mapping)


def run_revenue(clock=now_shanghai, initial: bool | None = None) -> tuple[list[dict], object, bool]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    if initial is None:
        initial = needs_revenue_backfill(hotel_id)
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan = collect_revenue(cookie, hotel_id, name, org_id, snapshot, initial=initial)
    remember("meituan", "rs01")
    rezen = collect_rezen_revenue(snapshot, initial=initial)
    remember("rezen", "rs01")
    rows = _combine(
        prefix_orders(meituan, "mt", hotel_id, name, snapshot, RS01_COLUMNS, "pms_meituan", unique_order=False),
        prefix_orders(rezen, "rz", hotel_id, name, snapshot, RS01_COLUMNS, "pms_rezen", unique_order=False),
        unique_order=False)
    return rows, snapshot, initial


def run_bookings(clock=now_shanghai) -> tuple[list[dict], object]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan, _stamp = collect_bookings(cookie, hotel_id, name, org_id, clock=_clock(snapshot))
    remember("meituan", "jd01")
    start, end = collection_window(snapshot.date())
    rezen = [rezen_booking_row(account, hotel_id, name, snapshot, start, end) for account in fetch_reservations(start, end)]
    remember("rezen", "jd01")
    rows = _combine(
        prefix_orders(meituan, "mt", hotel_id, name, snapshot, JD01_COLUMNS, "pms_meituan"),
        prefix_orders(rezen, "rz", hotel_id, name, snapshot, JD01_COLUMNS, "pms_rezen"))
    return rows, snapshot


def run_guests(clock=now_shanghai) -> tuple[list[dict], object]:
    hotel_id, name = hotel_context()
    snapshot = clock()
    cookie, hotel_id, org_id = _ids(hotel_id, name)
    meituan, _stamp = collect_guests(cookie, hotel_id, name, org_id, clock=_clock(snapshot))
    remember("meituan", "jd02")
    day = departure_day(snapshot.date())
    rezen = [rezen_guest_row(account, hotel_id, name, day, snapshot) for account in fetch_departures(day)]
    remember("rezen", "jd02")
    rows = _combine(
        prefix_orders(meituan, "mt", hotel_id, name, snapshot, JD02_COLUMNS, "pms_meituan", unique_order=False),
        prefix_orders(rezen, "rz", hotel_id, name, snapshot, JD02_COLUMNS, "pms_rezen", unique_order=False),
        unique_order=False)
    return rows, snapshot


def _run(argv, collect, write, title) -> int:
    parser = argparse.ArgumentParser(description=title)
    parser.add_argument("--dry-run", action="store_true", help="只查询并合并，不写入 MySQL")
    args = parser.parse_args(argv)
    try:
        result = collect(args.dry_run)
        rows = result[0] if isinstance(result, tuple) else result
        if not args.dry_run:
            write(result if isinstance(result, tuple) else rows)
        print(f"{title}{'校验' if args.dry_run else '入库'}完成：{len(rows)} 行")
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        return 1
    except Exception:
        print("美团+Rezen 报表执行失败，请检查数据库配置；本轮未完成入库")
        return 1


def main_daily(argv=None):
    return _run(argv, lambda _dry: run_daily(), write_jy01, "美团+Rezen 经营日报")


def main_monthly(argv=None):
    def collect(dry_run):
        return run_monthly(initial=True if dry_run else None)
    return _run(argv, collect, write_jy03, "美团+Rezen 经营月报")


def main_performance(argv=None):
    return _run(argv, lambda _dry: run_performance(), write_jl02, "美团+Rezen 经营业绩")


def main_room_performance(argv=None):
    return _run(argv, lambda _dry: run_room_performance(), write_jl01, "美团+Rezen 房型业绩")


def main_classification(argv=None):
    return _run(argv, lambda _dry: run_classification(), write_jl11, "美团+Rezen 房型分类")


def main_revenue(argv=None):
    def collect(dry_run):
        return run_revenue(initial=False if dry_run else None)

    def write(result):
        rows, snapshot, initial = result
        write_rs01(rows, snapshot, initial=initial)

    return _run(argv, collect, write, "美团+Rezen 房费明细")


def main_bookings(argv=None):
    def write(result):
        rows, snapshot = result
        write_jd01(rows, snapshot)
    return _run(argv, lambda _dry: run_bookings(), write, "美团+Rezen 预订明细")


def main_guests(argv=None):
    def write(result):
        rows, snapshot = result
        write_jd02(rows, snapshot)
    return _run(argv, lambda _dry: run_guests(), write, "美团+Rezen 离店客人")
