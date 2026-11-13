#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""余乐通房型级实时经营快照。

pms_room_type_forecast 记录「此刻各房型经营情况」，每次采集整批替换；
pms_room_type_hourly_status 是同一批数据按小时留档，用于观察日内变化。

口径
  · 房量   近 30 天有房态记录或当前在住的房号，按最近一次入住归到房型
  · 在住   T_RoomOrder.F_Status='入住' 的房号数，available = total - occupied
  · 房费   T_PriceList 营业日累计（05:00 为界），仅 F_RoomPriceOrOther='房价'
  · 房晚   每笔房费计 1，钟点房计 0.5
  · adr    房费 / 房晚，房晚为 0 时留 NULL
  · revpar 房费 / 房号数

房费是营业日到采集时刻的累计实际值，白天随时间增长，不是全日预测值。
余乐通没有超预订概念，overbooking_rooms 恒为 NULL。

用法
  python yolo_room_status_data.py              # 采集并落库
  python yolo_room_status_data.py --dry-run    # 只取数不落库
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

# 便携运行时是嵌入式发行版，脚本自身目录不在 sys.path 里，需显式加入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    YoloError,
    cleanup_rows,
    mysql_connection,
    query,
    report_main,
    save_output,
)
from yolo_sql import (  # noqa: E402
    ROOM_ORPHAN_REVENUE_QUERY,
    ROOM_REVENUE_QUERY,
    ROOM_STATUS_QUERY,
    ROOM_TYPE_ID_QUERY,
)

FORECAST_TABLE = "pms_room_type_forecast"
HOURLY_TABLE = "pms_room_type_hourly_status"

FORECAST_COLUMNS = [
    "hotel_id", "hotel_name", "source_platform", "snapshot_time", "stay_date",
    "room_type_name", "room_type_id", "pms_room_type_id", "total_rooms",
    "available_rooms", "occupied_rooms", "overbooking_rooms",
    "room_revenue", "adr", "revpar",
]
HOURLY_COLUMNS = [
    "hotel_id", "hotel_name", "source_platform", "snapshot_time", "snapshot_hour",
    "stay_date", "room_type_name", "room_type_id", "pms_room_type_id",
    "total_rooms", "available_rooms", "occupied_rooms", "overbooking_rooms",
]

BUSINESS_DAY_START = time(5, 0)   # 余乐通营业日以 05:00 为界
ROOM_LOOKBACK_DAYS = 30           # 房态有效范围：逾期的房号视为已停用
SUMMARY_FILE = "yolo_room_status_summary.json"


# ---------------------------------------------------------------------------
# 时间与取值
# ---------------------------------------------------------------------------


def business_window(moment: datetime) -> tuple[date, datetime, datetime]:
    """营业日与取数窗口；05:00 前仍属前一营业日。"""
    day = (moment - timedelta(hours=BUSINESS_DAY_START.hour)).date()
    start = datetime.combine(day, BUSINESS_DAY_START)
    return day, start, start + timedelta(days=1)


def to_decimal(value: str) -> Decimal:
    """客户端会把 0.00 输出成 .00，需补前导零后再解析。"""
    text = (value or "").strip()
    if not text:
        return Decimal("0")
    if text.startswith("."):
        text = "0" + text
    elif text.startswith("-."):
        text = "-0" + text[1:]
    try:
        return Decimal(text)
    except InvalidOperation:
        raise YoloError(f"无法解析数值：{value!r}") from None


def to_int(value: str) -> int:
    return int(to_decimal(value))


def rounded(value: Decimal) -> Decimal:
    """目标列为 decimal(18,4)。"""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def stamp_text(moment: datetime) -> str:
    """余乐通时间字段是 char，按定长文本比较。"""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def fetch_room_type_ids(config) -> dict[str, str]:
    """房型名称 → 余乐通房型标识（T_RoomInApps.f_id）。"""
    rows = query(ROOM_TYPE_ID_QUERY, config, timeout=120,
                 required=("pms_room_type_id", "room_type_name"))
    mapping = {
        row["room_type_name"].strip(): row["pms_room_type_id"].strip()
        for row in rows
        if row["room_type_name"].strip()
    }
    if not mapping:
        raise YoloError("T_RoomInApps 没有可用房型，无法确定 pms_room_type_id。")
    return mapping


