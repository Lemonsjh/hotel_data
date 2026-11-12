#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通 JL11：最近 30 个完整营业日各房型的经营汇总。

与其他 4 家 PMS（宝寓 / 西软 / 美团 / 慧云）保持同一形态：section 只有 summary，
一行一个房型，周期是「昨天往前 30 个已结账营业日」，按酒店 + 来源整段替换。

口径
  · 房晚 / 房费   窗口内逐日累加，同源于 T_Web_ReportMultiple「房型分类统计」
  · 房型房量      房号清单按最近一次入住归到房型后计数（与实时快照同一算法）
  · room_count    房型房量 × 30，即 30 天累计房日
  · 出租率        房晚 / 房日，房日为 0 时留 NULL
  · 均价 / RevPAR 房费 / 房晚、房费 / 房日
  · overnight_*   与整日口径同值，沿用其他 PMS 的填法

余乐通报表没有「房型 × 客源」「房型 × 渠道」交叉，也不设这些分区。

用法
  python yolo_jl11_data.py              # 采集并落库
  python yolo_jl11_data.py --dry-run    # 只取数不落库
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    YoloError,
    query,
    replace_batch,
    report_main,
    save_output,
)
from yolo_room_status_data import (  # noqa: E402
    ROOM_LOOKBACK_DAYS,
    fetch_room_type_ids,
    fetch_rooms,
    to_decimal,
)
from yolo_sql import JL11_SUMMARY_QUERY, ROOM_TYPE_GROUP  # noqa: E402

COLLECTION_DAYS = 30
TABLE = "jl11_room_type_classification"
SECTION = "summary"
SUMMARY_FILE = "yolo_jl11_summary.json"

COLUMNS = (
    "hotel_id", "hotel_name", "source_platform", "snapshot_date",
    "period_start", "period_end", "section", "room_type_id", "room_type_name",
    "dimension_code", "dimension_name", "room_count", "room_nights",
    "occupancy_rate", "room_revenue", "average_room_price", "revpar",
    "overnight_room_count", "overnight_occupancy_rate", "snapshot_time",
)


def report_window(today: date) -> tuple[date, date]:
    """最近 N 个已结账营业日；当天要等次日 05:00 夜审后才进源表，不计入。"""
    end = today - timedelta(days=1)
    return end - timedelta(days=COLLECTION_DAYS - 1), end


def rounded(value: Decimal) -> Decimal:
    """目标列为 decimal(18,4)。"""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def fetch_summary(config: Config, start: date, end: date) -> list[dict[str, str]]:
    """窗口内按房型累加房晚与房费。"""
    rows = query(
        JL11_SUMMARY_QUERY.format(
            caption=ROOM_TYPE_GROUP,
            start=f"{start.isoformat()} 00:00:00",
            end=f"{(end + timedelta(days=1)).isoformat()} 00:00:00",
        ),
        config,
        timeout=180,
        required=("room_type_name", "room_nights", "room_revenue", "hotel_rooms"),
    )
    if not rows:
        raise YoloError(f"经营报表在 {start}~{end} 无房型数据，拒绝写入空结果。")
    return rows


def fetch_capacity(config: Config, today: date) -> dict[str, int]:
    """房型 → 房号数。与实时快照用同一条查询，保证两处房量口径一致。"""
    capacity: dict[str, int] = {}
    for room in fetch_rooms(config, today - timedelta(days=ROOM_LOOKBACK_DAYS)):
        name = room["room_type_name"].strip()
        if not name:
            raise YoloError(f"房号 {room['room_no']} 查不到房型，拒绝汇总 JL11。")
        capacity[name] = capacity.get(name, 0) + 1
    if not capacity:
        raise YoloError("房号清单为空，无法确定房型房量。")
    return capacity


