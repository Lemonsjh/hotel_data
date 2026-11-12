"""余乐通预订 / 入住明细：jd01。

余乐通把预订与入住合并记在 T_RoomOrder，用 F_Status 区分阶段
（入住 / 已结帐 / 取消预订 / 取消入住 / 已换房 / 挂起），因此一次查询即可覆盖全流程。
T_BkOrder 预订单自 2024-08 后已停用，不纳入本报表。

采集范围是「抵店时间近 N 天」加上「全部未来预订」，同时涵盖长期住宿中尚未离店的单
（离店时间仍不早于窗口起点）。窗口只设下界，整体替换同口径数据保证幂等。

jd01 表没有 business_date 列，按上述窗口整段替换保证幂等；
窗口外的历史在同一事务内按 pms_daily_days（默认 400 天）清理。

用法
  python yolo_jd01_data.py              # 采集并落库
  python yolo_jd01_data.py --dry-run    # 只取数不落库
环境变量
  YOLO_JD01_DAYS  抵店回溯天数，默认 7
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yolo_common import (  # noqa: E402
    SOURCE_PLATFORM,
    Config,
    YoloError,
    cleanup_rows,
    mysql_connection,
    query,
    report_main,
    save_output,
)
from yolo_sql import BOOKING_QUERY  # noqa: E402

SUMMARY_FILE = "yolo_jd01_summary.json"
TABLE = "jd01_booking_detail"

COLUMNS = (
    "hotel_name", "hotel_id", "source_platform", "order_id", "booking_time", "contact",
    "guest_source", "member_level", "arrival_time", "departure_time", "room_type_name",
    "room_type_id", "room_count", "price_type", "room_price", "prepayment",
    "guarantee_method", "booking_status", "hold_time", "remarks", "operator_name",
    "snapshot_time",
)


def collection_days() -> int:
    try:
        return max(1, int(os.environ.get("YOLO_JD01_DAYS") or "7"))
    except ValueError:
        return 7


def to_datetime(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_decimal(value: str | None, label: str) -> Decimal | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise YoloError(f"{label} 数值无效：{value!r}") from None


def build_rows(records: list[dict[str, str]], config: Config, snapshot: datetime) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    for record in records:
        order_id = (record.get("order_id") or "").strip()
        if not order_id:
            raise YoloError("订单查询返回了空单号。")
        if order_id in seen:
            raise YoloError(f"订单查询单号重复：{order_id}")
        seen.add(order_id)

        rows.append({
            "hotel_name": config.hotel_name,
            "hotel_id": config.hotel_id,
            "source_platform": SOURCE_PLATFORM,
            "order_id": order_id,
            "booking_time": to_datetime(record.get("booking_time")),
            "contact": (record.get("contact") or "").strip() or None,
            "guest_source": (record.get("guest_source") or "").strip() or None,
            "member_level": None,
            "arrival_time": to_datetime(record.get("arrival_time")),
            "departure_time": to_datetime(record.get("departure_time")),
            "room_type_name": (record.get("room_type_name") or "").strip() or None,
            "room_type_id": None,
            # 余乐通一单对应一间房，房间数以单为单位
            "room_count": 1,
            "price_type": (record.get("price_type") or "").strip() or None,
            "room_price": to_decimal(record.get("room_price"), "房价"),
            "prepayment": to_decimal(record.get("prepayment"), "已收金额"),
            "guarantee_method": None,
            "booking_status": (record.get("booking_status") or "").strip() or None,
            "hold_time": None,
            "remarks": (record.get("remarks") or "").strip() or None,
            "operator_name": (record.get("operator_name") or "").strip() or None,
            "snapshot_time": snapshot,
        })
    return rows


def sync_rows(rows: list[dict], config: Config, start: datetime) -> int:
    """整段替换窗口内的订单：先删同口径旧数据再写入，保证重跑幂等。

    窗口只有下界，与查询口径一致：抵店或离店不早于 start 的行都属于待替换范围，
    因此未来预订每轮都会被刷新，不会出现改期后残留旧行。
    写入后在同一事务内按 pms_daily_days（默认 400 天）清理历史。
    """
    if not rows:
        raise YoloError("订单明细为空，已保留目标表原样。")
    condition = ("hotel_id=%s AND source_platform=%s "
                 "AND (arrival_time >= %s OR departure_time >= %s)")
    args = (config.hotel_id, SOURCE_PLATFORM, start, start)
    placeholders = ", ".join(["%s"] * len(COLUMNS))
    column_sql = ", ".join(f"`{name}`" for name in COLUMNS)

    connection = mysql_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{TABLE}` WHERE {condition}", args)
            cursor.executemany(
                f"INSERT INTO `{TABLE}` ({column_sql}) VALUES ({placeholders})",
                [tuple(row[name] for name in COLUMNS) for row in rows],
            )
            cleanup_rows(cursor, TABLE, rows, config.hotel_id, SOURCE_PLATFORM)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


def collect(config: Config, snapshot: datetime) -> dict:
    days = collection_days()
    start = snapshot.replace(microsecond=0) - timedelta(days=days)

    records = query(
        BOOKING_QUERY.format(start=start.strftime("%Y-%m-%d %H:%M:%S")),
        config, timeout=180, required=("order_id", "arrival_time", "booking_status"),
    )
    rows = build_rows(records, config, snapshot)

    dry_run = "--dry-run" in sys.argv
    written = 0 if dry_run or not rows else sync_rows(rows, config, start)
    if not rows:
        print(f"WARN 窗口内（{start:%Y-%m-%d} 之后）无订单记录")

    future = [row for row in rows if row["arrival_time"] and row["arrival_time"] > snapshot]
    summary = {
        "report": "jd01",
        "window": {"arrival_from": start.isoformat(), "arrival_to": None},
        "hotel_id": config.hotel_id,
        "source_platform": SOURCE_PLATFORM,
        "snapshot_time": snapshot.isoformat(),
        "row_count": len(rows),
        "future_booking_count": len(future),
        "max_arrival_time": max((row["arrival_time"] for row in future), default=None),
        "written": written,
        "dry_run": dry_run,
        "business_date": snapshot.date().isoformat(),
    }
    save_output(SUMMARY_FILE, summary, config)
    return summary


def main() -> int:
    return report_main("jd01", collect)


if __name__ == "__main__":
    raise SystemExit(main())
