"""余乐通经营报表：jl01 房型经营日报 + jy01 日度总营业指标。

两者同源（T_Web_ReportMultiple），一次查询同时产出：
房型分类统计逐房型落 jl01；当日各房型合计构成 jy01 唯一一行「总营业指标」。
源表另有客人/策略/消费/外卖四个分类口径，属明细视角，不落 jy01，
其中策略分类统计的房晚用于与房型口径交叉校验。

采集范围：最近 30 个已结账营业日（不含今天，当天要等次日 05:00 夜审后才进源表）。
落库按营业日整段替换，历史在同一事务内按 pms_daily_days（默认 400 天）清理。

用法
  python yolo_daily_report_data.py              # 采集并落库
  python yolo_daily_report_data.py --dry-run    # 只取数不落库，验证映射
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
    query,
    replace_snapshot,
    report_main,
    save_output,
)
from yolo_sql import DAILY_REPORT_QUERY, ROOM_TYPE_GROUP, STRATEGY_GROUP  # noqa: E402

SUMMARY_FILE = "yolo_daily_report_summary.json"
JL01_TABLE = "jl01_room_type_performance_daily"
JY01_TABLE = "jy01_hotel_statistics_daily"
# jy01 每天只有一行汇总，维度取平台通用名，便于与其他 PMS 的口径直接对齐
TOTAL_DIMENSION = "总营业指标"
# 每轮重算的营业日数，与别样红/宝寓/西软/美团 PMS 保持一致
COLLECTION_DAYS = 30

JL01_COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "business_date", "room_type_name",
    "room_type_id", "pms_rate_room_type_id", "room_nights", "occupancy_rate",
    "room_revenue", "adr", "revpar", "snapshot_time",
)
JY01_COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "business_date", "dimension_type",
    "dimension_name", "room_type_id", "room_count", "room_nights", "room_revenue",
    "occupancy_rate", "adr", "revpar", "sold_rooms", "remaining_rooms",
    "orders_today", "snapshot_time",
)
# 这三种口径的均价由余乐通正确计算，可作为现算值的校验基准；
# 「消费分类统计」「外卖分类统计」的均价与出租率在源表里恒为 0，不能作为基准。
SOURCE_ADR_GROUPS = {ROOM_TYPE_GROUP, "客人分类统计", STRATEGY_GROUP}


def collection_dates(today: date) -> list[date]:
    """最近 N 个已结账营业日（昨天往前，不含今天）。

    营业日以 05:00 为界，当天的数据要等次日 05:00 夜审后才写进源表，
    因此窗口右端取昨天。窗口保持 N 天重叠是为了自愈：某轮采集失败、
    机器停机或夜审延迟，下一轮会把这几天重新算一遍补回来。
    """
    latest = today - timedelta(days=1)
    return [latest - timedelta(days=offset) for offset in reversed(range(COLLECTION_DAYS))]


def number(value: str | None, label: str) -> Decimal:
    """余乐通 money 列经客户端输出后形如 `.00`，统一转 Decimal 而不是静默补零。"""
    text = str(value or "").replace(",", "").strip()
    if not text:
        return Decimal(0)
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise YoloError(f"经营报表 {label} 数值无效：{value!r}") from None
    if not result.is_finite():
        raise YoloError(f"经营报表 {label} 含非有限数值。")
    return result


def rounded(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def total_row(day: date, totals: dict[str, Decimal] | None, config: Config, snapshot: datetime) -> dict | None:
    """由当日房型合计生成总营业指标行；房量缺失时返回 None，不虚构出租率分母。"""
    if not totals or totals["rooms"] <= 0:
        return None
    rooms, nights, revenue = totals["rooms"], totals["nights"], totals["revenue"]
    return {
        "hotel_name": config.hotel_name,
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "business_date": day,
        "dimension_type": TOTAL_DIMENSION,
        "dimension_name": TOTAL_DIMENSION,
        "room_type_id": None,
        "room_count": int(rooms),
        "room_nights": rounded(nights, "0.01"),
        "room_revenue": rounded(revenue, "0.01"),
        "occupancy_rate": rounded(nights * 100 / rooms, "0.01"),
        "adr": rounded(revenue / nights, "0.01") if nights else None,
        "revpar": rounded(revenue / rooms, "0.01"),
        "sold_rooms": None,
        "remaining_rooms": None,
        "orders_today": None,
        "snapshot_time": snapshot,
    }


def build_rows(records: list[dict[str, str]], config: Config, snapshot: datetime,
               days: list[date]) -> tuple[list[dict], list[dict], dict]:
    """把多维报表整理成 jl01 房型行与 jy01 总营业指标行。

    第三个返回值是校验用的统计：各口径房晚合计、每日房型合计、未落库口径行数、
    源表均价与现算值的偏差行数。
    """
    jl01: list[dict] = []
    totals: dict[date, dict[str, Decimal]] = {}
    nights_by_group: dict[str, Decimal] = {}
    ignored: dict[str, int] = {}
    seen: set[tuple] = set()
    adr_mismatch = 0

    for record in records:
        raw_day = (record.get("business_date") or "").strip()
        group = (record.get("dimension_group") or "").strip()
        name = (record.get("dimension_value") or "").strip()
        if not raw_day or not group or not name:
            raise YoloError("经营报表存在缺少营业日、分类或维度值的行。")
        try:
            day = date.fromisoformat(raw_day)
        except ValueError:
            raise YoloError(f"经营报表营业日无效：{raw_day!r}") from None
        if day not in days:
            raise YoloError(f"经营报表返回了非请求营业日：{day}")
        if (day, group, name) in seen:
            raise YoloError(f"经营报表维度重复：{day} {group} {name}")
        seen.add((day, group, name))

        total_rooms = number(record.get("total_rooms"), "总房数")
        nights = number(record.get("room_nights"), "房晚")
        revenue = number(record.get("room_revenue"), "房费")
        source_adr = number(record.get("adr"), "平均房价")
        if nights < 0 or revenue < 0:
            raise YoloError(f"经营报表出现负值：{day} {group} {name}")
        if group in SOURCE_ADR_GROUPS and nights and abs(source_adr - revenue / nights) > Decimal("0.02"):
            adr_mismatch += 1

        nights_by_group[group] = nights_by_group.get(group, Decimal(0)) + nights
        if group != ROOM_TYPE_GROUP:
            ignored[group] = ignored.get(group, 0) + 1
            continue

        # 均价与出租率按定义现算，不取源表字段：余乐通在部分口径里
        # 这两列恒为 0，照抄会把 5 房晚 433 元写成均价 0。
        jl01.append({
            "hotel_id": config.hotel_id,
            "hotel_name": config.hotel_name,
            "source_platform": SOURCE_PLATFORM,
            "business_date": day,
            "room_type_name": name,
            "room_type_id": None,
            "pms_rate_room_type_id": "",
            "room_nights": rounded(nights),
            "occupancy_rate": rounded(nights * 100 / total_rooms, "0.01") if total_rooms else Decimal(0),
            "room_revenue": rounded(revenue),
            "adr": rounded(revenue / nights, "0.01") if nights else None,
            "revpar": rounded(revenue / total_rooms) if total_rooms else None,
            "snapshot_time": snapshot,
        })
        # 源表每行的 F_TotalRooms 都是酒店总房量（不是该房型房量），故取最大值而非累加；
        # 这样即便某天只返回部分房型，总房量也不会被低估。
        agg = totals.setdefault(day, {"rooms": Decimal(0), "nights": Decimal(0), "revenue": Decimal(0)})
        agg["rooms"] = max(agg["rooms"], total_rooms)
        agg["nights"] += nights
        agg["revenue"] += revenue

    jy01 = [row for row in (total_row(day, totals.get(day), config, snapshot) for day in days) if row]
    stats = {
        "nights_by_group": nights_by_group,
        "totals_by_day": {day: totals[day] for day in totals},
        "ignored_groups": ignored,
        "adr_mismatch": adr_mismatch,
    }
    return jl01, jy01, stats


def collect(config: Config, snapshot: datetime) -> dict:
    days = collection_dates(snapshot.date())
    start = f"{days[0].isoformat()} 00:00:00"
    end = f"{(days[-1] + timedelta(days=1)).isoformat()} 00:00:00"

    records = query(DAILY_REPORT_QUERY.format(start=start, end=end), config, timeout=180,
                    required=("business_date", "dimension_group", "dimension_value"))
    if not records:
        raise YoloError(f"经营报表在 {days[0]}~{days[-1]} 无数据，拒绝写入空结果。")

    jl01_rows, jy01_rows, stats = build_rows(records, config, snapshot, days)
    if not jl01_rows and not jy01_rows:
        raise YoloError("经营报表未产生任何可映射维度，请检查口径配置。")

    room_nights = stats["nights_by_group"].get(ROOM_TYPE_GROUP, Decimal(0))
    strategy_nights = stats["nights_by_group"].get(STRATEGY_GROUP, Decimal(0))
    warnings = []
    if not jl01_rows:
        warnings.append(f"营业日 {days[0]}~{days[-1]} 无房型维度数据")
    covered = {row["business_date"] for row in jy01_rows}
    missing = [day.isoformat() for day in days if day not in covered]
    if missing:
        warnings.append(f"{len(missing)} 个营业日无房型合计，未生成总营业指标：{', '.join(missing)}")
    if strategy_nights and abs(room_nights - strategy_nights) > Decimal("0.01"):
        warnings.append(f"房型房晚 {room_nights} 与价格策略房晚 {strategy_nights} 不一致")
    if stats["adr_mismatch"]:
        warnings.append(f"{stats['adr_mismatch']} 行源表均价与「房费/房晚」现算值不符，请核对余乐通口径")

    dry_run = "--dry-run" in sys.argv
    written = 0
    if not dry_run:
        if jl01_rows:
            written += replace_snapshot(JL01_TABLE, JL01_COLUMNS, jl01_rows, config, tuple(days),
                                        unique_key="business_date,room_type_name")
        if jy01_rows:
            written += replace_snapshot(JY01_TABLE, JY01_COLUMNS, jy01_rows, config, tuple(days),
                                        unique_key="business_date,dimension_type,dimension_name")

    for message in warnings:
        print(f"WARN {message}")
    summary = {
        "report": "daily_report",
        "business_date": days[-1].isoformat(),
        "dates": [day.isoformat() for day in days],
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "row_count": len(jl01_rows) + len(jy01_rows),
        "jl01_rows": len(jl01_rows),
        "jy01_rows": len(jy01_rows),
        "written": written,
        "dry_run": dry_run,
        "warnings": warnings,
        "total_rooms_by_date": {day.isoformat(): str(agg["rooms"]) for day, agg in stats["totals_by_day"].items()},
        "room_nights_by_group": {key: str(value) for key, value in stats["nights_by_group"].items()},
        "ignored_groups": stats["ignored_groups"],
        "basis": "jl01=房型分类统计逐房型；jy01=日度总营业指标（当日各房型房量/房晚/房费合计）；"
                 "ADR=房费/房晚，出租率=房晚/总房数，RevPAR=房费/总房数（三者均现算）；"
                 "客人/策略/消费/外卖分类口径不落库",
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("daily_report", collect)


if __name__ == "__main__":
    raise SystemExit(main())