def build_rows(records: list[dict[str, str]], capacity: dict[str, int],
               type_ids: dict[str, str], config: Config, snapshot: datetime,
               start: date, end: date) -> tuple[list[dict], dict]:
    """把窗口累计值展开成房型汇总行；房量为 0 时只留 NULL 分母，不虚构出租率。"""
    hotel_rooms = None
    seen: set[str] = set()
    rows: list[dict] = []
    no_capacity: list[str] = []

    for record in records:
        name = (record.get("room_type_name") or "").strip()
        if not name:
            raise YoloError("经营报表存在空房型名称。")
        if name in seen:
            raise YoloError(f"经营报表房型重复：{name}")
        seen.add(name)
        if name not in type_ids:
            raise YoloError(f"房型「{name}」不在 T_RoomInApps 中，请先在余乐通上架该房型。")

        nights = to_decimal(record.get("room_nights"))
        revenue = to_decimal(record.get("room_revenue"))
        if nights < 0 or revenue < 0:
            raise YoloError(f"经营报表出现负值：{name}")
        hotel_rooms = max(hotel_rooms or 0, int(to_decimal(record.get("hotel_rooms") or "0")))

        rooms = capacity.get(name, 0)
        if not rooms:
            no_capacity.append(name)
        room_days = Decimal(rooms * COLLECTION_DAYS)

        rows.append({
            "hotel_id": config.hotel_id,
            "hotel_name": config.hotel_name,
            "source_platform": SOURCE_PLATFORM,
            "snapshot_date": snapshot.date(),
            "period_start": start,
            "period_end": end,
            "section": SECTION,
            "room_type_id": None,      # 等人工映射后由 room_type_enrichment 回填
            "room_type_name": name,
            "dimension_code": "",
            "dimension_name": "",
            "room_count": rounded(room_days),
            "room_nights": rounded(nights),
            "occupancy_rate": rounded(nights * 100 / room_days) if room_days else None,
            "room_revenue": rounded(revenue),
            "average_room_price": rounded(revenue / nights) if nights else None,
            "revpar": rounded(revenue / room_days) if room_days else None,
            "overnight_room_count": rounded(nights),
            "overnight_occupancy_rate": rounded(nights * 100 / room_days) if room_days else None,
            "snapshot_time": snapshot,
        })

    if not rows:
        raise YoloError("JL11 未产生任何房型行，拒绝写入。")

    total_rooms = sum(capacity.get(row["room_type_name"], 0) for row in rows)
    stats = {
        "hotel_rooms_by_report": hotel_rooms,
        "capacity_total": total_rooms,
        "capacity": {name: count for name, count in capacity.items()},
        "room_types_without_capacity": no_capacity,
        "room_days_total": total_rooms * COLLECTION_DAYS,
        "room_nights_total": rounded(sum((row["room_nights"] for row in rows), Decimal(0))),
        "room_revenue_total": rounded(sum((row["room_revenue"] for row in rows), Decimal(0))),
    }
    return rows, stats


def collect(config: Config, snapshot: datetime) -> dict:
    start, end = report_window(snapshot.date())

    records = fetch_summary(config, start, end)
    capacity = fetch_capacity(config, snapshot.date())
    type_ids = fetch_room_type_ids(config)
    rows, stats = build_rows(records, capacity, type_ids, config, snapshot, start, end)

    warnings = []
    if stats["room_types_without_capacity"]:
        warnings.append(
            f"房号清单里没有房型 {stats['room_types_without_capacity']}，"
            "其房量与出租率留空，请核查余乐通房态"
        )
    if stats["hotel_rooms_by_report"] and stats["capacity_total"] != stats["hotel_rooms_by_report"]:
        warnings.append(
            f"房号清单合计 {stats['capacity_total']} 间与报表总房量 "
            f"{stats['hotel_rooms_by_report']} 间不一致，出租率分母可能偏小"
        )

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else replace_batch(
        TABLE, COLUMNS, rows, config, unique_key="room_type_name")

    for message in warnings:
        print(f"WARN {message}")
    summary = {
        "report": "jl11",
        "business_date": end.isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "row_count": len(rows),
        "written": written,
        "dry_run": dry_run,
        "warnings": warnings,
        "days": COLLECTION_DAYS,
        **{key: value for key, value in stats.items() if key != "capacity"},
        "basis": "房晚/房费取房型分类统计窗口累计；房量取房号清单按最近一次入住归型后计数，"
                 "room_count=房量×30；出租率=房晚/房日，ADR=房费/房晚，RevPAR=房费/房日",
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jl11", collect)


if __name__ == "__main__":
    raise SystemExit(main())