def fetch_rooms(config, since: date) -> list[dict[str, str]]:
    rows = query(ROOM_STATUS_QUERY.format(since=since.isoformat()), config, timeout=240,
                 required=("room_no", "room_type_name", "inhouse_count"))
    if not rows:
        raise YoloError("房态查询为空，已保留上次快照。")
    return rows


def fetch_revenue(config, start: datetime, end: datetime) -> tuple[dict[str, dict], dict]:
    """房型 → 营业日房费与折算房晚；同时返回无主流水统计。"""
    window = {"start": stamp_text(start), "end": stamp_text(end)}
    revenue: dict[str, dict] = {}
    for row in query(ROOM_REVENUE_QUERY.format(**window), config, timeout=180,
                     required=("room_type_name", "room_revenue", "room_nights")):
        revenue[row["room_type_name"].strip()] = {
            "room_revenue": to_decimal(row["room_revenue"]),
            "room_nights": to_decimal(row["room_nights"]),
            "charge_count": to_int(row.get("charge_count", "0")),
        }

    orphans = query(ROOM_ORPHAN_REVENUE_QUERY.format(**window), config, timeout=120)
    orphan = orphans[0] if orphans else {}
    return revenue, {
        "orphan_count": to_int(orphan.get("orphan_count", "0")),
        "orphan_revenue": to_decimal(orphan.get("orphan_revenue", "0")),
    }


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_rows(rooms: list[dict[str, str]], type_ids: dict[str, str],
               revenue: dict[str, dict], config,
               moment: datetime, stay_date: date) -> list[dict]:
    """房号聚合到房型，再并入房费；任何口径异常都抛错，不写半成品。"""
    buckets: dict[str, dict] = {}
    seen: set[str] = set()
    for room in rooms:
        room_no = room["room_no"].strip()
        if not room_no:
            raise YoloError("房态存在空房号，拒绝替换实时快照。")
        if room_no in seen:
            raise YoloError(f"房号 {room_no} 在房态中出现多次，拒绝替换实时快照。")
        seen.add(room_no)

        name = room["room_type_name"].strip()
        if not name:
            raise YoloError(f"房号 {room_no} 查不到房型，拒绝替换实时快照。")
        if name not in type_ids:
            raise YoloError(f"房型「{name}」不在 T_RoomInApps 中，请先在余乐通上架该房型。")

        bucket = buckets.setdefault(name, {"total": 0, "occupied": 0})
        bucket["total"] += 1
        bucket["occupied"] += int(to_int(room["inhouse_count"]) > 0)

    unknown = sorted(set(revenue) - set(buckets))
    if unknown:
        raise YoloError(f"房费含房态中不存在的房型 {unknown}，请检查房态有效范围。")

    rows: list[dict] = []
    # 按房型标识排序，保证每次采集的行序稳定；zfill 让数值序与字典序一致
    for name in sorted(buckets, key=lambda item: type_ids[item].zfill(12)):
        total = buckets[name]["total"]
        occupied = buckets[name]["occupied"]
        money = revenue.get(name, {})
        room_revenue = money.get("room_revenue", Decimal("0"))
        nights = money.get("room_nights", Decimal("0"))
        rows.append({
            "hotel_id": config.hotel_id,
            "hotel_name": config.hotel_name,
            "source_platform": SOURCE_PLATFORM,
            "snapshot_time": moment,
            "snapshot_hour": moment.replace(minute=0, second=0, microsecond=0),
            "stay_date": stay_date,
            "room_type_name": name,
            # 留给 room_type_enrichment 按名称回填 OTA 侧统一房型 ID
            "room_type_id": None,
            "pms_room_type_id": type_ids[name],
            "total_rooms": Decimal(total),
            "occupied_rooms": Decimal(occupied),
            "available_rooms": Decimal(total - occupied),
            "overbooking_rooms": None,
            "room_revenue": room_revenue,
            "adr": rounded(room_revenue / nights) if nights else None,
            "revpar": rounded(room_revenue / total) if total else None,
        })
    return rows


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def column_sql(columns: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in columns)


def placeholders(columns: list[str]) -> str:
    return ", ".join(["%s"] * len(columns))


