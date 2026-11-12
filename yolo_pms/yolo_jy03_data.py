#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通 JY03：酒店经营月报，每月一行「总营业指标」。

与宝寓 / 西软 / 慧云 / 美团保持同一形态：dimension_type = dimension_name =
「总营业指标」，一行一个月；首次采最近 3 个月及去年同月，之后每轮刷新
「本月 + 上月」及各自去年同月（去年同期用于同比，必须跟着一起滚；
上月是为了在当月漏跑月末时把尾巴补成整月）。

同比口径：去年同月截到同一进度
  本月只统计到最新营业日（今天要等夜审），因此去年同月也必须截到「同一个
  日号」，否则会用「本月 10 天」去比「去年整月」，出租率与 RevPAR 直接失真。
  例：2026-11-11 采集时，2026-11 统计 11-01~11-10，2025-11 也统计 11-01~11-10。
  本月的进度每天推进，去年同月次日会跟着一起往后延，到月末自然补成整月。

口径
  · 房晚 / 房费   直接取源表月累计 F_MNums / F_MRoomSalesum
  · room_count    该月逐日房量累加，即「可售房日」；跨房量月不会被记成整月 × 单一房量
  · 出租率        房晚 / 房日；ADR 房费 / 房晚；RevPAR 房费 / 房日，三者均现算

为什么取月累计而不是逐日累加
  余乐通在月末会重算月累计，把夜审漏记的房费补回来。实测 2026-10：
  逐日累加 522 房晚 / 65283.32 元，月累计 526 房晚 / 65682.84 元，
  后者与账务流水（rs01 口径）分厘不差。取月累计才能和流水对平。

两个不能用的源字段
  · F_MonthDays   实测值等于「日号 + 1」（2026-10-31 是 32），且存在漏递增
                  （2026-11-09 仍是 9），据此算房日会把出租率算错。
  · F_MRentalRate 分母正是上面那个错误的房日，同样不可用。

房量历史（决定房日必须逐日累加，不能按月取单一值）
  2017-05-01 ~ 2020-07-09  62 间
  2020-07-10 ~ 2024-10-09  61 间
  2024-10-10 ~ 今          30 间
  因此 2024-10 的正确房日是 9 天 × 61 + 22 天 × 30 = 1209，而非 31 × 30。

用法
  python yolo_jy03_data.py              # 采集并落库
  python yolo_jy03_data.py --dry-run    # 只取数不落库
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    YoloError,
    mysql_connection,
    query,
    replace_periods,
    report_main,
    save_output,
)
from yolo_sql import MONTHLY_REPORT_QUERY, ROOM_TYPE_GROUP  # noqa: E402

TABLE = "jy03_hotel_statistics_month"
SUMMARY_FILE = "yolo_jy03_summary.json"
TOTAL_DIMENSION = "总营业指标"
# 首次采 3 个月（含本月）及各自去年同月；之后只刷新本月与去年同月。
INITIAL_MONTHS = 3
ZERO = Decimal(0)

COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "period_month", "dimension_type",
    "dimension_name", "room_type_id", "room_count", "room_nights", "room_revenue",
    "maintain_rooms", "occupancy_rate", "adr", "revpar", "snapshot_time",
)


# ---------------------------------------------------------------------------
# 月份与日期
# ---------------------------------------------------------------------------


def month_start(value: date, offset: int = 0) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def month_end(month: date) -> date:
    return month_start(month, 1) - timedelta(days=1)


def month_key(value: date) -> str:
    return value.strftime("%Y-%m")


def collection_months(today: date, initialized: bool) -> list[date]:
    """本轮要重算的月份，升序。

    未初始化时取最近 3 个月，各自再带一个去年同月；已初始化时取本月 +
    上月，各自再带一个去年同月。

    为什么带上是月：窗口若只有本月，当月最后一次采集不在月末（比如只跑到
    11-25）时，本月与去年同月会永久停在部分月——滚到 12 月后窗口变成
    12 月与去年 12 月，11 月那两行再也不会被回看修正。多算一个月才能把
    上个月的尾巴补成整月。月份重叠是刻意保留的自愈能力：某轮失败或夜审
    延迟，下一轮会把同一个月重新算一遍覆盖回去。
    """
    count = 2 if initialized else INITIAL_MONTHS
    current = [month_start(today, -offset) for offset in range(count)]
    return sorted(current + [month_start(month, -12) for month in current])


# ---------------------------------------------------------------------------
# 取数与校验
# ---------------------------------------------------------------------------


def number(value: str | None, label: str) -> Decimal:
    """余乐通 money 列经客户端输出后形如 `.00`，统一转 Decimal 而不是静默补零。"""
    text = str(value or "").replace(",", "").strip()
    if not text:
        return ZERO
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise YoloError(f"经营报表 {label} 数值无效：{value!r}") from None
    if not result.is_finite():
        raise YoloError(f"经营报表 {label} 含非有限数值。")
    return result


def rounded(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def has_monthly_data(config: Config) -> bool:
    """库里已有本酒店的月报，说明不是首次采集。"""
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT 1 FROM `{TABLE}` WHERE hotel_id=%s AND source_platform=%s "
                "AND dimension_type=%s LIMIT 1",
                (config.hotel_id, SOURCE_PLATFORM, TOTAL_DIMENSION),
            )
            return bool(cursor.fetchall())
    finally:
        connection.close()


def month_ranges(months: list[date]) -> str:
    """把月份列表拼成半开区间，源表 F_ReportDayfrom 是 char，按字符串比较即可。"""
    return " or ".join(
        f"(F_ReportDayfrom >= '{month.isoformat()} 00:00:00' "
        f"and F_ReportDayfrom < '{month_start(month, 1).isoformat()} 00:00:00')"
        for month in months
    )


def fetch_daily(config: Config, months: list[date]) -> list[dict[str, str]]:
    """取窗口内每个营业日一行（房型分类口径），月累计字段随行返回。"""
    return query(
        MONTHLY_REPORT_QUERY.format(caption=ROOM_TYPE_GROUP, ranges=month_ranges(months)),
        config,
        timeout=180,
        required=("business_date", "total_rooms", "month_nights", "month_revenue"),
    )


# ---------------------------------------------------------------------------
# 组装月度指标
# ---------------------------------------------------------------------------


def room_days_of(daily_rooms: dict[date, int], start: date, end: date) -> int:
    """把 start~end 每天的房量累加，得到可售房日。

    某天没有报表行时沿用最近一个已知房量；月初几天缺失时用该月第一个
    已知值向前填充——2020-02 整月只有月末 1 行（疫情停业），
    这样算出的 29 × 62 = 1798 才对得上日历天。
    """
    ordered = sorted(daily_rooms)
    if not ordered:
        return 0
    current = daily_rooms[ordered[0]]
    total = 0
    day = start
    while day <= end:
        current = daily_rooms.get(day, current)
        total += current
        day += timedelta(days=1)
    return total


def period_end(month: date, today: date, cutoff: int | None) -> tuple[date, bool]:
    """该月统计截止日，以及是否被「同期」截断。

    cutoff 是本月最新营业日的日号。本月自身靠 window 过滤到最新报表日，
    这里返回日历月末即可；去年同月必须显式截到同一日号，否则同比两边
    统计天数不同，出租率与 RevPAR 没有可比性。cutoff 为空（本月还没有
    任何报表，如月初第一天）时不截断，退回整月口径。
    """
    if month_key(month) == month_key(today):
        return month_end(month), False
    if cutoff and month_key(month) == month_key(month_start(today, -12)):
        # 2 月 29 日这类边界做一次夹取，避免构造非法日期。
        end = min(month_start(month) + timedelta(days=cutoff - 1), month_end(month))
        return end, True
    return month_end(month), False


def same_period_day(by_month: dict[str, list[date]], today: date) -> int | None:
    """本月最新营业日的日号，作为去年同月的截断进度。"""
    days = sorted(by_month.get(month_key(today)) or [])
    return days[-1].day if days else None