def sync_snapshot(rows: list[dict], config) -> int:
    """同一事务内替换当日快照与本小时快照；当日快照只留最新一批。"""
    if not rows:
        raise YoloError("实时快照为空，拒绝替换。")
    moment, stay_date = rows[0]["snapshot_time"], rows[0]["stay_date"]
    if any(row["hotel_id"] != config.hotel_id or row["source_platform"] != SOURCE_PLATFORM
           or row["snapshot_time"] != moment or row["stay_date"] != stay_date
           for row in rows):
        raise YoloError("实时快照的酒店、来源或营业日不一致，拒绝替换。")
    if len({row["pms_room_type_id"] for row in rows}) != len(rows):
        raise YoloError("房型标识重复，拒绝替换实时快照。")

    hourly = [{name: row[name] for name in HOURLY_COLUMNS} for row in rows]
    hour = rows[0]["snapshot_hour"]
    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT MAX(snapshot_time) FROM `{FORECAST_TABLE}` "
                "WHERE hotel_id=%s FOR UPDATE",
                (config.hotel_id,),
            )
            existing = cursor.fetchone()
            if existing and existing[0] and existing[0] > moment:
                raise YoloError("已有更新的实时快照，拒绝用较早数据覆盖。")

            cursor.execute(
                f"DELETE FROM `{FORECAST_TABLE}` WHERE hotel_id=%s",
                (config.hotel_id,),
            )
            cursor.executemany(
                f"INSERT INTO `{FORECAST_TABLE}` ({column_sql(FORECAST_COLUMNS)}) "
                f"VALUES ({placeholders(FORECAST_COLUMNS)})",
                [tuple(row[name] for name in FORECAST_COLUMNS) for row in rows],
            )

            cursor.execute(
                f"DELETE FROM `{HOURLY_TABLE}` "
                "WHERE hotel_id=%s AND source_platform=%s AND snapshot_hour=%s",
                (config.hotel_id, SOURCE_PLATFORM, hour),
            )
            cursor.executemany(
                f"INSERT INTO `{HOURLY_TABLE}` ({column_sql(HOURLY_COLUMNS)}) "
                f"VALUES ({placeholders(HOURLY_COLUMNS)})",
                [tuple(row[name] for name in HOURLY_COLUMNS) for row in hourly],
            )
            cleanup_rows(cursor, HOURLY_TABLE, hourly, config.hotel_id, SOURCE_PLATFORM)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


# ---------------------------------------------------------------------------
# 采集入口
# ---------------------------------------------------------------------------


def collect(config, moment: datetime) -> dict:
    stay_date, start, end = business_window(moment)
    since = moment.date() - timedelta(days=ROOM_LOOKBACK_DAYS)

    type_ids = fetch_room_type_ids(config)
    rooms = fetch_rooms(config, since)
    revenue, orphan = fetch_revenue(config, start, end)
    rows = build_rows(rooms, type_ids, revenue, config, moment, stay_date)

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run else sync_snapshot(rows, config)

    if orphan["orphan_count"]:
        print(f"WARN {orphan['orphan_count']} 笔房费关联不到入住单，"
              f"合计 {orphan['orphan_revenue']} 元未计入房型口径。")

    save_output(SUMMARY_FILE, {
        "report": "room_status",
        "source_platform": SOURCE_PLATFORM,
        "hotel_id": config.hotel_id,
        "business_date": stay_date.isoformat(),
        "snapshot_time": moment.isoformat(timespec="seconds"),
        "window": [start.isoformat(), end.isoformat()],
        "room_count": len(rooms),
        "inhouse_rooms": sum(1 for room in rooms if to_int(room["inhouse_count"]) > 0),
        "row_count": len(rows),
        "written": written,
        "dry_run": dry_run,
        "orphan_revenue": orphan,
        "basis": "房量取近30天有房态记录或当前在住的房号；房费为营业日05:00起累计实际值，"
                 "钟点房按0.5房晚折算；超预订余乐通无此概念，留空",
        "rows": rows,
    }, config)
    return {
        "business_date": stay_date.isoformat(),
        "row_count": len(rows),
        "written": written,
    }


def main() -> int:
    return report_main("room_status", collect, realtime=True)


if __name__ == "__main__":
    raise SystemExit(main())