def expected_days(month: date, today: date, cutoff: int | None = None) -> int:
    """该月应有的营业日数：当月算到昨天（今天要等夜审），其余月份与 period_end
    保持一致——整月按日历天数，被同期截断的去年同月按截断日号算。
    复用 period_end 而不是重算日号，闰年 2 月 29 日这类情况才不会多算一天。"""
    if month_key(month) == month_key(today):
        return max(0, (today - month).days)
    end, _ = period_end(month, today, cutoff)
    return (end - month).days + 1


def build_rows(records: list[dict[str, str]], config: Config, snapshot: datetime,
               months: list[date]) -> tuple[list[dict], dict]:
    """按营业日归集，再逐月取「最后一个报表日的月累计」作为该月指标。"""
    daily: dict[date, dict[str, Decimal | int]] = {}
    for record in records:
        raw_day = (record.get("business_date") or "").strip()
        if not raw_day:
            raise YoloError("经营报表存在缺少营业日的行。")
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            raise YoloError(f"经营报表营业日无效：{raw_day!r}") from None
        if day > snapshot.date():
            raise YoloError(f"经营报表返回了未来营业日：{day}")

        agg = daily.setdefault(day, {
            "rooms": 0, "day_nights": ZERO, "day_revenue": ZERO,
            "month_nights": ZERO, "month_revenue": ZERO,
        })
        # 源表每行 F_TotalRooms 都是酒店总房量而非房型房量，故取最大值。
        agg["rooms"] = max(int(agg["rooms"]), int(number(record.get("total_rooms"), "总房数")))
        agg["day_nights"] += number(record.get("day_nights"), "当日房晚")
        agg["day_revenue"] += number(record.get("day_revenue"), "当日房费")
        agg["month_nights"] += number(record.get("month_nights"), "月累计房晚")
        agg["month_revenue"] += number(record.get("month_revenue"), "月累计房费")

    if not daily:
        raise YoloError("经营报表在请求月份内无任何数据，拒绝写入空结果。")

    today = snapshot.date()
    by_month: dict[str, list[date]] = {}
    for day in daily:
        by_month.setdefault(month_key(day), []).append(day)

    cutoff = same_period_day(by_month, today)

    rows: list[dict] = []
    warnings: list[str] = []
    detail: dict[str, dict] = {}
    skipped: list[str] = []

    for month in months:
        key = month_key(month)
        days = sorted(by_month.get(key) or [])
        if not days:
            # 该月源表没有任何报表，跳过即可；与西软 jy03 的 missing 处理一致，
            # 只做记录、不算异常——月初当天本来也还没有当月报表。
            skipped.append(key)
            continue

        end, truncated = period_end(month, today, cutoff)
        # 截断月只认同截止日之前的报表行，月累计也必须取窗口内最后一天的值。
        window = [day for day in days if day <= end]
        if not window:
            skipped.append(key)
            continue

        last_day = window[-1]
        latest = daily[last_day]
        nights = latest["month_nights"]
        revenue = latest["month_revenue"]
        if nights < 0 or revenue < 0:
            raise YoloError(f"经营报表 {key} 出现负的月累计：{nights} 房晚 / {revenue} 元")

        # 房日截止日取「应有截止日」与「最后一个有报表的营业日」中较早者。
        # 数据完整的月份两者相等，按日历天算；数据不全的月份（漏跑、夜审中断）
        # 必须按实际报表日算，否则分母虚高、出租率被严重低估——例如 11 月只
        # 跑到 10 号，用 900 房日去除 90 房晚，出租率会从 30% 掉到 10%。
        # 2020-02 疫情只有月末 1 行，last_day 就是月末，仍得 29 × 62 = 1798。
        room_day_end = min(last_day, end)
        room_days = room_days_of({day: int(daily[day]["rooms"]) for day in days},
                                 month, room_day_end)
        if room_days <= 0:
            raise YoloError(f"{key} 房量为 0，无法计算出租率，拒绝写入。")

        rows.append({
            "hotel_name": config.hotel_name,
            "hotel_id": config.hotel_id,
            "source_platform": SOURCE_PLATFORM,
            "period_month": key,
            "dimension_type": TOTAL_DIMENSION,
            "dimension_name": TOTAL_DIMENSION,
            "room_type_id": None,          # 与 jy01 一致，月报不按房型拆
            "room_count": room_days,
            "room_nights": rounded(nights),
            "room_revenue": rounded(revenue),
            "maintain_rooms": None,        # 余乐通报表没有维修房口径
            "occupancy_rate": rounded(nights * 100 / room_days),
            "adr": rounded(revenue / nights) if nights else None,
            "revpar": rounded(revenue / room_days),
            "snapshot_time": snapshot,
        })

        daily_nights = sum((daily[day]["day_nights"] for day in window), ZERO)
        daily_revenue = sum((daily[day]["day_revenue"] for day in window), ZERO)
        expected = expected_days(month, today, cutoff)
        detail[key] = {
            "report_days": len(window),
            "expected_days": expected,
            "period_start": month.isoformat(),
            "period_end": room_day_end.isoformat(),
            "same_period_truncated": truncated,
            "last_report_date": last_day.isoformat(),
            "room_days": room_days,
            "month_cumulative_nights": str(rounded(nights)),
            "month_cumulative_revenue": str(rounded(revenue)),
            "daily_sum_nights": str(rounded(daily_nights)),
            "daily_sum_revenue": str(rounded(daily_revenue)),
            "month_adjust_nights": str(rounded(nights - daily_nights)),
            "month_adjust_revenue": str(rounded(revenue - daily_revenue)),
        }

        # 月累计少于逐日累加说明源系统把已入账的房费抹掉了，属于异常方向。
        if nights < daily_nights or revenue < daily_revenue:
            warnings.append(
                f"{key} 月累计低于逐日累加（房晚 {nights} < {daily_nights}，"
                f"房费 {revenue} < {daily_revenue}），请核对余乐通夜审"
            )

    if not rows:
        raise YoloError(f"请求的 {len(months)} 个月份均无可用数据，拒绝写入。")

    stats = {
        "months": [row["period_month"] for row in rows],
        "detail": detail,
        "same_period_day": cutoff,
        "skipped_months": skipped,
    }
    return rows, {"warnings": warnings, **stats}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def collect(config: Config, snapshot: datetime) -> dict:
    initialized = has_monthly_data(config)
    months = collection_months(snapshot.date(), initialized)
    records = fetch_daily(config, months)
    rows, stats = build_rows(records, config, snapshot, months)

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else replace_periods(
        TABLE, COLUMNS, rows, config, "period_month",
        [row["period_month"] for row in rows],
        unique_key="period_month,dimension_type,dimension_name",
    )

    warnings = stats["warnings"]
    for message in warnings:
        print(f"WARN {message}")
    summary = {
        "report": "jy03",
        "business_date": (snapshot.date() - timedelta(days=1)).isoformat(),
        "period_months": stats["months"],
        "latest_month": stats["months"][-1] if stats["months"] else None,
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "initialized": initialized,
        "row_count": len(rows),
        "written": written,
        "dry_run": dry_run,
        "same_period_day": stats["same_period_day"],
        "skipped_months": stats["skipped_months"],
        "warnings": warnings,
        "months_detail": stats["detail"],
        "basis": "房晚/房费取房型分类统计的月累计 F_MNums/F_MRoomSalesum（余乐通月末重算，"
                 "与 rs01 流水口径一致）；room_count=该月逐日房量累加；"
                 "出租率=房晚/房日，ADR=房费/房晚，RevPAR=房费/房日，三者均现算；"
                 "去年同月截到本月最新营业日的同一日号，保证同比天数一致；"
                 "F_MonthDays 与 F_MRentalRate 为脏字段，不采用",
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jy03", collect)


if __name__ == "__main__":
    raise SystemExit(main())
